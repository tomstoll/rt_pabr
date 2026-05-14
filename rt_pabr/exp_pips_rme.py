# -*- coding: utf-8 -*-
"""
Created on ---

@author: tom stoll
"""

from __future__ import print_function

import argparse
import io
import json
import os
import socket
import sys
import threading
import time
from ctypes import windll

import numpy as np

os.environ["SD_ENABLE_ASIO"] = "1"
os.environ["_EXPYFUN_WIN_INVISIBLE"] = "true"
import sounddevice  # noqa: E402
from expyfun import ExperimentController, decimals_to_binary, get_config  # noqa: E402
from expyfun.io import read_hdf5  # noqa: E402
from expyfun.visual import ProgressBar  # noqa: E402

from rt_pabr import config

parser = argparse.ArgumentParser()
parser.add_argument('--run_file', type=str, default=None)
parser.add_argument('--subject', type=str, default=None)
parser.add_argument('--start_trial', type=int, default=None)
parser.add_argument('--transducer', type=str, default='ER2')
parser.add_argument('--workspace', type=str, default='')
args, unknown = parser.parse_known_args()


# try to prevent screensaver from starting
windll.kernel32.SetThreadExecutionState(0x80000002)  # this will prevent the screen saver or sleep.

# =============================================================================
# SET RUN SETTINGS
# =============================================================================
ac = None
gapless = get_config('SOUND_CARD_BACKEND') == 'sounddevice'
if not gapless:
    if getattr(config, 'FORCE_SOUNDDEVICE', False):
        ac = {'TYPE': 'sound_card', 
              'SOUND_CARD_BACKEND': 'sounddevice',
              'SOUND_CARD_TRIGGER_ID_AFTER_ONSET': True}
        gapless = True
        print("Setting SOUND_CARD_BACKEND to 'sounddevice' for this experiment.")
    else:
        print("WARNING: Your expyfun SOUND_CARD_BACKEND is not set to 'sounddevice'.\n"
              "This means there will be a gap between trials.\n"
              "You can enable 'FORCE_SOUNDDEVICE' in the Settings GUI to fix this automatically.")

transducer = args.transducer
if transducer not in config.TRANSDUCERS:
    raise ValueError("Transducer must be one of " + str(list(config.TRANSDUCERS.keys())))
ref_rms = config.TRANSDUCERS[transducer]
print('You have selected to run %s' % transducer)

run_file_options = [
                    'CALIBRATION',  # plays 1 kHz tone, should be 80 dB
                    ]

if args.run_file is not None:
    run_file = args.run_file
    print('Running selected file via UI/CLI: %s' % run_file)
    if run_file not in run_file_options:
        run_file_options.append(run_file)
else:
    print('Here are your options for paradigms to run:')
    for ri, rf in enumerate(run_file_options):
        print('    [%i] %s' % (ri, rf))
    run_file_input = ''
    while run_file_input not in [str(ii) for ii in range(len(run_file_options))]:
        run_file_input = input(
                'Enter the number of the paradigm to run: ')
    run_file = run_file_options[int(run_file_input)]
    print('You have selected to run %s' % run_file)
    print(' ' * 300)
    sys.stdout.flush()

if run_file == 'CALIBRATION':
    doing_calibration = True
    output_dir = 'data'
    if args.workspace:
        output_dir = os.path.join(args.workspace, output_dir).replace('\\', '/')
    sub = 'calibration'
    output_dir = '/'.join([output_dir, sub])
    os.makedirs(output_dir, exist_ok=True)
    runs = [dict(fn_stim='calibration', stim_db=80, n_trials=600,
                 band_picks=[], ear_picks=[])]
    data_fn = [r['fn_stim'] for r in runs]
    fs = int(48e3)
    # put some dummy data with 1 minute trials for calibration
    tmp = dict(fs=fs, dur_band=0.0001, f_band=0, interleave_flips=1,
               invert_sign=1, n_tokens=60,
               pips=np.array([[0.01414214, 0.01414214, 0.01414214,
                               0.01414214, 0.01414214]]),
               rate=np.array([20.]), x=np.zeros([60, 2, 1, fs]),
               x_pulse=np.zeros([60, 2, 1, fs]))
    data = dict(calibration=tmp)
    print('\x1B[38;5;11m========= WARNING ===========\x1B[0m\n'
          'This is a calibration run. Do not put headphones in anyones ears!\n'
          'Calibration level is 80 dB.')
