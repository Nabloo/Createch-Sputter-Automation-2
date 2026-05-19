
# Sputter Automation - Measurement & Control System

## Why

Control the I/O of various measurement systems (pressure gauges, temperature controllers, flow controllers, RF generators, pumps, etc.) connected via USB-to-serial adapters to a Windows 11 PC during sputter deposition processes.

Replace manual LabVIEW-based monitoring with a modern, extensible Python application that provides:
- real-time visualization,
- modular hardware integration,
- reliable logging,
- scalable multi-device support,
- future automation capabilities.

---

# What

A modular Python desktop application consisting of four layers:

## 1. Device Drivers
One class per physical device type.
Responsible only for:
- serial communication,
- command protocol,
- parsing responses,
- connection state.

No GUI logic or plotting logic.

---

## 2. Acquisition Engine
Background worker system that:
- polls all active devices,
- handles reconnects,
- timestamps measurements,
- publishes updates to the shared data model.

---

## 3. Shared Data Model
Thread-safe central storage for:
- latest measurements,
- rolling time-series buffers,
- device status,
- events and alarms.

Acts as the communication layer between hardware and GUI.

---

## 4. GUI Application
PySide6 desktop application with:
- dockable/rearrangeable panels,
- real-time plots,
- live device values,
- configurable channel selection,
- future extensibility for controls and automation.

GUI must never directly communicate with serial ports.

---

# Constraints

## Must

### Platform
- Python 3.10+
- Windows 11

### GUI
- PySide6 (Qt6)
- pyqtgraph for plotting
- Qt dock widget system for rearrangeable panels
- Dark Fusion-style theme optimized for laboratory use

### Communication
- pyserial for COM-port communication
- Multiple COM ports simultaneously
- Graceful disconnect/reconnect handling
- Automatic reconnect attempts
- Per-device configurable polling intervals

### Architecture
- One device class per physical device
- All device classes inherit from `BaseDevice`
- Device drivers isolated from GUI logic
- GUI updates only through Qt signals/slots
- Serial communication only in worker threads
- Thread-safe shared data model
- No blocking serial operations in GUI thread
- Rolling data buffers with configurable size limits
- Event-driven architecture between acquisition and GUI

### Configuration
- JSON or YAML configuration files
- Persistent GUI layout restoration
- Persistent device configuration
- Persistent plot configuration

### Logging
- All measurement logs must be stored as CSV files
- One CSV log file per calendar day
- Automatic daily log rotation at midnight
- File naming format:
  logs/YYYY-MM-DD_<device_name>.csv
- CSV headers must include:
  - timestamp
  - channel names
  - measurement units
- Logging must continue seamlessly across day changes
- Files must be flushed safely during runtime to minimize data loss on crash

---

## Must Not

- No hardcoded COM ports
- No hardcoded device addresses
- No global singleton device manager
- No GUI access directly to serial ports
- No external databases
- No polling from GUI widgets
- No unbounded memory growth for plots/logging

---

## Out of Scope

- Analog IO
- Digital relay control
- PID loops
- Recipe sequencing
- Web interface
- Distributed networking
- PLC integration
- Real-time OS support

---

# Threading Rules

- One worker thread per active device
- Serial communication occurs only inside worker threads
- GUI updates occur only in Qt main thread
- Inter-thread communication uses Qt signals/slots
- Device drivers are not thread-safe unless explicitly stated
- Acquisition engine owns all polling scheduling
- GUI must remain responsive during device failure or timeout

---

# Architecture

```text
+--------------------+
|      GUI Layer     |
|  plots / controls  |
+--------------------+
           ↑
           ↓ Qt signals/slots
+--------------------+
| Shared Data Model  |
| latest + history   |
+--------------------+
           ↑
           ↓ events
+--------------------+
| Acquisition Engine |
| polling/reconnect  |
+--------------------+
           ↑
           ↓
+--------------------+
|  Device Drivers    |
| serial protocols   |
+--------------------+
````

---

# Current State

## Project Root

```text
C:\Users\Robin\...\Sputter-Automation-2
```

## Existing Files

```text
Infos/
├── Spec template.md
└── JEVAmet VCU manual.pdf
```

## Current Hardware

First device to integrate:

* JEVAmet VCU vacuum pressure controller

No production Python code exists yet.

---

# JEVAmet VCU Protocol Summary

| Parameter        | Value                |
| ---------------- | -------------------- |
| Interface        | RS232 / RS485        |
| Baud rates       | 9600 / 19200 / 38400 |
| Data bits        | 8                    |
| Stop bits        | 1                    |
| Parity           | None                 |
| Flow control     | None                 |
| Encoding         | ASCII                |
| Delimiter        | Comma (0x2C)         |
| Termination      | <CR>                 |
| RS485 addressing | 1-126                |

---

# String Structure

```text
Write:
[Address] Command , [Parameter] <CR>

Response:
OK <CR>

Read:
[Address] Command <CR>

Response:
[Parameter] <CR>

