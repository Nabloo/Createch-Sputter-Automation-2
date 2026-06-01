# Unified CSV Logging – Single File, Units in Row 2

## Why

Currently each device writes its own CSV file. SQM data stores units in separate `_unit` columns. A single unified CSV with a dedicated unit row simplifies loading, plotting, and external analysis.

## What

One CSV file per day (`logs/YYYY-MM-DD.csv`) with all device data. Row 1 = clean column names (`VCU-0_ch1_pressure`), Row 2 = units (`mbar` or empty), Row 3+ = data. Reader skips row 2.

### CSV format example
```
timestamp,VCU-0_ch1_pressure,VCU-0_ch1_status_code,SQM-0_ch1_rate,SQM-0_ch1_thickness
,mbar,,Hz,kA
2026-06-01T12:00:00+00:00,9.9e+02,0,,,
2026-06-01T12:00:01+00:00,,,5.0,100.0
```

## Constraints

### Must
- Single file per day: `logs/YYYY-MM-DD.csv`
- Row 1: `<device_id>_<channel_name>` (clean, no unit)
- Row 2: unit string per column, empty for unitless columns like `status_code`, `timestamp`
- SQM rate/thickness/frequency units in row 2, NEVER as separate `_unit` columns
- VCU status columns (`status_code`, `status_text`) remain unitless
- Non-numeric columns (e.g. `status_text`) are written but skipped by reader for plotting
- Each row = one device poll (no inter-device timestamp alignment)
- Reader skips row 2; uses row 1 column names for value dict keys
- `_find_log_column` matches device-prefixed column names from row 1
- Backwards compatibility: old-format CSVs with `ch1_pressure [mbar]` headers still work

### Must Not
- No new dependencies
- Don't break live-mode UI
- Don't change DataStore interface

### Out of Scope
- Real-time inter-device timestamp alignment
- Log viewer UI changes
- Metadata rows beyond row 1+2

## Current State

**Writer** (`src/data_logging/data_logger.py`): one file per device, VCU units in header brackets, SQM units as `_unit` columns.
**Reader** (`src/data_logging/log_reader.py`): parses `YYYY-MM-DD_<device_id>.csv`, extracts device_id from filename.
**Column matching** (`src/gui/plot_widget.py`): `_find_log_column` matches `ch1_pressure` -> `ch1_pressure [mbar]` by prefix.
**DeviceManager**: `_apply_log_data_to_plots` pushes LogData to every plot.
**SQM** (`src/devices/sqm_controller.py`): `poll()` returns flat dict with `_unit` suffix fields.

## Tasks

### T1: Refactor DataLogger
**What:**
1. Filename: `YYYY-MM-DD.csv` (single file)
2. Row 1: `<device_id>_<channel>` (e.g. `VCU-0_ch1_pressure`)
3. Row 2: unit for each column (, `mbar`, empty for status_code, etc.)
4. Remove `_unit` columns — SQM units go to row 2 only
5. Single file keyed by date string; remove per-device file tracking
6. `_generate_headers()` -> `_generate_header_rows(device_id, data)` returns (row1, row2)
7. `_build_row()` skips `_unit` fields
**Files:** `src/data_logging/data_logger.py`
**Verify:** `python -m unittest tests.test_data_logger -v`

### T2: Update LogFileReader
**What:** skips row 2 when parsing, treats row 1 as column names, `_parse_filename` for `YYYY-MM-DD.csv`
**Files:** `src/data_logging/log_reader.py`
**Verify:** `python -m unittest tests.test_log_reader -v`

### T3: Update _find_log_column
**What:** accept device_id, match `<device_id>_<channel>` in row 1 names, fall back to old `channel [unit]` format
**Files:** `src/gui/plot_widget.py`
**Verify:** `python -m unittest tests.test_log_viewer -v`

### T4: Update load_log_data
**What:** pass device_id to `_find_log_column`
**Files:** `src/gui/plot_widget.py`
**Verify:** `python -m unittest tests.test_log_viewer -v`

### T5: Update tests
**What:** single file, device-prefixed columns, unit row, no `_unit` cols, backwards-compat
**Files:** `tests/test_data_logger.py`, `tests/test_log_reader.py`, `tests/test_log_viewer.py`
**Verify:** `python -m unittest tests.test_data_logger tests.test_log_reader tests.test_log_viewer -v`

## Validation
```
python -m unittest tests.test_data_logger tests.test_log_reader tests.test_log_viewer tests.test_acquisition_engine tests.test_datastore tests.test_vcu_controller tests.test_sqm_controller tests.test_sqm_protocol -v
```
