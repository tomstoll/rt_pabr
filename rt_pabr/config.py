import json
import os

CONFIG_FILE = 'config.json'

DEFAULTS = {
    "WORKSPACE_DIR": os.path.abspath(os.path.join(os.path.dirname(__file__), '..')),
    "FORCE_SOUNDDEVICE": False,
    "BAYESIAN_WEIGHTING": True,
    "CH_LEFT_NONINV": "None",
    "CH_LEFT_INV": "ABR-L",
    "CH_RIGHT_NONINV": "None",
    "CH_RIGHT_INV": "ABR-R",
    "UDP_IP": "127.0.0.1",
    "UDP_PORT": 55555,
    "TUBE_DELAY": 0.001,
    "L_FREQ": 30,
    "H_FREQ": 1500,
    "FILT_ORDER": 1,
    "NOTCH_FREQS": [60, 180, 300, 420, 540],
    "NOTCH_WIDTH": 5,
    "DYN_HP_OPTIONS": [1, 30, 150],
    "DYN_LP_OPTIONS": [1500, 2000],
    "DYN_ORDER_OPTIONS": [1, 2, 3, 4],
    "TMIN": -0.5,
    "TMAX": 1.5,
    "PEAK_MIN_MS": 4.0,
    "PEAK_MAX_MS": 16.0,
    "NOISE_WIN_MIN_MS": -350.0,
    "NOISE_WIN_MAX_MS": -20.0,
    "RESP_WIN_MIN_MS": 1.0,
    "RESP_WIN_MAX_MS": 20.0,
    "BUFFER_SEC": 10.0,
    "XLIMS": [-5, 25],
    "TOGGLE_EXP_KEY": "1",
    "DECIMATION_FACTOR": 2,
    "TRANSDUCERS": {
        "ER2": 0.01,
        "ER3": 0.01 * 10**(26.5/20),
        "HD 650": 0.01 * 10**(8.7/20)
    }
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULTS, f, indent=4)
        return DEFAULTS.copy()
    with open(CONFIG_FILE, 'r') as f:
        try:
            user_config = json.load(f)
        except json.JSONDecodeError:
            user_config = {}
    merged = DEFAULTS.copy()
    merged.update(user_config)
    return merged

def save_config(new_config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(new_config, f, indent=4)

_current_config = load_config()
for key, value in _current_config.items():
    globals()[key] = value