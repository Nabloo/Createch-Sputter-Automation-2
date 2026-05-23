"""Stateless utility for the INFICON SQM-160 serial packet protocol.

Implements the CRC14 checksum, command-packet building, and
response-packet parsing as defined in the SQM-160 Operating Manual
pp. 70–73.

Protocol summary:
  - RS-232, 8N1, 2 400–115 200 baud
  - Command packet:  ! <Length> <Message> <CRC1> <CRC2>
    Length = char_count + 34
  - Response packet: ! <Length> <Status> <Message> <CRC1> <CRC2>
    Length = char_count + 35 (note the different offset!)
  - CRC14: init 0x3FFF, XOR byte into LSB, shift-right 8x,
    conditional XOR 0x2001 if LSB was 1, mask 0x3FFF,
    split into CRC1 (bits 0–6 + 34) and CRC2 (bits 7–13 + 34).
"""

from typing import Tuple


class SQMProtocolError(RuntimeError):
    """Raised when the SQM-160 returns a protocol-level error."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def compute_crc14(data: bytes) -> Tuple[int, int]:
    crc: int = 0x3FFF
    for byte in data:
        crc = (crc ^ byte) & 0xFFFF
        for _ in range(8):
            cy: int = crc & 1
            crc = (crc >> 1) & 0xFFFF
            if cy:
                crc = crc ^ 0x2001
                crc = crc & 0xFFFF
    crc = crc & 0x3FFF
    crc1 = (crc & 0x7F) + 34
    crc2 = ((crc >> 7) & 0x7F) + 34
    return crc1, crc2


def build_command_packet(command: str) -> bytes:
    msg_bytes = command.encode("ascii")
    length_byte = len(msg_bytes) + 34
    crc_data = bytes([length_byte]) + msg_bytes
    crc1, crc2 = compute_crc14(crc_data)
    return b"!" + bytes([length_byte]) + msg_bytes + bytes([crc1, crc2])


def parse_response_packet(data: bytes) -> str:
    if not data:
        raise SQMProtocolError("Empty response packet")
    if data[0] != 0x21:
        raise SQMProtocolError(
            f"Missing sync character: expected '!' (0x21), got 0x{data[0]:02X}"
        )
    if len(data) < 5:
        raise SQMProtocolError(
            f"Response packet too short: {len(data)} bytes (min 5)"
        )
    length_byte = data[1]
    payload_char_count = length_byte - 35
    if payload_char_count < 1:
        raise SQMProtocolError(
            f"Invalid response length byte: {length_byte} "
            f"(implies payload of {payload_char_count} chars)"
        )
    expected_total = 1 + 1 + payload_char_count + 2
    if len(data) < expected_total:
        raise SQMProtocolError(
            f"Response too short: got {len(data)} bytes, "
            f"expected {expected_total} (payload={payload_char_count} chars)"
        )
    payload = data[2 : 2 + payload_char_count]
    crc_data = bytes([length_byte]) + payload
    expected_crc1, expected_crc2 = compute_crc14(crc_data)
    actual_crc1 = data[2 + payload_char_count]
    actual_crc2 = data[2 + payload_char_count + 1]
    if actual_crc1 != expected_crc1 or actual_crc2 != expected_crc2:
        raise SQMProtocolError(
            f"CRC mismatch: expected ({expected_crc1}, {expected_crc2}), "
            f"got ({actual_crc1}, {actual_crc2})"
        )
    payload_str = payload.decode("ascii", errors="replace")
    if not payload_str:
        raise SQMProtocolError("Empty response payload")
    status = payload_str[0]
    if status == "C":
        raise SQMProtocolError(f"SQM-160: Invalid command (status C) for payload_str: {payload_str}")
    if status == "D":
        raise SQMProtocolError("SQM-160: Problem with data in command (status D)")
    return payload_str
