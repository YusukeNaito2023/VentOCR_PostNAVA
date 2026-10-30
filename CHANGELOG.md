# CHANGELOG

## 0.3.0 (2026-10-30)
The Edi scale is read from the axis label on every image. Up to 0.2.0 the scale was fixed at 20 µV (`edi_scale_max`) and a frame whose label differed from the "20" reference image was marked Abnormal.

- The Servo-U auto-scales the Edi axis: in the validation videos (2026-10-24, Servo-U Edi monitor in standby, 1920x1080, 8 h) the label changes every few to tens of seconds, between 5 and 80 µV. When it changes, the whole visible trace is redrawn at the new scale in the same frame (checked on the frames around changes at 16.4 s and 25.1 s of video 14-58-39; the pen keeps its position).
- 0.2.0 on video 14-58-39 (1 h): the label was not "20" on 99.8% of frames, so they were Abnormal and no Edipeak update counted as a breath: 1 breath, no waveforms.
- `axis_labels.py`: the label (box x 152-181, y 220-233 at 1280x720) is compared as a whole with templates (`axis_labels.npz`), as in the NAVA engine 0.2.1: RMS difference of the gray levels after subtracting the background, read only within 4.5 and with the next other label at least 3.0 further; pixels of the trace crossing the label are left out. A label may have several templates. Templates: `tools/build_axis_labels.py` from the key frames (every 8.3 s) of 7 of the 8 validation hours and the sample video, 22 clusters named by eye (5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80). The closest two different labels are 7.1 apart ("10" / "18").
  Held-out hour (14-58-39, 432 key frames): 430 read, all those checked by eye right (4 per label); the 2 not read had the trace across the label. Best distance: median 1.3, 99th percentile 1.73.
- `initial_ocr` reads the label (Frames.csv `Edi_Scale_Read`) when its box changed since the last read. `post_ocr` gives each frame a scale (`Edi_Scale`): the read value, or for a frame where it was not read, the value of the reads before and after when they are equal and at most `scale_fill_sec` apart (7 s: a trace drawn across the label stays there for one sweep, about 5.5 s). Otherwise the scale is unknown; it is never guessed.
- `extract_waves` converts each image's columns with that image's scale, so a breath read from images with different scales is right. A breath with an image whose scale is unknown has no waveform (new Alert `ScaleUnknown`, excluded from accuracy; replaces `ScaleChanged`). Breaths.csv `Edi_Scale` = scale of the breath's last image, `E_max` = the largest scale in the breath.
- Frames are no longer marked Abnormal for the scale label; `abnormal_scale_diff` and `scale_reference.png` are removed. `edi_scale_max` is used only when `axis_labels.npz` is missing.
- `supported_resolutions`: 1920x1080 added (the validation videos; the layout is the 1280x720 layout scaled by 1.5).

### Check (installed VentOCR app, CLI `python -m ventocr_core`, 10 fps, default workers, 2026-10-30)
- Sample video (306 s, label always "20"): Breaths.csv identical to 0.2.0 (96 breaths, 96 waveforms; EdiPeak, Edimin, Wave_Max, Wave_Min, Clipped_Points equal). With `scale_fill_sec` 5 s, 8 breaths had no waveform (the clipped trace covers the label for 5.3 s), hence 7 s.
- Validation video 14-58-39 (1 h, not used for the templates): 0.2.0: 1 breath, 0 waveforms, 933 s. 0.3.0: 933 breaths, 933 waveforms, 590 s (the file was in the disk cache for the second run). Label read on 99.9% of frames, scale known on 100%. Screen check 7.2-8.0 (threshold 15), sweep 164.1 px/s detected on 86.1% of frames (sample: 164.2, 85.6%): the 1080p video needs no other change.
- Edipeak (screen) vs the waveform maximum on the 866 breaths without excluded alerts: median +0.16 µV, within 1 µV on 92.4%, within 10% on 92.6%. No ventilator export exists for this screen (the Servo-U does not log in standby), so this and the eye check below are the accuracy check; manifest `accuracy` stays empty.
- By eye, 48 random breaths (end image with the cut range): the scale label and the trace height agree with the values on all of them; Wave_Max within 10% of Edipeak on 46. Known problems that remain (not changed in 0.3.0):
  - Breath boundaries are often late on this recording (about 14 of 48): the breath starts partway up the rise or ends partway up the next rise. The Edimin update comes 0.3-1.6 s before the Edipeak update here (median 1.2 s; sample 0.4 s), 16% of Edipeak updates have no Edimin update within 3 s, and 31% of onsets are estimated (`max_onset_lag_sec` 1.5 s). Most outliers of Edipeak vs Wave_Max (Wave_Max > Edipeak by > 10% on 52 breaths) are small breaths that include the next breath's rise.
  - Edipeak OCR: 3 of 933 breaths have impossible values with an extra digit (283.8, 333.8, 233.8; breath 214 checked by eye: the screen shows 28.8).

## 0.2.0 (2026-10-25)
Supports engine_api 3 (VentOCR no longer saves every frame image; it creates only the images the engine opens. ENGINE_API.md §5.1).
- The images used for waveform extraction are opened first, in ascending frame order, before the per-breath processing (`_frames_to_read`). The per-breath processing reads backwards from the end image, which would otherwise cause seeks in the segment video. Processing that looks at every frame happens only inside initial_ocr
- Frame counts are derived from the frame rate instead of being fixed: the sweep-fit window and block (150 / 25 frames at 10 fps) and the lag of the rough speed estimate (20). At 10 fps the values are the same as 0.1.1
- outputs: added alerts (the meaning of each Alert value and whether it is excluded from the accuracy evaluation)
- The engine no longer uses Japanese: manifest labels, README, code comments and docstrings and .gitignore are in English. Entries for earlier versions below remain in Japanese
- Check (VentOCR e0e306b, PostNAVA sample, 306 s): 0.1.1 (all frames saved) and 0.2.0 (only opened images created; 304 images) give identical Frames.csv, Breaths.csv (except File_Name) and waveforms for 96 breaths. At 20 fps: 96 breaths; EdiPeak, Edimin and the Edi scale are the same as at 10 fps

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
