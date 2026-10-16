ENGINE_SETTINGS_SCHEMA = {
    "initial_ocr": {
        "skip_keys": {
            "type": "multiselect",
            "choices": ["EdiPeak", "Edimin"],
            "default": [],
            "label": "ROI Keys to be skipped"
        },
        "use_gpu": {
            "type": "bool",
            "default": True,
            "label": "Enable GPU Processing"
        },
        "diff_thresh": {
            "type": "float",
            "default": 2.0,
            "label": "Display update when ROI changes more than (mean gray level)"
        }
    },
    "post_ocr": {
        "fps": {
            "type": "int",
            "default": 10,
            "label": "Video FPS"
        },
        "edi_scale_max": {
            "type": "float",
            "default": 20,
            "label": "Edi axis maximum on the screen (µV)"
        },
        "min_breath_sec": {
            "type": "float",
            "default": 0.3,
            "label": "Minimum interval between display updates (sec)"
        },
        "max_onset_lag_sec": {
            "type": "float",
            "default": 1.5,
            "label": "Max lag from Edimin update (onset) to Edipeak update (sec)"
        },
        "onset_shift_sec": {
            "type": "float",
            "default": -0.3,
            "label": "Shift of breath boundary from Edimin update (sec)"
        },
        "abnormal_screen_diff": {
            "type": "float",
            "default": 15.0,
            "label": "Abnormal frame threshold (screen label difference)"
        },
        "abnormal_scale_diff": {
            "type": "float",
            "default": 8.0,
            "label": "Abnormal frame threshold (Edi axis label difference)"
        }
    }
}
