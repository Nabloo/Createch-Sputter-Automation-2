# Log Viewer – Historical Data Browser

## Why

Users need to review past measurement runs without leaving the application.
Currently, CSV log files accumulate in the `logs/` directory but can only be
inspected with external tools (Excel, VS Code, etc.).  A built-in viewer lets
operators correlate pressure curves with process events, zoom into specific
time windows, and export or screenshot findings – all within the familiar
dark-themed lab UI.

## What

An **application-wide mode toggle** in the main toolbar that switches all
existing plot panels between **Live** (real-time data from `DataStore`) and
**View Log** (historical data from a CSV file).

- **Toggle button** in the toolbar: "Live" | "View Log"
- When switched to **View Log**, the toolbar reveals additional controls:
  log file picker, time-range widgets, and x-axis mode toggle.
- **No new panels** — the existing `PlotWidget` instances render the CSV
  data instead of live data.  Channel configuration (checkboxes, colours,
  visibility) from the live setup is preserved.
- **Acquisition keeps running** in the background — measurements continue
  to be collected and logged.  Only the *display* in the plots changes.
- Switching back to **Live** restores real-time plotting immediately.

The viewer is read-only – it never modifies log files.

## Constraints

### Must

- Use **pyqtgraph** for plotting (same library as live plots, same `PlotWidget`).
- Reuse the existing `PlotWidget` instances — no new plot panels.
- Mode toggle in the **main toolbar** (`MainWindow._build_toolbar`).
- Log-file controls (file picker, time range, x-axis mode) appear in the
  toolbar only when "View Log" mode is active.
- Follow the dark Fusion theme via `src/gui/theme.py`.
- Parse the existing CSV format produced by `DataLogger`
  (`logs/YYYY-MM-DD_<device_id>.csv` with ISO‑8601 timestamps in column 0).
- Run file I/O and parsing in a **worker thread**; GUI updates via
  Qt signals/slots.
- **Acquisition must keep running** in "View Log" mode — the engine is
  not stopped, the `DataStore` still receives data, and the `DataLogger`
  still writes CSV files.

### Must Not

- No new Python dependencies (use `csv` from stdlib).
- Don't modify `DataLogger`, `DataStore`, `AcquisitionEngine`, or the
  live `PlotWidget`'s internal rendering logic.
- Don't block the GUI thread during file loading.
- Don't create new dock panels — reuse existing plots.
- Don't stop acquisition when switching modes.

### Out of Scope

