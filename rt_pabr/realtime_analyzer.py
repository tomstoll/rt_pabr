"""
realtime_analyzer.py

Real-time processing for the pABR paradigm using Lab Streaming Layer (LSL).
It safely reads the expyfun run logs and hooks into the EEG LSL stream to process
epochs and update visualizations simultaneously with the experiment.
"""
import argparse
import csv
import glob
import os
import re
import socket
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
from matplotlib.ticker import FormatStrFormatter
import numpy as np
import pylsl
import scipy.signal as sig
import scipy.fft as sp_fft
from expyfun.io import read_tab_raw
from h5io import read_hdf5, write_hdf5

from rt_pabr import config

try:
    import cmocean
except ImportError:
    print("cmocean not installed. Using matplotlib's viridis instead.")
    cmocean = None

_START_TIME = time.time()

parser = argparse.ArgumentParser()
parser.add_argument('--workspace', type=str, default='')
args, _ = parser.parse_known_args()
WORKSPACE = args.workspace

def get_latest_tab_file():
    base_search = os.path.join(WORKSPACE, '*.tab') if WORKSPACE else '*.tab'
    data_search = os.path.join(WORKSPACE, 'data', '*', '*.tab') if WORKSPACE else 'data/*/*.tab'
    files = glob.glob(base_search) + glob.glob(data_search)
    # Only consider files modified/created after the analyzer started
    valid_files = [f for f in files if os.path.getmtime(f) >= _START_TIME - 2.0]
    if not valid_files:
        return None
    valid_files.sort(key=os.path.getmtime)
    return valid_files[-1]

def parse_tab_file(tab_file):
    try:
        tab = read_tab_raw(tab_file)
    except PermissionError:
        return {}, None, None, False
        
    run_data = {}
    current_run_num = None
    run_keys = ['run_file', 'stim_db', 'n_trials', 'n_tokens', 'ear_picks', 'band_picks', 
                'f_band', 'rate', 'fn_stim', 'stim_hash', 'units', 'correction_factors']
    n_run_bits = None
    n_tok_bits = None
    setup_complete = False
    participant = "Unknown"
    start_trial = 0
    
    for time_stamp, event, value in tab:
        if event == 'participant':
            participant = value
        elif event == 'run_num':
            current_run_num = int(value)
            if current_run_num not in run_data:
                run_data[current_run_num] = {'run_num': current_run_num}
        elif current_run_num is not None and event in run_keys:
            run_data[current_run_num][event] = value
        elif event == 'n_run_bits':
            n_run_bits = int(value)
        elif event == 'n_tok_bits':
            n_tok_bits = int(value)
        elif event == 'setup_complete':
            setup_complete = True
        elif event == 'start_trial':
            start_trial = int(value)
            
    return run_data, n_run_bits, n_tok_bits, setup_complete, participant, start_trial

def resolve_stim_path(fn_stim):
    if isinstance(fn_stim, str) and fn_stim.startswith('['):
        fn_stim = eval(fn_stim)[0]
    if os.path.exists(fn_stim):
        return fn_stim
    fallback = os.path.join(WORKSPACE, 'stimuli', Path(fn_stim).name) if WORKSPACE else os.path.join('stimuli', Path(fn_stim).name)
    if os.path.exists(fallback):
        return fallback
    return Path(fn_stim).name

