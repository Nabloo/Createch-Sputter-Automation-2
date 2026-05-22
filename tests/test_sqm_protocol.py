"""Unit tests for the SQM-160 protocol utility (CRC14, packet building, parsing)."""

import unittest

from src.devices.sqm_protocol import (
    SQMProtocolError,
    build_command_packet,
    compute_crc14,
    parse_response_packet,
)


class TestCRC14(unittest.TestCase):
    """Verify CRC14 against the SQM-160 manual examples (pp. 70-83)."""

    def test_crc_matches_manual_example_J(self):
        """!#J(79)(56) — Length=35, message='J'"""
        crc1, crc2 = compute_crc14(bytes([35, 74]))
        self.assertEqual((crc1, crc2), (79, 56))

    def test_crc_matches_manual_example_at(self):
        """!#@(79)(55) — Length=35, message='@'"""
        crc1, crc2 = compute_crc14(bytes([35, 64]))
        self.assertEqual((crc1, crc2), (79, 55))

    def test_crc_matches_manual_example_W(self):
        """!#W(143)(53) — Length=35, message='W'"""
        crc1, crc2 = compute_crc14(bytes([35, 87]))
        self.assertEqual((crc1, crc2), (143, 53))

    def test_crc_matches_manual_example_L1(self):
        """!%L1?(133)(123) — Length=37, message='L1?'"""
        crc1, crc2 = compute_crc14(bytes([37, 76, 49, 63]))
        self.assertEqual((crc1, crc2), (133, 123))

    def test_crc_different_inputs_different_outputs(self):
        """Different inputs should produce different CRC pairs."""
        crc_J = compute_crc14(bytes([35, 74]))
        crc_W = compute_crc14(bytes([35, 87]))
        self.assertNotEqual(crc_J, crc_W)


class TestBuildCommandPacket(unittest.TestCase):
    """Verify packet building matches manual examples."""

    def test_build_J_packet(self):
        """J command produces the expected bytes."""
        packet = build_command_packet("J")
        # ! (0x21)  # (0x23=35)  J (0x4A)  CRC1 (0x4F=79)  CRC2 (0x38=56)
        self.assertEqual(packet, bytes([0x21, 0x23, 0x4A, 0x4F, 0x38]))

    def test_build_W_packet(self):
        packet = build_command_packet("W")
        self.assertEqual(packet[0], 0x21)  # Sync
        self.assertEqual(packet[1], 35)     # Length = 1+34
        self.assertEqual(packet[2], 0x57)   # 'W'
        # CRC must be present
        self.assertEqual(len(packet), 5)

    def test_build_at_packet(self):
        packet = build_command_packet("@")
        self.assertEqual(packet[0], 0x21)  # Sync
        self.assertEqual(packet[1], 35)     # Length
        self.assertEqual(packet[2], 0x40)   # '@'
        self.assertEqual(packet[3], 79)     # CRC1
        self.assertEqual(packet[4], 55)     # CRC2

    def test_length_variable(self):
        """Longer commands produce proportionally larger Length bytes."""
        short = build_command_packet("J")
        long_cmd = build_command_packet("L1?")
        self.assertEqual(short[1], 35)   # 1+34
        self.assertEqual(long_cmd[1], 37)  # 3+34
        self.assertEqual(len(short), 5)
        self.assertEqual(len(long_cmd), 7)


class TestParseResponsePacket(unittest.TestCase):
    """Verify response parsing and error detection."""

    def _pack_response(self, payload: str) -> bytes:
        """Helper: build a valid response frame for a given payload."""
        payload_bytes = payload.encode("ascii")
        length_byte = len(payload_bytes) + 35  # response offset
        crc_data = bytes([length_byte]) + payload_bytes
        crc1, crc2 = compute_crc14(crc_data)
        return bytes([0x21, length_byte]) + payload_bytes + bytes([crc1, crc2])

    def test_parse_valid_response_J(self):
        """Parse a valid 'A6' response (6 channels)."""
        frame = self._pack_response("A6")
        result = parse_response_packet(frame)
        self.assertEqual(result, "A6")

    def test_parse_valid_firmware_response(self):
        """Parse a firmware version response."""
        frame = self._pack_response("AMON_Ver_4.13")
        result = parse_response_packet(frame)
        self.assertEqual(result, "AMON_Ver_4.13")

    def test_parse_valid_W_response(self):
        """Parse a realistic W command response."""
        payload = "A00.00_07.10_3076.190_5497894.642_07.10_3076.190_5498079.900"
        frame = self._pack_response(payload)
        result = parse_response_packet(frame)
        self.assertEqual(result, payload)

    def test_parse_bad_crc_raises(self):
        """Corrupt CRC byte raises SQMProtocolError."""
        frame = self._pack_response("A6")
        # Corrupt the last CRC byte
        corrupted = bytearray(frame)
        corrupted[-1] = (corrupted[-1] + 1) & 0xFF
        with self.assertRaises(SQMProtocolError):
            parse_response_packet(bytes(corrupted))

    def test_parse_invalid_status_C_raises(self):
        """Status 'C' (invalid command) raises SQMProtocolError."""
        frame = self._pack_response("C")
        with self.assertRaises(SQMProtocolError):
            parse_response_packet(frame)

    def test_parse_invalid_status_D_raises(self):
        """Status 'D' (bad data) raises SQMProtocolError."""
        frame = self._pack_response("D")
        with self.assertRaises(SQMProtocolError):
            parse_response_packet(frame)

    def test_parse_missing_sync_raises(self):
        """Frame without leading '!' raises SQMProtocolError."""
        frame = self._pack_response("A6")
        corrupted = bytearray(frame)
        corrupted[0] = 0x40  # '@' instead of '!'
        with self.assertRaises(SQMProtocolError):
            parse_response_packet(bytes(corrupted))

    def test_parse_empty_raises(self):
        """Empty input raises SQMProtocolError."""
        with self.assertRaises(SQMProtocolError):
            parse_response_packet(b"")

    def test_parse_too_short_raises(self):
        """Too-short frame raises SQMProtocolError."""
        with self.assertRaises(SQMProtocolError):
            parse_response_packet(b"!\x23")  # Only Sync + Length, no CRC

    def test_build_and_parse_roundtrip(self):
        """Build a command, simulate minimal response, parse it."""
        # Build a command, verify it starts with Sync
        cmd = build_command_packet("J")
        self.assertEqual(cmd[0], 0x21)
        # Build a matching response manually
        resp = self._pack_response("A6")
        result = parse_response_packet(resp)
        self.assertEqual(result, "A6")


if __name__ == "__main__":
    unittest.main()