- Editing or re-exporting log files.
- Comparing multiple log files simultaneously (one file at a time for V1).
- Real-time log tailing (the viewer loads a static snapshot).
- Statistical overlays (mean, std-dev, etc.).
- Multi-device log viewing — each `PlotWidget` shows data for its
  currently selected device (from the widget's own device dropdown).

## Current State

### CSV Log Format

Produced by `src/data_logging/data_logger.py`.  One file per device per day.

```text
logs/2026-05-21_VCU-0.csv
```

| Column | Example value |
|--------|---------------|
| 0 – `timestamp` | `2026-05-21T15:30:00.123456+02:00` |
| 1 – `ch1_pressure [mbar]` | `1.23e-05` |
| 2 – `ch1_status_code` | `0` |
| 3 – `ch2_pressure [mbar]` | `5.67e-04` |
| 4 – `ch2_status_code` | `0` |
| … | … |

- Timestamps are **ISO‑8601 with timezone offset** (local time).
- Pressure columns embed the unit in brackets (`[mbar]`).
- Status columns are plain integers.
- Rows are written in chronological order but the parser must not assume
  sorted data – it should sort after loading.

### Toolbar Layout (current `_build_toolbar`)

```
┌─────────────────────────────────────────────────────────────┐
│ ▶ Start │ ■ Stop │ ⚡ Connect All │ ⏻ Disconnect All │      │
│ 📊 Add Plot │ ♻ Clear All                                  │
└─────────────────────────────────────────────────────────────┘
```

### Toolbar Layout (after this feature)

```
┌─────────────────────────────────────────────────────────────┐
│ ▶ Start │ ■ Stop │ ⚡ Connect All │ ⏻ Disconnect All │      │
│ ⬤ Live │ 📂 [2026-05-21_VCU-0.csv] │ From: [14:00]  │      │
│ To: [16:00] │ X-axis: [Seconds ▾] │ 📊 Add Plot │ ♻ Clear   │
└─────────────────────────────────────────────────────────────┘
```

The log-file controls (file label, time range, x-axis mode) are **hidden**
when in "Live" mode and **shown** when in "View Log" mode.

### Existing PlotWidget Architecture

The live `PlotWidget` (`src/gui/plot_widget.py`) renders data from a
`deque` buffer.  Data arrives via `push_data(device_id, timestamp, data)`
which is called from `DataStore`'s subscription system.  The key rendering
path is:

```
DataStore._on_update() → plot.push_data() → signal → _on_data_arrived()
  → buffer append → _update_curve() → curve.setData(xs, ys)
```

The log-viewer mode needs to **bypass this pipeline**: instead of buffering
from `push_data`, the plot should display pre-loaded CSV data directly.

### Relevant Files

| File | Role |
|------|------|
| `src/gui/main_window.py` | Toolbar (`_build_toolbar`), mode toggle, log controls. |
| `src/gui/plot_widget.py` | Existing plot — needs a `load_log_data()` method. |
| `src/gui/dock_manager.py` | Adds/removes `QDockWidget` panels. |
| `src/gui/plot_config_dialog.py` | Channel/colour/visibility dialog (already used by plots). |
| `src/gui/theme.py` | Dark/light Fusion palette. |
| `src/data_logging/data_logger.py` | Produces the CSV files we need to read. |
| `src/config.py` | Device config access (`find_device_config`, `get_device_configs`). |
| `src/devices/device_manager.py` | Wires panels → orchestrates mode switch. |
| `src/main.py` | Entry point — passes config to MainWindow/DeviceManager. |

## Tasks

### T1 — CSV Parser & Data Model

**What:** A pure-data module that reads a CSV log file, extracts headers,
parses timestamps, and returns a structured in-memory representation
suitable for plotting.

**Details:**
- Class `LogFileReader` in `src/data_logging/log_reader.py`.
- `read(filepath: str) -> LogData`:
  - Opens CSV, reads headers from row 1.
  - Parses every subsequent row: `datetime.fromisoformat(ts_str)` for
    column 0, `float` for numeric columns, skip non-numeric.
  - Sorts rows by timestamp ascending.
  - Returns a `LogData` dataclass with:
    - `headers: List[str]` (original CSV column names).
    - `timestamps: List[datetime]` (sorted).
    - `values: Dict[str, List[float]]` (column-name → parallel list).
    - `filepath: str`, `device_id: str`, `date: str`.
- Run file I/O in a worker thread; emit result via a Qt Signal.

**Files:** `src/data_logging/log_reader.py`, `tests/test_log_reader.py`

**Verify:** `python -m unittest tests.test_log_reader -v` (all parsing tests pass)

---

### T2 — PlotWidget: `load_log_data()` Method

**What:** Add a method to `PlotWidget` that replaces the live buffer
with pre-loaded CSV data and re-renders all curves.

**Details:**
- Method `load_log_data(log_data: LogData, t_range_start: datetime, t_range_end: datetime)`:
  - Clears all existing buffers (`_buffers`).
  - For each channel in `_channels`, looks up matching values in
    `log_data.values` (by column name matching, e.g. `ch1_pressure [mbar]`).
  - Converts timestamps to x-values:
    - Relative mode: `x = (t - t_range_start).total_seconds()`
    - Absolute mode: `x = (t - epoch_start).timestamp()` (or similar numeric)
  - Applies **gap detection** (> 60 s breaks the curve into separate
    `PlotDataItem` segments – see T3).
  - Calls `curve.setData(xs, ys)` for visible channels; sets `[]` for
    hidden channels.
  - Updates the x-axis label ("Time (s)" or "HH:MM:SS").
- Method `clear_log_data()`:
  - Clears all curves to `[]`.
  - Re-enables live data buffering (resumes `push_data` → `_on_data_arrived`).
- A flag `_log_mode: bool = False` toggles whether `push_data` is
  processed or ignored.  When `_log_mode` is True, `_on_data_arrived`
  returns early without buffering.
- The existing `_update_curve` method continues to work for live data;
  log data bypasses it and writes directly to the curves.

**Files:** `src/gui/plot_widget.py`

**Verify:** Manual check — call `load_log_data()` with test data, verify
curves render correctly.  Call `clear_log_data()`, verify live data resumes.

---

### T3 — Gap Detection & Curve Rendering

**What:** When rendering loaded CSV data, detect gaps > 60 s between
consecutive timestamps and split the curve into separate `PlotDataItem`
segments so no connecting line is drawn across the gap.

**Details:**
- Function `_split_into_segments(xs, ys, gap_threshold_s=60.0)` returns
  a list of `(x_segment, y_segment)` tuples.
- Each segment is a contiguous block where consecutive x-differences
  are ≤ `gap_threshold_s`.
- In `load_log_data`, for each channel:
  - Split the data into segments.
  - Clear existing `PlotDataItem` curves for that channel.
  - Create one `pg.PlotDataItem` per segment (or one if no gaps).
  - Each segment uses `connect="all"` (the default).
  - Pyqtgraph does **not** connect across separate `PlotDataItem`
    instances, so gaps appear automatically.
- The legend should show only one entry per channel (not one per segment).
  Achieve this by setting `name=` on the first segment only, `name=None`
  on subsequent segments.

**Files:** `src/gui/plot_widget.py` (helper in same file, or separate utility)

**Verify:** Manual check with CSV that has intentional 120 s gaps — verify
no line crosses the gap.

---

### T4 — Toolbar Mode Toggle & Log Controls

**What:** Add a mode toggle button and log-file controls to the main
toolbar in `MainWindow`.  `DeviceManager` orchestrates the mode switch.

**Details:**

**MainWindow toolbar additions (`_build_toolbar`):**

- After the existing separator (after Disconnect All), add:
  - **Mode toggle**: `QPushButton` (checkable) with text "⬤ Live" (unchecked)
    / "⬤ View Log" (checked).  Styled distinctly (e.g. green/blue when
    live, orange/amber when viewing log).
  - **File label**: `QLabel` showing the loaded file name or "No file".
    Hidden in Live mode.
  - **Load button**: `QPushButton` "📂 Load…" — opens `QFileDialog`.
    Hidden in Live mode.
  - **From / To**: two `QDateTimeEdit` widgets.  Hidden in Live mode.
  - **X-axis mode**: `QComboBox` with "Seconds from start" and "HH:MM:SS".
    Hidden in Live mode.

- All log controls are hidden/shown via `setVisible()` when the mode
  toggle changes state.

**DeviceManager mode orchestration:**

- Method `set_view_mode(live: bool)`:
  - If `live=False` (View Log mode): pauses plot subscriptions
    (plots stop receiving live `push_data` calls), loads the selected CSV
    via `LogFileReader` (worker thread), calls `plot.load_log_data()` on
    each plot.
  - If `live=True` (Live mode): calls `plot.clear_log_data()` on each
    plot, resumes subscriptions.
  - Updates the toolbar visibility.
- The mode toggle's `toggled` signal connects to `set_view_mode`.
- The Load button triggers a `QFileDialog` filtered to `logs/*.csv`,
  then calls `_load_log_file(path)` which does the worker-thread read.

**Files:** `src/gui/main_window.py`, `src/devices/device_manager.py`

**Verify:** `python src/main.py` → click mode toggle → log controls appear.
Click Load → select a CSV → plots render the log data.  Toggle back to Live →
plots show live data again.  Acquisition status bar still shows "Running".

---

### T5 — X-Axis Mode Switching

**What:** The x-axis mode combo switches between relative seconds and
absolute wall-clock time (`HH:MM:SS`).

**Details:**
- For **relative mode**: x = seconds since the start of the selected time
  range.  Axis label: "Time (s)".
- For **absolute mode**: x = raw numeric timestamps, but the axis tick
  labels are formatted as `HH:MM:SS`.  Achieved via a custom
  `AxisItem` subclass that overrides `tickStrings()`.
- Switching modes triggers a re-render of all curves (calling
  `load_log_data` with the current time range but new x-axis mode).
- The x-axis mode combo is only visible in "View Log" mode.

**Files:** `src/gui/plot_widget.py` (custom `TimeAxisItem`), `src/devices/device_manager.py`

**Verify:** Load a CSV in View Log mode.  Switch x-axis mode — tick labels
change between seconds and `HH:MM:SS`.  Verify the curves remain correct.

---

### T6 — Tests

**What:** Unit tests for the CSV parser (T1) and gap detection (T3).

**Details:**
- `tests/test_log_reader.py`:
  - `test_parse_valid_csv` — correct headers, timestamps, values.
  - `test_missing_file` — graceful error (exception or error result).
  - `test_empty_csv` — only headers, no data rows.
  - `test_unsorted_timestamps` — output is sorted ascending.
  - `test_non_numeric_columns_skipped` — text columns are ignored.
- `tests/test_log_viewer.py` (or inline in plot_widget tests):
  - `test_split_into_segments` — two segments for 120 s gap, one for
    contiguous data.
  - `test_no_segments` — empty input returns empty list.

**Files:** `tests/test_log_reader.py`

**Verify:** `python -m unittest tests.test_log_reader -v` (all pass)

## Validation

After all tasks complete, perform end-to-end verification:

1. `python -m unittest tests.test_log_reader tests.test_datastore
   tests.test_acquisition_engine tests.test_vcu_controller
   tests.test_data_logger -v` → all tests pass.
2. `python src/main.py` → start acquisition (▶ Start).
3. Verify live plots are showing real-time data.
4. Click mode toggle → "⬤ View Log" (checked).
5. Verify log controls appear in the toolbar (Load, From, To, X-axis).
6. Click **📂 Load…** → select `logs/2026-05-21_VCU-0.csv`.
7. Verify plots render CSV data with correct channel colours and legend.
8. Verify the status bar still shows "Running" (acquisition continues).
9. Toggle a channel off via the plot's **Channels** button → its curve
   disappears (same behaviour as live mode).
10. Adjust time range via `QDateTimeEdit` → curves update.
11. Switch x-axis mode to `HH:MM:SS` → tick labels show wall-clock time.
12. Verify gaps > 60 s show as broken lines (no connector).
13. Click mode toggle back to "Live" → plots resume showing live data.
14. Close and reopen the app → the mode toggle and dock layout are
    restored.
