# Real-Time pABR System

This system runs parallel Auditory Brainstem Response (pABR) experiments with microsecond-precision audio/trigger timing while simultaneously pulling a live EEG stream via Lab Streaming Layer (LSL) to compute, analyze, and plot the brainstem responses in real-time.

This has been developed and tested with a BioSemi ActiveTwo system running ActiView 10.4+. Future work is planned to add support for other amplifiers, such as the BrainVision ActiCHamp Plus.

## Core Files
- `run_realtime.py`: The master GUI launcher.
- `exp_pips_rme.py`: The underlying `expyfun` experiment controller.
- `realtime_analyzer.py`: The background LSL analyzer and live-plotting engine.
- `pip_trains_rme.py`: The stimulus generation logic.
- `config.py`: The main configuration file for analysis parameters.

---

## 1. Installation & Setup

It is highly recommended to run this inside a dedicated Python environment (e.g., using Anaconda or `venv`).

1.  Create an empty folder for your experiment workspace (this is where data, stimuli, and configs will be saved) and navigate into it:
   ```bash
   mkdir pABR_Experiment
   cd pABR_Experiment
   ```
2.  Open your terminal (Anaconda Prompt or Command Prompt) and activate your environment. For example, to create and activate a venv named `.venv` in the current directory:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
  ```
3.  Install the package directly from GitHub:
   ```bash
   pip install git+https://github.com/tomstoll/rt_pabr.git
   ```

*Note: The `expyfun` library relies on a properly configured system audio backend. Ensure your RME drivers and ASIO settings are configured correctly as you normally would for your offline experiments.*

---

## 2. Hardware / ActiView Configuration

The real-time analyzer relies on LSL to access the live EEG data without interfering with the BDF file saving. 

1.  Open **BioSemi ActiView** (Version 10.4+ recommended).
2.  Configure your standard recording settings and channel selections.
3.  LSL should be running by default when data is streaming. Locate the **LSL (Lab Streaming Layer)** / Network streaming options built natively into the ActiView interface and **enable LSL streaming**.
4.  Ensure that the stream includes the `Status` (trigger) channel, as well as your required ABR channels (e.g., `ABR-L` and `ABR-R`, or the individual electrodes for a bipolar montage like `Cz`, `A1`, `A2`).
    *(The analyzer will automatically detect channels named `ABR-L`/`ABR-R`. If not found, it will default to creating a bipolar montage from the first few channels, which is likely incorrect. You can select the correct channels for your setup in the settings GUI.)*
5.  Press **Start** in ActiView so the data is actively streaming over the network.

---

## 3. Usage Instructions

From inside your workspace directory, you can launch the program using the command-line shortcut:

```bash
rt-pabr
```

This will open the main launcher window.

### 3.1. General Settings & Presets

Click the **Settings** button on the main launcher to configure experiment parameters. From here, you can set your LSL channels, default filter bounds, epoch windows, and network settings. 
You can **Save As...** to create custom configuration presets (e.g., different settings for different environments) or **Restore Defaults** if you make a mistake.

### 3.2. Generating Stimuli & Paradigms

Before running an experiment, you need stimulus files (`.hdf5`) and a paradigm file (`.json`).

-   **Generate Stimuli:** Click this to open the stimulus creator. Specify a stimulus rate (stim/s), duration (in minutes, for generating unique tokens), and type (Pips vs. Clicks). The `.hdf5` file will be automatically saved into the `stimuli/` folder.
-   **Create New Paradigm:** Click this to open the paradigm designer.
    -   Use the dropdowns to select the stimulus file for a given run.
    -   Enter the desired presentation level (dB) and the number of trials.
    -   **Correction Factors:** Input system-specific calibration corrections for individual frequencies, along with custom units (e.g., `peSPL`, `dB nHL`). These corrections are applied to the stimuli at the start of the experiment.
    -   **Mixed Paradigms:** You can freely mix Pip and Click runs in the same paradigm; the analyzer will automatically handle the matrix dimensions and naming.
    -   Click "Add Another Run" to create multi-level or multi-rate experiments.
    -   Give the paradigm a descriptive filename and click "Save & Select".

### 3.3. Running the Experiment

1.  Enter the **Subject ID**. A corresponding `data/<SubjectID>/` folder will be created.
2.  Select your `.json` **Paradigm File** from the dropdown (or select `CALIBRATION`).
    *(Note: If you select `CALIBRATION`, a dedicated safety window appears with a giant red warning to ensure headphones are not in a subject's ears during the 80 dB continuous calibration tone).*
3.  Select the **Transducer** you are using from the dropdown. This ensures the correct calibration values are used.
4.  Enter the **Start Epoch**. This defaults to `0` but can be set to a later number to resume an interrupted experiment.
5.  Click **Launch Experiment**. The button will first check for a live LSL stream before starting the real-time analyzer GUI.

### 3.4. Interacting with the Analyzer

The analyzer window provides comprehensive control and visualization.

#### Display Elements
-   **Waveform Plots:** A grid showing the real-time averaged ABR for each condition (run). Unused frequencies (e.g., 500 Hz during a Click run) are gracefully hidden for a cleaner interface.
-   **Row Labels:** To the left of each row, the stimulus type, level, custom units, and rate are displayed.
-   **Statistics Box:** Shows the number of epochs presented, epochs dropped due to trigger errors, and total elapsed time.
-   **Filter Parameters Box:** Allows you to change the High-Pass, Low-Pass, and Order of the causal filter applied to the averaged waveforms. Changes are reflected instantly.
-   **X-Axis Zoom Box:** Use the `+`, `-`, and `Reset` buttons to zoom the time axis.
-   **Progress Bar:** Shows the percentage of total epochs completed and an estimated time remaining.

#### Peak Picking
The system automatically picks the largest peak within the time window defined in `config.py` (default 4-16 ms). You can manually override this:
-   **Move a Peak:** Left-click and drag a peak's triangle marker to a new latency. The amplitude will snap to the waveform.
-   **Reject a Peak:** Right-click a peak's triangle marker to hide it. This marks it as rejected (`NaN`) in the data export.
-   **Restore Auto-Picking:** If you have moved or rejected a peak, right-click on an empty area of that same subplot to resume automatic peak finding for that trace.
-   **Manually Place a Peak:** If a trace has no peak (or you rejected it), left-click on an empty area of the subplot to place a new manual peak at the crosshair location.

#### Experiment Controls
-   **Start/Pause Exp:** Click this button (or press the `1` key) to pause or resume the stimulus presentation. The analyzer will continue processing any backlogged data.
-   **Stop Experiment:** Click this to safely terminate the experiment. You will be asked to confirm.
-   **Export Peaks:** Saves a `.csv` file of the currently displayed peak latencies and amplitudes.
-   **Screenshot:** Saves a high-resolution `.png` image of the current plot view.

---

## 4. Configuration & Performance

Most core parameters can be adjusted directly via the **Settings** GUI on the main launcher. The backend parameters are stored in `config.py` and `config.json`. This includes:
-   Default filter settings (`L_FREQ`, `H_FREQ`, `FILT_ORDER`).
-   Epoch timing (`TMIN`, `TMAX`).
-   Peak picking and SNR windows.
-   Decimation factor.

**Performance Note:** The analysis pipeline is highly optimized. However, if you experience lag between the "Epochs Presented" counter in the GUI and the trial count in the terminal, it means the analysis is not keeping up with the data stream. The simplest way to fix this is to increase the `DECIMATION_FACTOR` in `config.py` from `2` to `4`. This will reduce the sampling rate by a factor of 4, significantly decreasing the computational load.

---

## 5. Output Data
When the experiment finishes (or is stopped), the analyzer window will remain open for final inspection and peak adjustments. Upon closing the analyzer window, two files are saved into the `data/<SubjectID>/` directory:
1.  A final `_screenshot.png` of the plot in its last state.
2.  A `_results.hdf5` file containing:
    -   The raw (unfiltered) and dynamically filtered waveform arrays.
    -   The final peak latencies and amplitudes.
    -   All associated metadata about the runs, levels, rates, and stimuli.