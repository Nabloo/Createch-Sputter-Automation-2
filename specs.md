# INFICON SQM-160 QCM Deposition Monitor – Read-Only Serial Driver

## Why

The SQM-160 quartz crystal microbalance monitors the rate, thickness, and
frequency of deposition processes on up to six sensor channels. Currently only
the VCU pressure controller is supported. Adding the SQM-160 lets operators
correlate pressure curves (VCU) with deposition data (SQM-160) side by side
in real time — rate drops, thickness plateaus, and crystal health all become
visible in the existing plot panels.

## What

A new **read-only** device driver that speaks the INFICON binary-like packet
protocol over RS-232. The driver polls all configured sensor channels with a
single `W` command and exposes rate, thickness, and frequency as named
channels to `DataStore`, making them plottable immediately.

## Constraints

### Must

- Inherit from `BaseDevice` and fit the existing `config.json` → device
  factory → `DeviceManager` pattern used by `VCUController`.
- Implement the full SQM-160 packet protocol:
  - Sync: `!` (ASCII 33)
  - Command packet length: `char_count + 34`
  - Response packet length: `char_count + 35`
  - CRC14 with init `0x3FFF`, XOR-each-byte, 8×shift-right, conditional XOR
    `0x2001`, mask `0x3FFF`, split into CRC1 (bits 0–6 + 34) and CRC2
    (bits 7–13 + 34).
- Poll via the `W` command (one packet → all six channels' rate, thickness,
  frequency) — not per-channel `L`/`N`/`P` commands.
- Parse the `W` response correctly (note: first `00.00` value in the response
  is a dummy and must be ignored, per the manual).
- RS-232, 8 data bits, 1 stop bit, no parity. Default baud 19 200,
  configurable 2 400–115 200.
- Handle timeout / bad CRC / invalid-command responses with custom exceptions
  and automatic reconnection via `BaseDevice._reconnect_worker`.

### Must Not

- No control commands (shutter, zero, parameter updates) — read-only for V1.
- Don't modify `BaseDevice` core logic. The SQM-160 does **not** use
  ASCII + `\r` + readline; the driver must bypass `_send_command` and
  use `_write` / `_serial.read()` directly.
- No new Python dependencies (use `struct` and `pyserial`; CRC is
  self-contained).
- Don't break the existing VCU driver or acquisition pipeline.

### Out of Scope

- Setting film parameters, active film selection, or any write/update
  commands (`A`, `B`, `C`, `D`, `Z`).
- Shutter control (`U`), zeroing (`S`, `T`), or relay I/O.
- Ethernet / USB connectivity (RS-232 only for V1).
- Simulate mode or etch-mode support.
- Multiple SQM-160 units on the same serial bus (one device per port).

## Current State

### Existing Device Architecture

| File | Role |
|------|------|
| `src/devices/base_device.py` | Abstract base: serial open/close, reconnect loop, `_write`, thread safety. |
| `src/devices/vcu_controller.py` | Read-only RS-232 driver for VCU pressure controller. ASCII line protocol, `_send_command`. |
| `src/devices/device_manager.py` | Creates devices from config entries, wires them to plots and `DataStore`. |
| `src/config.py` | `config.json` loader/saver; `get_device_configs`, `find_device_config`. |
| `src/data/datastore.py` | Central data hub; subscribes plots to device channels. |
| `src/acquisition/engine.py` | Calls `device.poll()` on a timer, pushes results to `DataStore`. |

### Existing Pattern: VCUController

```python
class VCUController(BaseDevice):
    def __init__(self, config): ...       # reads config dict
    @property
    def device_id(self) -> str: ...       # "VCU-0"
    @property
    def channels(self) -> list[str]: ...  # ["pressure", "status_code", ...]
    def poll(self) -> Dict[...]: ...      # called by AcquisitionEngine
    def _after_connect(self): ...         # read sensor ID, firmware
```

The SQM driver follows the same interface — `channels`, `poll()`,
`_after_connect()` — but replaces `_send_command`'s ASCII-line logic with
binary packet framing.

### SQM-160 Protocol Summary (from Operating Manual pp. 63–83)

**Physical layer:** RS-232, 8N1, 9-pin female D-sub (DCE). Default 19 200 bps.

