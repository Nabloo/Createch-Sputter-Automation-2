# Eurotherm 3504 Temperature Controller — Modbus TCP Driver

## Why

Currently the system supports the VCU pressure controller (RS-232) and the
SQM-160 QCM (RS-232). The Eurotherm 3504 is the third monitoring device in
the sputtering setup — it measures process temperature. Adding it lets
operators correlate temperature curves alongside pressure (VCU) and
deposition rate/thickness (SQM-160), all in real time.

The Eurotherm communicates over **Modbus TCP (Ethernet)** — a fundamentally
different transport from serial. This makes it the first network-based device
in the system.

## What

A new **read-only** device driver that speaks Modbus TCP via `pymodbus`.
The driver polls holding register 1 (slave 255) to read the process
temperature as a 16-bit signed integer scaled by 10, and exposes it as a
plottable channel.

## Constraints

### Must

- Implement the same duck-type interface as `BaseDevice`: `device_id`,
  `plot_channels`, `status_channels`, `channel_units`, `connected`,
  `connect()`, `disconnect()`, `poll()`, `start_reconnect_loop()`,
  `stop_reconnect_loop()`.
- Use `pymodbus` (already used in `manual_test.py`). Add it to
  `requirements.txt`.
- Follow the VCU pattern: config-driven construction, auto-verification on
  connect, reconnect loop, `poll()` → flat dict.
- Integrate into the existing factory in `device_manager.py`
  (`_DEVICE_TYPE_MAP`).
- Register in `src/devices/__init__.py`.

### Must Not

- Don't modify `BaseDevice` core logic. The Eurotherm controller must
  **extend BaseDevice** and override its serial-specific methods
  (`connect()`, `disconnect()`) — this reuses the reconnect worker,
  connection-state tracking, and threading infrastructure.
- Don't break the existing VCU or SQM drivers or the acquisition pipeline.
- No write/tuning commands — read-only for V1.
- No new Python dependencies beyond `pymodbus`.

### Out of Scope

- Ramping, setpoint control, or any write commands.
- Multi-variable reads (just temperature for V1).
- DevicePanel redesign for Ethernet fields (IP/host/slave) — the existing
  COM port / baudrate UI will be inherited as-is for now (the panel
  gracefully degrades).

### Architecture Decision: Why Extend BaseDevice

| Option | Pros | Cons |
|--------|------|------|
| **A: Extend BaseDevice** | Reuses `_reconnect_worker`, `_lock`, `_connected`/`_running` state, `start_reconnect_loop()`/`stop_reconnect_loop()` — proven, tested | Must inject dummy values for `config["port"]` and serial params that `BaseDevice.__init__` expects; `_serial` attribute is unused |
| B: Standalone class | Clean, no serial baggage | Must reimplement reconnect loop, threading, connection-state management (~60 lines duplicated) |
| C: New BaseEthernetDevice | Clean separation for future Ethernet devices | Premature abstraction — only one Ethernet device exists |

**Decision: Option A.** The Eurotherm controller extends `BaseDevice` and
overrides `connect()` / `disconnect()`. The `__init__` injects
`config["port"] = f"{host}:{modbus_port}"` before calling
`super().__init__()` so BaseDevice's serial-param extraction doesn't crash.
The `_serial` attribute sits unused (`connect()` stores the Modbus client
on `self._client` instead).

## Current State

### Existing Device Architecture

| File | Role |
|------|------|
| `src/devices/base_device.py` | Abstract base: serial open/close, reconnect loop, `_write`/`_readline`/`_send_command`, thread safety |
| `src/devices/vcu_controller.py` | Read-only RS-232 driver for VCU pressure controller. ASCII line protocol. |
| `src/devices/sqm_controller.py` | Read-only RS-232 driver for SQM-160. Binary packet protocol, overrides `_send_command`. |
| `src/devices/device_manager.py` | Creates devices from config via `_DEVICE_TYPE_MAP`, wires to engine/datastore/plots |
| `src/config.py` | `config.json` loader/saver; `get_device_configs`, `DEFAULT_CONFIG` template |
| `tests/manual_test.py` | Already contains working Modbus TCP code for Eurotherm (`192.168.117.30:502`, reg 1, slave 255) |

### Existing Pattern: VCUController

