"""Build the Edi axis-label templates (axis_labels.npz) for reading the Edi scale (axis_labels.py, 0.3.0). Same steps as the
NAVA engine's tools/build_axis_labels.py.

1. python tools/build_axis_labels.py extract VIDEO BOXES.npz
   Takes the key frames of a video (ffmpeg, read-only) and keeps the label box as initial_ocr sees it: the decoded frame
   resized to BASE_RESOLUTION with INTER_AREA. Also stores each key frame's time (s).
2. python tools/build_axis_labels.py cluster OUT_DIR BOXES.npz [BOXES.npz ...]
   Groups the boxes without the trace crossing them (RMS difference CLUSTER_RMS or less) and writes OUT_DIR/clusters.json
   and OUT_DIR/labels.png (cluster numbers at the top left).
3. Look at the picture and write LABELS.json: {"<cluster number>": "<label text>", ...}. Leave out clusters that are not a
   clean label (pop-ups, a label being redrawn).
4. python tools/build_axis_labels.py build OUT_DIR/clusters.json LABELS.json OUT.npz BOXES.npz [BOXES.npz ...]
   Each template is the average of its cluster. A label may have several templates (clusters with the same text).
Run it from anywhere; it imports axis_labels.py and Settings.py from the engine directory.
"""
import json
import os
import subprocess
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import axis_labels as al  # noqa: E402
import Settings  # noqa: E402

CLUSTER_RMS = 5.0


def extract(video, out):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", video], capture_output=True, text=True, check=True)
    w, h = map(int, probe.stdout.strip().split(",")[:2])
    times = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                            "-show_entries", "frame=pts_time", "-of", "csv=p=0", video],
                           capture_output=True, text=True, check=True).stdout.split()
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", video, "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    boxes = []
    x, y, bw, bh = al.BOX
    s = al.SHIFT
    while True:
        buf = p.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        img = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        if (w, h) != Settings.BASE_RESOLUTION:
            img = cv2.resize(img, Settings.BASE_RESOLUTION, interpolation=cv2.INTER_AREA)
        boxes.append(img[y - s:y + bh + s, x - s:x + bw + s])
    p.wait()
    t = np.array([float(v.split(",")[0]) for v in times[:len(boxes)]] + [np.nan] * max(0, len(boxes) - len(times)))
    np.savez_compressed(out, boxes=np.array(boxes), times=t)
    print(f"{len(boxes)} key frames -> {out}")


def _box(stored):
    """Stored box (with SHIFT margin) -> gray box and trace mask without the margin."""
    s = al.SHIFT
    hsv = cv2.cvtColor(stored, cv2.COLOR_BGR2HSV)
    colour = (hsv[..., 1] >= al.COLOR_SAT) & (hsv[..., 2] >= al.COLOR_VAL)
    gray = cv2.cvtColor(stored, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return gray[s:gray.shape[0] - s, s:gray.shape[1] - s], colour[s:colour.shape[0] - s, s:colour.shape[1] - s]


def _load(paths):
    return np.concatenate([np.load(p)["boxes"] for p in paths])


def cluster(out_dir, *boxes_paths):
    boxes = _load(boxes_paths)
    os.makedirs(out_dir, exist_ok=True)
    reps, members = [], []
    for i, stored in enumerate(boxes):
        gray, colour = _box(stored)
        if colour.any():
            continue
        v = al.normalized(gray)
        d = [float(np.sqrt(np.mean((v - r) ** 2))) for r in reps]
        if d and min(d) <= CLUSTER_RMS:
            members[int(np.argmin(d))].append(i)
        else:
            reps.append(v)
            members.append([i])
    tiles = []
    for k, m in enumerate(members):
        g = np.mean([_box(boxes[i])[0] for i in m], 0)
        t = cv2.resize(g, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
        t = np.pad(t, ((14, 2), (2, 2)), constant_values=90)
        cv2.putText(t, f"{k} ({len(m)})", (2, 11), cv2.FONT_HERSHEY_PLAIN, 0.8, 255, 1)
        tiles.append(t)
    if tiles:
        cols = 6
        th, tw = tiles[0].shape
        grid = np.full((-(-len(tiles) // cols) * th, cols * tw), 90, np.float32)
        for j, t in enumerate(tiles):
            grid[(j // cols) * th:(j // cols + 1) * th, (j % cols) * tw:(j % cols + 1) * tw] = t
        cv2.imwrite(os.path.join(out_dir, "labels.png"), np.clip(grid, 0, 255).astype(np.uint8))
    print("clusters (size):", [len(m) for m in members])
    with open(os.path.join(out_dir, "clusters.json"), "w") as f:
        json.dump(members, f)


def build(clusters_path, labels_path, out, *boxes_paths):
    boxes = _load(boxes_paths)
    clusters = json.load(open(clusters_path))
    labels = json.load(open(labels_path))
    named = sorted(labels.items(), key=lambda kv: float(kv[1]))
    texts = [text for _, text in named]
    templates = [al.normalized(np.mean([_box(boxes[i])[0] for i in clusters[int(k)]], 0)).astype(np.float32)
                 for k, _ in named]
    np.savez_compressed(out, texts=np.array(texts), templates=np.stack(templates))
    print([(text, len(clusters[int(k)])) for k, text in named])


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"extract": extract, "cluster": cluster, "build": build}[cmd](*args)