else:
    doing_calibration = False
    output_dir = 'data'
    if args.workspace:
        output_dir = os.path.join(args.workspace, output_dir).replace('\\', '/')
    sub = ''
    # ask for the subject name/number
    if output_dir is not None:
        if args.subject is not None:
            sub = args.subject
            output_dir = '/'.join([output_dir, sub])
        else:
            while sub == '':
                sub = input("Subject: ")
            # if given a subject number, make it 3 digits
            sub = '%03i' % int(sub) if sub.isdigit() else sub
            output_dir = '/'.join([output_dir, sub])  # update output directory
        os.makedirs(output_dir, exist_ok=True)

    runs = json.load(io.open(run_file, 'r', encoding='utf-8-sig'))
    data = dict()
    data_fn = [r['fn_stim'] for r in runs]
    print('Loading data for %i runs...' % len(runs)),
    for r in runs:
        if r['fn_stim'] not in data.keys():
            data[r['fn_stim']] = read_hdf5(r['fn_stim'])
            
            # Apply correction factors across frequencies if present
            c_factors = r.get('correction_factors', None)
            if c_factors is not None:
                c_factors = np.array(c_factors, dtype=float)
                if np.any(c_factors != 0):
                    f_bands = data[r['fn_stim']]['f_band']

                    # Fix for click files where f_band has multiple elements but the correspond x dimension has shape 1
                    if np.all(f_bands == f_bands[0]) and data[r['fn_stim']]['x'].shape[2] == 1:
                        f_bands = [f_bands[0]]

                    freq_map = {0: 0, 500: 1, 1000: 2, 2000: 3, 4000: 4, 8000: 5}

                    # Get the relevant correction factor for each frequency band (default 0 dB)
                    scale_array = np.array([c_factors[freq_map[f]] if f in freq_map else 0.0 for f in f_bands])

                    scale_mult = 10 ** (scale_array / 20.0)
                    # Broadcast scale values: (n_tokens, n_ch, n_band, len_trial)
                    data[r['fn_stim']]['x'] *= scale_mult[np.newaxis, np.newaxis, :, np.newaxis]
            
    print('%i unique data files loaded.' % len(data))
    fs = data[data_fn[0]]['fs']

do_interleave = True
assert do_interleave,  "Trial planning not done for non-interleave. MUST be True"

# estimate how long it will take
dur = data[data_fn[0]]['x'].shape[-1] / float(fs)
n_trials_tot = sum([r['n_trials'] for r in runs])
print('Stimulus time: %i minutes' % (dur * n_trials_tot / 60.))
print('Run order will %sbe interleaved.' % ['**NOT** ', ''][do_interleave])
print(' ' * 300)
sys.stdout.flush()

# Ask if we should start on the first trial or in the middle
if args.start_trial is not None:
    start_trial = args.start_trial
else:
    start_trial = ''
    while start_trial not in [str(ii) for ii in range(n_trials_tot)]:
        start_trial = input('Enter which trial you want to start on '
                            '(0-%i) [0]: ' % (n_trials_tot - 1))
        if start_trial == '':
            start_trial = '0'
    start_trial = int(start_trial)
print('You are starting on trial %i.\n\n' % start_trial)
sys.stdout.flush()

# =============================================================================
# Plan the trials
# =============================================================================
n_epochs = np.array([r['n_trials'] for r in runs])
n_runs = len(n_epochs)
n_seq_min = 1
n_ep_min = np.min(n_epochs)
n_laps = int(np.floor(float(n_ep_min) / n_seq_min))
starts = np.round(np.array([np.arange(n_laps + 1) * ne / float(n_laps)
                            for ne in n_epochs])).astype(int)