```python
class VCUController(BaseDevice):
    def __init__(self, config): ...           # reads config, sets self._address, self._pressure_unit
    @property
    def device_id(self) -> str: ...           # "VCU-0"
    @property
    def plot_channels(self) -> list[str]: ... # ["ch1_pressure", ...]
    @property
    def status_channels(self) -> list[str]: ...# ["ch1_pressure", "ch1_status_text", ...]
    @property
    def channel_units(self) -> Dict: ...      # {"ch1_pressure": "mbar", ...}
    def poll(self) -> Dict[...]: ...          # called by AcquisitionEngine
    def _after_connect(self): ...             # read sensor ID, firmware, pressure unit
```

The Eurotherm driver follows the same interface — `plot_channels`,
`status_channels`, `poll()`, `_after_connect()` — but replaces serial I/O
with Modbus TCP calls.

### Modbus Protocol (from Eurotherm 3504 Manual + manual_test.py)

**Transport:** Modbus TCP over Ethernet, port 502.

**Register map (temperature):**

- Function: Read Holding Registers (FC 03)
- Address: 1 (offset 1 in the controller's address space)
- Count: 1 register (16-bit)
- Slave/Unit ID: 255
- Data format: 16-bit signed integer, scaled x0.1 (register value / 10.0 = deg C)
- Example: register value 425 -> 42.5 deg C; register value -50 -> -5.0 deg C

**Signed conversion** (from `manual_test.py`):

```python
raw = result.registers[0]
if raw > 32767:       # negative in 16-bit two's complement
    raw -= 65536
temperature = raw / 10.0
```

### Channel Model

The Eurotherm reports one temperature value. The driver exposes:

| Channel key | Description | Unit |
|-------------|-------------|------|
| `ch1_temperature` | Process temperature | deg C |

## Tasks

### T1 — EurothermController Class (Construction + Connection)

**What:** Create `EurothermController(BaseDevice)` with Modbus TCP
connection management, overriding the serial-specific parts of `BaseDevice`.

**Details:**

- `__init__(config)`:
  - Injects `config["port"] = f"{host}:{modbus_port}"` so
    `super().__init__()` succeeds.
  - Reads `"host"` (default `"192.168.117.30"`), `"modbus_port"` (default
    `502`), `"slave_id"` (default `255`), `"number_of_sensors"` (default
    `1`).
  - Stores `self._address: int`, `self._unit: str = "deg C"`,
    `self._client: Optional[ModbusTcpClient] = None`.
  - Builds channel list from `number_of_sensors`.
- `device_id` property: returns `f"Eurotherm-{self._address}"`.
- `plot_channels` property: e.g. `["ch1_temperature"]`.
- `status_channels` property: e.g. `["ch1_temperature"]`.
- `channel_units` property: e.g. `{"ch1_temperature": "deg C"}`.
- `connect()` -> override entirely:
  - Creates `ModbusTcpClient(host, port=modbus_port)`, calls
    `client.connect()`.
  - Stores client on `self._client`, sets `self._connected = True`.
  - Calls `self._after_connect()` for verification.
  - Returns `True` on success, `False` on failure.
- `disconnect()` -> override entirely:
  - Closes `self._client`, sets `self._connected = False`.
- `_after_connect()`:
  - Reads temperature once (register 1) to verify communication.
  - Logs firmware/info if available (may be N/A via Modbus — gracefully
    skip).

**Files:** `src/devices/eurotherm_controller.py`

**Verify:** `python -m unittest tests.test_eurotherm_controller -v`

- Test `device_id` returns `"Eurotherm-0"`.
- Test `plot_channels` returns `["ch1_temperature"]`.
- Test `connect()` with mocked `ModbusTcpClient` sets `connected = True`.
- Test `disconnect()` closes client and sets `connected = False`.

---

### T2 — Implement poll() Using Modbus Read Holding Registers

**What:** Implement `poll()` to read register 1 via Modbus TCP, apply signed
conversion, and return a flat dict.

**Details:**

- `poll() -> Dict[str, Any]`:
  - Checks `self._client` is connected, raises `ConnectionError` if not.
  - Calls `self._client.read_holding_registers(address=1, count=1,
    slave=self._slave_id)`.
  - Applies signed 16-bit conversion (same logic as `manual_test.py`):
    ```python
    raw = result.registers[0]
    if raw > 32767:
        raw -= 65536
    temperature = raw / 10.0
    ```
  - Returns `{"ch1_temperature": temperature}`.
  - Returns `float("nan")` on error so the plot shows a gap.
- Lets `ModbusException`, `ConnectionException` propagate for the engine's
  error handler.

**Files:** `src/devices/eurotherm_controller.py`

**Verify:** `python -m unittest tests.test_eurotherm_controller -v`

- Mock `read_holding_registers` returning `registers=[425]` -> `poll()`
  returns `42.5`.
- Mock returning `registers=[0]` -> `poll()` returns `0.0`.
- Mock returning `registers=[65486]` (which is -50 in signed 16-bit) ->
  `poll()` returns `-5.0`.
- Test that `ConnectionError` is raised when client is not connected.
- Test that `float("nan")` is returned when `read_holding_registers` raises
  `ModbusException`.

---

### T3 — Device Manager Integration

**What:** Wire `EurothermController` into the device factory and config so it
can be instantiated from `config.json`.

**Details:**

- In `src/devices/device_manager.py`:
  - Add `from src.devices.eurotherm_controller import EurothermController`.
  - Add `"EurothermController": EurothermController` to `_DEVICE_TYPE_MAP`.
- In `src/config.py`:
  - Add a Eurotherm entry to `DEFAULT_CONFIG` under `"devices"`:
    ```json
    {
      "type": "EurothermController",
      "device_id": "Eurotherm-0",
      "address": 0,
      "host": "192.168.117.30",
      "modbus_port": 502,
      "slave_id": 255,
      "timeout": 1.0,
      "reconnect_interval": 3.0,
      "max_retries": 5,
      "number_of_sensors": 1,
      "poll_interval": 1.0,
      "unit": "deg C"
    }
    ```
- In `src/devices/__init__.py`:
  - Add `from .eurotherm_controller import EurothermController` and add to
    `__all__`.

**Files:** `src/devices/device_manager.py`, `src/config.py`,
`src/devices/__init__.py`

**Verify:** Launch app -> Eurotherm appears in device list. Add a plot ->
`ch1_temperature` appears as a selectable channel.

---

### T4 — Add pymodbus to requirements

**What:** Add `pymodbus` to `requirements.txt`.

**Files:** `requirements.txt`

**Verify:** `pip install -r requirements.txt` succeeds (or
`python -c "import pymodbus"`).

---

### T5 — Unit Tests

**What:** Comprehensive unit tests for the Eurotherm controller.

**Details:**

- Test `device_id`, `plot_channels`, `status_channels`, `channel_units` for
  default and multi-sensor configs.
- Test `connect()` with mock `ModbusTcpClient` — client created, `connected`
  set, `_after_connect` called.
- Test `disconnect()` — client closed, `connected` cleared.
- Test `poll()` with various register values (positive, zero, negative,
  max).
- Test `poll()` error handling (disconnected, Modbus exception, timeout).
- Test `_after_connect()` verification read.
- Test multi-sensor config (e.g. `number_of_sensors=2` -> 2 temperature
  channels).

**Files:** `tests/test_eurotherm_controller.py`

**Verify:** `python -m unittest tests.test_eurotherm_controller -v` -> all
tests pass.

---

### T6 — Hardware Test

**What:** Verify with the real Eurotherm 3504 hardware.

**Details:**

- Use the existing `tests/manual_test.py` code as a verification script.
- Extend it to instantiate `EurothermController` from a config dict, call
  `connect()`, `poll()`, and print results.

**Files:** `tests/test_eurotherm_hardware.py` (similar to
`tests/test_vcu_hardware.py`)

**Verify:** Run against the actual Eurotherm at 192.168.117.30:502 ->
temperature value printed matches the controller's front panel.

---

## Validation

1. `python -m unittest tests.test_eurotherm_controller tests.test_vcu_controller tests.test_sqm_controller tests.test_acquisition_engine tests.test_datastore -v` -> all tests pass.
2. `python src/main.py` -> GUI starts without errors, Eurotherm appears in device list.
3. Connect to Eurotherm -> temperature value appears in device panel.
4. Start acquisition -> `ch1_temperature` plots in real time.
5. Disconnect Ethernet -> reconnection loop activates, no crash.
6. Reconnect -> data resumes automatically.
7. All existing devices (VCU, SQM) unaffected.