Error:
? <TAB> X <TAB> [...]
```

Where:

* I = invalid command
* P = invalid parameter
* C = checksum error
* S = syntax error
* K = communication timeout

---

# Important VCU Commands

| Command  | Description           |
| -------- | --------------------- |
| RPV[a]   | Read pressure         |
| RID[a]   | Read sensor ID        |
| RVN      | Read firmware version |
| RSS      | Read setpoint status  |
| SHV[a,b] | HV on/off             |
| SDG[a,b] | Degas on/off          |
| SAC      | Save config           |

---

# Pressure Status Codes

| Code | Meaning            |
| ---- | ------------------ |
| 0    | OK                 |
| 1    | Below range        |
| 2    | Above range        |
| 3    | Err Lo             |
| 4    | Err Hi             |
| 5    | Sensor off         |
| 6    | HV on              |
| 7    | Sensor error       |
| 8    | BA error           |
| 9    | No sensor          |
| 10   | No trigger         |
| 11   | Pressure error     |
| 12   | Pirani error       |
| 13   | 24V error          |
| 15   | Filament defective |

---

# Sensor IDs

| ID | Sensor |
| -- | ------ |
| 0  | none   |
| 1  | Ptr    |
| 2  | ttr1   |
| 3  | ttr    |
| 4  | Ctr    |
| 5  | bA     |
| 6  | bEE    |
| 7  | At     |
| 8  | Ptr90  |
| 9  | du200  |
| 10 | du2000 |
| 11 | durEL  |

---

# Device Driver Requirements

Each device driver must expose:

* available channels
* units
* polling capabilities
* supported commands
* writable parameters
* connection status
* identification information

Device drivers should prefer composition over inheritance except for `BaseDevice`.

---

# Tasks

## T1 — Project Structure & BaseDevice

### What

Create:

* project structure,
* virtual environment setup,
* BaseDevice abstraction,
* serial management,
* reconnect logic,
* configuration loader.

### Files

```text
src/
├── main.py
├── config.py
├── devices/
│   ├── __init__.py
│   └── base_device.py
```

### Verify

```bash
python -c "from src.devices.base_device import BaseDevice; print('OK')"
```

---

## T2 — JEVAmet VCU Driver

### What

Implement:

* full protocol handling,
* pressure polling,
* pressure status,
* pressure units,
* timeout handling,
* reconnect handling,
* command parsing.

### Files

```text
src/devices/vcu_controller.py
tests/test_vcu_controller.py
```

### Verify

Try to connect to a VCU at COM6. Expected behavior: No device found.

---

## T3 — Acquisition Engine

### What

Implement:

* one worker thread per device,
* polling scheduler,
* reconnect handling,
* event publishing,
* timestamping.

### Files

```text
src/acquisition/
├── __init__.py
└── engine.py
```

### Verify

Mock-device integration test.

---

## T4 — Shared Data Model

### What

Implement:

* thread-safe measurement store,
* rolling buffers,
* event system,
* subscription mechanism.

### Files

```text
src/data/
├── __init__.py
└── datastore.py
```

### Verify

Multiple readers/writers operate safely.

---

## T5 — Main GUI Window

### What

Implement:

* main Qt window,
* dock system,
* toolbar,
* status bar,
* dark theme,
* persistent layouts.

### Files

```text
src/gui/
├── main_window.py
├── theme.py
└── dock_manager.py
```

### Verify

```bash
python src/main.py
```

GUI launches successfully.

---

## T6 — Plot Widget

### What

Implement:

* pyqtgraph plots,
* dynamic trace selection,
* rolling history,
* autoscaling,
* configurable channels.

### Files

```text
src/gui/plot_widget.py
src/gui/plot_config_dialog.py
```

### Verify

Live plot updates correctly.

---

## T7 — Device Panels

### What

Implement:

* live values,
* connection status,
* controls,
* per-device widgets.

### Files

```text
src/gui/device_panel.py
```

### Verify

Device values update in real time.

---

## T8 — Configuration Persistence

### What

Implement:

* JSON config loading,
* GUI state restore,
* COM-port persistence,
* plot persistence.

### Files

```text
src/config.py
config.json
```

### Verify

Restart restores layout and configuration.

---

## T9 — Data Logging

### What

Implement:

* CSV logging,
* daily rotation,
* metadata headers,
* buffered writes.

### Files

```text
src/logging/data_logger.py
```

### Verify

CSV logs contain correct timestamps and units.

---

## T10 — Device Manager

### What

Implement:

* central device registry,
* auto-detection,
* connection management,
* device discovery.

### Files

```text
src/devices/device_manager.py
```

### Verify

Detected devices appear automatically.

---

# Validation

## Functional

* GUI launches successfully
* Real VCU connects through USB-serial
* Live pressure plotting works
* Multiple plots update simultaneously
* Dynamic panel creation/removal works
* Device disconnect/reconnect handled gracefully
* Layout restores after restart
* CSV logging works continuously

---

## Performance

* GUI remains responsive at 10 Hz polling
* Supports minimum 5 simultaneous devices
* Plot latency < 200 ms
* No unbounded memory growth
* Stable during 24 h runtime

