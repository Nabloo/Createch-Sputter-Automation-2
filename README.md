# Sputter Automation

Modular real-time monitoring and data-logging application for sputter
deposition processes.  Reads measurement data from up to three hardware
devices simultaneously, plots it live, and records everything to
time-zone-aware CSV log files with automatic daily rotation.

## Table of Contents

- [Quick Start](#quick-start)
- [User Guide](#user-guide)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Running Tests](#running-tests)
- [For Developers](#for-developers)
  - [Adding a New Device](#adding-a-new-device)
  - [Fixing or Modifying an Existing Device](#fixing-or-modifying-an-existing-device)
  - [Project Structure](#project-structure)
- [Supported Hardware](#supported-hardware)

--------------------------------------------------------------------

## Quick Start

### Prerequisites

- **Python 3.9+** (tested on Windows)
- Serial ports available for VCU and SQM devices
- Ethernet access for the Eurotherm controller

### Installation

```bash
git clone <repo-url>
cd Sputter-Automation-2
pip install -r requirements.txt
```

### Running

```bash
python src/main.py
```

On first launch a `config.json` file is created with default settings.
Edit it to match your hardware setup (COM ports, IP addresses, baud
rates, etc.).

--------------------------------------------------------------------

## User Guide

### Main Window

The application window is divided into three areas:

| Area | Content |
|------|---------|
| **Left** | Device panels: live sensor readings, connection status, and controls |
| **Right** | Plot widgets: real-time scrolling charts of measurement data |
| **Top** | Toolbar: start/stop acquisition, add plots, history window, log viewer |

### Toolbar

| Button | Action |
|--------|--------|
| `▶ Start` | Begin polling all devices and streaming data to plots |
| `■ Stop` | Pause polling (devices stay connected) |
| `📊 Add Plot` | Add a new plot widget |
| `♻ Clear All` | Clear all plot data without removing the widgets |
| **X-axis** | Toggle between relative seconds and absolute HH:MM:SS |
| **History** | Rolling window in seconds (0 = infinite) |

### Device Panels (left side)

Each registered device gets a panel showing:

- Connection status LED (green = connected, red = disconnected)
- Device type and ID
- COM port + baud rate (serial devices) or host + Modbus port (Ethernet devices)
- Live measurement values with units
- Connect / Disconnect buttons

### Plots (right side)

- Each plot can display multiple channels from one device
- Use the **Device** dropdown to switch which device the plot shows
- Use the **Channels** button to toggle individual channels on/off
- Use the **Show Datapoints** checkbox to overlay scatter points
- Right-click and drag to zoom; double-click to reset

### Log Viewer (row 2)

Click **View Log** to switch from live data to browsing historical CSV
logs:

- **Load...** -- open a CSV log file
- **From / To** -- narrow the displayed time range
- The X-axis mode toggle works in both live and log-viewer modes

CSV log files are saved in the configured log directory (default:
`logs/` inside the project folder).  One file per day, named
`YYYY-MM-DD.csv`.

### Menu Bar

| Menu | Item | Description |
|------|------|-------------|
| File | Choose Log Directory... | Change where CSV logs are saved |
| View | Dark Theme | Toggle dark/light theme |
| View | Gap Threshold... | Set the gap-detection timeout |
| View | Reset to Defaults | Restore the backup config and restart |
| Devices | Connect / Disconnect All | Bulk connection management |

--------------------------------------------------------------------

## Architecture

The application is built in four layers connected by a publish/subscribe
pattern:

```
                         GUI
     (main_window, device_panel, plot_widget,
      dock_manager, theme)
            PySide6 + pyqtgraph
                          ^
                          | subscribes to updates
                          |
                     Data Store
          Thread-safe shared data model
      (latest snapshot + rolling history)
                          ^
                          | subscribes to engine
                          | also feeds DataLogger --> CSV files
                          |
                Acquisition Engine
          One polling thread per device,
          UTC timestamps every measurement
                          ^
                          | calls device.poll()
                          |
                   Device Drivers
     VCU (RS-232), SQM (RS-232 binary),
     Eurotherm (Modbus TCP)
               BaseDevice ABC
```

### Data Flow

1. **AcquisitionEngine** runs a daemon thread per device and calls
   `device.poll()` at the configured interval.
2. The engine timestamps every reading with `datetime.now(timezone.utc)`
   and pushes it to the **DataStore**.
3. The **DataStore** stores the latest snapshot and a rolling history
   deque, then fans out the update to all subscribers (GUI panels,
   plot widgets, **DataLogger**).
4. The **DataLogger** converts UTC timestamps to the detected local
   timezone, writes rows to a daily CSV file, and rotates at local
   midnight.

### Key Design Decisions

- **One thread per device** -- serial/Modbus I/O is slow; dedicated threads
  prevent one slow device from blocking others.
- **Thread-safe data model** -- `DataStore` uses an `RLock` and publishes
  outside the lock to avoid deadlocks with Qt signals.
- **Fixed-offset timezone** -- the `DataLogger` detects the local timezone
  at startup.  A long-running process that spans a DST transition will
  keep the offset captured at start.  Restart the app after DST changes
  to get the correct offset.
- **Publish/subscribe** -- all communication is via callbacks
  (no polling by the GUI).  This keeps the GUI responsive even under
  heavy data rates.

### Device Driver Interface

All device drivers inherit from `BaseDevice` and expose this interface:

| Member | Type | Purpose |
|--------|------|---------|
| `device_id` | `str` property | Unique identifier, e.g. `"VCU-0"` |
| `plot_channels` | `list[str]` property | Numeric channels available for plotting |
| `status_channels` | `list[str]` property | All channels to display in the device panel |
| `channel_units` | `Dict[str, str]` property | Channel --> unit string mapping |
| `connect()` | method | Open connection, verify, return `bool` |
| `disconnect()` | method | Close connection |
| `poll()` | method | Read data and return `Dict[str, Any]` |
| `_after_connect()` | method | Post-connection verification |
| `start_reconnect_loop()` | method | Start background auto-reconnect (inherited) |
| `stop_reconnect_loop()` | method | Stop reconnection (inherited) |

--------------------------------------------------------------------

## Configuration

All configuration lives in `config.json` at the project root.
On first launch, the file is created from the built-in defaults in
`src/config.py` (`DEFAULT_CONFIG`).  Missing keys are always
deep-merged from the defaults.

### Devices

Each entry in `devices` describes one hardware device:

| Key | Type | Description |
|-----|------|-------------|
| `type` | string | Driver class name (`VCUController`, `SQMController`, `EurothermController`) |
| `device_id` | string | Unique ID, e.g. `"VCU-0"` |
| `port` | string | COM port for serial devices |
| `host` | string | IP address (Ethernet devices only) |
| `modbus_port` | int | TCP port (Ethernet devices only) |
| `baudrate` | int | Baud rate for serial devices |
| `poll_interval` | float | Seconds between polls |
| `number_of_sensors` | int | How many sensor channels to expose |
| `reconnect_interval` | float | Seconds between reconnection attempts |
| `max_retries` | int | Max reconnection attempts (0 = unlimited) |
| `unit` / `units` | string/object | Measurement unit(s) for display |

Example VCU entry:
```json
{
  "type": "VCUController",
  "device_id": "VCU-0",
  "address": 0,
  "port": "COM6",
  "baudrate": 19200,
  "number_of_sensors": 3,
  "poll_interval": 0.5,
  "unit": "mbar"
}
```

### Logging

```json
{
  "logging": {
    "enabled": true,
    "directory": "logs",
    "rotation_enabled": true,
    "flush_interval": 5.0,
    "timezone_offset": null
  }
}
```

- `timezone_offset`: explicit UTC offset in hours.  When `null`
  (default), the app auto-detects the local timezone.
- `flush_interval`: how often (seconds) buffered rows are written to
  disk.

### GUI

```json
{
  "gui": {
    "theme": "dark",
    "gap_threshold_seconds": 60.0,
    "window": { ... },
    "plots": [ ... ],
    "device_panels": [ ... ]
  }
}
```

- `plots` -- saved plot configurations (device, channels, colours,
  visibility, history window, log scale, scatter points).
- `device_panels` -- saved panel dock positions.
- `gap_threshold_seconds` -- plot lines are broken when the gap between
  consecutive data points exceeds this value.

--------------------------------------------------------------------

## Running Tests

```bash
python -m unittest discover tests -v

# Run a specific suite
python -m unittest tests.test_vcu_controller -v
python -m unittest tests.test_eurotherm_controller -v
python -m unittest tests.test_data_logger -v
```

Hardware tests (require actual instruments connected):

```bash
python tests/manual_test.py          # Eurotherm Modbus verification
python tests/test_vcu_hardware.py    # VCU serial verification
```

--------------------------------------------------------------------

## For Developers

### Adding a New Device

#### Step 1 -- Understand the Interface

Read `src/devices/base_device.py` -- every device driver must implement
the abstract methods listed in the table above under
[Architecture](#architecture).

Study an existing driver that's similar to your new device:

| If your device uses ... | Study this driver |
|-------------------------|-------------------|
| RS-232, ASCII line protocol | `src/devices/vcu_controller.py` |
| RS-232, binary packet protocol | `src/devices/sqm_controller.py` |
| Ethernet / Modbus TCP | `src/devices/eurotherm_controller.py` |

Also read `src/devices/sqm_protocol.py` if your device needs custom
packet framing or CRC calculations.

#### Step 2 -- Write the Driver

Create `src/devices/my_device_controller.py`:

```python
"""Driver for the MyDevice XYZ."""

from typing import Any, Dict
from src.devices.base_device import BaseDevice

class MyDeviceController(BaseDevice):

    def __init__(self, config: Dict[str, Any]) -> None:
        # For serial devices, just call super().__init__(config)
        super().__init__(config)

        # For Ethernet devices, inject a dummy port string first:
        # host = config.get("host", "192.168.1.1")
        # own_config = dict(config)
        # own_config["port"] = f"{host}:{config.get('port', 502)}"
        # super().__init__(own_config)

        # Read your custom config keys here
        self._address = config.get("address", 0)
        self._num_sensors = config.get("number_of_sensors", 1)

    @property
    def device_id(self) -> str:
        return f"MyDevice-{self._address}"

    @property
    def plot_channels(self) -> list[str]:
        return [f"ch{i}_value" for i in range(1, self._num_sensors + 1)]

    @property
    def status_channels(self) -> list[str]:
        return list(self.plot_channels)

    @property
    def channel_units(self) -> Dict[str, str]:
        return {ch: "unit" for ch in self.plot_channels}

    def poll(self) -> Dict[str, Any]:
        # Read data from the instrument and return a flat dict.
        # For serial: use self._send_command(cmd) or self._write()+self._readline()
        # For Ethernet/Modbus: use your own client stored on self
        data = {}
        for i in range(1, self._num_sensors + 1):
            data[f"ch{i}_value"] = 42.0         # the measurement
            data[f"ch{i}_value_unit"] = "unit"  # unit for display
        return data

    def _after_connect(self) -> None:
        # Verify communication and read metadata (firmware, sensor ID, etc.)
        # Raise ConnectionError if verification fails.
        pass
```

**Important conventions:**

- **`poll()` must return a flat dict** with measurement keys (e.g.
  `ch1_temperature`) and optional `_unit`-suffixed keys (e.g.
  `ch1_temperature_unit`).  The unit keys are what make the unit labels
  appear in the device panel and the CSV header.
- **For serial devices**, use `self._send_command(cmd)` for ASCII-line
  protocols or `self._write(data)` / `self._serial.read(n)` for binary
  protocols.
- **For Ethernet devices**, override `connect()` and `disconnect()`
  entirely.  Store your client on a new attribute (e.g.
  `self._client`).  Inject a dummy `config["port"]` so `BaseDevice`
  doesn't crash.  See `EurothermController` for the pattern.
- **Error handling**: return `float("nan")` for values that couldn't
  be read (the plot shows a gap).  Raise `ConnectionError` if the
  connection is dead.

#### Step 3 -- Register the Driver

In `src/devices/__init__.py`, add:
```python
from .my_device_controller import MyDeviceController
```
and add `"MyDeviceController"` to `__all__`.

In `src/devices/device_manager.py`, add to `_DEVICE_TYPE_MAP`:
```python
"MyDeviceController": MyDeviceController,
```

#### Step 4 -- Add to Default Config

In `src/config.py`, add an entry under `"devices"` in `DEFAULT_CONFIG`.

For Ethernet devices, use `"host"` and `"modbus_port"` instead of
`"port"` and `"baudrate"`.  Also add a panel entry under
`"gui"` -> `"device_panels"`.

#### Step 5 -- Write Tests

Create `tests/test_my_device_controller.py`:

- Use `unittest.mock` to mock the serial port or network client.
- Test `device_id`, `plot_channels`, `status_channels`, `channel_units`.
- Test `connect()` / `disconnect()`.
- Test `poll()` returns correct values.
- Test `poll()` handles errors (returns NaN or raises appropriate exception).
- Test multi-sensor configs.

#### Step 6 -- Verify

```bash
python -m unittest tests.test_my_device_controller -v
python src/main.py
```

#### Step 7 -- Hardware Test (if available)

Create `tests/test_my_device_hardware.py` following the pattern in
`tests/test_vcu_hardware.py` or `tests/manual_test.py`.

### Fixing or Modifying an Existing Device

#### Debugging a Device That Stopped Working

1. **Check the connection** -- open the app, look at the device panel.
   Is the LED green?  Does the panel show live values?
2. **Check the logs** -- the app logs to the console.  Look for:
   `"Failed to connect"`, `"Poll error"`, `"CRC mismatch"` (SQM).
3. **Run the hardware test** if one exists:
   ```bash
   python tests/manual_test.py      # Eurotherm
   python tests/test_vcu_hardware.py # VCU
   ```
4. **Check the protocol** -- the device manuals are in the `Infos/`
   folder:
   - `JEVAmet VCU manual.pdf`
   - `SQM-160 Operating Manual.pdf`
   - `Eurotherm 3504 manual.pdf`

#### Common Issues and Fixes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Device never connects | Wrong COM port or IP | Edit `config.json`, verify with device manager / `ping` |
| Connects but no data | Wrong baud rate or protocol params | Check device front-panel settings |
| SQM "CRC mismatch" | Electrical noise, long cable | Lower baud rate, shorter cable, better shielding |
| Plots show gaps | Poll interval too long | Increase `poll_interval` or check device health |
| Log timestamps wrong | Timezone detection failed | Set `timezone_offset` in `config.json` |
| "Unknown device type" | Driver not registered | Check `_DEVICE_TYPE_MAP` in `device_manager.py` |

#### Changing a Device's Protocol

If the hardware changes (e.g. a firmware update changes the command
format), modify the driver:

1. **Update the protocol comments** at the top of the file.
2. **Update `poll()`** and helper methods.
3. **Update the tests** to match the new protocol.
4. **Run the test suite**: `python -m unittest discover tests -v`

### Project Structure

```
Sputter-Automation-2/
├── README.md                          This file
├── requirements.txt                   Python dependencies
├── config.json                        User configuration (auto-created)
├── backup_config.json                 Backup of last working config
├── specs.md                           Original SQM-160 specification
│
├── Infos/                             Hardware manuals and specs
│   ├── JEVAmet VCU manual.pdf
│   ├── SQM-160 Operating Manual.pdf
│   ├── Eurotherm 3504 manual.pdf
│   ├── Specs Eurotherm.md
│   ├── Specs Logs.md
│   └── Spec template.md
│
├── logs/                              CSV log output directory
│
├── src/                               Application source
│   ├── main.py                        Entry point
│   ├── config.py                      Config loader/saver + defaults
│   │
│   ├── acquisition/
│   │   └── engine.py                  Polling workers, UTC timestamps
│   │
│   ├── data/
│   │   └── datastore.py               Thread-safe shared data model
│   │
│   ├── data_logging/
│   │   ├── data_logger.py             CSV writer with daily rotation
│   │   └── log_reader.py              CSV parser for log viewer
│   │
│   ├── devices/
│   │   ├── __init__.py                Public exports
│   │   ├── base_device.py             Abstract base: serial, reconnect
│   │   ├── vcu_controller.py          VCU pressure controller (RS-232)
│   │   ├── sqm_controller.py          SQM-160 QCM (RS-232, binary)
│   │   ├── sqm_protocol.py            SQM CRC14 + packet framing
│   │   ├── eurotherm_controller.py    Eurotherm 3504 (Modbus TCP)
│   │   └── device_manager.py          Factory, wiring, orchestration
│   │
│   └── gui/
│       ├── __init__.py
│       ├── main_window.py             Top-level window, toolbar, menus
│       ├── device_panel.py            Per-device live-value panel
│       ├── dock_manager.py            QDockWidget persistence
│       ├── plot_widget.py             pyqtgraph plot widget
│       ├── plot_config_dialog.py      Channel visibility/colour editor
│       └── theme.py                   Dark & light Fusion themes
│
└── tests/
    ├── __init__.py
    ├── test_vcu_controller.py         VCU unit tests
    ├── test_vcu_hardware.py           VCU hardware verification
    ├── test_eurotherm_controller.py   Eurotherm unit tests
    ├── test_acquisition_engine.py     Engine unit tests
    ├── test_datastore.py              DataStore unit tests
    ├── test_data_logger.py            DataLogger unit tests
    ├── test_log_reader.py             Log reader unit tests
    ├── test_log_viewer.py             Log viewer unit tests
    └── manual_test.py                 Quick Eurotherm Modbus check
```

--------------------------------------------------------------------

## Supported Hardware

| Device | Driver | Interface | Protocol | Measured Values |
|--------|--------|-----------|----------|----------------|
| **JEVAmet VCU** | VCUController | RS-232 | ASCII, comma-delimited, CR-terminated | Pressure (ch1-ch3), status codes |
| **INFICON SQM-160** | SQMController | RS-232 | Binary packets, CRC14 | Rate, thickness, frequency (ch1-ch6) |
| **Eurotherm 3504** | EurothermController | Ethernet | Modbus TCP (FC 03), slave 255 | Temperature + setpoint (ch1) |
