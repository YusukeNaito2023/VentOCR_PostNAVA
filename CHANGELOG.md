# CHANGELOG

## 0.2.0 (2026-10-25)
engine_api 3 に対応（VentOCR がフレームの画像を全部は保存せず、エンジンが開いた画像だけを作る。ENGINE_API.md §5.1）。
- 波形の抽出で使う画像を、呼吸ごとの処理の前に番号の小さい順に開いておく（`_frames_to_read`）。呼吸ごとの処理は
  終了画像から後ろへ戻りながら読むので、そのままだと区間動画のシークが起きる。全フレームを見る処理は initial_ocr の中だけ
- 秒とフレーム数の換算をフレームレートから: 掃引の当てはめの区間（10 fps で 150 / 25 フレーム）と速さの粗い推定の間隔（20）。
  10 fps では 0.1.1 と同じ
- outputs: alerts（Alert の値の意味と、精度の評価で除外するか）を追加
- 確認（VentOCR e0e306b、PostNAVA のサンプル 306 秒）: 0.1.1（全フレームを保存）と 0.2.0（開いた画像だけを作る。作った画像は 304 枚）で、
  Frames.csv・Breaths.csv（File_Name 以外）・波形 96 呼吸が一致。20 fps でも 96 呼吸、EdiPeak・Edimin・Edi の目盛りは 10 fps と同じ

## 0.1.1 (2026-10-21)
解析の結果は 0.1.0 と同じ。manifest.json に出力の宣言 `outputs` を足した（VentOCR 0.6.2 から。ENGINE_API.md §8.1）。
- waves: Edi（µV、縦軸は E_min / E_max = 画面の Edi の目盛り）。色・単位・縦軸は VentOCR 0.6.1 までの見え方と同じ
- breath_values: EdiPeak・Edimin・Edimin_displayed・Wave_Max・Clipped_Points（今までの breath_summary・labels・単位と同じ）
- `ventocr_min` を 0.6.2 に（それより古いアプリは宣言を読めない）

## 0.1.0 (2026-10-19)
旧 v2.0.0 と中身は同じ。版の付け方を 0.X.Y に改めた（古い版のタグは削除）。

## 2.0.0 (2026-10-11)
- 作り直し（端末版）。サンプル: resource/postnava_ventocr_sample.mp4（1280x720、Servo-U の Edi モニタ画面・スタンバイ中、306秒・96呼吸、約47秒で解析）
- 1呼吸 = Edimin の表示の更新（-0.3秒）〜次の呼吸の Edimin の更新。呼吸は Edipeak の更新で数える
  - 旧: Edimin の値が変わったフレームで区切っていた（同じ値が続くと区切れない、Edipeak の更新と対応しない）
- OCR: 表示が変わったフレームだけ OCR（旧: 全フレーム OCR）。「***」（呼吸が検出されていない）を値なしとして扱う
- Breaths.csv の Edimin を「その呼吸の呼気の最小値（次の呼吸の始まりで表示される値）」に。表示されていた値は Edimin_displayed
- 波形: ペン先の位置を ECG と Edi の消去帯から求め、動画全体の掃引モデルで補う（旧: ECG の列ごとの標準偏差で掃引線を探していた）
  - 右端の Edi バー（濃い紫）を波形として拾っていた不具合を修正
  - 縦軸を較正（0 µV = パネルの下端。旧: ROI 内の画素の高さをそのまま出力していた）。値は µV
  - 縦軸の上限を超えた部分を頭打ちとして上限の値で埋める。1周より長い呼吸は複数の画像からつなぐ
  - 平滑化（移動平均）をやめた（画面の波形をそのまま出す）
- Breaths.csv に Wave_T0 / Wave_Rate（各点の時刻）、Start_Time / End_Time、E_min / E_max（縦軸）を追加
- ExtractWaves.py を削除（core.py に統合）

## 1.0 (2026-04)
- サーバー版の初版
