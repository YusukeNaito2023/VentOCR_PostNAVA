"""PostNAVA（Servo-U の Edi モニタ画面）VentOCR Engine

- OCR: Edipeak、Edimin（右側の数値）、Edi の縦軸の上限（目盛り）
- 1呼吸 = Edimin の表示の更新（神経吸気の始まり）から、次の呼吸の Edimin の更新まで
- 波形: Edi（1つだけ）

画面の数値は Servo-U が検出した呼吸ごとに更新される:
    Edimin  … Edi が立ち上がる（神経吸気の始まり）ときに更新。直前の呼気の最小値
    Edipeak … Edi がピークを過ぎて下がり始めたあと（神経吸気の終わり）に更新。その呼吸のピーク値
    約10秒間呼吸が検出されないと、どちらも「***」になる
値が前と同じときは表示が変わらず更新が見えないので、呼吸は Edipeak の更新で数え、
その直前の Edimin の更新を呼吸の始まりとする（見えないときは典型的な遅れから推定する）。

波形の画面は左から右へ一定の速さで掃引し、右端に達すると左端に戻って古い波形を上書きする（wrap around）。
各フレームでペン先の前の空白（消去帯）を探してペン先の位置を記録し（initial_ocr）、
動画全体から掃引の速さと位相を推定して、各フレームのペン先の位置を決める（post_ocr）。
呼吸の波形は、開始時のペン先〜終了時のペン先の列を、最後のフレーム（1周より長い呼吸はそれより前のフレームも）から切り出す（extract_waves）。
"""
import json
import os
import re

import cv2
import numpy as np
import pandas as pd
from tqdm import tqdm

from . import Settings

ENGINE_VERSION = "0.1.1"
VENTOCR_TESTED = "0.5.5"

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_KEYS = list(Settings.ROIs)
NO_VALUE = "***"


# ============================================================
# EasyOCR初期化（V600_ventocr と同じ）
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
# 共通
# ============================================================

def _normalize_frame(frame):
    """基準解像度（1280x720）にそろえる。"""
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
    """True が続く区間の (開始, 終了) のリスト（終了を含む）。"""
    idx = np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0]))
    return list(zip(idx[0::2], idx[1::2] - 1))


def _screen_crop(gray):
    return np.hstack([_crop(gray, r) for r in Settings.SCREEN_CHECK_ROIS]) if Settings.SCREEN_CHECK_ROIS else None


def _load_reference(name):
    path = os.path.join(ENGINE_DIR, name)
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE) if os.path.isfile(path) else None
    return None if img is None else img.astype(np.int16)


def _scale_label_diff(frame, gray, ref):
    """縦軸の上限の目盛りと基準画像の差（Edi の線が重なった画素は除く）。"""
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
    """フレーム番号からHH:MM:SS:CC形式のタイムスタンプを生成"""
    total_seconds = frame / fps
    s = int(total_seconds)
    centiseconds = int((total_seconds - s) * 100)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{ss:02d}:{centiseconds:02d}"


def edi_mask(frame):
    """Edi の帯（EDI_ROWS）で波形の色の画素。右端の Edi バーの画素は除く。"""
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
    """ペン先の x（画面の座標）。ECG と Edi のどちらも描かれていない列（消去帯）の左端の1つ手前。

    消去帯が見つからない（右端の Edi バーに重なっている、左端に戻った直後など）ときは NaN。
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
# OCR結果パース
# ============================================================

def _parse_ocr(result):
    """EasyOCR の結果 → (値の文字列, 生の文字列, 信頼度の文字列)。

    「***」（呼吸が検出されていない）は値を NO_VALUE にする。小数1桁で表示されるが、
    小数点を読み落とすことがあるので、小数点が無い2桁以上の数字は最後の1桁の前に小数点を補う（生の文字列は *_raw に残す）。
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
    各フレームで Edipeak・Edimin を読み取り、ペン先の位置を記録する。

    数値は、読み取り位置の画像が前回OCRしたときから変わったフレームだけOCRし、それ以外は前の値を引き継ぐ。
    *_Diff は前のフレームからの変化の大きさ（表示の更新の検出に使う）。

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

            # --- 数値（表示が変わったときだけOCR） ---
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

            # --- ペン先の位置 ---
            row["Sweep_Raw"] = detect_pen_tip(frame, gray)

            # --- Edi モニタ画面かどうか（動かない文字を基準画像と比べる） ---
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
# 掃引モデル
# ============================================================

def _circ_center(values, period, trim):
    """周期 period の値の代表値（外れ値を除いた円周平均）。"""
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
    """区間ごとの位相（tip − speed*i を mod W した値の代表値）を、折り返しを展開してつないだもの。"""
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


