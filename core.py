"""PostNAVA (Servo-U Edi monitor screen) VentOCR Engine

- OCR: Edipeak, Edimin (values on the right), and the upper limit of the Edi vertical axis (scale label)
- 1 breath = from an Edimin display update (onset of neural inspiration) to the next breath's Edimin update
- Waveform: Edi (only one)

The on-screen values are updated for each breath the Servo-U detects:
    Edimin  … updated when Edi rises (onset of neural inspiration). Minimum of the preceding expiration
    Edipeak … updated after Edi passes its peak and starts to fall (end of neural inspiration). Peak value of that breath
    If no breath is detected for about 10 s, both become "***"
When a value equals the previous one the display does not change and the update is invisible, so breaths are counted
by Edipeak updates, and the Edimin update just before each is taken as the breath onset (if invisible, it is estimated from the typical lag).

The waveform screen sweeps left to right at a constant speed; on reaching the right edge it returns to the left edge and overwrites the old waveform (wrap around).
In each frame the blank ahead of the pen tip (erase band) is located and the pen-tip position recorded (initial_ocr);
the sweep speed and phase are estimated from the whole video to determine the pen-tip position in each frame (post_ocr).
A breath's waveform is cut out as the columns from the start pen tip to the end pen tip, from the last frame (and, for breaths longer than one sweep, earlier frames too) (extract_waves).
"""
import json
import os
import re

import cv2
import numpy as np
import pandas as pd
from tqdm import tqdm

from . import Settings

ENGINE_VERSION = "0.2.0"
VENTOCR_TESTED = "0.5.5"

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_KEYS = list(Settings.ROIs)
NO_VALUE = "***"


# ============================================================
# EasyOCR initialization (same as V600_ventocr)
# ============================================================

_EASYOCR_LOCKFILE = "easyocr_init.lock"


def _get_easyocr_model_dir():
    model_dir = os.path.join(".", "_runtime_models", "easyocr")
    os.makedirs(model_dir, exist_ok=True)
    return model_dir


def _easyocr_model_ready(model_dir):
    from pathlib import Path
    p = Path(model_dir)
    if not p.exists():
        return False
    patterns = ["craft*.pth", "dbnet*.pth", "latin*.pth", "english_g2*.pth", "standard*.pth"]
    return any(list(p.glob(pat)) for pat in patterns)


def _acquire_easyocr_file_lock(model_dir, timeout=300, poll=0.5):
    import time
    lock_path = os.path.join(model_dir, _EASYOCR_LOCKFILE)
    start = time.time()
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            return fd, lock_path
        except FileExistsError:
            if (time.time() - start) > timeout:
                raise TimeoutError(f"Timeout waiting for EasyOCR lock: {lock_path}")
            time.sleep(poll)


def _release_easyocr_file_lock(fd, lock_path):
    try:
        os.close(fd)
    except Exception:
        pass
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass


def _ensure_easyocr_reader_ready(gpu_enabled):
    from easyocr import Reader
    model_dir = _get_easyocr_model_dir()
    if _easyocr_model_ready(model_dir):
        return Reader(["en"], gpu=gpu_enabled, model_storage_directory=model_dir, download_enabled=True)

    fd, lock_path = _acquire_easyocr_file_lock(model_dir)
    try:
        if not _easyocr_model_ready(model_dir):
            Reader(["en"], gpu=gpu_enabled, model_storage_directory=model_dir, download_enabled=True)
    finally:
        _release_easyocr_file_lock(fd, lock_path)

    return Reader(["en"], gpu=gpu_enabled, model_storage_directory=model_dir, download_enabled=True)


# ============================================================
# Common
# ============================================================

def _normalize_frame(frame):
    """Resize to the base resolution (1280x720)."""
    w, h = Settings.BASE_RESOLUTION
    if frame.shape[1] != w or frame.shape[0] != h:
        frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    return frame


def _to_gray(frame):
    return np.dot(frame[..., :3], [0.114, 0.587, 0.299]).astype(np.uint8)


def _crop(img, roi):
    x, y, w, h = roi
    return img[y:y + h, x:x + w]


def _runs(mask):
    """List of (start, end) of consecutive True runs (end inclusive)."""
    idx = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return list(zip(idx[0::2], idx[1::2] - 1))


def _screen_crop(gray):
    return np.hstack([_crop(gray, r) for r in Settings.SCREEN_CHECK_ROIS]) if Settings.SCREEN_CHECK_ROIS else None


def _load_reference(name):
    path = os.path.join(ENGINE_DIR, name)
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE) if os.path.isfile(path) else None
    return None if img is None else img.astype(np.int16)


