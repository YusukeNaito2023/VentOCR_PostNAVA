# PostNAVA VentOCR Engine (postnava_ventocr)

## Supported Devices
- Servo-U "Edi monitor" screen (ECG at the top, Edi waveform in the middle, Edipeak / Edimin values on the right; e.g. after weaning from NAVA, in standby)
- Videos recorded at 1280x720 (other resolutions are scaled to 1280x720 before reading)

## Description
- **OCR**: Edipeak and Edimin (values on the right). Only frames where the display changed are OCR'd
  - If no breath is detected for about 10 s, both become "***" (no value; `NoBreathDetected` in that breath's Alert)
- **1 breath** = from an Edimin display update to the next breath's Edimin update
  - The Servo-U updates Edimin when Edi rises (onset of neural inspiration), and Edipeak after Edi passes its peak and starts to fall (about 0.35 s later)
  - When a value equals the previous one the display does not change and the update is invisible, so breaths are counted by Edipeak updates, and the Edimin update just before each (within `max_onset_lag_sec`) is taken as the onset.
    If it is invisible, the onset is estimated from the typical lag (0.4 s in the sample) (`OnsetEstimated` in Alert)
  - The Edimin update lags slightly behind the Edi rise (median 0.1 s after passing 20% of the height), so the boundary is placed `onset_shift_sec` (default -0.3 s) earlier
- **Values in Breaths.csv**
  - `EdiPeak`: the Edipeak shown for that breath
  - `Edimin`: minimum of that breath's expiration = the Edimin shown at the next breath's onset (verified to match the waveform minimum).
    `Edimin_displayed` is the value shown during that breath (= minimum of the previous breath's expiration)
  - `Wave_Max` / `Wave_Min`: maximum / minimum of the extracted waveform (µV)
- **Waveform**: Edi (columns of BreathCSVs/<n>.csv are X, Edi; unit µV)
  - The sweep draws left to right at about 164 px/s; on reaching x=1046 it returns to x=150 and overwrites (one sweep 896 px ≈ 5.4 s, wrap around)
  - In each frame, the blank where neither ECG nor Edi is drawn (the erase band ahead of the pen tip, about 35-40 px wide) is located and taken as the pen tip;
    the sweep speed and phase are estimated from the whole video to determine the pen tip in each frame (false blanks, e.g. while Edi is not drawn, are removed as outliers)
  - A breath's waveform is cut out from the image of that breath's last frame as "the column after the start pen tip through the end pen tip column".
    For breaths longer than one sweep (about 5.4 s), the missing earlier part is cut out from images of earlier frames and joined (`Wave_Images` = number of images used)
  - Time of each point = `Wave_T0` + row number / `Wave_Rate` (Breaths.csv)
  - Vertical axis: 0 µV = panel bottom edge (y=419.4), 20 µV = panel top edge (y=219.4). 10.0 px/µV (calibrated with 24 breaths' Edipeak in the sample, residual SD 0.1 µV)
  - Parts above the vertical-axis upper limit are not drawn, so they are clipped at the upper-limit value (20 µV) (`Clipped_Points` = number of such points, `Clipped` in Alert).
    For such breaths, the on-screen Edipeak value is the correct one

## Expected Layout
See `layout.jpg` (red boxes: OCR, white box: Edi waveform, gray box: ECG used to detect the sweep position, yellow box: Edi bar (excluded), blue boxes: text for the screen check and the vertical-axis scale label).

## Limitations
- The vertical-axis upper limit is a setting (`edi_scale_max`, default 20 µV). The scale label "20" is too small to OCR, so frames that differ from the reference image (`scale_reference.png`)
  are marked Abnormal and that breath's waveform is not output (`ScaleChanged` in Alert). For videos with a different upper limit, set `edi_scale_max` accordingly and recreate the reference image
- Parts above the vertical-axis upper limit (20 µV) are clipped (see above)
- Breath boundaries follow the Servo-U's breath detection (display updates). Small Edi fluctuations may also be counted as one breath
- The first and last breaths of a recording are cut off partway (`Fragment` in Alert)
- Columns overlapping the Edi bar at the right edge (x=1000-1048) and the white dashed line (x=1003-1007, 1041-1045) are not read; they are interpolated from both neighbors

## Author
- VentOCR Developer Team
- Contact: naito623@naramed-u.ac.jp