def fit_sweep(tip_raw, fps, window=150, block=25, trim=25, tol=12):
    """各フレームのペン先の位置を求める。

    掃引は一定の速さなので、tip ≒ speed*i + phase (mod W)。区間ごとに位相の代表値を求めて（外れ値に強い円周平均）
    つなぎ、その傾きから速さを補正する。Edi が描かれない間に偽の空白が止まって見えるなど、検出の誤りがあっても崩れない。

    Args:
        tip_raw: 各フレームで検出したペン先（画面の x）。検出できなかったフレームは NaN
    Returns:
        dict(
            tip_u:  各フレームのペン先の位置（X_LEFT からの距離。折り返しを展開した通しの値）,
            speed:  掃引の速さ（px/フレーム）,
            detected: 検出値を使ったフレームか,
        )
    """
    W = Settings.SWEEP_X_RIGHT - Settings.SWEEP_X_LEFT
    n = len(tip_raw)
    tip = np.asarray(tip_raw, float) - Settings.SWEEP_X_LEFT
    valid = np.isfinite(tip)
    idx = np.arange(n)
    speed = Settings.SWEEP_PX_PER_SEC / fps
    if valid.sum() < 10:
        return {"tip_u": speed * idx, "speed": speed, "detected": np.zeros(n, bool)}

    # 速さの粗い推定: lag フレーム離れた2点の進み（折り返しは mod で吸収）の中央値
    lag = 20
    if n > lag:
        both = valid[:-lag] & valid[lag:]
        if both.sum() >= 20:
            est = np.median(np.mod(tip[lag:][both] - tip[:-lag][both], W)) / lag
            if 0 < est * lag < W / 2:
                speed = float(est)

    # 速さの補正: 位相の傾き（外れ値を除いた直線回帰）を速さに足す
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
    model_u = model_u - np.floor(model_u[0] / W) * W  # 先頭を [0, W) に

    # 検出値がモデルと近いフレームは検出値を使う（その画像の実際のペン先）
    tip_u = model_u.copy()
    d = np.mod(tip - model_u + W / 2, W) - W / 2
    use = valid & (np.abs(d) <= tol)
    tip_u[use] = model_u[use] + d[use]
    tip_u = np.maximum.accumulate(tip_u)  # 逆戻りしない
    return {"tip_u": tip_u, "speed": speed, "detected": use}


# ============================================================
# post_ocr
# ============================================================

def _mode(series):
    s = series.dropna()
    s = s[s.astype(str) != ""]
    return s.mode().iloc[0] if len(s) else np.nan


