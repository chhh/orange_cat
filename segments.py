"""Rolling compressed-video buffer: receive without decoding.

Keeping decoded frames in memory costs ~24% of a CPU for two cameras, because
turning H.264 into pixels is expensive and we were doing it continuously for
video nobody looked at. Remuxing instead -- `ffmpeg -c copy` -- writes the
compressed packets straight through without ever decoding them. Measured at 0%
CPU and about 650KB for a rolling ten-second window.

Decoding then happens only when a motion notification arrives, on the few
seconds of footage that matter: 0.146s to decode, against the 3.1s RTSP
handshake it replaces.

    connect on demand    0% idle,  4.0s per event, no pre-trigger frames
    decoded ring buffer 24% idle, 0.25s per event
    segments (this)      0% idle, ~1.0s per event, ~10s of history

The history matters as much as the latency. The stray loiters near the door
before going in, so a notification can look *back* to when it was still
approaching at a distance -- which is the geometry the classifier can actually
measure, unlike the close-ups that arrive once it reaches the flap.

Segments live in /dev/shm so the continuous trickle of small writes never
touches the SSD.
"""

import glob
import os
import shutil
import subprocess
import threading
import time

import cv2

# 2s segments, 6 of them, cycled -- about 12 seconds of history, bounded.
SEGMENT_SECONDS = 2
SEGMENT_COUNT = 6

RESTART_DELAY = 3.0

# 2026-09-07 20:14: both recorders WEDGED. The ffmpegs were alive for 4h16m but
# had written nothing since 18:55 -- 79 minutes blind, discovered only because a
# health watch happened to be armed. The RTSP source was reachable throughout.
#
# Two independent causes, so two fixes:
#   1. ffmpeg had no I/O timeout, so a stalled read blocks forever instead of
#      erroring out. SOCKET_TIMEOUT_US gives it one.
#   2. _supervise blocked on proc.wait(), which only returns when ffmpeg EXITS.
#      A hung-but-alive process was invisible to it. STALE_LIMIT watches the
#      output instead of the process -- the only signal that cannot lie, since
#      what we actually need is fresh segments, not a live pid.
SOCKET_TIMEOUT_US = int(float(os.getenv("SEG_SOCKET_TIMEOUT", "10")) * 1_000_000)
STALE_LIMIT = float(os.getenv("SEG_STALE_LIMIT", "30"))


def _base_dir():
    """Prefer RAM; fall back to a temp dir if /dev/shm is unavailable."""
    if os.path.isdir("/dev/shm"):
        return "/dev/shm/ocp"
    return os.path.join(os.getenv("TMPDIR", "/tmp"), "ocp-segments")


