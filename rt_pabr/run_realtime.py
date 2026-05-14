import glob
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog

import pylsl

from rt_pabr import config
from rt_pabr import pip_trains_rme


def launch():
    sub = sub_entry.get()
    run_f = file_var.get()
    start_t_str = start_var.get()
    ws = workspace_var.get()
    
    if not sub or not run_f:
        return
        
    if not start_t_str.isdigit():
        start_t_str = "0"
        start_var.set("0")
    start_t = int(start_t_str)
    
    if run_f == 'CALIBRATION':
        n_trials_tot = 600
    else:
        try:
            run_file_path = os.path.join(ws, run_f).replace('\\', '/')
            with open(run_file_path, 'r', encoding='utf-8-sig') as f:
                runs = json.load(f)
            n_trials_tot = sum([int(r.get('n_trials', 0)) for r in runs])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse paradigm file:\n{e}")
            return
            
    if start_t >= n_trials_tot or start_t < 0:
        messagebox.showerror("Invalid Start Epoch", f"Start epoch ({start_t}) must be between 0 and {max(0, n_trials_tot - 1)}.")
        return

    if run_f != 'CALIBRATION':
        btn_launch.config(text="Checking LSL...", state='disabled')
        root.update()
        
        # Check that LSL is broadcasting before allowing the experiment to launch
        streams = pylsl.resolve_byprop('type', 'EEG', timeout=3.0)
        if not streams:
            messagebox.showerror("LSL Stream Not Found", "No EEG stream was found on the network.\n\n"
                                 "Please ensure that ActiView is open and LSL streaming is turned ON.")
            btn_launch.config(text="Launch Experiment", state='normal')
            return
        
    root.quit()
    