def _display_events(df, key, diff_thresh, min_gap):
    """表示が更新されたフレームの位置（df の行番号）。

    前のフレームから読み取り位置の画像が変わったフレーム（区間の最初のフレームは値が変わったとき）。
    続けて変わったとき（書き換えの途中）は最後のフレームを採る。
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
    呼吸の区切り（Edipeak・Edimin の表示の更新）を求め、呼吸番号・ペン先の位置を付ける。

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

    # Edi モニタ画面でないフレーム
    screen = pd.to_numeric(df["Screen_Diff"], errors="coerce") if "Screen_Diff" in df else pd.Series(np.nan, index=df.index)
    df["Abnormal"] = (screen > abnormal_thresh).astype(int)
    # 縦軸の上限が基準画像（edi_scale_max）と違うフレーム: 波形の値を換算できない
    scale_diff = pd.to_numeric(df["Scale_Diff"], errors="coerce") if "Scale_Diff" in df else pd.Series(np.nan, index=df.index)
    df["Scale_Changed"] = (scale_diff > scale_thresh).astype(int)
    df.loc[df["Scale_Changed"] == 1, "Abnormal"] = 1

    # --- 表示の更新 ---
    peak_events = _display_events(df, "EdiPeak", diff_thresh, min_gap) if "EdiPeak_Diff" in df else []
    min_events = _display_events(df, "Edimin", diff_thresh, min_gap) if "Edimin_Diff" in df else []
    abnormal = df["Abnormal"].to_numpy() == 1
    peak_str = df["EdiPeak"].astype(str).to_numpy() if "EdiPeak" in df else np.full(n, "")
    # 呼吸 = Edipeak が数値に更新されたとき（「***」になったのは呼吸ではない）
    peak_events = [p for p in peak_events if not abnormal[p] and peak_str[p] not in ("", NO_VALUE, "nan")]
    min_events = [m for m in min_events if not abnormal[m]]
    df["EdiPeak_Update"] = 0
    df.loc[peak_events, "EdiPeak_Update"] = 1
    df["Edimin_Update"] = 0
    df.loc[min_events, "Edimin_Update"] = 1

    # 呼吸の始まり: Edipeak の更新の直前（max_onset_lag_sec 以内）の Edimin の更新
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
    cut_at_start = False   # 最初の呼吸の始まりが録画の先頭より前（途中から始まる呼吸）
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
        df["BreathNumber"] += 1          # 最初の区切りより前は呼吸1（途中から始まる呼吸）
    df["Time"] = df["Frame"].apply(lambda f: _format_time(f, fps))

    # 数値（「***」は NaN）
    for key in OCR_KEYS:
        if key in df:
            df[f"{key}_Value"] = pd.to_numeric(df[key].where(df[key].astype(str) != NO_VALUE), errors="coerce")

    # --- ペン先の位置 ---
    sw = fit_sweep(df["Sweep_Raw"].to_numpy(float) if "Sweep_Raw" in df else np.full(n, np.nan), fps)
    W = Settings.SWEEP_X_RIGHT - Settings.SWEEP_X_LEFT
    df["Sweep_U"] = np.round(sw["tip_u"], 2)
    df["Sweep_X"] = np.round(Settings.SWEEP_X_LEFT + np.mod(sw["tip_u"], W), 1)
    df["Sweep_Detected"] = sw["detected"].astype(int)
    df["Sweep_Rate"] = round(sw["speed"] * fps, 4)  # px/秒
    print(f"[post_ocr] sweep {sw['speed'] * fps:.2f} px/s, detected {sw['detected'].mean() * 100:.1f}% of frames")

    # --- 呼吸ごとの表 ---
    est_of = {s: e for s, e in zip(starts, estimated)}
    peak_set = set(peak_events)
    rows = []
    groups = list(df.groupby("BreathNumber", sort=True))
    for k, (no, g) in enumerate(groups):
        s, e = int(g.index[0]), int(g.index[-1])
        normal = g[g["Abnormal"] == 0]
        nxt = groups[k + 1][1] if k + 1 < len(groups) else None
        nxt_normal = nxt[nxt["Abnormal"] == 0] if nxt is not None else None
        # この呼吸の Edipeak の更新（呼吸の中の最初の更新）〜次の呼吸の Edipeak の更新の前
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
        # Edipeak: この呼吸の更新で表示された値
        if p is not None:
            seg = df.iloc[p:q]
            row["EdiPeak"] = _mode(seg.loc[seg["Abnormal"] == 0, "EdiPeak_Value"])
            row["EdiPeak_Frame"] = int(df.at[p, "Frame"])
        else:
            row["EdiPeak"], row["EdiPeak_Frame"] = np.nan, np.nan
        # Edimin: この呼吸の呼気の最小値 = 次の呼吸の始まりで表示される値。*_displayed はこの呼吸の間に表示されていた値
        row["Edimin"] = _mode(nxt_normal["Edimin_Value"]) if nxt_normal is not None and "Edimin_Value" in df else np.nan
        row["Edimin_displayed"] = _mode(normal["Edimin_Value"]) if "Edimin_Value" in df else np.nan
        row["Onset_Estimated"] = int(est_of.get(s, False)) if s in est_of else np.nan
        row["Edi_Scale"] = scale_max
        alerts = []
        if k == 0 and (lead or cut_at_start):
            alerts.append("Fragment")   # 録画の途中から始まる呼吸
        if nxt is None:
            alerts.append("Fragment")   # 録画の終わりで切れている呼吸
        if g["Scale_Changed"].any():
            alerts.append("ScaleChanged")  # 縦軸の上限が変わっている（波形は出力しない）
        if g["Abnormal"].any():
            alerts.append("Abnormal")
        if row["Onset_Estimated"] == 1:
            alerts.append("OnsetEstimated")
        if "EdiPeak" in g and (g["EdiPeak"].astype(str) == NO_VALUE).any():
            alerts.append("NoBreathDetected")  # 呼吸器が約10秒呼吸を検出しなかった（「***」）
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
    """画面の列 xs（screen x）ごとの Edi の y。

    Returns:
        (その色の画素の y の中央値, 最も上の y, 最も下の y)。画素が無い列・EDI_SKIP_X の列は NaN
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
    """画面の y → Edi（µV）。"""
    return (Settings.EDI_Y_ZERO - y) * scale / (Settings.EDI_Y_ZERO - Settings.EDI_Y_TOP)