def main():
    print("Waiting for experiment to start...", flush=True)
    tab_file = None
    while tab_file is None:
        tab_file = get_latest_tab_file()
        time.sleep(1)
    
    print(f"Found tab file: {tab_file}", flush=True)
    
    # Wait until run config is fully written to the log
    while True:
        run_data, n_run_bits, n_tok_bits, setup_complete, participant, start_trial = parse_tab_file(tab_file)
        if setup_complete and n_run_bits is not None and len(run_data) > 0:
            break
        time.sleep(0.5)

    is_calibration = any(r_dict.get('fn_stim') == 'calibration' for r_dict in run_data.values())
    if is_calibration:
        print("Calibration mode detected. Launching simplified UI...", flush=True)
        
        root = tk.Tk()
        root.title(f"Real-Time pABR - CALIBRATION - Subject: {participant}")
        root.geometry("800x400")
        root.configure(bg='red')
        
        lbl = tk.Label(root, text="CALIBRATION IN PROGRESS\n\nDO NOT PUT HEADPHONES\nIN SUBJECT'S EARS!\n\nLevel is 80 dB peSPL.", 
                       bg='red', fg='white', font=("Arial", 32, "bold"))
        lbl.pack(expand=True, fill='both')
        
        def send_udp_cmd(cmd):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(cmd.encode('utf-8'), (config.UDP_IP, config.UDP_PORT))
            except Exception as e:
                print(f"Error sending UDP command: {e}")
        
        running = False
        def toggle_start():
            nonlocal running
            if not running:
                send_udp_cmd('run')
                btn_start.config(text="Pause Calibration", bg='gold')
                running = True
            else:
                send_udp_cmd('pause')
                btn_start.config(text="Resume Calibration", bg='lightgreen')
                running = False

        def stop_exp():
            send_udp_cmd('stop')
            root.destroy()

        frame = tk.Frame(root, bg='red')
        frame.pack(pady=30)
        btn_start = tk.Button(frame, text="Start Calibration", command=toggle_start, font=("Arial", 16, "bold"), bg="lightgreen", width=20)
        btn_start.pack(side='left', padx=20)
        btn_stop = tk.Button(frame, text="Stop Calibration", command=stop_exp, font=("Arial", 16, "bold"), bg="white", width=20)
        btn_stop.pack(side='left', padx=20)
        
        root.protocol("WM_DELETE_WINDOW", stop_exp)
        root.mainloop()
        return

    n_runs = len(run_data)
    n_bits_tot = 1 + n_run_bits + n_tok_bits + 1

    # Load Stimuli
    print("Loading stimuli...", flush=True)
    stim_dn_fs = None
    trial_dur = None
    all_f_bands = set()
    run_f_bands = {}
    for run in range(n_runs):
        stim_path = resolve_stim_path(run_data[run]['fn_stim'])
        s = read_hdf5(stim_path, title='expyfun')
        all_f_bands.update(s['f_band'].tolist())
        run_f_bands[run] = s['f_band'].tolist()
        if stim_dn_fs is None:
            stim_dn_fs = s['fs']
            trial_dur = s['x_pulse'].shape[-1] / stim_dn_fs

    f_band = np.array(sorted(list(all_f_bands)))
    n_freq = len(f_band)
    
    # Connect to LSL stream
    print("Looking for an EEG stream on LSL...", flush=True)
    streams = []
    while not streams:
        streams = pylsl.resolve_byprop('type', 'EEG', timeout=3.0)
        if not streams:
            print("Still looking for an EEG stream... Please check that ActiView LSL streaming is ON.", flush=True)
    inlet = pylsl.StreamInlet(streams[0])
    info = inlet.info()
    fs_raw = int(info.nominal_srate())
    q = getattr(config, 'DECIMATION_FACTOR', 1)
    fs = fs_raw // q
    n_total_chs = info.channel_count()
    print(f"Connected to LSL stream! fs_raw={fs_raw}, downsampled_fs={fs}, channels={n_total_chs}")
    
    ch_names = []
    ch = info.desc().child("channels").child("channel")
    while not ch.empty():
        name = ch.child_value("name") or ch.child_value("label")
        ch_names.append(name if name else "Unknown")
        ch = ch.next_sibling()
        
    def get_ch_idx(possible_names, default_idx):
        for name in possible_names:
            if name in ch_names:
                return ch_names.index(name)
        return default_idx

    def resolve_ch(user_cfg_val):
        if user_cfg_val == "None": return None
        elif user_cfg_val in ch_names: return ch_names.index(user_cfg_val)
        else: return None

    idx_l_noninv = resolve_ch(getattr(config, 'CH_LEFT_NONINV', 'None'))
    idx_l_inv = resolve_ch(getattr(config, 'CH_LEFT_INV', 'ABR-L'))
    idx_r_noninv = resolve_ch(getattr(config, 'CH_RIGHT_NONINV', 'None'))
    idx_r_inv = resolve_ch(getattr(config, 'CH_RIGHT_INV', 'ABR-R'))

    is_left_valid = (idx_l_noninv is not None) or (idx_l_inv is not None)
    is_right_valid = (idx_r_noninv is not None) or (idx_r_inv is not None)

    available_ears = []
    if is_left_valid:
        available_ears.append('Left')
    if is_right_valid:
        available_ears.append('Right')

    if not available_ears:
        is_left_valid = True
        available_ears.append('Left')
        print("WARNING: Configured channels not found in LSL stream. Falling back to default channel indices.", flush=True)
        idx_l_noninv = None
        idx_l_inv = 1 if len(ch_names) > 1 else 0

    # print(f"Using ABR-L Derivation: {ch_names[idx_l_noninv] if idx_l_noninv is not None else 'None'} - {ch_names[idx_l_inv] if idx_l_inv is not None else 'None'}", flush=True)
    # print(f"Using ABR-R Derivation: {ch_names[idx_r_noninv] if idx_r_noninv is not None else 'None'} - {ch_names[idx_r_inv] if idx_r_inv is not None else 'None'}", flush=True)
    # print(f"Active Ears: {available_ears}", flush=True)

    status_ch_index = get_ch_idx(['Status', 'STATUS'], n_total_chs - 1)
    n_chs = 2  # Always 2 for internal processing arrays, duplicate if needed

    print("Downsampling stimuli pulses...", flush=True)
    print("Precalculating stimulus FFTs for fast epoching...", flush=True)
    x_pulse_all = {}
    X_conj_all = {}
    pulse_sum_all = {}
    n_ears = 2
    for run in range(n_runs):
        s = read_hdf5(resolve_stim_path(run_data[run]['fn_stim']), title='expyfun')
        stim_fs = s['fs']
        x_pulse = s['x_pulse']
        n_toks = x_pulse.shape[0]
        x_pulse_dn = np.zeros([n_toks, n_ears, n_freq, int((config.TMAX-config.TMIN)*fs)])
        t_idx, e_idx, f_idx, n_idx = np.where(np.abs(x_pulse) > 0.5)
        
        # Map the run's frequency indices into the global unified array
        run_f_band = s['f_band']
        global_f_idx_map = np.array([np.where(f_band == f)[0][0] for f in run_f_band])
        f_idx_mapped = global_f_idx_map[f_idx]
        
        mapped_idx = np.round(n_idx/stim_fs*fs).astype(int)
        valid = mapped_idx < x_pulse_dn.shape[-1]
        x_pulse_dn[t_idx[valid], e_idx[valid], f_idx_mapped[valid], mapped_idx[valid]] = 1
        x_pulse_all[run] = x_pulse_dn
        X_conj_all[run] = sp_fft.rfft(x_pulse_dn, axis=-1).conj()
        pulse_sum_all[run] = np.sum(np.abs(x_pulse_dn) > 0.5, axis=-1)

    print("Initializing stateful filters...", flush=True)
    # Combine Continuous 1 Hz 1st-order highpass and Notch filters into a single SOS cascade
    # This runs at the full native sampling rate to stabilize DC offset before any epoching
    sos_list = [sig.butter(1, 1.0, btype='high', fs=fs_raw, output='sos')]
    for f in config.NOTCH_FREQS:
        b, a = sig.iirnotch(f, f/config.NOTCH_WIDTH, fs=fs_raw)
        sos_list.append(sig.tf2sos(b, a))
    sos_continuous = np.vstack(sos_list)
    zi_sos_continuous = sig.sosfilt_zi(sos_continuous)
    zi_sos_continuous = np.repeat(zi_sos_continuous[:, np.newaxis, :], n_chs, axis=1)
    
    dyn_lfreq = config.L_FREQ
    dyn_hfreq = config.H_FREQ
    dyn_order = config.FILT_ORDER
    sos_dynamic = sig.butter(dyn_order, [dyn_lfreq, dyn_hfreq], btype='band', fs=fs, output='sos')

    buffer_samples = int(config.BUFFER_SEC * fs_raw)
    ring_buffer = np.zeros((n_chs, buffer_samples))
    global_samples = 0
    
    pending_epochs = []
    detected_triggers = []
    prev_status = 0
    
    n_samples_epoch = int((config.TMAX - config.TMIN) * fs)
    
    # We maintain mathematical equivalence to your offline Bayesian weights
    # by accumulating a weighted numerator and the sum of weights (denominator)
    num = np.zeros((n_runs, n_chs, n_ears, n_freq, n_samples_epoch))
    den = np.zeros((n_runs, n_chs, n_ears, n_freq, 1))

    plt.ion()
    levels = [run_data[r].get('stim_db', 0) for r in range(n_runs)]
    levels_float = np.array(levels).astype(float)
    levels_indices = np.argsort(levels_float)[::-1]
    levels_sorted = levels_float[levels_indices]
    n_levels = len(levels_sorted)
    
    if cmocean:
        cm_lines, cmlb, cmub = cmocean.cm.phase, 1.0, 0.2
        base_colors = cm_lines(np.linspace(cmlb, cmub, n_freq))
    else:
        base_colors = plt.cm.viridis(np.linspace(0, 1, n_freq))
        
    pabr_colors = ['black' if f == 0 else base_colors[i] for i, f in enumerate(f_band)]

    fig = plt.figure(figsize=(14, max(6.0, 1.5*n_levels + 1.5)))
    fig.suptitle(f"Subject: {participant}", fontsize=14, fontweight='bold')
    plt.subplots_adjust(left=0.15, right=0.98, bottom=0.12, top=0.90, wspace=0.15, hspace=0.25)
    axs = fig.subplots(n_levels, n_freq, sharex=True, sharey=True)
    
    if n_levels == 1 and n_freq == 1: axs = np.array([[axs]])
    elif n_levels == 1: axs = np.expand_dims(axs, 0)
    elif n_freq == 1: axs = np.expand_dims(axs, 1)
        
    lines = {}
    t_plot = np.linspace(config.TMIN, config.TMAX, n_samples_epoch, endpoint=False) * 1e3
    
    peak_markers = {}
    peak_texts = {}
    crosshairs = {}
    snr_texts = {}
    
    last_used_i_for_j = {}
    for j, freq in enumerate(f_band):
        used_is = [i for i in range(n_levels) if freq in run_f_bands[levels_indices[i]]]
        last_used_i_for_j[j] = max(used_is) if used_is else n_levels - 1

    for i, lvl in enumerate(levels_sorted):
        run_idx = levels_indices[i]
        rate_val = str(run_data[run_idx].get('rate', 0))
        match = re.search(r'\d+(\.\d+)?', rate_val)
        rate = float(match.group()) if match else 0.0
        rate_str = f"{int(rate)} stim/s" if rate.is_integer() else f"{rate} stim/s"
        
        stim_name = os.path.basename(run_data[run_idx].get('fn_stim', '')).lower()
        stim_type = "Pips" if "pips" in stim_name else "Clicks"
        
        units = run_data[run_idx].get('units', 'peSPL')
        
        first_used_j = next((idx for idx, f in enumerate(f_band) if f in run_f_bands[run_idx]), 0)

        for j, freq in enumerate(f_band):
            ax = axs[i, j]
            is_used = freq in run_f_bands[run_idx]

            line, = ax.plot(t_plot, np.zeros_like(t_plot), color=pabr_colors[j])
            lines[(i, j)] = line
            if not is_used:
                line.set_visible(False)

            if i == 0: ax.set_title('Clicks' if freq == 0 else f'{int(freq)} Hz')
            if j == 0: 
                ax.annotate(f'{stim_type}\n{int(lvl)} dB {units}\n{rate_str}', xy=(-0.75, 0.5), xycoords='axes fraction', 
                            ha='center', va='center', fontsize=12, fontweight='bold')
            if j == first_used_j:
                ax.set_ylabel('Potential (\u03BCV)')
                ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
                ax.tick_params(labelleft=True)

            if i == n_levels - 1 and j == int(n_freq/2): ax.set_xlabel('Time (ms)')
            ax.set_xlim(config.XLIMS) 
            
            if is_used:
                ax.grid(True)
            else:
                ax.axis('off')
                
            if i == last_used_i_for_j[j] and is_used:
                ax.tick_params(labelbottom=True)
            elif not is_used:
                ax.tick_params(labelbottom=False)
            
            pm, = ax.plot([], [], 'v', color=pabr_colors[j], markersize=8, zorder=5,
                          transform=mtransforms.offset_copy(ax.transData, fig=fig, x=0, y=5, units='points'))
            pt = ax.annotate('', (0, 0), xytext=(0, 15), textcoords='offset points', 
                             color=pabr_colors[j], fontsize=8, ha='center', va='bottom', 
                             zorder=6, bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=1))
            peak_markers[(i, j)] = pm
            peak_texts[(i, j)] = pt
            snr_texts[(i, j)] = ax.annotate('SNR: --', (0.03, 0.05), xycoords='axes fraction', ha='left', va='bottom',
                                            fontsize=9, bbox=dict(facecolor='white', alpha=0.8, edgecolor='lightgray', pad=2))
            
            if not is_used:
                snr_texts[(i, j)].set_visible(False)

            ch_v = ax.axvline(0, color='gray', linestyle='--', alpha=0.7, visible=False, zorder=4)
            ch_h = ax.axhline(0, color='gray', linestyle='--', alpha=0.7, visible=False, zorder=4)
            ch_t = ax.text(0, 0, '', color='black', fontsize=9, bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=1), visible=False, zorder=7)
            crosshairs[ax] = {'vline': ch_v, 'hline': ch_h, 'text': ch_t, 'idx': (i, j), 'is_used': is_used}
    
    # Create Progress Bar
    ax_prog = fig.add_axes([0.15, 0.02, 0.83, 0.03])
    ax_prog.set_xlim(0, 1)
    ax_prog.set_ylim(0, 1)
    ax_prog.axis('off')
    ax_prog.add_patch(plt.Rectangle((0, 0), 1, 1, facecolor='lightgray'))
    prog_rect = plt.Rectangle((0, 0), 0, 1, facecolor='green')
    ax_prog.add_patch(prog_rect)
    prog_text = ax_prog.text(0.5, 0.5, '0% (Epoch 0, Time Elapsed --:--, Est. Time Remaining --:--)', 
                             ha='center', va='center', color='black', weight='bold', fontsize=9)
                             
    dropped_text = ax_prog.text(1.02, 0.5, '', color='red', ha='left', va='center', weight='bold', fontsize=10, clip_on=False)
    
    # Initialize state variables before binding interactive widgets
    experiment_active = True
    experiment_finished = False
    needs_save = False
    needs_full_draw = True
    bg = None
    new_epochs_count = 0
    runs_updated = set()
    presented_epochs = 0
    processed_epochs = 0
    dropped_epochs = 0
    exp_start_time = None
    last_plot_time = time.time()
    last_save_time = time.time()
    total_epochs = sum(int(run_data[r].get('n_trials', 0)) for r in range(n_runs))
    total_expected_time = total_epochs * trial_dur
    
    # Shared response tracker for interactive hover
    r_ipsi_ordered = np.zeros((2, n_levels, n_freq, n_samples_epoch))
    r_ipsi_filt = np.zeros_like(r_ipsi_ordered)
    r_ipsi_smooth = np.zeros_like(r_ipsi_ordered)
    
    # Interactive dragging states
    user_modified_peaks = {(ear, i, j): False for ear in (0, 1) for i in range(n_levels) for j in range(n_freq)}
    user_removed_peaks = {(ear, i, j): False for ear in (0, 1) for i in range(n_levels) for j in range(n_freq)}
    dragging_peak = None

    peaks_latency = np.full((2, n_levels, n_freq), np.nan)
    peaks_amplitude = np.full((2, n_levels, n_freq), np.nan)

    # Identify all dynamic artists that need to be redrawn during blitting
    animated_artists = []
    for line in lines.values(): animated_artists.append(line)
    for ch in crosshairs.values(): animated_artists.extend([ch['vline'], ch['hline']])
    for pm in peak_markers.values(): animated_artists.append(pm)
    for pt in peak_texts.values(): animated_artists.append(pt)
    for ch in crosshairs.values(): animated_artists.append(ch['text'])
    for snr in snr_texts.values(): animated_artists.append(snr)
    animated_artists.extend([prog_rect, prog_text, dropped_text])
    
    for artist in animated_artists:
        artist.set_animated(True)
        
    def draw_animated():
        for artist in animated_artists:
            if artist.get_visible():
                ax = getattr(artist, 'axes', None)
                if ax is not None:
                    ax.draw_artist(artist)
                elif getattr(artist, 'figure', None) is not None:
                    artist.figure.draw_artist(artist)

    def trigger_blit():
        if bg is not None:
            fig.canvas.restore_region(bg)
            draw_animated()
            fig.canvas.blit(fig.bbox)
            fig.canvas.flush_events()

    def on_draw(event):
        nonlocal bg
        if event is not None and event.canvas != fig.canvas:
            return
        bg = fig.canvas.copy_from_bbox(fig.bbox)
        draw_animated()
    fig.canvas.mpl_connect('draw_event', on_draw)

    def on_press(event):
        nonlocal dragging_peak, needs_save
        if event.inaxes is None or event.button not in (1, 3): return
        
        clicked_idx = None
        for ax, elements in crosshairs.items():
            if event.inaxes == ax:
                if not elements['is_used']: return
                clicked_idx = elements['idx']
                break
                
        if clicked_idx is None or active_ear is None: return
        i, j = clicked_idx
        ear = active_ear
        pm = peak_markers[(i, j)]
        x, y = pm.get_data()
        
        if user_removed_peaks[(ear, i, j)]:
            if event.button == 3:  # Right click: Restore automatic tracking
                user_modified_peaks[(ear, i, j)] = False
                user_removed_peaks[(ear, i, j)] = False
                needs_save = True
            elif event.button == 1:  # Left click: Place manual peak at crosshair
                user_modified_peaks[(ear, i, j)] = True
                user_removed_peaks[(ear, i, j)] = False
                
                x_mouse = event.xdata
                idx = np.argmin(np.abs(t_plot - x_mouse))
                x_val = t_plot[idx]
                y_val = r_ipsi_filt[ear, i, j][idx]
                
                peaks_latency[ear, i, j] = x_val
                peaks_amplitude[ear, i, j] = y_val
                
                pm.set_data([x_val], [y_val])
                peak_texts[(i, j)].xy = (x_val, y_val)
                peak_texts[(i, j)].set_text(f'{x_val:.1f}ms\n{y_val:.2f}\u03BCV')
                
                pm.set_visible(True)
                peak_texts[(i, j)].set_visible(True)
                dragging_peak = (i, j)
                trigger_blit()
                needs_save = True
            return

        if len(x) == 0: return
        xlim = pm.axes.get_xlim(); ylim = pm.axes.get_ylim()
        dx = (event.xdata - x[0]) / (xlim[1] - xlim[0])
        dy = (event.ydata - y[0]) / (ylim[1] - ylim[0])
        if np.sqrt(dx**2 + dy**2) < 0.05:
            if event.button == 1:  # Left click (drag)
                dragging_peak = (i, j)
                user_modified_peaks[(ear, i, j)] = True
            elif event.button == 3:  # Right click (remove)
                user_modified_peaks[(ear, i, j)] = True
                user_removed_peaks[(ear, i, j)] = True
                pm.set_visible(False)
                peak_texts[(i, j)].set_visible(False)
                trigger_blit()
                needs_save = True

    def on_release(event):
        nonlocal dragging_peak, needs_save
        if dragging_peak is not None:
            needs_save = True
        dragging_peak = None

    def on_hover(event):
        if dragging_peak is not None and event.inaxes is not None and active_ear is not None:
            i, j = dragging_peak
            ear = active_ear
            if event.inaxes == peak_markers[(i, j)].axes:
                x_mouse = event.xdata
                idx = np.argmin(np.abs(t_plot - x_mouse))
                x_val = t_plot[idx]
                y_val = r_ipsi_filt[ear, i, j][idx]
                
                peaks_latency[ear, i, j] = x_val
                peaks_amplitude[ear, i, j] = y_val
                
                peak_markers[(i, j)].set_data([x_val], [y_val])
                peak_texts[(i, j)].set_position((x_val, y_val))
                peak_texts[(i, j)].set_text(f'{x_val:.1f}ms\n{y_val:.2f}\u03BCV')
                trigger_blit()
            return

        for ax, elements in crosshairs.items():
            if event.inaxes == ax and elements['is_used']:
                x_mouse = event.xdata
                idx = np.argmin(np.abs(t_plot - x_mouse))
                x_val = t_plot[idx]
                idx_i, idx_j = elements['idx']
                
                if active_ear is not None:
                    y_val = r_ipsi_filt[active_ear, idx_i, idx_j][idx]
                else:
                    y_val = r_ipsi_filt[:, idx_i, idx_j, idx].mean()
                
                elements['vline'].set_xdata([x_val, x_val])
                elements['hline'].set_ydata([y_val, y_val])
                elements['text'].set_position((x_val, y_val))
                elements['text'].set_text(f'{x_val:.1f}ms\n{y_val:.2f}\u03BCV')
                
                elements['vline'].set_visible(True)
                elements['hline'].set_visible(True)
                elements['text'].set_visible(True)
            else:
                elements['vline'].set_visible(False)
                elements['hline'].set_visible(False)
                elements['text'].set_visible(False)
        trigger_blit()
        
    fig.canvas.mpl_connect('button_press_event', on_press)
    fig.canvas.mpl_connect('button_release_event', on_release)
    fig.canvas.mpl_connect('motion_notify_event', on_hover)
    
    hp_opts = [str(x) for x in config.DYN_HP_OPTIONS]
    if str(dyn_lfreq) not in hp_opts: hp_opts.append(str(dyn_lfreq))
    lp_opts = [str(x) for x in config.DYN_LP_OPTIONS]
    if str(dyn_hfreq) not in lp_opts: lp_opts.append(str(dyn_hfreq))
    order_opts = [str(x) for x in config.DYN_ORDER_OPTIONS]
    if str(dyn_order) not in order_opts: order_opts.append(str(dyn_order))

    def send_udp_cmd(cmd):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(cmd.encode('utf-8'), (config.UDP_IP, config.UDP_PORT))
        except Exception as e:
            print(f"Error sending UDP command: {e}")

    # Initialize to True because the experiment starts paused waiting for the UI
    exp_paused = True
    btn_toggle = None
    def toggle_exp(event=None):
        nonlocal exp_paused
        exp_paused = not exp_paused
        send_udp_cmd('pause' if exp_paused else 'run')
        if btn_toggle is not None:
            if exp_paused:
                btn_toggle.config(text=f"Start / Resume\n(Hotkey: {config.TOGGLE_EXP_KEY})", bg='lightgreen')
            else:
                btn_toggle.config(text=f"Pause Experiment\n(Hotkey: {config.TOGGLE_EXP_KEY})", bg='gold')
        
    def on_key_press(event):
        if event.key == config.TOGGLE_EXP_KEY:
            toggle_exp(None)
    fig.canvas.mpl_connect('key_press_event', on_key_press)
    
    def stop_exp(event=None):
        nonlocal experiment_active
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        if messagebox.askyesno("Confirm Stop", "Are you sure you want to fully stop the experiment?", parent=root):
            send_udp_cmd('stop')
            experiment_active = False
        root.destroy()
        
    try:
        fig.canvas.manager.window.protocol("WM_DELETE_WINDOW", stop_exp)
    except Exception:
        def handle_close(evt):
            nonlocal experiment_active
            experiment_active = False
        fig.canvas.mpl_connect('close_event', handle_close)
        
    def export_csv(event=None):
        base = os.path.basename(tab_file).split('.')[0]
        csv_path = f"{base}_peaks.csv"
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Ear", "Level_dB", "Frequency_Hz", "Latency_ms", "Amplitude_uV"])
            for ear_name in available_ears:
                ear_idx = 0 if ear_name == 'Left' else 1
                for i, lvl in enumerate(levels_sorted):
                    run_idx = levels_indices[i]
                    for j, freq in enumerate(f_band):
                        if freq not in run_f_bands[run_idx]: continue
                        if user_removed_peaks[(ear_idx, i, j)]:
                            writer.writerow([ear_name, lvl, freq, "NaN", "NaN"])
                        else:
                            t = peaks_latency[ear_idx, i, j]
                            y = peaks_amplitude[ear_idx, i, j]
                            if not np.isnan(t):
                                writer.writerow([ear_name, lvl, freq, f"{t:.2f}", f"{y:.4f}"])
                            else:
                                writer.writerow([ear_name, lvl, freq, "NaN", "NaN"])
        print(f"Saved peaks to {csv_path}")
        
    def export_png(event=None):
        base = os.path.basename(tab_file).split('.')[0]
        data_dir = os.path.dirname(tab_file)
        png_path = os.path.join(data_dir, f"{base}_screenshot.png")
        fig.savefig(png_path, dpi=300)
        print(f"Saved screenshot to {png_path}")

    fig.canvas.manager.set_window_title(f"Real-Time pABR - Subject: {participant}")
    window = fig.canvas.manager.window
    if hasattr(window, 'pack_slaves'):
        canvas_widget = fig.canvas.get_tk_widget()
        for slave in window.pack_slaves():
            slave.pack_forget()
            
        side_panel = tk.Frame(window)
        side_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=15, pady=15)
        
        if hasattr(fig.canvas.manager, 'toolbar') and fig.canvas.manager.toolbar:
            fig.canvas.manager.toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        dyn_lfreq_var = tk.StringVar(value=str(dyn_lfreq))
        dyn_hfreq_var = tk.StringVar(value=str(dyn_hfreq))
        dyn_order_var = tk.StringVar(value=str(dyn_order))

        def refresh_filter(*args):
            nonlocal sos_dynamic, needs_save, needs_full_draw
            try:
                dl = float(dyn_lfreq_var.get())
                dh = float(dyn_hfreq_var.get())
                do = int(dyn_order_var.get())
                sos_dynamic = sig.butter(do, [dl, dh], btype='band', fs=fs, output='sos')
                needs_save = True
                needs_full_draw = True
            except ValueError:
                pass

        dyn_lfreq_var.trace_add("write", refresh_filter)
        dyn_hfreq_var.trace_add("write", refresh_filter)
        dyn_order_var.trace_add("write", refresh_filter)

        lf_filt = tk.LabelFrame(side_panel, text="Filter Parameters", padx=10, pady=10)
        lf_filt.pack(fill=tk.X, pady=(0, 10))

        tk.Label(lf_filt, text="HP (Hz)").grid(row=0, column=0, sticky="w")
        for i, opt in enumerate(hp_opts):
            tk.Radiobutton(lf_filt, text=opt, variable=dyn_lfreq_var, value=opt).grid(row=i+1, column=0, sticky="w")

        tk.Label(lf_filt, text="LP (Hz)").grid(row=0, column=1, sticky="w", padx=15)
        for i, opt in enumerate(lp_opts):
            tk.Radiobutton(lf_filt, text=opt, variable=dyn_hfreq_var, value=opt).grid(row=i+1, column=1, sticky="w", padx=15)

        tk.Label(lf_filt, text="Order").grid(row=0, column=2, sticky="w")
        for i, opt in enumerate(order_opts):
            tk.Radiobutton(lf_filt, text=opt, variable=dyn_order_var, value=opt).grid(row=i+1, column=2, sticky="w")

        active_ear = 0
        if len(available_ears) == 1:
            if 'Left' in available_ears: active_ear = 0
            else: active_ear = 1
            
        ear_var = tk.StringVar(value="Average" if active_ear is None else ("Left" if active_ear == 0 else "Right"))

        def refresh_view(*args):
            nonlocal active_ear, needs_full_draw
            val = ear_var.get()
            if len(available_ears) == 1:
                required = available_ears[0]
                if val != required: ear_var.set(required)
                return
            if val == 'Left': active_ear = 0
            elif val == 'Right': active_ear = 1
            else: active_ear = None
            needs_full_draw = True

        ear_var.trace_add("write", refresh_view)

        lf_ear = tk.LabelFrame(side_panel, text="Ear View", padx=10, pady=10)
        lf_ear.pack(fill=tk.X, pady=10)
        lf_ear.grid_columnconfigure(0, weight=1)
        lf_ear.grid_columnconfigure(1, weight=1)
        lf_ear.grid_columnconfigure(2, weight=1)
        for i, opt in enumerate(['Left', 'Average', 'Right']):
            state = tk.NORMAL
            if len(available_ears) == 1 and opt != available_ears[0]:
                state = tk.DISABLED
            tk.Label(lf_ear, text=opt, state=state).grid(row=0, column=i)
            tk.Radiobutton(lf_ear, variable=ear_var, value=opt, state=state).grid(row=1, column=i)

    def zoom_x(direction):
        nonlocal needs_full_draw
        xmin, xmax = axs[0, 0].get_xlim()
        if direction == 'reset': xmin, xmax = config.XLIMS
        else:
            rng = xmax - xmin
            shift = 0.2 * rng if direction == 'out' else -0.2 * rng
            xmin -= shift / 2; xmax += shift / 2
        for ax in axs.flat: ax.set_xlim(xmin, xmax)
        needs_full_draw = True

    lf_zoom = tk.LabelFrame(side_panel, text="X-Axis Zoom", padx=10, pady=10)
    lf_zoom.pack(fill=tk.X, pady=10)
    f_zoom_btns = tk.Frame(lf_zoom)
    f_zoom_btns.pack()
    tk.Button(f_zoom_btns, text="-", width=4, command=lambda: zoom_x('out')).pack(side=tk.LEFT, padx=2)
    tk.Button(f_zoom_btns, text="Reset", width=4, command=lambda: zoom_x('reset')).pack(side=tk.LEFT, padx=2)
    tk.Button(f_zoom_btns, text="+", width=4, command=lambda: zoom_x('in')).pack(side=tk.LEFT, padx=2)

    def reset_peaks():
        nonlocal needs_save, needs_full_draw
        if active_ear is None: return
        for i in range(n_levels):
            for j in range(n_freq):
                user_modified_peaks[(active_ear, i, j)] = False
                user_removed_peaks[(active_ear, i, j)] = False
        needs_save = True
        needs_full_draw = True
        
    def clear_peaks():
        nonlocal needs_save, needs_full_draw
        if active_ear is None: return
        for i in range(n_levels):
            for j in range(n_freq):
                user_modified_peaks[(active_ear, i, j)] = True
                user_removed_peaks[(active_ear, i, j)] = True
                peaks_latency[active_ear, i, j] = np.nan
                peaks_amplitude[active_ear, i, j] = np.nan
        needs_save = True
        needs_full_draw = True
        
    lf_exp = tk.LabelFrame(side_panel, text="Experiment Controls", padx=10, pady=10)
    lf_exp.pack(side=tk.BOTTOM, fill=tk.X, pady=10)
    btn_toggle = tk.Button(lf_exp, text=f"Start / Resume\n(Hotkey: {config.TOGGLE_EXP_KEY})", command=toggle_exp, bg='lightgreen')
    btn_toggle.pack(fill=tk.X, pady=(0, 5))
    tk.Button(lf_exp, text="Stop Experiment", command=stop_exp, bg='salmon').pack(fill=tk.X)

    lf_peaks = tk.LabelFrame(side_panel, text="Peak Management & Output", padx=10, pady=10)
    lf_peaks.pack(fill=tk.X, pady=10)
    f_peak_btns = tk.Frame(lf_peaks)
    f_peak_btns.pack()
    tk.Button(f_peak_btns, text="Reset Peaks", command=reset_peaks, bg='lightyellow').pack(side=tk.LEFT, padx=2)
    tk.Button(f_peak_btns, text="Clear Peaks", command=clear_peaks, bg='lightcoral').pack(side=tk.LEFT, padx=2)
    tk.Button(f_peak_btns, text="Export CSV", command=export_csv, bg='thistle').pack(side=tk.LEFT, padx=2)
    
    tk.Button(lf_peaks, text="Screenshot", command=export_png, bg='peachpuff').pack(fill=tk.X, pady=(10, 0))

    plt.show()
    fig.canvas.draw()
    needs_full_draw = False

    print("Starting real-time processing loop...", flush=True)
    
    use_bayesian_weighting = getattr(config, 'BAYESIAN_WEIGHTING', True)

    # Lowpass filter specifically for smoothing before peak picking (zero-phase 500Hz)
    peak_cutoff = min(500, fs/2.0 - 1)
    sos_peak = sig.butter(2, peak_cutoff, btype='low', fs=fs, output='sos')

    def save_data():
        try:
            base = os.path.basename(tab_file).split('.')[0]
            data_dir = os.path.dirname(tab_file)
            hdf5_path = os.path.join(data_dir, f"{base}_results.hdf5")
            
            run_data_strs = {str(k): v for k, v in run_data.items()}
            
            has_pips = any('pips' in str(r.get('fn_stim', '')).lower() for r in run_data.values())
            has_clicks = any('clicks' in str(r.get('fn_stim', '')).lower() for r in run_data.values())
            if has_pips and has_clicks: stim_type = 'Mixed'
            elif has_pips: stim_type = 'Pips'
            else: stim_type = 'Clicks'
            
            rates = [r.get('rate', [0])[0] if isinstance(r.get('rate'), (list, np.ndarray)) else r.get('rate', 0) for r in run_data.values()]
            
            valid_ear_indices = [0 if ear == 'Left' else 1 for ear in available_ears]
            axes_ear_str = f'Ear ({", ".join(available_ears)})'

            waveforms_raw_save = r_ipsi_ordered[valid_ear_indices].copy()
            waveforms_filtered_save = r_ipsi_filt[valid_ear_indices].copy()
            
            for i in range(n_levels):
                run_idx = levels_indices[i]
                for j, freq in enumerate(f_band):
                    if freq not in run_f_bands[run_idx]:
                        waveforms_raw_save[:, i, j, :] = np.nan
                        waveforms_filtered_save[:, i, j, :] = np.nan

            write_hdf5(hdf5_path, {
                'waveforms_raw': waveforms_raw_save,
                'waveforms_filtered': waveforms_filtered_save,
                'waveforms_axes': f'{axes_ear_str}, Run (ordered by level), Frequency, Time',
                'peaks_latency_ms': peaks_latency[valid_ear_indices],
                'peaks_amplitude_uV': peaks_amplitude[valid_ear_indices],
                'peaks_axes': f'{axes_ear_str}, Run (ordered by level), Frequency',
                'runs_ordered_by_level': levels_indices,
                'fs': fs,
                't_ms': t_plot,
                'levels_dB': levels_sorted,
                'frequencies_Hz': f_band,
                'stim_type': stim_type,
                'stim_rates': rates,
                'run_data': run_data_strs
            }, overwrite=True)
        except Exception:
            pass

    while experiment_active:
        chunk, timestamps = inlet.pull_chunk(timeout=0.01)
        if chunk:
            chunk = np.array(chunk).T
            n_new = chunk.shape[1]
            
            chunk_eeg = np.zeros((2, n_new))
            
            if is_left_valid:
                if idx_l_noninv is not None: chunk_eeg[0, :] += chunk[idx_l_noninv, :]
                if idx_l_inv is not None: chunk_eeg[0, :] -= chunk[idx_l_inv, :]

            if is_right_valid:
                if idx_r_noninv is not None: chunk_eeg[1, :] += chunk[idx_r_noninv, :]
                if idx_r_inv is not None: chunk_eeg[1, :] -= chunk[idx_r_inv, :]

            if not is_left_valid and is_right_valid:
                chunk_eeg[0, :] = chunk_eeg[1, :]
            elif not is_right_valid and is_left_valid:
                chunk_eeg[1, :] = chunk_eeg[0, :]

            chunk_status = chunk[status_ch_index, :]
            
            # Apply stateful zero-latency filtering
            chunk_eeg, zi_sos_continuous = sig.sosfilt(sos_continuous, chunk_eeg, zi=zi_sos_continuous, axis=-1)
                
            # Shift ring buffer
            if n_new >= buffer_samples:
                ring_buffer = chunk_eeg[:, -buffer_samples:]
            else:
                ring_buffer = np.roll(ring_buffer, -n_new, axis=1)
                ring_buffer[:, -n_new:] = chunk_eeg
                
            # Detect triggers (rising edges)
            chunk_status_int = np.bitwise_and(chunk_status.astype(int), 255)
            status_concat = np.concatenate(([prev_status], chunk_status_int))
            edges = np.where(np.diff(status_concat) > 0)[0]
            for idx in edges:
                detected_triggers.append((global_samples + idx, status_concat[idx + 1]))
            prev_status = status_concat[-1]
            
            # Parse TTL ID Sequences into Run/Tok IDs
            idx = 0
            while idx < len(detected_triggers):
                t_idx, val = detected_triggers[idx]
                if val == 1:
                    found_end = False
                    incomplete = False
                    for j in range(idx + 1, len(detected_triggers)):
                        if detected_triggers[j][1] == 2:
                            found_end = True
                            end_idx = j
                            break
                        elif detected_triggers[j][1] == 1:
                            # Found another 1 before a 2. The first 1 is orphaned.
                            found_end = True
                            end_idx = j - 1
                            break
                    else:
                        incomplete = True
                    
                    if incomplete:
                        break
                        
                    seq = detected_triggers[idx:end_idx+1]
                    vals = [x[1] for x in seq]
                    
                    if vals[-1] == 2 and len(seq) == n_bits_tot and all(v in (4, 8) for v in vals[1:-1]):
                        event_time_raw = seq[0][0] + int(np.round(fs_raw * config.TUBE_DELAY))
                        bits = [0 if v == 4 else 1 for v in vals[1:-1]]
                        
                        pending_epochs.append({
                            'start_idx': event_time_raw + int(config.TMIN * fs_raw),
                            'end_idx': event_time_raw + int(config.TMAX * fs_raw),
                            'run_id': int(''.join(map(str, bits[:n_run_bits])), 2),
                            'tok_id': int(''.join(map(str, bits[n_run_bits:])), 2)
                        })
                        presented_epochs += 1
                    else:
                        print(f"Warning: Trigger error around sample {seq[0][0]}. Expected 1...[4/8]...2 ({n_bits_tot} total), got {vals}. Dropping.")
                        dropped_epochs += 1
                    
                    idx = end_idx + 1
                else:
                    idx += 1
                    
            detected_triggers = detected_triggers[idx:]
            
            if len(detected_triggers) > 100: detected_triggers = detected_triggers[-50:]
                
            # Extract pending epochs that are fully inside the ring buffer
            remaining_pending = []
            for ep in pending_epochs:
                if global_samples + n_new >= ep['end_idx']:
                    start_in_buf = ep['start_idx'] - (global_samples + n_new - buffer_samples)
                    end_in_buf = ep['end_idx'] - (global_samples + n_new - buffer_samples)
                    
                    if start_in_buf >= 0 and end_in_buf <= buffer_samples:
                        ep_data = ring_buffer[:, start_in_buf:end_in_buf]
                        
                        if q > 1:
                            ep_data = sig.resample_poly(ep_data, 1, q, axis=-1)
                            if ep_data.shape[-1] > n_samples_epoch:
                                ep_data = ep_data[:, :n_samples_epoch]
                            elif ep_data.shape[-1] < n_samples_epoch:
                                ep_data = np.pad(ep_data, ((0,0), (0, n_samples_epoch - ep_data.shape[-1])))
                                
                        run, tok = ep['run_id'], ep['tok_id']
                        runs_updated.add(run)
                        
                        # Bayesian Weighting per epoch
                        if use_bayesian_weighting:
                            var = ep_data.var(axis=-1, keepdims=True)
                            weight = 1.0 / (var + 1e-12)
                        else:
                            weight = 1.0
                        
                        # Convert to frequency domain using Real FFT (50% less overhead)
                        Y = sp_fft.rfft(ep_data, axis=-1)
                        
                        # Broadcast Y and X_conj to compute all ears/frequencies in one highly optimized C-call
                        Y_reshaped = Y[:, np.newaxis, np.newaxis, :]  # Shape: (n_chs, 1, 1, n_bins)
                        X_conj_block = X_conj_all[run][tok][np.newaxis, :, :, :]  # Shape: (1, n_ears, n_freq, n_bins)
                        
                        # Batched Inverse Real FFT
                        resp_contrib_block = sp_fft.irfft(X_conj_block * Y_reshaped, n=n_samples_epoch, axis=-1)
                        
                        for ear in range(n_ears):
                            for fi in range(n_freq):
                                p_sum = pulse_sum_all[run][tok, ear, fi]
                                if p_sum > 0:
                                    num[run, :, ear, fi, :] += weight * (resp_contrib_block[:, ear, fi, :] / p_sum)
                                    den[run, :, ear, fi, :] += weight
                                
                        new_epochs_count += 1
                        processed_epochs += 1
                else:
                    remaining_pending.append(ep)
            pending_epochs = remaining_pending
            global_samples += n_new
            
        # Render plot dynamically (~1 FPS) or instantly if parameters change
        if (time.time() - last_plot_time > 1.0 and new_epochs_count > 0) or needs_full_draw or needs_save:
            if processed_epochs > 0:
                resp = num / (den + 1e-12)
                resp_uV = resp  # ActiView LSL streams natively output in microvolts
                
                # Select ipsilateral pairs per the offline script logic
                r_sel = np.zeros((n_runs, 2, n_freq, n_samples_epoch))
                r_sel[:, 0] = resp_uV[:, 0, 0] # ABR-L (ch 0), Ear 0
                r_sel[:, 1] = resp_uV[:, 1, 1] # ABR-R (ch 1), Ear 1
                
                r_sel_ordered = r_sel[levels_indices]
                r_ipsi_ordered[:] = np.transpose(r_sel_ordered, (1, 0, 2, 3))
                
                # Apply Dynamic Filter Causally!
                r_ipsi_filt[:] = sig.sosfilt(sos_dynamic, r_ipsi_ordered, axis=-1)
                
                # Filter the entire waveform vectorially to avoid edge artifacts during peak slicing
                r_ipsi_smooth[:] = sig.sosfiltfilt(sos_peak, r_ipsi_filt, axis=-1)
                
                if exp_start_time is None and processed_epochs > 0:
                    exp_start_time = time.time()
                
                if exp_start_time is not None:
                    elapsed_sec = time.time() - exp_start_time
                    el_m, el_s = divmod(int(elapsed_sec), 60)
                    el_str = f"{el_m:02d}:{el_s:02d}"
                else:
                    el_str = "--:--"

                progress = (start_trial + processed_epochs) / total_epochs if total_epochs > 0 else 0
                prog_rect.set_width(min(progress, 1.0))
                rem_time = total_expected_time * (1.0 - progress)
                if not experiment_finished:
                    if 0 < progress < 1.0:
                        mins, secs = divmod(int(rem_time), 60)
                        prog_text.set_text(f'{progress*100:.1f}% (Epoch {start_trial + presented_epochs}, Time Elapsed {el_str}, Est. Time Remaining {mins:02d}:{secs:02d})')
                    else:
                        if progress >= 1.0:
                            prog_text.set_text(f'{progress*100:.1f}% (Done)')
                        else:
                            prog_text.set_text(f'0% (Epoch {start_trial + presented_epochs}, Time Elapsed {el_str}, Est. Time Remaining --:--)')
                            
                if dropped_epochs > 0:
                    dropped_text.set_text(f'Epochs dropped: {dropped_epochs}')
                else:
                    dropped_text.set_text('')

            t_mask = (t_plot >= config.PEAK_MIN_MS) & (t_plot <= config.PEAK_MAX_MS)
            t_mask_noise = (t_plot >= config.NOISE_WIN_MIN_MS) & (t_plot <= config.NOISE_WIN_MAX_MS)
            t_mask_resp = (t_plot >= config.RESP_WIN_MIN_MS) & (t_plot <= config.RESP_WIN_MAX_MS)

            n_resp_samples = np.sum(t_mask_resp)
            noise_idx = np.where(t_mask_noise)[0]
            if n_resp_samples > 0:
                n_noise_chunks = len(noise_idx) // n_resp_samples
                if n_noise_chunks > 0:
                    noise_idx_chunked = noise_idx[-n_noise_chunks * n_resp_samples:].reshape(n_noise_chunks, n_resp_samples)
                else:
                    noise_idx_chunked = None
            else:
                noise_idx_chunked = None

            if processed_epochs > 0:
                for ear in range(n_chs):
                    for i, lvl in enumerate(levels_sorted):
                        run_idx = levels_indices[i]
                        for j, freq in enumerate(f_band):
                            if freq not in run_f_bands[run_idx]: continue
                            if not user_modified_peaks[(ear, i, j)]:
                                y_smooth_segment = r_ipsi_smooth[ear, i, j][t_mask]
                                y_valid = r_ipsi_filt[ear, i, j][t_mask]
                                t_valid = t_plot[t_mask]
                                if len(y_smooth_segment) > 10:
                                    try:
                                        p_idx = np.argmax(y_smooth_segment)
                                        peaks_latency[ear, i, j] = t_valid[p_idx]
                                        peaks_amplitude[ear, i, j] = y_valid[p_idx]
                                    except Exception:
                                        pass
                            elif not user_removed_peaks[(ear, i, j)]:
                                x_curr = peaks_latency[ear, i, j]
                                if not np.isnan(x_curr):
                                    idx = np.argmin(np.abs(t_plot - x_curr))
                                    peaks_amplitude[ear, i, j] = r_ipsi_filt[ear, i, j][idx]
                            
                            if user_removed_peaks[(ear, i, j)]:
                                peaks_latency[ear, i, j] = np.nan
                                peaks_amplitude[ear, i, j] = np.nan

            if active_ear is not None:
                r_plot = r_ipsi_filt[active_ear]
            else:
                r_plot = r_ipsi_filt.mean(axis=0)

            for i, lvl in enumerate(levels_sorted):
                run_idx = levels_indices[i]
                for j, freq in enumerate(f_band):
                    if freq not in run_f_bands[run_idx]: continue
                    if processed_epochs > 0:
                        y_data = r_plot[i, j]
                        
                        if run_idx in runs_updated or needs_full_draw or needs_save:
                            lines[(i, j)].set_ydata(y_data)
                            
                            # Estimate and update SNR
                            if noise_idx_chunked is not None:
                                var_noise = np.mean(np.var(y_data[noise_idx_chunked], axis=1))
                            else:
                                var_noise = np.var(y_data[t_mask_noise])
                            var_resp = np.var(y_data[t_mask_resp])
                            snr_lin = (var_resp - var_noise) / max(var_noise, 1e-12)
                            snr = 10 * np.log10(max(snr_lin, 1e-12))
                            snr_texts[(i, j)].set_text(f'SNR: {snr:.2f}')
                            
                            if active_ear is not None and not user_removed_peaks[(active_ear, i, j)]:
                                p_t = peaks_latency[active_ear, i, j]
                                p_y = peaks_amplitude[active_ear, i, j]
                                if not np.isnan(p_t):
                                    peak_markers[(i, j)].set_data([p_t], [p_y])
                                    peak_texts[(i, j)].xy = (p_t, p_y)
                                    peak_texts[(i, j)].set_text(f'{p_t:.1f}ms\n{p_y:.2f}\u03BCV')
                                    peak_markers[(i, j)].set_visible(True)
                                    peak_texts[(i, j)].set_visible(True)
                                else:
                                    peak_markers[(i, j)].set_visible(False)
                                    peak_texts[(i, j)].set_visible(False)
                            else:
                                peak_markers[(i, j)].set_visible(False)
                                peak_texts[(i, j)].set_visible(False)

            if new_epochs_count > 0 or needs_full_draw:
                current_xmin, current_xmax = axs[0, 0].get_xlim()
                t_mask_plot = (t_plot >= current_xmin) & (t_plot <= current_xmax)
                if not np.any(t_mask_plot): t_mask_plot = np.ones_like(t_plot, dtype=bool)
                
                if active_ear is not None:
                    global_ymin = np.min(r_ipsi_filt[active_ear, :, :, t_mask_plot])
                    global_ymax = np.max(r_ipsi_filt[active_ear, :, :, t_mask_plot])
                else:
                    avg_plot = r_ipsi_filt.mean(axis=0)
                    global_ymin = np.min(avg_plot[:, :, t_mask_plot])
                    global_ymax = np.max(avg_plot[:, :, t_mask_plot])
                
                range_y = global_ymax - global_ymin
                
                current_ymin, current_ymax = axs[0, 0].get_ylim()
                if range_y > 0:
                    # Expand limits if data goes outside, or shrink if data is much smaller (<50%)
                    if global_ymin < current_ymin or global_ymax > current_ymax or range_y < 0.5 * (current_ymax - current_ymin):
                        new_ymin = global_ymin - 0.10 * range_y
                        new_ymax = global_ymax + 0.15 * range_y
                        for ax in axs.flat:
                            ax.set_ylim(new_ymin, new_ymax)
                        needs_full_draw = True
                
                if needs_full_draw or bg is None:
                    fig.canvas.draw()
                    needs_full_draw = False
                
                trigger_blit()
            
            if (time.time() - last_save_time > 10.0) or needs_save:
                save_data()
                last_save_time = time.time()
                needs_save = False

            if new_epochs_count > 0:
                last_plot_time = time.time()
                new_epochs_count = 0
                runs_updated.clear()
                
        if not experiment_finished and (start_trial + presented_epochs) >= total_epochs and len(pending_epochs) == 0:
            experiment_finished = True
            prog_text.set_text('Experiment Finished')
            prog_text.set_color('red')
            trigger_blit()
                
        fig.canvas.flush_events()
        if len(timestamps) == 0:
            time.sleep(0.001)
            
    # Final save and cleanup when experiment breaks the loop
    export_png(None)
    save_data()
    plt.close(fig)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        import sys
        traceback.print_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            messagebox.showerror("Analyzer Crashed", f"The Real-Time Analyzer encountered a fatal error:\n\n{e}\n\nCheck the console log for the full traceback.")
            root.destroy()
        except Exception: pass
        sys.exit(1)