**Command packet** (host → instrument):
```
! <Length> <Message> <CRC1> <CRC2>
```
- `!` = Sync (ASCII 33), resets packet framing
- `Length` = `len(message_bytes) + 34` (char)
- `Message` = ASCII command string (e.g. `W`, `@`, `A1?`)
- `CRC1`, `CRC2` = 14-bit CRC split into two 7-bit halves, each + 34

**Response packet** (instrument → host):
```
! <Length> <Status> <Message> <CRC1> <CRC2>
```
- `Length` = `len(status_byte + message_bytes) + 35` (note: +35, not +34!)
- `Status`: `A` = OK, `C` = invalid command, `D` = bad data
- Message may include leading `A` (the status echoed in the payload — see examples)

**CRC14 algorithm:**
1. Init CRC register (16-bit) to `0x3FFF`
2. For each byte in `Length + Message` (not Sync, not CRCs):
   - XOR byte with CRC's LSB, store back in LSB
   - Repeat 8 times:
     - Save LSB of CRC as `Cy`
     - Shift CRC right 1 bit, 0 into MSB
     - If `Cy == 1`: CRC = CRC XOR `0x2001`
3. CRC = CRC AND `0x3FFF` (keep 14 bits)
4. CRC1 = `(CRC & 0x7F) + 34` (bits 0–6)
5. CRC2 = `((CRC >> 7) & 0x7F) + 34` (bits 7–13)

**Key commands for monitoring:**

| Cmd | Type | Description | Example Response |
|-----|------|-------------|-----------------|
| `@` | Query | Firmware version | `AMON_Ver_4.13` |
| `J` | Query | Number of channels (2 or 6) | `A6` |
| `W` | Status | Rate, thickness, frequency for all 6 sensors | `A00.00_07.10_3076.190_5497894.642_...` |
| `L` | Status | Rate for one sensor | `A_0.00_` |
| `N` | Status | Thickness for one sensor | `A_0.000_` |
| `P` | Status | Frequency for one sensor | `A5500110.056` |

**W command response format** (the preferred polling command):
```
A<dummy>_<ch1_rate>_<ch1_thickness>_<ch1_freq>_<ch2_rate>_<ch2_thickness>_<ch2_freq>_..._<ch6_rate>_<ch6_thickness>_<ch6_freq>
```
- The first `00.00` after `A` is a dummy — ignore it (manual says so).
- Rate: `Å/s` or `nm/s` depending on display mode (units from System 1 params).
- Thickness: `kÅ` or `µm` depending on display mode.
- Frequency: `Hz` with up to 3 decimal places.
- Underscore (`_`) is the separator.
- Inactive channels return `00.00_00.000_<freq>` (frequency is always reported).

### Channel Model

The SQM-160 provides up to 6 sensor channels. Each sensor reports three
values: **rate**, **thickness**, and **frequency**. The driver exposes these
as flat named channels:

| Channel key | Description | Typical unit |
|-------------|-------------|-------------|
| `ch1_rate` | Sensor 1 deposition rate | Å/s |
| `ch1_thickness` | Sensor 1 accumulated thickness | kÅ |
| `ch1_frequency` | Sensor 1 crystal frequency | Hz |
| `ch2_rate` | Sensor 2 deposition rate | Å/s |
| … | … | … |
| `chN_rate` | Sensor N rate (N = 1..`number_of_sensors`) | Å/s |

The number of active sensors is configurable (default 2, max 6).

## Tasks

### T1 — SQM Protocol Utility Module

**What:** Standalone stateless functions for CRC14 calculation, packet
building, and packet parsing. This is pure logic with no serial I/O, making
it easy to unit-test against the manual's examples.

**Details:**
- `compute_crc14(data: bytes) -> Tuple[int, int]`:
  - Implements the CRC14 algorithm exactly as specified (0x3FFF init,
    XOR-each-byte, 8-bit shift loop, conditional 0x2001 XOR, 0x3FFF mask,
    split into two 7-bit halves with +34 offset).
  - Returns `(crc1, crc2)` as integers in range 34–161.
  - Must reproduce the CRC values from the manual's command examples
    (e.g. `!#J(79)(56)` → CRCs are 79 and 56).
- `build_command_packet(command: str) -> bytes`:
  - Encodes command as ASCII bytes.
  - Computes Length = `len(command_bytes) + 34`.
  - Builds: `b"!" + bytes([length]) + command_bytes + bytes([crc1, crc2])`.
  - Returns the complete byte packet ready to write to serial.