class SegmentRecorder:
    """One ffmpeg process per camera, remuxing RTSP to a rolling window."""

    def __init__(self, camera, url):
        self.camera = camera
        self.url = url
        self.dir = os.path.join(_base_dir(), camera)
        self.proc = None
        self._stop = threading.Event()
        self.thread = None
        self.restarts = 0
        self.stalls = 0
        self.last_error = ""

    # -- lifecycle --------------------------------------------------------

    def start(self):
        if self.thread and self.thread.is_alive():
            return self
        os.makedirs(self.dir, exist_ok=True)
        self._stop.clear()
        self.thread = threading.Thread(target=self._supervise, daemon=True,
                                       name=f"segments-{self.camera}")
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._kill()
        if self.thread:
            self.thread.join(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _newest_mtime(self):
        """mtime of the most recent segment file, or 0 if there are none."""
        try:
            files = [os.path.join(self.dir, f) for f in os.listdir(self.dir)
                     if f.endswith(".mp4")]
            return max((os.path.getmtime(f) for f in files), default=0)
        except OSError:
            return 0

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _command(self):
        return [
            "ffmpeg", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            # Without this a stalled read hangs forever (see STALE_LIMIT).
            "-timeout", str(SOCKET_TIMEOUT_US),
            "-i", self.url,
            "-an",              # no audio; smaller files, fewer codec surprises
            "-c", "copy",       # THE point: remux, never decode
            "-f", "segment",
            "-segment_time", str(SEGMENT_SECONDS),
            "-segment_wrap", str(SEGMENT_COUNT),
            "-reset_timestamps", "1",
            os.path.join(self.dir, "s%03d.mp4"),
        ]

    def _supervise(self):
        while not self._stop.is_set():
            # Recreate every time, not just at start(): /dev/shm can be
            # cleared out from under us by a reboot or by hand, and ffmpeg
            # will not make the directory itself -- it just fails to open the
            # segment, forever.
            try:
                os.makedirs(self.dir, exist_ok=True)
            except OSError as exc:
                self.last_error = f"cannot create {self.dir}: {exc}"
                self._stop.wait(RESTART_DELAY)
                continue
            try:
                self.proc = subprocess.Popen(
                    self._command(), stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE)
            except FileNotFoundError:
                self.last_error = "ffmpeg not installed"
                return
            except Exception as exc:
                self.last_error = f"spawn failed: {exc}"
                self._stop.wait(RESTART_DELAY)
                continue

            # Watch the OUTPUT, not the process. proc.wait() alone waits
            # forever on a wedged ffmpeg -- that is the 79-minute blind spot.
            spawned = time.time()
            while self.proc.poll() is None and not self._stop.is_set():
                age = time.time() - max(self._newest_mtime(), spawned)
                if age > STALE_LIMIT:
                    self.last_error = (f"recorder WEDGED: no new segment for "
                                       f"{age:.0f}s while ffmpeg was alive "
                                       f"-- killed it")
                    self.stalls += 1
                    self._kill()
                    break
                self._stop.wait(2.0)
            self.proc.wait()
            if self._stop.is_set():
                return
            err = b""
            if self.proc.stderr:
                try:
                    err = self.proc.stderr.read() or b""
                except Exception:
                    pass
            self.last_error = err.decode("utf-8", "replace").strip()[-200:] \
                or "ffmpeg exited"
            self.restarts += 1
            self._stop.wait(RESTART_DELAY)

    # -- reading ----------------------------------------------------------

    def _segments(self):
        """Completed segments, oldest first.

        The newest file is still being written, so it is dropped -- a partial
        mp4 has no usable index and decodes to nothing or garbage.
        """
        files = glob.glob(os.path.join(self.dir, "s*.mp4"))
        if len(files) < 2:
            return []
        files.sort(key=os.path.getmtime)
        return files[:-1]

    def frames(self, count=15, segments=2):
        """Decode the newest complete segments and return `count` frames.

        Frames are spread across the whole window rather than taken from the
        end: the earliest ones show the cat still approaching, which is when
        it is far enough away to measure against its background.
        """
        out = []
        for path in self._segments()[-segments:]:
            cap = cv2.VideoCapture(path)
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    out.append(frame)
            except Exception as exc:
                self.last_error = f"decode failed: {exc}"
            finally:
                cap.release()

        if not out or len(out) <= count:
            return out
        step = len(out) / count
        return [out[min(int(i * step), len(out) - 1)] for i in range(count)]

    def age(self):
        """Seconds since the newest complete segment was written.

        None when there is nothing to report. This is how a caller tells
        "the buffer is warm" from "the buffer is full of last minute's
        video" -- see the staleness guard in server.py, and the note there
        about why the two are not the same thing.
        """
        segs = self._segments()
        newest = max((os.path.getmtime(s) for s in segs), default=None)
        return None if newest is None else time.time() - newest

    def latest_frame(self):
        """One recent frame, for background refresh. Cheap: no new connection."""
        segs = self._segments()
        if not segs:
            return None
        cap = cv2.VideoCapture(segs[-1])
        frame = None
        try:
            while True:
                ok, f = cap.read()
                if not ok or f is None:
                    break
                frame = f
        finally:
            cap.release()
        return frame

    def status(self):
        segs = self._segments()
        newest = max((os.path.getmtime(s) for s in segs), default=None)
        return {
            "camera": self.camera,
            "alive": bool(self.thread and self.thread.is_alive()
                          and self.proc and self.proc.poll() is None),
            "segments": len(segs),
            "restarts": self.restarts,
            "stalls": self.stalls,
            "seconds_since_segment": None if newest is None
            else round(time.time() - newest, 2),
            "error": self.last_error,
        }


_recorders = {}


def get(camera, url):
    r = _recorders.get(camera)
    if r is None:
        r = SegmentRecorder(camera, url)
        _recorders[camera] = r
        r.start()
    elif not (r.thread and r.thread.is_alive()):
        r.start()
    return r


def all_status():
    return [r.status() for r in _recorders.values()]


def stop_all():
    for r in _recorders.values():
        r.stop()
    _recorders.clear()