def finish_wave(cols, scale, xs=None):
    """列ごとの y（extract_column_y の結果）→ Edi（µV）。

    縦軸をはみ出して描かれていない列（頭打ち）:
      - 両隣の列の線が上端（下端）に達していれば、上限（下限）の値にする
      - 片側だけ上端に達している（もう片側は右端のバー・左上の目盛りに隠れて下りてきた後など）ときは、
        上限の値から反対側の値まで直線でつなぐ

    Returns:
        (値の配列, 上端で頭打ちになった点の数)
    """
    top = Settings.EDI_Y_TOP + Settings.EDI_CLIP_MARGIN
    bottom = Settings.EDI_ROWS[1] - 1 - Settings.EDI_CLIP_MARGIN
    ys, ymin, ymax = (np.asarray(c, float).copy() for c in cols)
    xs = np.asarray(xs) if xs is not None else np.full(len(ys), -1)
    ox0, ox1, oy = Settings.EDI_LABEL_OCCLUSION
    top_of = np.where((xs >= ox0) & (xs <= ox1), max(oy, top), top)  # 列ごとの「上端に達した」の基準
    clipped = 0
    nan = ~np.isfinite(ys)
    for a, b in _runs(nan):
        # 両隣（それぞれ近い3列。線の縁の小さな画素の塊に惑わされないように）
        left = [j for j in range(a - 1, a - 4, -1) if j >= 0 and np.isfinite(ys[j])]
        right = [j for j in range(b + 1, b + 4) if j < len(ys) and np.isfinite(ys[j])]
        sides = [s for s in (left, right) if s]
        at_top = [min(ymin[j] - top_of[j] for j in s) <= 0 for s in sides]
        if sides and all(at_top):
            ys[a:b + 1] = Settings.EDI_Y_TOP
        elif sides and all(max(ymax[j] for j in s) >= bottom for s in sides):
            ys[a:b + 1] = Settings.EDI_ROWS[1] - 1   # 0 µV 付近で線がパネルの下の縁に重なった（頭打ちには数えない）
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


def _read_frame(frames_root, frame_no):
    path = os.path.join(frames_root, f"frame_{int(frame_no):08d}.jpg")
    img = cv2.imread(path) if os.path.isfile(path) else None
    return None if img is None else _normalize_frame(img)


def extract_waves(df_frames, df_breaths, frames_root, output_dir, user_ID,
                  settings={}, overwrite=False, archive_dir=None):
    """
    呼吸ごとの波形CSV（列: X, Edi）を output_dir/<BreathNumber>.csv に書き出す。

    呼吸の開始時のペン先の次の列〜終了時のペン先の列を、呼吸の最後のフレームの画像から切り出す。
    1周（約5.4秒）より長い呼吸は、足りない前の部分をそれより前のフレームの画像から切り出してつなぐ。
    各点の時刻 = Wave_T0 + 行番号 / Wave_Rate（秒、動画の先頭から）。

    Returns:
        (CSV を書き出した呼吸の数, df_breaths)
    """
    opts = settings.get("post_ocr", {})
    fps = float(opts.get("fps", 10))
    os.makedirs(output_dir, exist_ok=True)
    W = Settings.SWEEP_X_RIGHT - Settings.SWEEP_X_LEFT
    max_len = W - Settings.SWEEP_ERASE_MARGIN  # 1枚の画像から読める列の数（ペン先の前の消去帯と、1周前の波形を除く）

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

        # 波形の範囲（通しの位置）: (前のフレームのペン先, 最後のフレームのペン先]
        u_start = tip_u[s - 1] if s > 0 else tip_u[s]
        first = int(np.floor(u_start)) + 1
        last = int(np.floor(tip_u[e]))
        if last < first:
            for c in new_cols:
                new_cols[c].append(rec[c])
            continue

        # 後ろから順に、各列がまだ消されていない画像から切り出す
        ys = np.full((3, last - first + 1), np.nan)
        hi, f, used = last, e, 0
        while hi >= first and 0 <= f <= e:
            tip = int(np.floor(tip_u[f]))
            lo = max(first, tip - max_len + 1)
            top = min(hi, tip)                 # この画像でまだ描かれていない列（ペン先より先）は読まない
            frame = _read_frame(frames_root, frame_no[f]) if lo <= top else None
            if frame is not None:
                u = np.arange(lo, top + 1)
                ys[:, lo - first:top - first + 1] = extract_column_y(frame, Settings.SWEEP_X_LEFT + np.mod(u, W))
                used += 1
            if lo > hi:
                break
            hi = lo - 1
            # 次の画像: 列 hi を描いた直後（少し余裕をみて20px先まで描いた）フレーム。異常なフレームは避ける
            nf = min(int(np.searchsorted(tip_u, hi + 20)), f - 1)
            while nf >= 0 and abnormal[nf]:
                nf -= 1
            f = nf
        # ペン先のすぐ手前は描き終わっていないことがある: 読めなかった列だけ、直後のフレームの画像から補う
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

        # 最初の点が描かれた時刻: ペン先が first に達した時刻（各フレームのペン先の位置から内挿）
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
