# PostNAVA Engine Settings
# All coordinates are screen coordinates at the base resolution (1280x720). Input frames are normalized to this resolution before use.
# Reference image: layout.jpg (resource/postnava_ventocr_sample.mp4, Servo-U "Edi monitor" screen, in standby)

import os

BASE_RESOLUTION = (1280, 720)  # (width, height)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------
# Value ROIs (x, y, w, h)
# ------------------------------------------------------------
ROIs = {
    "EdiPeak": (1070, 248, 160, 60),   # Edipeak (µV). Becomes "***" when no breath is detected for a while
    "Edimin":  (1095, 373, 110, 31),   # Edimin (µV)
}

# Screen check (frames that are not the Edi monitor screen become Abnormal): regions of static text
SCREEN_CHECK_ROIS = [
    (1062, 218, 80, 16),   # "Edipeak"
    (1062, 355, 70, 16),   # "Edimin"
]
SCREEN_REFERENCE_IMAGE = "screen_reference.png"  # the regions above cropped from the reference image and placed side by side (grayscale)

# Upper-limit label of the Edi vertical axis ("20"). Too small to OCR, so frames that differ from the reference image become Abnormal
# (the vertical-axis upper limit is post_ocr.edi_scale_max in engine_options; default 20 µV)
SCALE_LABEL_ROI = (152, 220, 30, 14)
SCALE_REFERENCE_IMAGE = "scale_reference.png"

# ------------------------------------------------------------
# Waveform drawing area and sweep
# ------------------------------------------------------------
# The sweep draws rightward from the left edge X_LEFT; on reaching X_RIGHT it returns to X_LEFT and overwrites the old waveform (wrap around).
# ECG and Edi are drawn at the same position simultaneously, erasing about 35-40 px ahead of the pen tip (erase band).
# Sweep speed is about 164 px/s in the sample (one sweep 896 px ≈ 5.4 s). The actual value is estimated from the video.
SWEEP_X_LEFT = 150
SWEEP_X_RIGHT = 1046          # this column is not drawn ([X_LEFT, X_RIGHT) is one sweep)
SWEEP_PX_PER_SEC = 164.0      # default sweep speed
SWEEP_GAP_MIN = 15            # width of a blank treated as the erase band (px)
SWEEP_GAP_MAX = 80
SWEEP_GAP_X_MIN = 195         # blanks starting left of this are not used (indistinguishable from the scale-label band at the left edge)
SWEEP_ERASE_MARGIN = 80       # range readable from one image = from the pen tip back to (one sweep − this width) (erase band ~40 px + margin for pen-tip estimation error)

# ECG (used only to detect the sweep position)
ECG_ROWS = (124, 212)
ECG_GRAY_MIN = 140

# ------------------------------------------------------------
# Edi waveform
# ------------------------------------------------------------
EDI_ROWS = (217, 421)         # Edi waveform band (panel top to bottom edge)
# Vertical axis: 0 µV = panel bottom edge, upper limit = panel top edge (not the position of the scale-label text).
# Calibrated from the sample video's Edipeak (24 breaths below 20) and the y of the waveform peaks: 10.0 px/µV, residual SD 1.0 px
EDI_Y_ZERO = 419.4            # y of 0 µV
EDI_Y_TOP = 219.4             # y of the vertical-axis upper limit (edi_scale_max)
EDI_DEFAULT_SCALE = 20.0      # default vertical-axis upper limit (µV)
EDI_HUE = (135, 170)          # waveform color (purple). OpenCV HSV (0-179)
EDI_SAT_MIN = 40
EDI_VAL_MIN = 60
# Edi bar at the right edge (dark purple rectangle whose height changes). A cluster of high-saturation pixels in the EDI_BAR_X columns is the bar;
# within EDI_BAR_PAD px around it, only pixels brighter than the bar (the waveform trace) are kept (the edges are blurred by JPEG and lose saturation)
EDI_BAR_X = (1000, 1048)
EDI_BAR_SAT_MIN = 150
EDI_BAR_PAD = 4
EDI_BAR_TRACE_VAL_MIN = 135   # bar brightness is about 112; trace overlapping the bar is about 150
EDI_BAR_TRACE_X_MAX = 1040    # right of this (the bar's right edge and the white dashed line), pixels around the bar are not used
EDI_SKIP_X = [(1003, 1007), (1041, 1045)]  # the bar's left edge and the white dashed line on the right. These columns are not read; they are interpolated from both neighbors
EDI_GAP_FILL = 12             # gaps of this width (px) or less are linearly interpolated
EDI_CLIP_MARGIN = 6           # values within this many px of the vertical axis top/bottom edge are treated as overflow (clipping)
# Region where the top-left scale label "20" overlaps and hides the trace (x_min, x_max, y_max). In these columns, reaching y_max counts as reaching the top edge
EDI_LABEL_OCCLUSION = (150, 188, 250)
