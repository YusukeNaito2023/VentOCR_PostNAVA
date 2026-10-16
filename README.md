# PostNAVA VentOCR Engine (postnava_ventocr)

## Supported Devices
- Servo-U の「Edi モニタ」画面（上に ECG、中央に Edi 波形、右に Edipeak / Edimin の数値。NAVA 離脱後・スタンバイ中など）
- 1280x720 で収録した動画（ほかの解像度は 1280x720 に縮小・拡大してから読む）

## Description
- **OCR**: Edipeak、Edimin（右側の数値）。表示が変わったフレームだけ OCR する
  - 約10秒間呼吸が検出されないと、どちらも「***」になる（値なし。その呼吸の Alert に `NoBreathDetected`）
- **1呼吸** = Edimin の表示の更新から、次の呼吸の Edimin の更新まで
  - Servo-U は Edi が立ち上がる（神経吸気の始まり）ときに Edimin を、ピークを過ぎて下がり始めたあと（約0.35秒後）に Edipeak を更新する
  - 値が前と同じだと表示が変わらず更新が見えないので、呼吸は Edipeak の更新で数え、その直前（`max_onset_lag_sec` 以内）の Edimin の更新を始まりとする。
    見えないときは典型的な遅れ（サンプルで0.4秒）から推定する（Alert に `OnsetEstimated`）
  - Edimin の更新は Edi の立ち上がりより少し遅れる（20%の高さを過ぎてから中央値0.1秒）ので、区切りは `onset_shift_sec`（既定 -0.3秒）だけ前に置く
- **Breaths.csv の値**
  - `EdiPeak`: その呼吸で表示された Edipeak
  - `Edimin`: その呼吸の呼気の最小値 = 次の呼吸の始まりで表示される Edimin（波形の最小値と一致することを確認済み）。
    `Edimin_displayed` はその呼吸の間に表示されていた値（= 1つ前の呼吸の呼気の最小値）
  - `Wave_Max` / `Wave_Min`: 抽出した波形の最大・最小（µV）
- **波形**: Edi（BreathCSVs/<n>.csv の列は X, Edi。単位 µV）
  - 掃引は約164 px/秒で左から右へ描き、x=1046 に達すると x=150 に戻って上書きする（1周 896px ≒ 5.4秒、wrap around）
  - 各フレームで ECG と Edi のどちらも描かれていない空白（ペン先の前の消去帯、幅約35〜40px）を探してペン先とし、
    動画全体から掃引の速さと位相を推定して各フレームのペン先を決める（Edi が描かれない間の偽の空白などは外れ値として除く）
  - 呼吸の波形は、その呼吸の最後のフレームの画像から「開始時のペン先の次の列〜終了時のペン先の列」を切り出す。
    1周（約5.4秒）より長い呼吸は、足りない前の部分をそれより前のフレームの画像から切り出してつなぐ（`Wave_Images` = 使った画像の数）
  - 各点の時刻 = `Wave_T0` + 行番号 / `Wave_Rate`（Breaths.csv）
  - 縦軸: 0 µV = パネルの下端（y=419.4）、20 µV = パネルの上端（y=219.4）。10.0 px/µV（サンプルの Edipeak 24呼吸で較正、残差 SD 0.1 µV）
  - 縦軸の上限を超えた部分は描かれないので、上限の値（20 µV）で頭打ちにする（`Clipped_Points` = その点の数、Alert に `Clipped`）。
    この呼吸の Edipeak は画面の数値の方が正しい

## Expected Layout
See `layout.jpg`（赤枠: OCR、白枠: Edi 波形、灰枠: 掃引位置の検出に使う ECG、黄枠: Edi バー（除外）、青枠: 画面確認用の文字・縦軸の目盛り）。

## Limitations
- 縦軸の上限は設定値（`edi_scale_max`、既定 20 µV）。目盛り「20」は小さく OCR で読めないため、基準画像（`scale_reference.png`）と違う
  フレームは Abnormal にし、その呼吸の波形は出力しない（Alert に `ScaleChanged`）。上限を変えた動画は `edi_scale_max` を合わせ、基準画像を作り直す
- 縦軸の上限（20 µV）を超えた部分は頭打ちになる（上記）
- 呼吸の区切りは Servo-U の呼吸検出（表示の更新）に従う。小さな Edi の揺れも1呼吸として数えられることがある
- 録画の最初と最後の呼吸は途中で切れている（Alert に `Fragment`）
- 右端の Edi バー（x=1000〜1048）と白い破線に重なる列（x=1003〜1007, 1041〜1045）は読まずに両隣から補間する

## Author
- VentOCR Developer Team
- Contact: naito623@naramed-u.ac.jp