def open_creator():
    creator = tk.Toplevel(root)
    creator.title("Create Paradigm")
    creator.geometry("700x450")
    
    runs_frame = tk.Frame(creator)
    runs_frame.pack(fill='both', expand=True, pady=10)
    
    btn_add_frame = tk.Frame(creator)
    btn_add_frame.pack(fill='x', pady=5)
    
    ws = workspace_var.get()
    runs = []
    # Search stimuli directory and root for hdf5s, standardizing slashes
    stim_files = [f.replace('\\', '/') for f in glob.glob(os.path.join(ws, 'stimuli', '*.hdf5')) + glob.glob(os.path.join(ws, '*.hdf5'))]
    if not stim_files:
        stim_files = ['No hdf5 files found']
        
    corr_frame = tk.LabelFrame(creator, text="Correction Factors & Units")
    corr_frame.pack(fill='x', padx=10, pady=5)

    labels = ['Clicks', '500 Hz', '1 kHz', '2 kHz', '4 kHz', '8 kHz', 'Units']
    defaults = ['0', '0', '0', '0', '0', '0', 'peSPL']
    corr_vars = []

    for i, (lbl, default) in enumerate(zip(labels, defaults)):
        frame = tk.Frame(corr_frame)
        frame.grid(row=0, column=i, padx=8, pady=5)
        tk.Label(frame, text=lbl).pack()
        var = tk.StringVar(value=default)
        tk.Entry(frame, textvariable=var, width=8, justify='center').pack()
        corr_vars.append(var)
        
    save_frame = tk.Frame(creator)
    save_frame.pack(fill='x', padx=10, pady=15)
    tk.Label(save_frame, text="Filename:").pack(side='left')
    fn_var = tk.StringVar(value=os.path.join(ws, "stimuli", "new_paradigm.json").replace('\\', '/'))
    fn_entry = tk.Entry(save_frame, textvariable=fn_var, width=35)
    fn_entry.pack(side='left', padx=5)
    
    manual_override = [False]
    fn_entry.bind('<Key>', lambda e: manual_override.__setitem__(0, True))
    
    def update_filename(*args):
        if manual_override[0] or not runs:
            return
            
        types_groups = {'clicks': [], 'pips': []}
        for stim_var, db_var, _ in runs:
            stim = stim_var.get().lower()
            t = 'pips' if 'pips' in stim else 'clicks'
            try: db = int(db_var.get())
            except ValueError: db = 80
            
            rate_match = re.search(r'rate(\d+)', stim)
            r = rate_match.group(1) if rate_match else 'UNKNOWN'
            types_groups[t].append((r, db))
            
        name_parts = []
        for t in ['clicks', 'pips']:
            if types_groups[t]:
                g = types_groups[t]
                r = g[0][0]
                levels = [x[1] for x in g]
                if len(levels) == 1:
                    name_parts.append(f"{t}_rate{r}_{levels[0]}dB")
                else:
                    name_parts.append(f"{t}_sweep_rate{r}_{min(levels)}-{max(levels)}dB")
                    
        new_name = os.path.join(ws, "stimuli", "+".join(name_parts) + ".json").replace('\\', '/')
        fn_var.set(new_name)
        
    def add_run():
        row = tk.Frame(runs_frame)
        row.pack(fill='x', pady=5, padx=10)
        
        tk.Label(row, text="Stim:").pack(side='left')
        stim_var = tk.StringVar(value=stim_files[0])
        stim_var.trace_add('write', update_filename)
        tk.OptionMenu(row, stim_var, *stim_files).pack(side='left', padx=(0, 10))
        
        tk.Label(row, text="dB:").pack(side='left')
        db_var = tk.StringVar(value="80")
        db_var.trace_add('write', update_filename)
        tk.Entry(row, textvariable=db_var, width=5, justify='center').pack(side='left', padx=(0, 10))
        
        tk.Label(row, text="Trials:").pack(side='left')
        tr_var = tk.StringVar(value="900")
        tk.Entry(row, textvariable=tr_var, width=5, justify='center').pack(side='left')
        
        runs.append((stim_var, db_var, tr_var))
        update_filename()
        
    add_run()
    tk.Button(btn_add_frame, text="Add Another Run", command=add_run).pack(pady=5)
    
    def save_paradigm():
        try:
            cf = [float(v.get()) for v in corr_vars[:-1]]
        except ValueError:
            messagebox.showerror("Invalid Input", "Correction factors must be numbers.")
            return

        units_val = corr_vars[-1].get()

        out = []
        for stim, db, tr in runs:
            out.append({
                "fn_stim": stim.get(), 
                "stim_db": int(db.get()), 
                "n_trials": int(tr.get()), 
                "band_picks": [], 
                "ear_picks": [],
                "correction_factors": cf,
                "units": units_val
            })
        fn = fn_var.get()
        if not fn.endswith('.json'): fn += '.json'
        if 'stimuli' not in fn and '/' not in fn and '\\' not in fn:
            fn = os.path.join(ws, 'stimuli', fn).replace('\\', '/')
        os.makedirs(os.path.join(ws, 'stimuli'), exist_ok=True)
        with open(fn, 'w') as f:
            json.dump(out, f, indent=4)
        refresh_dropdowns()
        try:
            rel_fn = os.path.relpath(fn, ws).replace('\\', '/')
        except ValueError:
            rel_fn = fn.replace('\\', '/')
        file_var.set(rel_fn)
        creator.destroy()
        
    tk.Button(save_frame, text="Save & Select", command=save_paradigm, bg="lightblue").pack(side='right')

def open_stim_creator():
    stim_win = tk.Toplevel(root)
    stim_win.title("Generate Stimuli")
    stim_win.geometry("300x230")
    
    tk.Label(stim_win, text="Rate (stim/s):").pack(pady=(10, 0))
    rate_var = tk.StringVar(value="40")
    tk.Entry(stim_win, textvariable=rate_var, justify='center').pack()
    
    tk.Label(stim_win, text="Duration (min):").pack(pady=(10, 0))
    dur_var = tk.StringVar(value="2")
    tk.Entry(stim_win, textvariable=dur_var, justify='center').pack()
    
    tk.Label(stim_win, text="Stimulus Type:").pack(pady=(10, 0))
    type_var = tk.StringVar(value="Pips")
    tk.OptionMenu(stim_win, type_var, "Pips", "Clicks").pack()
    
    def do_gen():
        try:
            r = int(rate_var.get())
            d = float(dur_var.get())
            is_pips = (type_var.get() == "Pips")
            
            btn.config(text="Generating...", state='disabled')
            stim_win.update()
            
            pip_trains_rme.generate_stimuli(rate=r, do_pips=is_pips, n_minutes=d, save_dir=os.path.join(workspace_var.get(), 'stimuli'))
            
            btn.config(text="Done! Saved to stimuli/", bg="green")
            stim_win.after(2000, stim_win.destroy)
        except Exception as e:
            btn.config(text="Error!", bg="red", state='normal')
            print("Error generating stimuli:", e)
            
    btn = tk.Button(stim_win, text="Generate & Save", command=do_gen, bg="lightblue")
    btn.pack(pady=15)

