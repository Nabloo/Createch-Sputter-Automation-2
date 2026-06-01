# Unified CSV Logging – Single File, Units in Headers

## Why

Currently each device writes its own CSV file (`YYYY-MM-DD_<device_id>.csv`). This makes it cumbersome to correlate measurements across devices during analysis. Additionally, SQM channel data stores units in separate column fields (`ch1_rate_unit`) rather than embedding them in the column header, forcing the reader to parse multiple columns for one measurement. A single unified CSV with units folded into column headers simplifies loading, plotting, and external analysis.

## What

One CSV file per measurement day (`logs/YYYY-MM-DD.csv`) containing data from all active devices, where every data column header includes its unit (e.g. `VCU-0_ch1_pressure [mbar]`). Loading this file in the log viewer renders data for every device correctly, with channel names matched via device-prefixed column headers.

## Constraints

### Must
- Single file per day: `logs/YYYY-MM-DD.csv` (no device suffix)
- Column naming: `<device_id>_<channel> [unit]` — always include the unit in the header when known
- SQM rate/thickness/frequency units MUST be in the header, NEVER as separate `_unit` columns
- VCU status columns (`status_code`, `status_text`) remain unit-less
- Non-numeric columns (e.g. `status_text`) are still written but ignored by the reader for plotting
- Writer must handle concurrent data from multiple devices (each row is one device poll)
- Reader must match channel names via `_find_log_column` using the new device-prefixed header format
- Backwards compatibility: loading old-format CSV files should still work via unprefixed fallback

### Must Not
- No new Python dependencies
- Do NOT break the live-mode UI — log changes only affect the CSV writer and reader
- Do NOT change the `DataStore` interface

### Out of Scope
- Real-time inter-device timestamp alignment (each poll writes its own row)
- Changing the log viewer UI (date range, file picker, etc.)
- Adding metadata rows beyond the header line

## Current State

**Writer** (`src/data_logging/data_logger.py`):
- `_ensure_file()` opens `{date_str}_{device_id}.csv` — one file per device
- `_generate_headers()` for VCU embeds units like `ch1_pressure [mbar]`; for SQM (flat) uses raw key names (units are separate fields)
- `_build_row()` for VCU skips the `unit` field; for SQM writes all float/str fields including unit fields
- `_close_device()` closes a single device files
- Buffers indexed by `device_id` for separate flush

**Reader** (`src/data_logging/log_reader.py`):
- `_parse_filename()` extracts date + device_id from `YYYY-MM-DD_<device_id>.csv`
- `LogData` has `device_id` and `date` fields
- `LogFileReader.read()` parses all numeric columns into `values` dict keyed by header name

**Column matching** (`src/gui/plot_widget.py`):
- `_find_log_column()` matches `ch1_pressure` → `ch1_pressure [mbar]` by exact or prefix match
- Does NOT currently account for device prefix — assumes column names are device-agnostic

**DeviceManager** (`src/devices/device_manager.py`):
- `_apply_log_data_to_plots()` calls `plot.load_log_data()` with the current `LogData` for every plot
- Each plot independently finds its own channels in the log columns

**SQM data format** (`src/devices/sqm_controller.py`):
- `poll()` returns flat dict: `{"ch1_rate": 5.0, "ch1_thickness": 100.0, ..., "ch1_rate_unit": "Hz", ...}`
- `channel_units` property: `ch1_rate` → `"Hz"`, `ch1_thickness` → `"kÅ"`, `ch1_frequency` → `"Hz"`

**Relevant test files:**
- `tests/test_data_logger.py` — CSV output format, rotation, per-device files
- `tests/test_log_reader.py` — parsing, filename extraction, column types
- `tests/test_log_viewer.py` — `_find_log_column` matching, gap detection, example log integration

## Tasks

### T1: Refactor DataLogger — single file, device-prefixed columns, units in headers
**What:**
1. Change filename from `YYYY-MM-DD_<device_id>.csv` to `YYYY-MM-DD.csv`
2. Column naming: `<device_id>_<channel_name> [unit]` for channels with units
   - VCU: `VCU-0_ch1_pressure [mbar]`, `VCU-0_ch1_status_code`, `VCU-0_ch1_status_text`
   - SQM: `SQM-0_ch1_rate [Hz]`, `SQM-0_ch1_thickness [kA]`, `SQM-0_ch1_frequency [Hz]`
3. Remove `_unit` suffix columns — units are header-only
4. Single file keyed by date string only (remove per-device file tracking)
5. `_generate_headers()` must accept (device_id, data) and prefix all column names with device_id
6. `_build_row()` must NOT output `_unit` fields for SQM
**Files:** `src/data_logging/data_logger.py`
**Verify:** `python -m unittest tests.test_data_logger -v`

### T2: Update LogFileReader — new filename format, device_id handling
**What:**
1. Update `_parse_filename()` to match `YYYY-MM-DD.csv` (no device_id in filename)
2. Update `LogData` — `device_id` becomes `""` (file contains all devices)
3. Ensure reading still works: sort timestamps, handle mixed-device rows, skip non-numeric columns
**Files:** `src/data_logging/log_reader.py`
**Verify:** `python -m unittest tests.test_log_reader -v`

### T3: Update PlotWidget._find_log_column — device-prefixed matching
**What:**
1. Change `_find_log_column` to accept a `device_id` parameter
2. Match `<device_id>_<channel_name>` first (exact), then `<device_id>_<channel_name> [` (unit prefix)
3. Fall back to old unprefixed match for backwards compatibility (loading old-format logs)
**Files:** `src/gui/plot_widget.py`
**Verify:** `python -m unittest tests.test_log_viewer -v`

### T4: Update PlotWidget.load_log_data + DeviceManager — pass device_id through
**What:**
1. Pass `self.device_id` to all `_find_log_column` calls in `load_log_data`
2. DeviceManager `_apply_log_data_to_plots` does not need changes (each plot already has its device_id)
**Files:** `src/gui/plot_widget.py`
**Verify:** `python -m unittest tests.test_log_viewer -v`

### T5: Update all relevant tests
**What:**
1. `test_data_logger.py`: single file, device-prefixed columns, no `_unit` columns, multi-device test
2. `test_log_reader.py`: filename parsing for new format, multi-device CSV parsing test
3. `test_log_viewer.py`: `_find_log_column` for device-prefixed matching, backwards-compat fallback
4. Regenerate example logs in `tests/example_logs/` for new format (or adapt tests)
**Files:** `tests/test_data_logger.py`, `tests/test_log_reader.py`, `tests/test_log_viewer.py`
**Verify:** `python -m unittest tests.test_data_logger tests.test_log_reader tests.test_log_viewer -v`

## Validation

Run the full test suite:
```
python -m unittest tests.test_data_logger tests.test_log_reader tests.test_log_viewer tests.test_acquisition_engine tests.test_datastore tests.test_vcu_controller tests.test_sqm_controller tests.test_sqm_protocol -v
```

Manual checks:
- Start the app, acquire data from multiple devices → one CSV file in `logs/`
- Verify CSV header: `timestamp, VCU-0_ch1_pressure [mbar], VCU-0_ch1_status_code, SQM-0_ch1_rate [Hz], ...`
- Verify no `_unit` columns exist for SQM data
- Load the CSV in the log viewer → switch between devices in a plot → all channels render correctly
- Load an old-format CSV (from `tests/example_logs/`) → still works via backwards-compatible fallback