counts = np.diff(starts)
run_inds = np.array([])
trial_inds = np.array([])
for li in range(n_laps):
    for ri in range(n_runs):
        run_inds = np.concatenate((run_inds, np.ones(counts[ri, li]) * ri))
        trial_inds = np.concatenate(
                (trial_inds,
                 starts[ri, li] + np.arange(counts[ri, li])))

run_inds = run_inds.astype(int)
trial_inds = trial_inds.astype(int)

if not do_interleave:
    assert False, 'do_interleave must be True'
    run_inds = np.sort(run_inds)

n_run_bits = np.maximum(1, int(np.ceil(np.log2(len(runs)))))
n_tok_bits = int(np.ceil(np.log2(np.max([d['x'].shape[0]
                 for d in data.values()]))))


# =============================================================================
#  Set up the RME audio stream
# =============================================================================
# high priorty
def setpriority(pid=None, priority=1):
    """ Set The Priority of a Windows Process.  Priority is a value between
        0-5 where 2 is normal priority.  Default sets the priority of the
        current python process but can take any valid process ID. """

    import win32api  # pyright: ignore[reportMissingModuleSource]
    import win32con  # pyright: ignore[reportMissingModuleSource]
    import win32process  # pyright: ignore[reportMissingModuleSource]

    priorityclasses = [win32process.IDLE_PRIORITY_CLASS,
                       win32process.BELOW_NORMAL_PRIORITY_CLASS,
                       win32process.NORMAL_PRIORITY_CLASS,
                       win32process.ABOVE_NORMAL_PRIORITY_CLASS,
                       win32process.HIGH_PRIORITY_CLASS,
                       win32process.REALTIME_PRIORITY_CLASS]
    if pid is None:
        pid = win32api.GetCurrentProcessId()
    handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, True, pid)
    win32process.SetPriorityClass(handle, priorityclasses[priority])


setpriority(priority=4)