- `parse_response_packet(data: bytes) -> str`:
  - Validates Sync (`!`), extracts Length (expected = `char_count + 35`).
  - Extracts and validates CRC.
  - Raises `SQMProtocolError` on CRC mismatch, invalid status, or framing
    errors.
  - Returns the response payload string (everything between Length and CRC,
    including the status character).

**Files:** `src/devices/sqm_protocol.py`, `tests/test_sqm_protocol.py`

**Verify:** `python -m unittest tests.test_sqm_protocol -v`
- CRC must match at least 3 examples from the manual.
- Packet built for `J` must be `b"!#J(79)(56)"` (or whatever CRCs the
  algorithm computes — must match the manual's example).
- Parse the manual's example `W` response → correct payload extracted.

---

### T2 — SQMController Class (Construction + I/O)

**What:** Create `SQMController(BaseDevice)` with serial read/write
overriding the ASCII-line pattern. Uses the packet utility from T1.

**Details:**
- `__init__(config)`:
  - Calls `super().__init__(config)`.
  - Reads `"number_of_sensors"` (default 2, max 6) from config.
  - Builds `self._channels` list (e.g. `["ch1_rate", "ch1_thickness",
    "ch1_frequency", "ch2_rate", ...]`).
  - Initializes `self._version` and `self._num_channels` caches.
- `device_id` property: returns `f"SQM-{config['address']}"` (address
  defaults to 0, like VCU pattern).
- `channels` property: returns the flat channel list.
- `_sqm_send(command: str, timeout: float = 2.0) -> str`:
  - Builds packet via `build_command_packet(command)`.
  - Calls `self._write(packet)` (from BaseDevice).
  - Reads response using a robust byte-by-byte reader:
    1. Read until `!` sync byte (or timeout).
    2. Read 1 byte for Length → compute `expected = (length_byte - 35)`.
    3. Read `expected` bytes (message + status).
    4. Read 2 bytes for CRC.
  - Passes the complete frame to `parse_response_packet`.
  - Returns the payload string.
  - Raises `SQMProtocolError` on CRC mismatch or invalid status; lets
    `TimeoutError` / `ConnectionError` propagate for the reconnect loop.
- `_after_connect()`:
  - Queries firmware version: `_sqm_send("@")` → log.
  - Queries number of channels: `_sqm_send("J")` → log.
  - Caches results.

**Files:** `src/devices/sqm_controller.py`

**Verify:** `python -m unittest tests.test_sqm_controller -v`
- With a mock serial, verify `_sqm_send("J")` writes the correct bytes and
  returns `"A6"` for a 6-channel instrument.
- Verify `_after_connect` populates `_version` and `_num_channels`.
- Verify graceful handling of a bad-CRC response (SQMProtocolError raised).

---

### T3 — Implement poll() Using the W Command

**What:** Implement `poll()` to fetch all sensor data with one `W` command,
parse the response, and return a flat `Dict[str, Any]`.

**Details:**
- `poll() -> Dict[str, Any]`:
  - Calls `resp = self._sqm_send("W")`.
  - Splits the payload on `_` (underscore). Example payload:
    `"A00.00_07.10_3076.190_5497894.642_07.10_3076.190_5498079.900_..."`
  - Skips the first value after `A` (the dummy `00.00` as noted in the
    manual, page 79).
  - Groups remaining values into triplets per sensor:
    `(rate, thickness, frequency)`.
  - For sensor _N_ (1-indexed), creates keys:
    - `ch{N}_rate` → float
    - `ch{N}_thickness` → float
    - `ch{N}_frequency` → float
  - Only includes channels up to `self._number_of_sensors`.
  - Returns the flat dict.
- The `poll()` contract requires the return dict to match `channels`
  membership (enforced by `DataStore`). All declared channels must be
  present.

**Files:** `src/devices/sqm_controller.py`

**Verify:** `python -m unittest tests.test_sqm_controller -v`
- Mock the `_sqm_send("W")` response with real data from the manual example.
- Assert `poll()` returns 6 channels × 3 values = 18 keys for a
  6-sensor config.
- Assert `poll()` returns 2 channels × 3 values = 6 keys for the default
  2-sensor config.
- Assert rate ≈ 7.10, thickness ≈ 3076.190, frequency ≈ 5497894.642 for
  the first sensor from the manual's example.

---

### T4 — Device Manager Integration

**What:** Wire `SQMController` into the device factory so it can be
instantiated from `config.json` entries.

**Details:**
- In `src/devices/device_manager.py` (or wherever the device factory lives):
  - Add import for `SQMController`.
  - Add a branch for `device_type == "sqm160"` (or similar key) that
    constructs `SQMController(config)`.
- Add a sample SQM-160 entry to the default config in `src/config.py`
  (commented out or as documentation — actual entry is user-supplied).
- The existing `AcquisitionEngine` already calls `device.poll()` and feeds
  `DataStore` — no changes needed there if `poll()` returns the right shape.

**Files:** `src/devices/device_manager.py`, maybe `src/config.py`

**Verify:** `python -m unittest tests.test_device_manager -v`
- Create a config with device type `"sqm160"` → `DeviceManager` instantiates
  an `SQMController`.
- Verify `engine.poll()` produces data that flows into `DataStore` (mock
  serial required).

---

### T5 — Unit Tests

**What:** Comprehensive unit tests for T1 (protocol) and T2–T3 (controller).

**Details for `tests/test_sqm_protocol.py`:**
- `test_crc_matches_manual_example_J` — CRC for `"J"` matches the
  manual's `(79, 56)`.
- `test_crc_matches_manual_example_at` — CRC for `"@"` matches `(79, 55)`.
- `test_crc_matches_manual_example_W` — CRC for `"W"` matches `(143, 53)`.
- `test_crc_matches_manual_example_L1` — CRC for `"L1?"` matches
  `(133, 123)`.
- `test_build_and_parse_roundtrip` — build a packet, parse it → same
  command.
- `test_parse_bad_crc_raises` — corrupt CRC byte → `SQMProtocolError`.
- `test_parse_invalid_status_raises` — status `"C"` → `SQMProtocolError`.
- `test_parse_missing_sync_raises` — no `!` → `SQMProtocolError`.

**Details for `tests/test_sqm_controller.py`:**
- `test_device_id` — returns `"SQM-0"` for address 0.
- `test_channels_default` — 2 sensors → 6 channel names.
- `test_channels_max` — 6 sensors → 18 channel names.
- `test_sqm_send_builds_correct_packet` — verify bytes written to mock
  serial.
- `test_poll_parses_w_response` — provide a mock `W` response, verify the
  returned dict.
- `test_poll_handles_nan` — some values might be non-numeric (simulate
  error), verify graceful handling.
- `test_after_connect_queries_version` — mock responses for `@` and `J`,
  verify cache populated.

**Files:** `tests/test_sqm_protocol.py`, `tests/test_sqm_controller.py`

**Verify:** `python -m unittest tests.test_sqm_protocol tests.test_sqm_controller -v`
→ all tests pass.

---

### T6 — Integration / Smoke Test

**What:** End-to-end verification with the full application.

**Details:**
- Create a `config.json` with an SQM-160 entry:
  ```json
  {
    "type": "sqm160",
    "device_id": "SQM-0",
    "port": "COM3",
    "baudrate": 19200,
    "number_of_sensors": 2
  }
  ```
- Launch the app → verify SQM-0 appears in the device list.
- Click **Connect All** → verify firmware version and channel count appear
  in the log.
- Add a plot for SQM-0 → verify `ch1_rate`, `ch1_thickness`,
  `ch1_frequency` appear as selectable channels.
- Click **Start** → verify data flows into the plot.
- Disconnect the serial cable → verify reconnection loop kicks in.
- Reconnect the cable → verify data resumes.

**Files:** `config.json` (user-managed, not committed)

**Verify:** Manual check with actual SQM-160 hardware if available; otherwise
mock-serial smoke test in offscreen mode.

## Validation

1. `python -m unittest tests.test_sqm_protocol tests.test_sqm_controller tests.test_vcu_controller tests.test_acquisition_engine tests.test_datastore tests.test_data_logger tests.test_log_viewer tests.test_log_reader -v` → all tests pass.
2. `python src/main.py` → GUI starts without errors.
3. Add an SQM-160 device via `config.json` → device appears in Device Manager.
4. Connect to the SQM-160 (or mock serial) → firmware version and channel count logged.
5. Start acquisition → SQM-160 channels appear in plots with real-time data.
6. Disconnect cable → reconnection loop activates, no crash.
7. Reconnect cable → data resumes automatically.
8. Switch to View Log mode → existing log-viewer functionality is unaffected.
