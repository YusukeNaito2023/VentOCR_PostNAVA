# PostNAVA VentOCR Engine (postnava_ventocr)

## Supported Devices
- Servo-U "Edi monitor" screen (ECG at the top, Edi waveform in the middle, Edipeak / Edimin values on the right; e.g. after weaning from NAVA, in standby)
- Videos recorded at 1280x720 or 1920x1080 (other resolutions are scaled to 1280x720 before reading)

## Description
- **OCR**: Edipeak and Edimin (values on the right). Only frames where the display changed are OCR'd
  - If no breath is detected for about 10 s, both become "***" (no value; `NoBreathDetected` in that breath's Alert)
  - A read that is not what the Servo-U shows (1-2 digits and one decimal, e.g. "233, 8" when the decimal point is read as a digit) is read once more at twice the size; if that also fails, it is left empty. Values of 100 µV or more are never read
  - 0.3.2: an Edipeak of 10 or more above twice the breath's waveform maximum (not clipped) is read again on up to 4 later frames (EasyOCR can read the decimal point as a "3": "43, 8" for 4.8).
    A re-read that fits the waveform replaces it; otherwise a value read at least twice with its decimal point is kept (the Servo-U Edipeak can be well above the trace); otherwise it is left empty (`EdiPeakUnreadable` in Alert)
- **1 breath** = from the onset of an Edi rise to the next breath's onset; breaths are the ones the Servo-U detects (Edipeak display updates)
  - The Servo-U updates Edimin when Edi rises (onset of neural inspiration), and Edipeak after Edi passes its peak and starts to fall (about 0.35 s later)
  - When a value equals the previous one the display does not change and the update is invisible, so breaths are counted by Edipeak updates, and the Edimin update just before each (within `max_onset_lag_sec`) is taken as the onset.
    If it is invisible, the onset is estimated from the typical lag (0.4 s in the sample) (`OnsetEstimated` in Alert)
  - 0.3.1: the onset is placed on the Edi trace itself (`Onset_Source` = `wave`): the Edi just behind the pen tip in each frame (Frames.csv `Pen_Y`) gives Edi at 10 Hz;
    the breath's peak is the highest point in the `peak_search_sec` (1.5 s) before its Edipeak update, the trough the lowest point after the previous breath's Edipeak update,
    and the onset the last frame within max(0.3 µV, 10% of the rise) of the trough. Where the trace does not show it, the Edimin update (`edimin`) or the typical lag (`estimated`)
    is used, `onset_shift_sec` (default -0.3 s) earlier, and never before the previous breath's Edipeak update.
    The Edimin update can come well into the rise (validation video 14-58-39: 0.3-1.6 s before the Edipeak update), which made breaths start partway up the rise up to 0.3.0
- **Values in Breaths.csv**
  - `EdiPeak`: the Edipeak shown for that breath
  - `Edimin`: minimum of that breath's expiration = the Edimin shown at the next breath's onset (verified to match the waveform minimum).
    `Edimin_displayed` is the value shown during that breath (= minimum of the previous breath's expiration). Both are read from the breath's Edipeak update on (the Servo-U updates Edimin after the breath has started)
  - `Wave_Max` / `Wave_Min`: maximum / minimum of the extracted waveform (µV)
- **Waveform**: Edi (columns of BreathCSVs/<n>.csv are X, Edi; unit µV)
  - The sweep draws left to right at about 164 px/s; on reaching x=1046 it returns to x=150 and overwrites (one sweep 896 px ≈ 5.4 s, wrap around)
  - In each frame, the blank where neither ECG nor Edi is drawn (the erase band ahead of the pen tip, about 35-40 px wide) is located and taken as the pen tip;
    the sweep speed and phase are estimated from the whole video to determine the pen tip in each frame (false blanks, e.g. while Edi is not drawn, are removed as outliers)
  - A breath's waveform is cut out from the image of that breath's last frame as "the column after the start pen tip through the end pen tip column".
    For breaths longer than one sweep (about 5.4 s), the missing earlier part is cut out from images of earlier frames and joined (`Wave_Images` = number of images used)
  - Time of each point = `Wave_T0` + row number / `Wave_Rate` (Breaths.csv)
  - Vertical axis: 0 µV = panel bottom edge (y=419.4), upper limit = panel top edge (y=219.4) (with the upper limit 20 µV: 10.0 px/µV, calibrated with 24 breaths' Edipeak in the sample, residual SD 0.1 µV)
  - Upper limit: read from the label at the top left of the Edi band ("20", "25", ...) with whole-label templates (`axis_labels.npz`, labels 5-80 µV).
    The Servo-U changes the Edi scale often (auto-scaling; in the validation videos every few to tens of seconds) and redraws the whole trace at the new scale in the same frame,
    so each image's columns are converted with the label read on that image (`Edi_Scale` in Frames.csv; Breaths.csv `Edi_Scale` = the breath's last image, `E_max` = the largest in the breath).
    A label hidden by the trace is taken from the reads before and after when they are equal and at most `scale_fill_sec` (7 s) apart.
    Otherwise the breath has no waveform (`ScaleUnknown` in Alert). A label that has no template is never guessed
  - Parts above the vertical-axis upper limit are not drawn, so they are clipped at the upper-limit value (`Clipped_Points` = number of such points, `Clipped` in Alert).
    For such breaths, the on-screen Edipeak value is the correct one

## Expected Layout
See `layout.jpg` (red boxes: OCR, white box: Edi waveform, gray box: ECG used to detect the sweep position, yellow box: Edi bar (excluded), blue boxes: text for the screen check and the vertical-axis scale label).

## Limitations
- Edi axis labels without a template (e.g. above 80 µV) are not read: those breaths have no waveform (`ScaleUnknown`). Add templates with `tools/build_axis_labels.py`.
  `edi_scale_max` (default 20 µV) is used only when `axis_labels.npz` is missing
- Parts above the vertical-axis upper limit are clipped (see above)
- Accuracy: the Servo-U does not log while in standby, so no ventilator export covers this screen. The engine is checked with on-screen values
  (Edipeak vs the waveform maximum) and by eye (see CHANGELOG)
- Breath boundaries follow the Servo-U's breath detection (display updates). Small Edi fluctuations may also be counted as one breath
- Fast breathing with many small breaths (validation hour 16-58-39): a breath of a few µV on a 25-35 µV axis is only a few pixels high, so its waveform and boundaries are coarse (see CHANGELOG 0.3.2)
- The first and last breaths of a recording are cut off partway (`Fragment` in Alert)
- Columns overlapping the Edi bar at the right edge (x=1000-1048) and the white dashed line (x=1003-1007, 1041-1045) are not read; they are interpolated from both neighbors

## Author
- VentOCR Developer Team
- Contact: naito623@naramed-u.ac.jp