def open_settings():
    settings_win = tk.Toplevel(root)
    settings_win.title("Configuration Settings")
    settings_win.geometry("480x650")
    
    btn_frame = tk.Frame(settings_win)
    btn_frame.pack(side="bottom", fill="x", pady=10)
    
    canvas = tk.Canvas(settings_win, highlightthickness=0)
    scrollbar = ttk.Scrollbar(settings_win, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)
    
    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    
    canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    def configure_canvas(event):
        canvas.itemconfig(canvas_window, width=event.width)
    canvas.bind("<Configure>", configure_canvas)
    
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _on_mousewheel(event):
        if hasattr(event, 'delta') and event.delta:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif hasattr(event, 'num'):
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

    settings_win.bind_all("<MouseWheel>", _on_mousewheel)
    settings_win.bind_all("<Button-4>", _on_mousewheel)
    settings_win.bind_all("<Button-5>", _on_mousewheel)

    current_cfg = config.load_config()

    # Configuration Presets Section
    presets_dir = os.path.join(os.path.dirname(__file__), 'configs')
    os.makedirs(presets_dir, exist_ok=True)
    
    preset_frame = tk.LabelFrame(scrollable_frame, text="Configuration Presets", padx=10, pady=5)
    preset_frame.pack(side="top", fill="x", padx=10, pady=5)
    
    def get_presets():
        return sorted([f[:-5] for f in os.listdir(presets_dir) if f.endswith('.json')])
        
    preset_var = tk.StringVar()
    preset_dd = ttk.Combobox(preset_frame, textvariable=preset_var, values=get_presets(), state='readonly', width=30)
    preset_dd.pack(side='left', padx=5)
    
    def load_preset():
        name = preset_var.get()
        if not name: return
        path = os.path.join(presets_dir, f"{name}.json")
        if os.path.exists(path):
            with open(path, 'r') as f:
                try:
                    preset_cfg = json.load(f)
                    for k, v in preset_cfg.items():
                        if k in lsl_vars:
                            lsl_vars[k].set(v)
                        elif k in entries:
                            var, t = entries[k]
                            if t is list or t is dict:
                                var.set(json.dumps(v))
                            elif t is bool:
                                var.set(v)
                            else:
                                var.set(str(v))
                    messagebox.showinfo("Preset Loaded", f"Successfully loaded preset '{name}'.", parent=settings_win)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to load preset:\n{e}", parent=settings_win)

    def save_preset_as():
        name = simpledialog.askstring("Save Preset", "Enter preset name:", parent=settings_win)
        if not name: return
        # Sanitize filename
        name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
        if not name: return
        
        new_cfg = {}
        for k, v in lsl_vars.items():
            new_cfg[k] = v.get()
            
        for k, (var, t) in entries.items():
            val = var.get()
            try:
                if t is list or t is dict:
                    new_cfg[k] = json.loads(val)
                elif t is bool:
                    new_cfg[k] = bool(val)
                else:
                    new_cfg[k] = t(val)
            except Exception as e:
                messagebox.showerror("Validation Error", f"Invalid value for {k}: {e}", parent=settings_win)
                return
                
        path = os.path.join(presets_dir, f"{name}.json")
        with open(path, 'w') as f:
            json.dump(new_cfg, f, indent=4)
            
        preset_dd['values'] = get_presets()
        preset_var.set(name)
        messagebox.showinfo("Success", f"Preset '{name}' saved.", parent=settings_win)

    tk.Button(preset_frame, text="Load Preset", command=load_preset).pack(side='left', padx=5)
    tk.Button(preset_frame, text="Save As...", command=save_preset_as).pack(side='left', padx=5)

    # LSL Channel Selection Section
    lsl_frame = tk.LabelFrame(scrollable_frame, text="LSL Channel Selection (Non-Inverting \u2212 Inverting)", padx=10, pady=10)
    lsl_frame.pack(side="top", fill="x", padx=10, pady=5)
    
    lsl_vars = {
        "CH_LEFT_NONINV": tk.StringVar(value=current_cfg.get("CH_LEFT_NONINV", "None")),
        "CH_LEFT_INV": tk.StringVar(value=current_cfg.get("CH_LEFT_INV", "ABR-L")),
        "CH_RIGHT_NONINV": tk.StringVar(value=current_cfg.get("CH_RIGHT_NONINV", "None")),
        "CH_RIGHT_INV": tk.StringVar(value=current_cfg.get("CH_RIGHT_INV", "ABR-R")),
    }
    
    btn_scan = tk.Button(lsl_frame, text="Scan LSL Stream for Channels", command=lambda: update_lsl_dropdowns())
    btn_scan.grid(row=0, column=0, columnspan=4, pady=(0, 10))
    
    lsl_dds = {}
    tk.Label(lsl_frame, text="Left:").grid(row=1, column=0, sticky='e')
    lsl_dds["CH_LEFT_NONINV"] = ttk.Combobox(lsl_frame, textvariable=lsl_vars["CH_LEFT_NONINV"], width=12)
    lsl_dds["CH_LEFT_NONINV"].grid(row=1, column=1, padx=5)
    tk.Label(lsl_frame, text=" \u2212 ").grid(row=1, column=2)
    lsl_dds["CH_LEFT_INV"] = ttk.Combobox(lsl_frame, textvariable=lsl_vars["CH_LEFT_INV"], width=12)
    lsl_dds["CH_LEFT_INV"].grid(row=1, column=3, padx=5)
    
    tk.Label(lsl_frame, text="Right:").grid(row=2, column=0, sticky='e', pady=5)
    lsl_dds["CH_RIGHT_NONINV"] = ttk.Combobox(lsl_frame, textvariable=lsl_vars["CH_RIGHT_NONINV"], width=12)
    lsl_dds["CH_RIGHT_NONINV"].grid(row=2, column=1, padx=5)
    tk.Label(lsl_frame, text=" \u2212 ").grid(row=2, column=2)
    lsl_dds["CH_RIGHT_INV"] = ttk.Combobox(lsl_frame, textvariable=lsl_vars["CH_RIGHT_INV"], width=12)
    lsl_dds["CH_RIGHT_INV"].grid(row=2, column=3, padx=5)
    
    for key, dd in lsl_dds.items():
        curr_val = lsl_vars[key].get()
        vals = ["None"]
        if curr_val not in vals:
            vals.append(curr_val)
        dd['values'] = vals
        
    entries = {}
    
    setting_groups = [
        ("Experiment Settings", {
            "WORKSPACE_DIR": "Working directory",
            "FORCE_SOUNDDEVICE": "Force sounddevice",
            "BAYESIAN_WEIGHTING": "Use Bayesian Weighting",
            "TOGGLE_EXP_KEY": "Pause hotkey",
            "XLIMS": "Default x-limits (ms)",
            "TUBE_DELAY": "Tube delay",
            "TMIN": "Epoch start rel. to onset (s)",
            "TMAX": "Epoch end rel. to onset (s)",
            "BUFFER_SEC": "Buffer length (s)",
            "DECIMATION_FACTOR": "Downsample factor",
            "TRANSDUCERS": "Transducers (name:calibration)"
        }),
        ("Filter options", {
            "L_FREQ": "Highpass frequency",
            "H_FREQ": "Lowpass frequency",
            "FILT_ORDER": "Filter order",
            "NOTCH_FREQS": "Notch frequencies",
            "NOTCH_WIDTH": "Notch width"
        }),
        ("Interactive filter options", {
            "DYN_HP_OPTIONS": "Highpass frequencies",
            "DYN_LP_OPTIONS": "Lowpass frequencies",
            "DYN_ORDER_OPTIONS": "Filter orders"
        }),
        ("Peak Picking and SNR Windows", {
            "PEAK_MIN_MS": "Peak window start (ms)",
            "PEAK_MAX_MS": "Peak window end (ms)",
            "NOISE_WIN_MIN_MS": "Noise window start (ms)",
            "NOISE_WIN_MAX_MS": "Noise window end (ms)",
            "RESP_WIN_MIN_MS": "Response window start (ms)",
            "RESP_WIN_MAX_MS": "Response window end (ms)"
        }),
        ("Network settings", {
            "UDP_IP": "UDP IP",
            "UDP_PORT": "UDP PORT"
        })
    ]

    for group_name, group_keys in setting_groups:
        group_frame = tk.LabelFrame(scrollable_frame, text=group_name, padx=10, pady=10)
        group_frame.pack(fill='x', padx=10, pady=5)
        
        row = 0
        for key, display_name in group_keys.items():
            if key not in current_cfg:
                continue
                
            val = current_cfg[key]
            ttk.Label(group_frame, text=display_name + ":", font=('Arial', 10)).grid(row=row, column=0, sticky='e', padx=(0, 10), pady=5)
            
            if isinstance(val, (list, dict)):
                var = tk.StringVar(value=json.dumps(val))
                entry = ttk.Entry(group_frame, textvariable=var, font=('Arial', 10))
            elif isinstance(val, bool):
                var = tk.BooleanVar(value=val)
                entry = ttk.Checkbutton(group_frame, variable=var)
            else:
                var = tk.StringVar(value=str(val))
                entry = ttk.Entry(group_frame, textvariable=var, font=('Arial', 10))
                
            entry.grid(row=row, column=1, sticky='w' if isinstance(val, bool) else 'ew', pady=5)
            group_frame.grid_columnconfigure(1, weight=1)
            
            entries[key] = (var, type(val))
            row += 1
            
    # Catch any variables in the configuration file that are not defined in the groups above
    other_keys = [k for k in current_cfg.keys() if k not in lsl_vars and not any(k in g_keys for _, g_keys in setting_groups)]
    if other_keys:
        other_frame = tk.LabelFrame(scrollable_frame, text="Other Settings", padx=10, pady=10)
        other_frame.pack(fill='x', padx=10, pady=5)
        row = 0
        for key in other_keys:
            val = current_cfg[key]
            ttk.Label(other_frame, text=key + ":", font=('Arial', 10)).grid(row=row, column=0, sticky='e', padx=(0, 10), pady=5)
            
            if isinstance(val, (list, dict)):
                var = tk.StringVar(value=json.dumps(val))
                entry = ttk.Entry(other_frame, textvariable=var, font=('Arial', 10))
            elif isinstance(val, bool):
                var = tk.BooleanVar(value=val)
                entry = ttk.Checkbutton(other_frame, variable=var)
            else:
                var = tk.StringVar(value=str(val))
                entry = ttk.Entry(other_frame, textvariable=var, font=('Arial', 10))
                
            entry.grid(row=row, column=1, sticky='w' if isinstance(val, bool) else 'ew', pady=5)
            other_frame.grid_columnconfigure(1, weight=1)
            
            entries[key] = (var, type(val))
            row += 1
        
    def update_lsl_dropdowns():
        streams = pylsl.resolve_byprop('type', 'EEG', timeout=2.0)
        if not streams:
            messagebox.showwarning("LSL Stream Not Found", "No EEG stream was found on the network.", parent=settings_win)
            return
        
        inlet = pylsl.StreamInlet(streams[0])
        info = inlet.info()
        ch = info.desc().child("channels").child("channel")
        ch_names = ["None"]
        while not ch.empty():
            name = ch.child_value("name") or ch.child_value("label")
            if name: ch_names.append(name)
            ch = ch.next_sibling()
        
        for key, dd in lsl_dds.items():
            curr_val = lsl_vars[key].get()
            vals = list(ch_names)
            if curr_val not in vals:
                vals.append(curr_val)
            dd['values'] = vals
                
    def on_close():
        settings_win.unbind_all("<MouseWheel>")
        settings_win.unbind_all("<Button-4>")
        settings_win.unbind_all("<Button-5>")
        settings_win.destroy()
        
    settings_win.protocol("WM_DELETE_WINDOW", on_close)

    def save_and_close():
        new_cfg = {}
        for k, v in lsl_vars.items():
            new_cfg[k] = v.get()
            
        for k, (var, t) in entries.items():
            val = var.get()
            try:
                if t is list or t is dict:
                    new_cfg[k] = json.loads(val)
                elif t is bool:
                    new_cfg[k] = bool(val)
                else:
                    new_cfg[k] = t(val)
            except Exception as e:
                messagebox.showerror("Validation Error", f"Invalid value for {k}: {e}", parent=settings_win)
                return
        config.save_config(new_cfg)
        
        trans_options = list(new_cfg.get("TRANSDUCERS", {}).keys())
        if trans_options:
            trans_dd['values'] = trans_options
            if trans_var.get() not in trans_options:
                trans_var.set(trans_options[0])
        
        on_close()
        
    def restore_defaults():
        if messagebox.askyesno("Restore Defaults", "Are you sure you want to restore all settings to their default values?", parent=settings_win):
            for k, v in config.DEFAULTS.items():
                if k in lsl_vars:
                    lsl_vars[k].set(v)
                elif k in entries:
                    var, t = entries[k]
                    if t is list or t is dict:
                        var.set(json.dumps(v))
                    elif t is bool:
                        var.set(v)
                    else:
                        var.set(str(v))
                        
    btn_container = tk.Frame(btn_frame)
    btn_container.pack(expand=True)
    tk.Button(btn_container, text="Restore Defaults", command=restore_defaults, bg="lightcoral", font=('Arial', 10, 'bold')).pack(side="left", padx=10)
    tk.Button(btn_container, text="Save Config", command=save_and_close, bg="lightblue", font=('Arial', 10, 'bold')).pack(side="left", padx=10)

