# PostNAVA Engine Settings
# 座標はすべて基準解像度（1280x720）の画面座標。入力フレームはこの解像度に正規化してから使う。
# 基準画像: layout.jpg（resource/postnava_ventocr_sample.mp4、Servo-U の「Edi モニタ」画面、スタンバイ中）

import os

BASE_RESOLUTION = (1280, 720)  # (width, height)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------
# 数値の読み取り位置 (x, y, w, h)
# ------------------------------------------------------------
ROIs = {
    "EdiPeak": (1070, 248, 160, 60),   # Edipeak（µV）。呼吸がしばらく検出されないと「***」になる
    "Edimin":  (1095, 373, 110, 31),   # Edimin（µV）
}

# 画面の確認用（Edi モニタ画面でないフレームを Abnormal にする）: 動かない文字の範囲
SCREEN_CHECK_ROIS = [
    (1062, 218, 80, 16),   # 「Edipeak」
    (1062, 355, 70, 16),   # 「Edimin」
]
SCREEN_REFERENCE_IMAGE = "screen_reference.png"  # 上の範囲を基準画像から切り出して横に並べたもの（グレースケール）

# Edi の縦軸の上限の目盛り（「20」）。小さすぎて OCR では読めないので、基準画像と比べて違うフレームを Abnormal にする
# （縦軸の上限は engine_options の post_ocr.edi_scale_max。既定 20 µV）
SCALE_LABEL_ROI = (152, 220, 30, 14)
SCALE_REFERENCE_IMAGE = "scale_reference.png"

# ------------------------------------------------------------
# 波形の描画領域と掃引
# ------------------------------------------------------------
# 掃引は左端 X_LEFT から右へ描き、X_RIGHT に達すると X_LEFT に戻って古い波形を上書きする（wrap around）。
# ECG と Edi は同じ位置を同時に描き、ペン先の前の約35〜40px を消している（消去帯）。
# 掃引の速さはサンプルで約164 px/秒（1周 896px ≒ 5.4秒）。実際の値は動画から推定する。
SWEEP_X_LEFT = 150
SWEEP_X_RIGHT = 1046          # この列は描かれない（[X_LEFT, X_RIGHT) が1周）
SWEEP_PX_PER_SEC = 164.0      # 掃引の速さの既定値
SWEEP_GAP_MIN = 15            # 消去帯とみなす空白の幅（px）
SWEEP_GAP_MAX = 80
SWEEP_GAP_X_MIN = 195         # これより左で始まる空白は使わない（左端の目盛りの帯と区別できないため）
SWEEP_ERASE_MARGIN = 80       # 1枚の画像から読める範囲 = ペン先から (1周 − この幅) まで（消去帯 約40px ＋ ペン先の推定誤差の余裕）

# ECG（掃引位置の検出にだけ使う）
ECG_ROWS = (124, 212)
ECG_GRAY_MIN = 140

# ------------------------------------------------------------
# Edi 波形
# ------------------------------------------------------------
EDI_ROWS = (217, 421)         # Edi 波形の帯（パネルの上端〜下端）
# 縦軸: 0 µV = パネルの下端、上限 = パネルの上端（目盛りの文字の位置ではない）。
# サンプル動画の Edipeak（20 未満の24呼吸）と波形の頂点の y から較正: 10.0 px/µV、残差 SD 1.0 px
EDI_Y_ZERO = 419.4            # 0 µV の y
EDI_Y_TOP = 219.4             # 縦軸の上限（edi_scale_max）の y
EDI_DEFAULT_SCALE = 20.0      # 縦軸の上限の既定値（µV）
EDI_HUE = (135, 170)          # 波形の色（紫）。OpenCV の HSV（0-179）
EDI_SAT_MIN = 40
EDI_VAL_MIN = 60
# 右端の Edi バー（濃い紫の四角。高さが変わる）。EDI_BAR_X の列で彩度の高い画素の塊をバーとし、
# その周り EDI_BAR_PAD px では、バーより明るい画素（波形の線）だけを残す（縁は JPEG でぼけて彩度が下がるため）
EDI_BAR_X = (1000, 1048)
EDI_BAR_SAT_MIN = 150
EDI_BAR_PAD = 4
EDI_BAR_TRACE_VAL_MIN = 135   # バーの明度は約112、バーに重なった線は約150
EDI_BAR_TRACE_X_MAX = 1040    # これより右（バーの右端と白い破線）ではバーの周りの画素を使わない
EDI_SKIP_X = [(1003, 1007), (1041, 1045)]  # バーの左の縁と右の白い破線。この列は読まずに両隣から補間する
EDI_GAP_FILL = 12             # この幅（px）以下の欠けは直線で補間する
EDI_CLIP_MARGIN = 6           # 縦軸の上端/下端からこの px 以内の値は、はみ出し（頭打ち）とみなす
# 左上の目盛り「20」の文字が線に重なって隠す範囲 (x_min, x_max, y_max)。この列では y_max まで届いていれば上端に達したとみなす
EDI_LABEL_OCCLUSION = (150, 188, 250)
