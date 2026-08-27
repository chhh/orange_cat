"""Find the animal, before asking what colour it is.

Everything upstream of the classifier used to work by subtracting a model of
the empty patio and keeping the largest blob that changed. That is what broke,
repeatedly and silently: it needs a background model that tracks the light, a
region of interest narrow enough to exclude the gate, and an animal large
enough in frame to measure -- and on the nights that mattered at least one of
those three failed. On 2026-08-19 the stray raided twice, at 01:09 and again
at 04:47-04:57, and all three events measured *zero* changed pixels while the
cat sat plainly in the frame.

A detector has none of those dependencies. It does not care what the patio
looked like ten minutes ago, where we drew a box, or how the light moved. On
the exact frames the old path scored as empty it finds the cat in 15 of 15, at
0.56-0.81 confidence.

Two things it is NOT good for, both measured rather than assumed:

  * Species. The same cat came back as "cat", then "dog", then "cow" across
    one burst. Treat a detection as "an animal is here" and nothing more --
    the colour test is what identifies it.
  * Night colour, on its own. A tight box does not brighten the fur: inside
    it the stray still reads only ~3% ginger under the old `S > 90` test.
    What the box buys is the ability to *relax* that test, because a box is
    almost all animal where a motion mask was half wall. At `S > 40` the
    stray measures 29-55% against residents at 0.0-0.6%.

Runs under cv2.dnn on a plain ONNX export, deliberately: `ultralytics` plus
torch is 1.4 GB, and this has to fit on a Raspberry Pi at Dima's eventually.
Costs ~31 ms/frame on CPU, so a 15-frame burst is well under a second.
"""

import os

import cv2
import numpy as np

MODEL_PATH = os.getenv("ANIMAL_MODEL", "models/yolov8n.onnx")
INPUT_SIZE = 640

# COCO ids that could plausibly be the animal at this door. Cat and dog carry
# the load; the rest are here because the detector genuinely returns them for
# a cat at night (cow at 0.50 on one frame), and rejecting those would throw
# away good detections over a label we have already decided not to trust.
ANIMAL_CLASSES = {14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
PERSON_CLASS = 0

# Low, on purpose. A missed frame is cheap because fifteen of them vote, and
# the colour test still has to agree before anything is called a ginger cat.
CONF_THRESHOLD = float(os.getenv("ANIMAL_CONF", "0.25"))
NMS_THRESHOLD = 0.45

_net = None
_unavailable = None


def available():
    """Is the detector usable? False means fall back to the old motion path."""
    return load_net() is not None


def load_net():
    global _net, _unavailable
    if _net is not None:
        return _net
    if _unavailable:
        return None
    if not os.path.exists(MODEL_PATH):
        _unavailable = f"no model at {MODEL_PATH}"
        return None
    try:
        net = cv2.dnn.readNetFromONNX(MODEL_PATH)
        # OpenCV 5 defaults to a newer engine that rejects this export; the
        # classic path reads it fine.
        try:
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        except AttributeError:
            pass
        _net = net
        return _net
    except cv2.error as exc:
        _unavailable = str(exc)
        return None


def _letterbox(frame):
    """Resize into a square, preserving aspect ratio, padding the remainder.

    Squashing to 640x640 instead would distort a cat walking across the frame
    into something the model was never trained on.
    """
    h, w = frame.shape[:2]
    scale = min(INPUT_SIZE / w, INPUT_SIZE / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(frame, (nw, nh))
    canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, np.uint8)
    ox, oy = (INPUT_SIZE - nw) // 2, (INPUT_SIZE - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = resized
    return canvas, scale, ox, oy


def detect(frame, want_person=False):
    """Boxes for animals in the frame, best first.

    Returns a list of {"box": (x0, y0, x1, y1), "conf": float, "cls": int},
    in the frame's own pixel coordinates. Empty list when nothing is found or
    the model is unavailable -- callers must handle that, since it is also
    what an empty patio looks like.
    """
    net = load_net()
    if net is None or frame is None:
        return []

    canvas, scale, ox, oy = _letterbox(frame)
    blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, (INPUT_SIZE, INPUT_SIZE),
                                 swapRB=True, crop=False)
    net.setInput(blob)
    out = net.forward()

    # YOLOv8 emits (1, 4 + numclasses, numanchors); transpose to per-anchor
    # rows so each row is [cx, cy, w, h, score per class].
    pred = np.squeeze(out)
    if pred.ndim != 2:
        return []
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T

    wanted = set(ANIMAL_CLASSES)
    if want_person:
        wanted.add(PERSON_CLASS)

    scores = pred[:, 4:]
    cls_ids = np.argmax(scores, axis=1)
    confs = scores[np.arange(len(scores)), cls_ids]
    keep = (confs >= CONF_THRESHOLD) & np.isin(cls_ids, list(wanted))
    if not keep.any():
        return []

    rows, cls_ids, confs = pred[keep], cls_ids[keep], confs[keep]
    h, w = frame.shape[:2]
    boxes = []
    for cx, cy, bw, bh in rows[:, :4]:
        x0 = (cx - bw / 2 - ox) / scale
        y0 = (cy - bh / 2 - oy) / scale
        boxes.append([int(x0), int(y0), int(bw / scale), int(bh / scale)])

    idxs = cv2.dnn.NMSBoxes(boxes, confs.astype(float).tolist(),
                            CONF_THRESHOLD, NMS_THRESHOLD)
    if len(idxs) == 0:
        return []

    out_boxes = []
    for i in np.array(idxs).flatten():
        x, y, bw, bh = boxes[i]
        out_boxes.append({
            "box": (max(0, x), max(0, y), min(w, x + bw), min(h, y + bh)),
            "conf": round(float(confs[i]), 3),
            "cls": int(cls_ids[i]),
        })
    out_boxes.sort(key=lambda d: -d["conf"])
    return out_boxes


def _overlap(box, other):
    """Fraction of `box` that falls inside `other`."""
    ax0, ay0, ax1, ay1 = box
    bx0, by0, bx1, by1 = other
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area = (ax1 - ax0) * (ay1 - ay0)
    return inter / area if area > 0 else 0.0


# A person standing at the door is the one thing that must never be sprayed,
# and the species label cannot be trusted to say so: on 2026-08-23 at 09:06
# Dima was detected as class 17 and classified `orange_cat` at 35.3% warm.
# The person class fired on the same frame at IoU 0.95, so the box was right
# and only the label was wrong. Any animal box this well covered by a person
# box is that person.
PERSON_OVERLAP = 0.5


def best_box(frame, min_area_frac=0.0008, check_people=True):
    """The most confident animal, or None.

    `min_area_frac` throws away specks too small to carry colour -- measured
    against the real raids, which occupied 1.5-4% of the frame, so this is a
    long way below anything we need to keep.

    With `check_people`, the returned detection carries `person_overlap`: how
    much of the animal box a person box covers. Callers must refuse to fire on
    anything above `PERSON_OVERLAP`.
    """
    h, w = frame.shape[:2]
    dets = detect(frame, want_person=check_people)
    people = [d for d in dets if d["cls"] == PERSON_CLASS]
    for d in dets:
        if d["cls"] == PERSON_CLASS:
            continue
        x0, y0, x1, y1 = d["box"]
        if (x1 - x0) * (y1 - y0) < min_area_frac * w * h:
            continue
        cover = max((_overlap(d["box"], p["box"]) for p in people), default=0.0)
        return {**d, "person_overlap": round(cover, 3)}
    return None