if __name__ == '__main__':
    root = tk.Tk()
    root.title("Start Real-Time pABR")
    root.geometry("420x500")
    
    top_frame = tk.Frame(root)
    top_frame.pack(side="top", fill="x", padx=10, pady=10)
    
    tk.Button(top_frame, text="Generate Stimuli", command=open_stim_creator, font=('Arial', 9)).pack(side="left")
    tk.Button(top_frame, text="Create New Paradigm", command=open_creator, font=('Arial', 9)).pack(side="left", expand=True)
    # tk.Button(top_frame, text="⚙", command=open_settings, font=('Arial', 14)).pack(side="right")
    tk.Button(top_frame, text="Settings", command=open_settings, font=('Arial', 9)).pack(side="right")
    
    # Workspace UI
    frame_ws = tk.Frame(root)
    frame_ws.pack(pady=(10, 0), fill='x', padx=20)
    tk.Label(frame_ws, text="Workspace:", font=('Arial', 10, 'bold')).pack(side='left')
    
    workspace_var = tk.StringVar(value=config.WORKSPACE_DIR)
    entry_ws = tk.Entry(frame_ws, textvariable=workspace_var, state='readonly', font=('Arial', 9))
    entry_ws.pack(side='left', fill='x', expand=True, padx=5)
    
    def browse_workspace():
        d = filedialog.askdirectory(initialdir=workspace_var.get())
        if d:
            workspace_var.set(d)
            current_cfg = config.load_config()
            current_cfg["WORKSPACE_DIR"] = d
            config.save_config(current_cfg)
            refresh_dropdowns()
            
    tk.Button(frame_ws, text="Browse", command=browse_workspace, font=('Arial', 9)).pack(side='left')
    
    tk.Label(root, text="Subject ID:", font=('Arial', 12)).pack(pady=(15, 0))
    sub_entry = tk.Entry(root, font=('Arial', 12), justify='center')
    sub_entry.pack()
    
    tk.Label(root, text="Paradigm File (*.json):", font=('Arial', 12)).pack(pady=(15, 0))
    file_var = tk.StringVar()
    dd = ttk.Combobox(root, textvariable=file_var, width=40, font=('Arial', 10), justify='center')
    dd.pack()
    
    def refresh_dropdowns():
        ws = workspace_var.get()
        os.makedirs(os.path.join(ws, 'stimuli'), exist_ok=True)
        os.makedirs(os.path.join(ws, 'data'), exist_ok=True)
        
        abs_files = glob.glob(os.path.join(ws, "stimuli", "*.json"))
        rel_files = []
        for f in abs_files:
            try:
                rel_files.append(os.path.relpath(f, ws).replace('\\', '/'))
            except ValueError:
                rel_files.append(f.replace('\\', '/'))
                
        json_files = ['CALIBRATION'] + rel_files
        dd['values'] = json_files
        if not file_var.get() or file_var.get() not in json_files:
            if json_files: file_var.set(json_files[0])
            
    refresh_dropdowns()

    tk.Label(root, text="Transducer:", font=('Arial', 12)).pack(pady=(15, 0))
    trans_var = tk.StringVar()
    trans_options = list(config.TRANSDUCERS.keys())
    trans_var.set('ER2')
    trans_dd = ttk.Combobox(root, textvariable=trans_var, values=trans_options, width=35, font=('Arial', 10), justify='center', state='readonly')
    trans_dd.pack()
    
    tk.Label(root, text="Start Epoch:", font=('Arial', 12)).pack(pady=(15, 0))
    start_var = tk.StringVar(value="0")
    tk.Entry(root, textvariable=start_var, font=('Arial', 12), justify='center').pack()
    
    btn_launch = tk.Button(root, text="Launch Experiment", command=launch, bg="green", fg="white", font=('Arial', 12, 'bold'))
    btn_launch.pack(pady=20)
    root.mainloop()
    
    sub = sub_entry.get()
    run_f = file_var.get()
    trans = trans_var.get()
    start_t = start_var.get()
    if not start_t.isdigit():
        start_t = "0"
    ws = workspace_var.get()
    root.destroy()
    
    if sub and run_f:
        if run_f == 'CALIBRATION':
            log_dir = os.path.join(ws, 'data', 'calibration').replace('\\', '/')
            log_sub = 'calibration'
        else:
            log_dir = os.path.join(ws, 'data', sub).replace('\\', '/')
            log_sub = sub
            
        os.makedirs(log_dir, exist_ok=True)
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        log_file_path = os.path.join(log_dir, f"{log_sub}_{timestamp}_log.txt").replace('\\', '/')
        log_f = open(log_file_path, 'w')
        
        def log_msg(msg):
            print(msg)
            log_f.write(msg + '\n')
            log_f.flush()

        log_msg("=== Current Configuration Settings ===")
        current_cfg = config.load_config()
        for k, v in current_cfg.items():
            log_msg(f"{k}: {v}")
        log_msg("======================================\n")

        log_msg("Starting real-time analyzer in the background...")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"  # Forces Python to write logs immediately instead of buffering
        
        analyzer_proc = subprocess.Popen([sys.executable, '-m', 'rt_pabr.realtime_analyzer', '--workspace', ws], 
                                         stdout=log_f, stderr=subprocess.STDOUT, env=env)
        
        run_file_path = run_f
        if run_f != 'CALIBRATION':
            run_file_path = os.path.join(ws, run_f).replace('\\', '/')
            
        log_msg("Starting experiment controller...")
        exp_proc = subprocess.Popen([sys.executable, '-m', 'rt_pabr.exp_pips_rme', 
                                     '--subject', sub, '--run_file', run_file_path, '--start_trial', start_t,
                                     '--transducer', trans, '--workspace', ws], 
                                     stdout=log_f, stderr=subprocess.STDOUT, env=env)
        
        try:
            exp_proc.wait()
        except KeyboardInterrupt:
            exp_proc.terminate()
            
        log_msg("Experiment finished. Terminating real-time analyzer...")
        analyzer_proc.terminate()
        log_msg(f"Done. Log saved to {log_file_path}")
        log_f.close()