def _scale_label_diff(frame, gray, ref):
    """Difference between the vertical-axis upper-limit label and the reference image (excluding pixels overlapped by the Edi trace)."""
    if ref is None:
        return np.nan
    x, y, w, h = Settings.SCALE_LABEL_ROI
    crop = gray[y:y + h, x:x + w].astype(np.int16)
    if crop.shape != ref.shape:
        return np.nan
    y0 = Settings.EDI_ROWS[0]
    trace = edi_mask(frame)[y - y0:y - y0 + h, x:x + w] if y >= y0 else np.zeros(crop.shape, bool)
    trace = cv2.dilate(trace.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    if trace.all():
        return np.nan
    return round(float(np.abs(crop - ref)[~trace].mean()), 2)


def _format_time(frame, fps):
    """Build an HH:MM:SS:CC timestamp from a frame number"""
    total_seconds = frame / fps
    s = int(total_seconds)
    centiseconds = int((total_seconds - s) * 100)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{ss:02d}:{centiseconds:02d}"


def edi_mask(frame):
    """Pixels of the waveform color within the Edi band (EDI_ROWS), excluding pixels of the Edi bar at the right edge."""
    y0, y1 = Settings.EDI_ROWS
    hsv = cv2.cvtColor(frame[y0:y1], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    lo, hi = Settings.EDI_HUE
    mask = (h >= lo) & (h <= hi) & (s >= Settings.EDI_SAT_MIN) & (v >= Settings.EDI_VAL_MIN)
    bx0, bx1 = Settings.EDI_BAR_X
    bar = (mask[:, bx0:bx1] & (s[:, bx0:bx1] >= Settings.EDI_BAR_SAT_MIN)).astype(np.uint8)
    if bar.any():
        k = 2 * Settings.EDI_BAR_PAD + 1
        near = cv2.dilate(bar, np.ones((k, k), np.uint8)).astype(bool)
        keep = v[:, bx0:bx1] >= Settings.EDI_BAR_TRACE_VAL_MIN
        keep[:, Settings.EDI_BAR_TRACE_X_MAX - bx0:] = False
        mask[:, bx0:bx1] &= ~near | keep
    return mask


def detect_pen_tip(frame, gray):
    """Pen-tip x (screen coordinates): one column before the left edge of the columns where neither ECG nor Edi is drawn (erase band).

    NaN if no erase band is found (e.g. it overlaps the Edi bar at the right edge, or just after returning to the left edge).
    """
    x0, x1 = Settings.SWEEP_X_LEFT, Settings.SWEEP_X_RIGHT
    e0, e1 = Settings.ECG_ROWS
    drawn = (gray[e0:e1, x0:x1] > Settings.ECG_GRAY_MIN).any(0) | edi_mask(frame)[:, x0:x1].any(0)
    gaps = [(a + x0, b + x0) for a, b in _runs(~drawn)
            if Settings.SWEEP_GAP_MIN <= b - a + 1 <= Settings.SWEEP_GAP_MAX and a + x0 >= Settings.SWEEP_GAP_X_MIN]
    if len(gaps) != 1:
        return np.nan
    return float(gaps[0][0] - 1)


# ============================================================
# OCR result parsing
# ============================================================

def _parse_ocr(result):
    """EasyOCR result → (value string, raw string, confidence string).

    "***" (no breath detected) sets the value to NO_VALUE. Values are shown with one decimal place, but the
    decimal point is sometimes missed, so a number of 2+ digits without a decimal point gets one inserted before the last digit (the raw string is kept in *_raw).
    """
    texts = [t for _, t, _ in result]
    confs = [s for _, _, s in result]
    raw = ", ".join(texts)
    conf = ", ".join(f"{s:.2f}" for s in confs)
    joined = "".join(texts)
    if "*" in joined:
        return NO_VALUE, raw, conf
    digits = re.sub(r"[^0-9.]", "", joined)
    if not re.fullmatch(r"\d+\.?\d*", digits or ""):
        return "", raw, conf
    if "." not in digits and len(digits) >= 2:
        digits = digits[:-1] + "." + digits[-1]
    return digits.rstrip("."), raw, conf


# ============================================================
# initial_ocr
# ============================================================

def initial_ocr(frames, total_frames, subfolder, settings={},
                progress_callback=None, frames_dir=None):
    """
    Read Edipeak and Edimin in each frame and record the pen-tip position.

    Values are OCR'd only in frames where the ROI image has changed since the last OCR; otherwise the previous value is carried over.
    *_Diff is the magnitude of change from the previous frame (used to detect display updates).

    Returns:
        pd.DataFrame (columns: Frame, BreathMark,
                      EdiPeak, EdiPeak_raw, EdiPeak_conf, EdiPeak_Diff, Edimin, Edimin_raw, Edimin_conf, Edimin_Diff,
                      OCR_Done, Sweep_Raw, Screen_Diff, Scale_Diff)
    """
    opts = settings.get("initial_ocr", {})
    skip_keys = opts.get("skip_keys", [])
    gpu_enabled = opts.get("use_gpu", True)
    diff_thresh = float(opts.get("diff_thresh", 2.0))

    reader = _ensure_easyocr_reader_ready(gpu_enabled)
    print(f"GPU:{gpu_enabled}")

    screen_ref = _load_reference(Settings.SCREEN_REFERENCE_IMAGE)
    scale_ref = _load_reference(Settings.SCALE_REFERENCE_IMAGE)
    rois = Settings.ROIs
    keys = [k for k in rois if k not in skip_keys]

    last_ocr_roi = {k: None for k in keys}
    prev_roi = {k: None for k in keys}
    last_val = {k: ("", "", "0.00") for k in keys}
    data = []

    with tqdm(total=total_frames, desc="OCR Processing") as pbar:
        for frame_count, color_frame in enumerate(frames):
            frame = _normalize_frame(color_frame)
            gray = _to_gray(frame)
            row = {"Frame": frame_count, "BreathMark": 0, "OCR_Done": 0}

            # --- Values (OCR only when the display changed) ---
            for key in keys:
                roi = _crop(gray, rois[key]).astype(np.int16)
                row[f"{key}_Diff"] = round(float(np.abs(roi - prev_roi[key]).mean()), 2) if prev_roi[key] is not None else np.nan
                prev_roi[key] = roi
                ref = last_ocr_roi[key]
                if ref is None or np.abs(roi - ref).mean() > diff_thresh:
                    try:
                        result = reader.readtext(roi.astype(np.uint8), allowlist="0123456789.*")
                        last_val[key] = _parse_ocr(result)
                    except Exception:
                        last_val[key] = ("", "", "0.00")
                    last_ocr_roi[key] = roi
                    row["OCR_Done"] = 1
                row[key], row[f"{key}_raw"], row[f"{key}_conf"] = last_val[key]

            # --- Pen-tip position ---
            row["Sweep_Raw"] = detect_pen_tip(frame, gray)

            # --- Is this the Edi monitor screen? (compare static text with the reference image) ---
            crop = _screen_crop(gray)
            if screen_ref is not None and crop is not None and crop.shape == screen_ref.shape:
                row["Screen_Diff"] = round(float(np.abs(crop.astype(np.int16) - screen_ref).mean()), 2)
            else:
                row["Screen_Diff"] = np.nan
            row["Scale_Diff"] = _scale_label_diff(frame, gray, scale_ref)

            data.append(row)
            pbar.update(1)
            if progress_callback is not None and (frame_count % 100 == 0 or frame_count == total_frames - 1):
                progress_callback(current=frame_count, total=total_frames, message="OCR Processing")

    df = pd.DataFrame(data)
    print(f"[initial_ocr] {len(df)} frames processed")
    return df


# ============================================================
# Sweep model
# ============================================================

def _circ_center(values, period, trim):
    """Representative value of values with period `period` (circular mean excluding outliers)."""
    ang = values / period * 2 * np.pi
    center = np.angle(np.mean(np.exp(1j * ang)))
    for _ in range(3):
        d = np.angle(np.exp(1j * (ang - center))) / (2 * np.pi) * period
        keep = np.abs(d) <= trim
        if keep.sum() == 0:
            break
        center = np.angle(np.mean(np.exp(1j * ang[keep])))
    return center / (2 * np.pi) * period


def _phase_track(tip, valid, speed, W, window, block, trim):
    """Per-block phase (representative value of tip − speed*i mod W), joined with wrap-arounds unwrapped."""
    n = len(tip)
    idx = np.arange(n)
    phase = np.mod(tip - speed * idx, W)
    centers = np.arange(0, n, block)
    ph = np.array([_circ_center(phase[sel], W, trim) if (sel := valid & (np.abs(idx - c) <= window)).sum() >= 5 else np.nan
                   for c in centers], float)
    ok = np.isfinite(ph)
    if not ok.any():
        return None, None
    return centers[ok], np.unwrap(ph[ok] / W * 2 * np.pi) / (2 * np.pi) * W


def fit_sweep(tip_raw, fps, window=None, block=None, trim=25, tol=12):
    """Determine the pen-tip position in each frame.

    The sweep runs at constant speed, so tip ≈ speed*i + phase (mod W). A representative phase is computed per block (outlier-robust
    circular mean), the blocks are joined, and the speed is corrected from their slope. Robust to detection errors, such as a false blank appearing stationary while Edi is not drawn.

    Args:
        tip_raw: pen tip detected in each frame (screen x). NaN for frames where detection failed
    Returns:
        dict(
            tip_u:  pen-tip position in each frame (distance from X_LEFT; continuous value with wrap-arounds unwrapped),
            speed:  sweep speed (px/frame),
            detected: whether the detected value was used for the frame,
        )
    """
    W = Settings.SWEEP_X_RIGHT - Settings.SWEEP_X_LEFT
    # 0.2.0: block widths (in frames) are derived from seconds (at 10 fps, the same 150 / 25 / 20 frames as 0.1.x). trim and tol are in pixels
    window = window if window is not None else max(1, round(15.0 * fps))
    block = block if block is not None else max(1, round(2.5 * fps))
    n = len(tip_raw)
    tip = np.asarray(tip_raw, float) - Settings.SWEEP_X_LEFT
    valid = np.isfinite(tip)
    idx = np.arange(n)
    speed = Settings.SWEEP_PX_PER_SEC / fps
    if valid.sum() < 10:
        return {"tip_u": speed * idx, "speed": speed, "detected": np.zeros(n, bool)}

    # Rough speed estimate: median advance between two points lag frames apart (wrap-around absorbed by mod)
    lag = max(1, round(2.0 * fps))
    if n > lag:
        both = valid[:-lag] & valid[lag:]
        if both.sum() >= 20:
            est = np.median(np.mod(tip[lag:][both] - tip[:-lag][both], W)) / lag
            if 0 < est * lag < W / 2:
                speed = float(est)

    # Speed correction: add the phase slope (linear regression excluding outliers) to the speed
    for _ in range(3):
        centers, ph_u = _phase_track(tip, valid, speed, W, window, block, trim)
        if centers is None or len(centers) < 3:
            break
        keep = np.ones(len(centers), bool)
        for _ in range(3):
            slope, icpt = np.polyfit(centers[keep], ph_u[keep], 1)
            r = ph_u - (slope * centers + icpt)
            keep = np.abs(r) <= max(3 * np.std(r[keep]), 5)
        speed += float(slope)
        if abs(slope) < 1e-4:
            break

    centers, ph_u = _phase_track(tip, valid, speed, W, window, block, trim)
    model_u = speed * idx + (np.interp(idx, centers, ph_u) if centers is not None else 0.0)
    model_u = model_u - np.floor(model_u[0] / W) * W  # put the first value in [0, W)

    # Use the detected value in frames where it is close to the model (the actual pen tip in that image)
    tip_u = model_u.copy()
    d = np.mod(tip - model_u + W / 2, W) - W / 2
    use = valid & (np.abs(d) <= tol)
    tip_u[use] = model_u[use] + d[use]
    tip_u = np.maximum.accumulate(tip_u)  # never moves backward
    return {"tip_u": tip_u, "speed": speed, "detected": use}


# ============================================================
# post_ocr
# ============================================================

def _mode(series):
    s = series.dropna()
    s = s[s.astype(str) != ""]
    return s.mode().iloc[0] if len(s) else np.nan


def _display_events(df, key, diff_thresh, min_gap):
    """Positions (df row numbers) of frames where the display was updated.

    Frames whose ROI image changed from the previous frame (for the first frame of a segment, when the value changed).
    When consecutive frames change (mid-redraw), the last frame is taken.
    """
    diff = pd.to_numeric(df[f"{key}_Diff"], errors="coerce")
    value = df[key].astype(str)
    changed = (diff > diff_thresh) | (diff.isna() & (value != value.shift()))
    changed.iloc[0] = False
    events = []
    for i in np.flatnonzero(changed.to_numpy()):
        if events and i - events[-1] < min_gap:
            events[-1] = int(i)
        else:
            events.append(int(i))
    return events


def post_ocr(df, Analyze_Id, user_ID, settings={}):
    """
    Find breath boundaries (Edipeak/Edimin display updates) and assign breath numbers and pen-tip positions.

    Returns:
        (df_frames, df_breaths)
    """
    opts = settings.get("post_ocr", {})
    ocr_opts = settings.get("initial_ocr", {})
    fps = float(opts.get("fps", 10))
    diff_thresh = float(ocr_opts.get("diff_thresh", 2.0))
    min_gap = max(1, int(round(float(opts.get("min_breath_sec", 0.3)) * fps)))
    max_lag = int(round(float(opts.get("max_onset_lag_sec", 1.5)) * fps))
    shift = int(round(float(opts.get("onset_shift_sec", -0.3)) * fps))
    abnormal_thresh = float(opts.get("abnormal_screen_diff", 15.0))
    scale_thresh = float(opts.get("abnormal_scale_diff", 8.0))
    scale_max = float(opts.get("edi_scale_max", Settings.EDI_DEFAULT_SCALE))

    df = df.sort_values("Frame").reset_index(drop=True).copy()
    n = len(df)

    # Frames that are not the Edi monitor screen
    screen = pd.to_numeric(df["Screen_Diff"], errors="coerce") if "Screen_Diff" in df else pd.Series(np.nan, index=df.index)
    df["Abnormal"] = (screen > abnormal_thresh).astype(int)
    # Frames whose vertical-axis upper limit differs from the reference image (edi_scale_max): waveform values cannot be converted
    scale_diff = pd.to_numeric(df["Scale_Diff"], errors="coerce") if "Scale_Diff" in df else pd.Series(np.nan, index=df.index)
    df["Scale_Changed"] = (scale_diff > scale_thresh).astype(int)
    df.loc[df["Scale_Changed"] == 1, "Abnormal"] = 1

    # --- Display updates ---
    peak_events = _display_events(df, "EdiPeak", diff_thresh, min_gap) if "EdiPeak_Diff" in df else []
    min_events = _display_events(df, "Edimin", diff_thresh, min_gap) if "Edimin_Diff" in df else []
    abnormal = df["Abnormal"].to_numpy() == 1
    peak_str = df["EdiPeak"].astype(str).to_numpy() if "EdiPeak" in df else np.full(n, "")
    # Breath = Edipeak updated to a number (changing to "***" is not a breath)
    peak_events = [p for p in peak_events if not abnormal[p] and peak_str[p] not in ("", NO_VALUE, "nan")]
    min_events = [m for m in min_events if not abnormal[m]]
    df["EdiPeak_Update"] = 0
    df.loc[peak_events, "EdiPeak_Update"] = 1
    df["Edimin_Update"] = 0
    df.loc[min_events, "Edimin_Update"] = 1

    # Breath onset: the Edimin update just before the Edipeak update (within max_onset_lag_sec)
    onsets, paired = [], []
    prev_p = -1
    for p in peak_events:
        cand = [m for m in min_events if prev_p < m <= p and p - m <= max_lag]
        onsets.append(cand[-1] if cand else None)
        if cand:
            paired.append(p - cand[-1])
        prev_p = p
    typical_lag = int(round(np.median(paired))) if paired else int(round(0.4 * fps))
    starts, estimated = [], []
    cut_at_start = False   # the first breath's onset is before the start of the recording (breath cut at the start)
    for p, o in zip(peak_events, onsets):
        s = (o if o is not None else p - typical_lag) + shift
        if not starts and s < 0:
            cut_at_start = True
        s = min(max(s, 0), n - 1)
        if starts and s <= starts[-1]:
            s = starts[-1] + 1
        if s >= n:
            continue
        starts.append(s)
        estimated.append(o is None)

    df["BreathMark"] = 0
    df.loc[starts, "BreathMark"] = 1
    df["BreathNumber"] = df["BreathMark"].cumsum()
    lead = not starts or starts[0] > 0
    if lead:
        df["BreathNumber"] += 1          # frames before the first boundary are breath 1 (breath cut at the start)
    df["Time"] = df["Frame"].apply(lambda f: _format_time(f, fps))

    # Numeric values ("***" becomes NaN)
    for key in OCR_KEYS:
        if key in df:
            df[f"{key}_Value"] = pd.to_numeric(df[key].where(df[key].astype(str) != NO_VALUE), errors="coerce")

    # --- Pen-tip position ---
    sw = fit_sweep(df["Sweep_Raw"].to_numpy(float) if "Sweep_Raw" in df else np.full(n, np.nan), fps)
    W = Settings.SWEEP_X_RIGHT - Settings.SWEEP_X_LEFT
    df["Sweep_U"] = np.round(sw["tip_u"], 2)
    df["Sweep_X"] = np.round(Settings.SWEEP_X_LEFT + np.mod(sw["tip_u"], W), 1)
    df["Sweep_Detected"] = sw["detected"].astype(int)
    df["Sweep_Rate"] = round(sw["speed"] * fps, 4)  # px/s
    print(f"[post_ocr] sweep {sw['speed'] * fps:.2f} px/s, detected {sw['detected'].mean() * 100:.1f}% of frames")

    # --- Per-breath table ---
    est_of = {s: e for s, e in zip(starts, estimated)}
    peak_set = set(peak_events)
    rows = []
    groups = list(df.groupby("BreathNumber", sort=True))
    for k, (no, g) in enumerate(groups):
        s, e = int(g.index[0]), int(g.index[-1])
        normal = g[g["Abnormal"] == 0]
        nxt = groups[k + 1][1] if k + 1 < len(groups) else None
        nxt_normal = nxt[nxt["Abnormal"] == 0] if nxt is not None else None
        # From this breath's Edipeak update (the first update within the breath) to just before the next breath's Edipeak update
        p = next((i for i in range(s, e + 1) if i in peak_set), None)
        q = next((i for i in range(e + 1, n) if i in peak_set), n)
        row = {
            "BreathNumber": int(no),
            "Frame": int(df.at[s, "Frame"]),
            "End_Frame": int(df.at[e, "Frame"]),
            "Time": df.at[s, "Time"],
            "Duration": round((e - s + 1) / fps, 2),
            "Start_Time": round(float(df.at[s, "Frame"]) / fps, 2),
            "End_Time": round(float(df.at[e, "Frame"] + 1) / fps, 2),
        }
        # Edipeak: the value shown by this breath's update
        if p is not None:
            seg = df.iloc[p:q]
            row["EdiPeak"] = _mode(seg.loc[seg["Abnormal"] == 0, "EdiPeak_Value"])
            row["EdiPeak_Frame"] = int(df.at[p, "Frame"])
        else:
            row["EdiPeak"], row["EdiPeak_Frame"] = np.nan, np.nan
        # Edimin: minimum of this breath's expiration = value shown at the next breath's onset. *_displayed is the value shown during this breath
        row["Edimin"] = _mode(nxt_normal["Edimin_Value"]) if nxt_normal is not None and "Edimin_Value" in df else np.nan
        row["Edimin_displayed"] = _mode(normal["Edimin_Value"]) if "Edimin_Value" in df else np.nan
        row["Onset_Estimated"] = int(est_of.get(s, False)) if s in est_of else np.nan
        row["Edi_Scale"] = scale_max
        alerts = []
        if k == 0 and (lead or cut_at_start):
            alerts.append("Fragment")   # breath already in progress when the recording starts
        if nxt is None:
            alerts.append("Fragment")   # breath cut off at the end of the recording
        if g["Scale_Changed"].any():
            alerts.append("ScaleChanged")  # vertical-axis upper limit has changed (no waveform output)
        if g["Abnormal"].any():
            alerts.append("Abnormal")
        if row["Onset_Estimated"] == 1:
            alerts.append("OnsetEstimated")
        if "EdiPeak" in g and (g["EdiPeak"].astype(str) == NO_VALUE).any():
            alerts.append("NoBreathDetected")  # ventilator detected no breath for about 10 s ("***")
        row["Alert"] = ",".join(dict.fromkeys(alerts))
        row["Abnormal"] = int(g["Abnormal"].any())
        row["E_min"], row["E_max"] = 0.0, float(row["Edi_Scale"])
        rows.append(row)
    df_breaths = pd.DataFrame(rows)
    if len(df_breaths):
        df["Alert"] = df["BreathNumber"].map(df_breaths.set_index("BreathNumber")["Alert"]).fillna("")
    else:
        df["Alert"] = ""

    print(f"[post_ocr] {len(df)} frames, {len(df_breaths)} breaths "
          f"({len(peak_events)} Edipeak updates, {sum(estimated)} onsets estimated, typical lag {typical_lag / fps:.1f}s)")
    return df, df_breaths


# ============================================================
# extract_waves
# ============================================================

def _fill_small_gaps(v, max_gap):
    v = v.copy()
    nan = ~np.isfinite(v)
    for a, b in _runs(nan):
        if a > 0 and b < len(v) - 1 and b - a + 1 <= max_gap:
            v[a:b + 1] = np.interp(np.arange(a, b + 1), [a - 1, b + 1], [v[a - 1], v[b + 1]])
    return v


def extract_column_y(frame, xs):
    """Edi y for each screen column xs (screen x).

    Returns:
        (median y of pixels of that color, topmost y, bottommost y). NaN for columns with no pixels and EDI_SKIP_X columns
    """
    mask = edi_mask(frame)
    for a, b in Settings.EDI_SKIP_X:
        mask[:, a:b + 1] = False
    sub = mask[:, xs]
    y0 = Settings.EDI_ROWS[0]
    out = np.full((3, len(xs)), np.nan)
    for c in np.flatnonzero(sub.any(0)):
        rows = np.flatnonzero(sub[:, c])
        out[:, c] = y0 + np.median(rows), y0 + rows[0], y0 + rows[-1]
    return out


def y_to_edi(y, scale):
    """Screen y → Edi (µV)."""
    return (Settings.EDI_Y_ZERO - y) * scale / (Settings.EDI_Y_ZERO - Settings.EDI_Y_TOP)


def finish_wave(cols, scale, xs=None):
    """Per-column y (result of extract_column_y) → Edi (µV).

    Columns not drawn because the trace exceeds the vertical axis (clipping):
      - if the trace in the neighboring columns on both sides reaches the top (bottom) edge, use the upper (lower) limit value
      - if only one side reaches the top edge (e.g. the other side is after the trace came back down while hidden by the right-edge bar or the top-left scale label),
        connect linearly from the upper-limit value to the value on the other side

    Returns:
        (array of values, number of points clipped at the top edge)
    """
    top = Settings.EDI_Y_TOP + Settings.EDI_CLIP_MARGIN
    bottom = Settings.EDI_ROWS[1] - 1 - Settings.EDI_CLIP_MARGIN
    ys, ymin, ymax = (np.asarray(c, float).copy() for c in cols)
    xs = np.asarray(xs) if xs is not None else np.full(len(ys), -1)
    ox0, ox1, oy = Settings.EDI_LABEL_OCCLUSION
    top_of = np.where((xs >= ox0) & (xs <= ox1), max(oy, top), top)  # per-column threshold for "reached the top edge"
    clipped = 0
    nan = ~np.isfinite(ys)
    for a, b in _runs(nan):
        # Both neighbors (the nearest 3 columns on each side, so small pixel clusters at the trace edge do not mislead)
        left = [j for j in range(a - 1, a - 4, -1) if j >= 0 and np.isfinite(ys[j])]
        right = [j for j in range(b + 1, b + 4) if j < len(ys) and np.isfinite(ys[j])]
        sides = [s for s in (left, right) if s]
        at_top = [min(ymin[j] - top_of[j] for j in s) <= 0 for s in sides]
        if sides and all(at_top):
            ys[a:b + 1] = Settings.EDI_Y_TOP
        elif sides and all(max(ymax[j] for j in s) >= bottom for s in sides):
            ys[a:b + 1] = Settings.EDI_ROWS[1] - 1   # trace near 0 µV overlapped the panel's bottom edge (not counted as clipping)
            continue
        elif len(sides) == 2 and any(at_top):
            y_left = Settings.EDI_Y_TOP if at_top[0] else ys[left[0]]
            y_right = Settings.EDI_Y_TOP if at_top[1] else ys[right[0]]
            ys[a:b + 1] = np.linspace(y_left, y_right, b - a + 3)[1:-1]
        else:
            continue
        clipped += b - a + 1
    vals = _fill_small_gaps(y_to_edi(ys, scale), Settings.EDI_GAP_FILL)
    clipped += int(np.sum(ys[np.isfinite(ys)] <= Settings.EDI_Y_TOP + 1))
    return vals, clipped


def _frames_to_read(df_breaths, pos_of_frame, tip_u, abnormal, max_len):
    """Images extract_waves will read (row positions in df_frames). Counted by the same steps as extract_waves, without reading images.
    The images right after the end (e+1, e+2) are used only when some columns could not be read, which is unknown until images are read, so they are included."""
    want = set()
    n = len(tip_u)
    for _, b in df_breaths.iterrows():
        s = pos_of_frame.get(int(b["Frame"]))
        e = pos_of_frame.get(int(b["End_Frame"]))
        if s is None or e is None or int(b.get("Abnormal", 0)) == 1:
            continue
        u_start = tip_u[s - 1] if s > 0 else tip_u[s]
        first = int(np.floor(u_start)) + 1
        last = int(np.floor(tip_u[e]))
        if last < first:
            continue
        hi, f = last, e
        while hi >= first and 0 <= f <= e:
            tip = int(np.floor(tip_u[f]))
            lo = max(first, tip - max_len + 1)
            if lo <= min(hi, tip):
                want.add(f)
            if lo > hi:
                break
            hi = lo - 1
            nf = min(int(np.searchsorted(tip_u, hi + 20)), f - 1)
            while nf >= 0 and abnormal[nf]:
                nf -= 1
            f = nf
        for nf in (e + 1, e + 2):
            if nf >= n or abnormal[nf]:
                break
            want.add(nf)
    return want


def _read_frame(frames_root, frame_no):
    path = os.path.join(frames_root, f"frame_{int(frame_no):08d}.jpg")
    img = cv2.imread(path) if os.path.isfile(path) else None
    return None if img is None else _normalize_frame(img)


def extract_waves(df_frames, df_breaths, frames_root, output_dir, user_ID,
                  settings={}, overwrite=False, archive_dir=None):
    """
    Write a per-breath waveform CSV (columns: X, Edi) to output_dir/<BreathNumber>.csv.

    The columns from the one after the pen tip at breath start to the pen tip at breath end are cut out from the image of the breath's last frame.
    For breaths longer than one sweep (about 5.4 s), the missing earlier part is cut out from images of earlier frames and joined.
    Time of each point = Wave_T0 + row number / Wave_Rate (seconds from the start of the video).

    Returns:
        (number of breaths whose CSV was written, df_breaths)
    """
    opts = settings.get("post_ocr", {})
    fps = float(opts.get("fps", 10))
    os.makedirs(output_dir, exist_ok=True)
    W = Settings.SWEEP_X_RIGHT - Settings.SWEEP_X_LEFT
    max_len = W - Settings.SWEEP_ERASE_MARGIN  # number of columns readable from one image (excluding the erase band ahead of the pen tip and the waveform from one sweep earlier)

    df_frames = df_frames.reset_index(drop=True)
    tip_u = df_frames["Sweep_U"].to_numpy(float)
    rate = float(df_frames["Sweep_Rate"].iloc[0]) if len(df_frames) else Settings.SWEEP_PX_PER_SEC
    times = df_frames["Frame"].to_numpy(float) / fps
    frame_no = df_frames["Frame"].to_numpy(int)
    abnormal = df_frames["Abnormal"].to_numpy(int) if "Abnormal" in df_frames else np.zeros(len(df_frames), int)
    pos_of_frame = {int(f): i for i, f in enumerate(frame_no)}

    df_breaths = df_breaths.copy()
    new_cols = {c: [] for c in ("Wave_T0", "Wave_Rate", "Wave_Points", "Wave_Images", "Clipped_Points",
                                "Wave_Max", "Wave_Min", "Sweep_Start", "Sweep_End", "Wrapped",
                                "Start_File_Name", "End_File_Name")}
    alerts = df_breaths["Alert"].fillna("").astype(str).tolist()
    written = 0

    # 0.2.0 (engine_api 3): open the images to be used up front, in ascending frame order. The app creates opened images from the segment video,
    # and forward reading proceeds without re-seeking. The processing below reads backward from each breath's end image,
    # which would otherwise cause seeks in the segment video
    for f in sorted(_frames_to_read(df_breaths, pos_of_frame, tip_u, abnormal, max_len)):
        os.path.isfile(os.path.join(frames_root, f"frame_{int(frame_no[f]):08d}.jpg"))

    for k, b in df_breaths.iterrows():
        rec = dict.fromkeys(new_cols, np.nan)
        s = pos_of_frame.get(int(b["Frame"]))
        e = pos_of_frame.get(int(b["End_Frame"]))
        if s is not None and e is not None:
            rec["Start_File_Name"] = os.path.join(frames_root, f"frame_{int(b['Frame']):08d}.jpg")
            rec["End_File_Name"] = os.path.join(frames_root, f"frame_{int(b['End_Frame']):08d}.jpg")
        if s is None or e is None or int(b.get("Abnormal", 0)) == 1:
            for c in new_cols:
                new_cols[c].append(rec[c])
            continue

        # Waveform range (continuous position): (pen tip of the previous frame, pen tip of the last frame]
        u_start = tip_u[s - 1] if s > 0 else tip_u[s]
        first = int(np.floor(u_start)) + 1
        last = int(np.floor(tip_u[e]))
        if last < first:
            for c in new_cols:
                new_cols[c].append(rec[c])
            continue

        # Working backward, cut out each column from an image in which it has not yet been erased
        ys = np.full((3, last - first + 1), np.nan)
        hi, f, used = last, e, 0
        while hi >= first and 0 <= f <= e:
            tip = int(np.floor(tip_u[f]))
            lo = max(first, tip - max_len + 1)
            top = min(hi, tip)                 # do not read columns not yet drawn in this image (beyond the pen tip)
            frame = _read_frame(frames_root, frame_no[f]) if lo <= top else None
            if frame is not None:
                u = np.arange(lo, top + 1)
                ys[:, lo - first:top - first + 1] = extract_column_y(frame, Settings.SWEEP_X_LEFT + np.mod(u, W))
                used += 1
            if lo > hi:
                break
            hi = lo - 1
            # Next image: the frame just after column hi was drawn (with some margin: drawn up to 20 px beyond). Avoid abnormal frames
            nf = min(int(np.searchsorted(tip_u, hi + 20)), f - 1)
            while nf >= 0 and abnormal[nf]:
                nf -= 1
            f = nf
        # Just behind the pen tip may not be fully drawn yet: fill only the unread columns from images of the following frames
        for nf in (e + 1, e + 2):
            missing = ~np.isfinite(ys[0])
            if not missing.any() or nf >= len(frame_no) or abnormal[nf]:
                break
            tip = int(np.floor(tip_u[nf]))
            u = np.arange(first, last + 1)
            usable = missing & (u > tip - max_len) & (u <= tip)
            frame = _read_frame(frames_root, frame_no[nf]) if usable.any() else None
            if frame is not None:
                cols = extract_column_y(frame, Settings.SWEEP_X_LEFT + np.mod(u[usable], W))
                ys[:, usable] = cols
        scale = float(b.get("Edi_Scale", Settings.EDI_DEFAULT_SCALE) or Settings.EDI_DEFAULT_SCALE)
        u = np.arange(first, last + 1)
        xs = Settings.SWEEP_X_LEFT + np.mod(u, W)
        vals, clipped = finish_wave(ys, scale, xs)
        wave = pd.DataFrame({"X": u, "Edi": np.round(vals, 2)})
        path = os.path.join(output_dir, f"{int(b['BreathNumber'])}.csv")
        if overwrite or not os.path.exists(path):
            wave.to_csv(path, index=False)
        written += 1

        # Time the first point was drawn: time the pen tip reached first (interpolated from per-frame pen-tip positions)
        rec["Wave_T0"] = round(float(np.interp(first, tip_u, times)), 4)
        rec["Wave_Rate"] = round(rate, 4)
        rec["Wave_Points"] = len(u)
        rec["Wave_Images"] = used
        rec["Clipped_Points"] = clipped
        rec["Wave_Max"] = round(float(np.nanmax(vals)), 2) if np.isfinite(vals).any() else np.nan
        rec["Wave_Min"] = round(float(np.nanmin(vals)), 2) if np.isfinite(vals).any() else np.nan
        rec["Sweep_Start"], rec["Sweep_End"] = int(xs[0]), int(xs[-1])
        rec["Wrapped"] = int(len(u) > 1 and (np.diff(xs) < 0).any())
        if clipped:
            alerts[k] = ",".join(filter(None, [alerts[k], "Clipped"]))
        for c in new_cols:
            new_cols[c].append(rec[c])

    for c, v in new_cols.items():
        df_breaths[c] = v
    df_breaths["Alert"] = alerts

    if archive_dir is not None:
        os.makedirs(archive_dir, exist_ok=True)
        with open(os.path.join(archive_dir, "fields.txt"), "w", encoding="utf-8") as f:
            f.write("[waves]\nEdi\n\n[fields]\n")
            for name in ("EdiPeak", "Edimin", "Edimin_displayed", "Duration", "Wave_Max", "Wave_Min",
                         "Clipped_Points", "Edi_Scale", "Alert"):
                f.write(f"{name}\n")
        meta = {"units": {"edi": "µV"}, "symmetric": {"edi": False}}
        with open(os.path.join(archive_dir, "scale_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"[extract_waves] {written} breaths written")
    return written, df_breaths