status_string = ''
with ExperimentController(run_file, verbose=True, screen_num=0,
                          window_size=[1, 1], stim_db=-np.inf,
                          noise_db=-np.inf, full_screen=False,
                          response_device='keyboard',
                          stim_fs=fs,
                          session=run_file,
                          output_dir=output_dir,
                          check_rms=None,
                          stim_rms=ref_rms,
                          n_channels=2,
                          suppress_resamp=True,
                          force_quit=['end'],
                          participant=sub,
                          version='dev',
                          audio_controller=ac,
                          gapless=gapless) as ec:

    # ec.refocus()
    # =========================================================================
    # Remove this and use ec.refocus instead for the window to stay on top
    m_hWnd = ec._win._hwnd  # experiment window handler
    # bring experiment window to the front
    windll.user32.SetWindowPos(m_hWnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    # =========================================================================

    # Write out some basic info to the log
    ec.write_data_line('do_interleave', do_interleave)
    ec.write_data_line('n_run_bits', n_run_bits)
    ec.write_data_line('n_tok_bits', n_tok_bits)
    ec.write_data_line('participant', sub)
    ec.write_data_line('start_trial', start_trial)

    # =========================================================================
    # Run the experiment
    # =========================================================================
    ec.write_data_line('run_file', run_file)
    # Write out info about each run to the log
    for ri, run in enumerate(runs):
        ec.write_data_line('run_num', ri)
        ec.write_data_line('fn_stim', run['fn_stim'])
        ec.write_data_line('stim_db', run['stim_db'])
        ec.write_data_line('n_trials', run['n_trials'])
        ec.write_data_line('n_tokens', data[data_fn[ri]]['n_tokens'])
        try:
            ec.write_data_line('stim_hash', data[data_fn[ri]]['stim_hash'])
        except KeyError:
            pass
        ec.write_data_line('ear_picks', run['ear_picks'])
        ec.write_data_line('band_picks', run['band_picks'])
        ec.write_data_line('f_band', data[data_fn[ri]]['f_band'])
        ec.write_data_line('rate', data[data_fn[ri]]['rate'])
        ec.write_data_line('invert_sign', data[data_fn[ri]]['invert_sign'])
        ec.write_data_line('units', run.get('units', 'peSPL'))
        ec.write_data_line('correction_factors', run.get('correction_factors', [0.0]*6))

    ec.write_data_line('setup_complete', 1)

    # UDP Listener for external control commands
    ext_cmd = 'pause'
    def udp_listener():
        global ext_cmd
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((config.UDP_IP, config.UDP_PORT))
        while True:
            try:
                data, _ = sock.recvfrom(1024)
                cmd = data.decode('utf-8').strip()
                if cmd in ['run', 'pause', 'stop']:
                    ext_cmd = cmd
                if cmd == 'stop':
                    break
            except Exception:
                pass
                
    listener_thread = threading.Thread(target=udp_listener, daemon=True)
    listener_thread.start()

    # Wait for UI to start experiment
    print("Waiting for real-time analyzer GUI to start the experiment...")
    while True:
        if ext_cmd == 'run':
            break
        elif ext_cmd == 'stop':
            sys.exit(0)
        ec.check_force_quit()
        ec.wait_secs(0.1)

    start_times = [ec.current_time]
    ec.listen_presses()
    for ti in range(start_trial, n_trials_tot):
        # Check external control command via UDP
        if ext_cmd == 'stop':
            print("Stop command received from UI. Exiting.")
            break
        elif ext_cmd == 'pause':
            ec.write_data_line('pause', 'start')
            while True:
                ec.wait_secs(0.1)
                ec.check_force_quit()
                if ext_cmd in ['run', 'stop']:
                    break
            ec.write_data_line('pause', 'stop')
            if ext_cmd == 'stop': break

        ri = run_inds[ti]
        tri = trial_inds[ti]
        tok = np.mod(tri, data[data_fn[ri]]['n_tokens'])

        ec.set_stim_db(runs[ri]['stim_db'])
        # put the audio data in there
        x = data[data_fn[ri]]['x'][tok]
        if len(runs[ri]['band_picks']):
            x1 = x[:, np.isin(
                np.round(data[data_fn[ri]]['f_band']).astype(int),
                np.round(runs[ri]['band_picks']).astype(int))]
        if runs[ri]['ear_picks'] == [0]:
            x[1] *= 0
        elif runs[ri]['ear_picks'] == [1]:
            x[0] *= 0

        trial_audio = x.sum(1)

        if doing_calibration:
            trial_audio[0] = np.sin(2 * np.pi * np.arange(x.shape[-1]) /
                                    fs * 1000.) / np.sqrt(1. / 2) * 0.01
            trial_audio[1] = np.sin(2 * np.pi * np.arange(x.shape[-1]) /
                                    fs * 1000.) / np.sqrt(1. / 2) * 0.01
            ec.set_stim_db(80.)

        ec.identify_trial(ec_id='run%02i,trial%03i,token%03i' %
                          (ri, ti, tok),
                          ttl_id=decimals_to_binary([ri, tok],
                                                    [n_run_bits, n_tok_bits]))
        ec.load_buffer(trial_audio)

        # calculate remaining time and display
        dt_med = dur  # data[data_fn[0]]
        ttr = dt_med * (n_trials_tot - ti - 1)
        status_string = ('\nPlaying trial %i / %i from run %i.\n' %
                         (tri + 1, n_epochs[ri], ri + 1) +
                         'Total time remaining: %i:%02i.\n' %
                         (ttr // 60, np.mod(ttr, 60)) +
                         'Finish time: %s.' %
                         time.strftime('%I:%M',
                                       time.localtime(time.time() + ttr)))

        # play the file
        start_times += [ec.current_time]
        ec.start_stimulus(flip=False)
        if not gapless:
            ec.wait_secs(max(trial_audio.shape)/fs + 0.03)
            ec.stop()

        ec.trial_ok()
        ec.write_data_line('trial number', ti)
        print('Last trial: %i.' % ti)
        sys.stdout.flush()

        ec.check_force_quit()

    ec.wait_secs(1)

windll.kernel32.SetThreadExecutionState(0x80000000)  # set back to normal
