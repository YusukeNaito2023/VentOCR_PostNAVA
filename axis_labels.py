"""0.3.0: read the upper-limit label of the Edi vertical axis (the grey number at the top left of the Edi band, e.g. "20", "25", "30").

The Servo-U changes the Edi scale during a recording (validation video 2026-10-24: "25" and "30"). At BASE_RESOLUTION the
digits are only 9 px tall and blurred, so the label is compared as a whole with labels seen before (axis_labels.npz, made
by tools/build_axis_labels.py), as in the NAVA engine 0.2.1: RMS difference of the gray levels after subtracting the
background level. A label is read only when its best template is within MAX_RMS and the next best is at least MIN_GAP
further away; otherwise (a label without a template, a pop-up, a label being redrawn) it is not read and the caller
falls back. Pixels of the Edi trace crossing the label (coloured) are left out of the comparison.

This module imports nothing from the engine (tools/build_axis_labels.py uses it directly).
"""
import os

import cv2
import numpy as np

BOX = (152, 220, 30, 14)        # label box (x, y, w, h) at BASE_RESOLUTION; the text is at about x 162-176, y 222-230
SHIFT = 1                       # the box is also compared shifted by up to this many pixels
MAX_RMS = 4.5                   # gray levels
MIN_GAP = 3.0                   # the second-best template must be at least this much further
MAX_MASKED = 0.25               # at most this fraction of the box may be covered by the trace
COLOR_SAT, COLOR_VAL = 60, 60   # a pixel with saturation and value at least these is part of the trace


def label_box(img, shift=0):
    """Gray label box (float32) and trace mask in a BASE_RESOLUTION BGR image. shift > 0 adds that many pixels on every side."""
    x, y, w, h = BOX
    c = img[y - shift:y + h + shift, x - shift:x + w + shift]
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    colour = ((hsv[..., 1] >= COLOR_SAT) & (hsv[..., 2] >= COLOR_VAL)).astype(np.uint8)
    return cv2.cvtColor(c, cv2.COLOR_BGR2GRAY).astype(np.float32), cv2.dilate(colour, np.ones((3, 3), np.uint8)).astype(bool)


def normalized(gray, mask=None):
    """Label box minus its background level (median of the pixels not covered by the trace)."""
    keep = gray if mask is None or not mask.any() else gray[~mask]
    return gray - np.median(keep)


class AxisLabelReader:
    def __init__(self, path):
        z = np.load(path)
        self.texts = [str(t) for t in z["texts"]]
        self.templates = z["templates"].astype(np.float32)

    def distances(self, img):
        """RMS distance to each template (best over the shifts); None when the trace covers too much of the box."""
        h, w = self.templates.shape[1:]
        gray, mask = label_box(img, SHIFT)
        best = np.full(len(self.texts), np.inf)
        for dy in range(2 * SHIFT + 1):
            for dx in range(2 * SHIFT + 1):
                g, m = gray[dy:dy + h, dx:dx + w], mask[dy:dy + h, dx:dx + w]
                if m.mean() > MAX_MASKED:
                    continue
                diff = (normalized(g, m) - self.templates)[:, ~m]
                best = np.minimum(best, np.sqrt((diff ** 2).mean(axis=1)))
        return None if not np.isfinite(best).any() else best

    def read(self, img):
        """BASE_RESOLUTION BGR image -> label value (float) or None."""
        best = self.distances(img)
        if best is None:
            return None
        # a label may have several templates (e.g. drawn one pixel apart): compare the best of each label
        per_text = {}
        for t, d in zip(self.texts, best):
            per_text[t] = min(d, per_text.get(t, np.inf))
        ranked = sorted(per_text.items(), key=lambda kv: kv[1])
        if not ranked[0][1] <= MAX_RMS:
            return None
        if len(ranked) > 1 and ranked[1][1] - ranked[0][1] < MIN_GAP:
            return None
        return float(ranked[0][0])


def load_reader(engine_dir, file_name):
    path = os.path.join(engine_dir, file_name)
    return AxisLabelReader(path) if os.path.isfile(path) else None
