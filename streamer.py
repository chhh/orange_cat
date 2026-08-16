"""Persistent RTSP readers holding a rolling buffer of recent frames.

Opening an RTSP connection costs about 3.1 seconds, and that was being paid on
every motion event -- long enough for a cat to cross the approach and get
through the flap before any verdict existed. That is tolerable for a
notification and useless for a deterrent.

So each camera gets a thread that holds one connection open and keeps the last
few seconds of frames in memory. An event then reads the buffer instantly.

The second benefit matters as much as the latency: the buffer contains frames
from *before* the trigger fired, when the animal was still approaching at a
distance. Those are the well-isolated frames the classifier actually wants --
the close-ups that arrive after a trigger are usually too large in frame to
measure against their background at all.

Feeding the background model is handled here too, replacing the separate
capture loop. Frames are only blended in when little is moving, so a cat
loitering by the door does not slowly become part of the scenery.
"""

import threading
import time
from collections import deque

import cv2

import detect

# ~2.7s of history at 30fps when keeping every second frame. Frames are big
# (1024x576x3 is 1.7MB), so this is a memory/history trade: 40 frames is
# roughly 70MB for the outdoor camera.
BUFFER_FRAMES = 40
KEEP_EVERY = 2

# How often to consider blending a frame into the background model.
BG_INTERVAL = 20.0

# Only learn background from frames where this little is moving.
BG_QUIET_FRACTION = 0.002

RECONNECT_DELAY = 3.0


class Streamer:
    """One thread, one open connection, one rolling buffer."""

    def __init__(self, camera, url, size=BUFFER_FRAMES):
        self.camera = camera
        self.url = url
        self.buf = deque(maxlen=size)
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self.thread = None
        self.frames_read = 0
        self.reconnects = 0
        self.last_frame_at = None
        self.last_error = ""

    # -- lifecycle --------------------------------------------------------

    def start(self):
        if self.thread and self.thread.is_alive():
            return self
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"streamer-{self.camera}")
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        counter = 0
        next_bg = 0.0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.url)
            if not cap.isOpened():
                self.last_error = "could not open stream"
                self.reconnects += 1
                cap.release()
                self._stop.wait(RECONNECT_DELAY)
                continue

            self.last_error = ""
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.last_error = "read failed, reconnecting"
                    self.reconnects += 1
                    break

                counter += 1
                self.frames_read += 1
                self.last_frame_at = time.time()

                if counter % KEEP_EVERY == 0:
                    with self.lock:
                        self.buf.append(frame)

                now = time.time()
                if now >= next_bg:
                    next_bg = now + BG_INTERVAL
                    self._maybe_learn(frame)

            cap.release()

    # -- background model -------------------------------------------------

    def _maybe_learn(self, frame):
        """Blend into the background only when the scene is quiet."""
        try:
            mask, info = detect.motion_mask(frame, self.camera)
            if mask is None:  # no model yet -- bootstrap from this frame
                detect.update_background(self.camera, frame)
            elif info["motion_frac"] < BG_QUIET_FRACTION:
                detect.update_background(self.camera, frame)
        except Exception as exc:  # never let this kill the reader thread
            self.last_error = f"background update failed: {exc}"

    # -- reading ----------------------------------------------------------

    def snapshot(self, count=15):
        """Frames spread evenly across the buffer, oldest first.

        Spreading rather than taking the newest `count` is deliberate: the
        oldest frames are from before the trigger, when the cat was further
        away and easier to measure.
        """
        with self.lock:
            frames = list(self.buf)
        if not frames:
            return []
        if len(frames) <= count:
            return frames
        step = len(frames) / count
        return [frames[min(int(i * step), len(frames) - 1)] for i in range(count)]

    def status(self):
        with self.lock:
            buffered = len(self.buf)
        age = None if self.last_frame_at is None else round(
            time.time() - self.last_frame_at, 2)
        return {
            "camera": self.camera,
            "alive": bool(self.thread and self.thread.is_alive()),
            "buffered": buffered,
            "frames_read": self.frames_read,
            "reconnects": self.reconnects,
            "seconds_since_frame": age,
            "error": self.last_error,
        }


_streamers = {}


def get(camera, url):
    """Fetch (starting if needed) the streamer for a camera."""
    s = _streamers.get(camera)
    if s is None:
        s = Streamer(camera, url)
        _streamers[camera] = s
        s.start()
    elif not (s.thread and s.thread.is_alive()):
        s.start()
    return s


def all_status():
    return [s.status() for s in _streamers.values()]


def stop_all():
    for s in _streamers.values():
        s.stop()
