import json
import os
import sys

# Determine workspace directory from CLI arguments if provided, else use current working directory
_ws_dir = os.getcwd()
if '--workspace' in sys.argv:
    try:
        _idx = sys.argv.index('--workspace')
        _ws_dir = sys.argv[_idx + 1]
    except IndexError:
        pass

CONFIG_FILE = os.path.join(_ws_dir, 'rt_pabr_config.json')

DEFAULTS = {
    "WORKSPACE_DIR": _ws_dir,
    "FORCE_SOUNDDEVICE": True,
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
    "PEAK_MIN_MS": 5.0,
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