import struct
import unittest

from protocol.bridge import BridgeDevice, _xor_encode, WIRELESS_RAW_HID_XOR_KEY
from util import MSG_LEN

class MockHidDevice:
    def __init__(self):
        self.written_data = []
        self.responses = []

    def write(self, data):
        # the bridge sends b"\x00" + payload
        self.written_data.append(data[1:])
        return len(data)

    def read(self, size, timeout_ms=None):
        if not self.responses:
            return b""
        return self.responses.pop(0)


class TestBridgeDevice(unittest.TestCase):
    def setUp(self):
        self.mock_hid = MockHidDevice()
        self.bridge = BridgeDevice(self.mock_hid)

    def test_bridge_initialize_success(self):
        # 1. FR_GET_PROTOCOL_VERSION (0xB1)
        # response: [0xB1, version_low, version_high, features0, features1]
        proto_resp = struct.pack("BBB", 0xB1, 0x01, 0x00) + b"\x01\x00"
        proto_resp += b"\x00" * (MSG_LEN - len(proto_resp))
        
        # 2. FR_GET_STATE (0xB2)
        # response: [0xB2, dummy, slot0_vid(2), slot0_pid(2), slot0_status(1), ...]
        state_resp = bytearray(b"\x00" * MSG_LEN)
        state_resp[0] = 0xB2
        # Slot 0: connected Keyboard with VID 0x1234 PID 0x5678
        state_resp[2:4] = struct.pack("<H", 0x1234)
        state_resp[4:6] = struct.pack("<H", 0x5678)
        state_resp[6] = 1 # connected
        
        # Slot 1: disconnected
        state_resp[7:9] = struct.pack("<H", 0x0000)
        state_resp[9:11] = struct.pack("<H", 0x0000)
        state_resp[11] = 0 # disconnected
        
        # 3. FR_GET_FW_VERSION (0xB3)
        fw_resp = struct.pack("B", 0xB3) + b"1.2.3"
        fw_resp += b"\x00" * (MSG_LEN - len(fw_resp))

        self.mock_hid.responses = [proto_resp, state_resp, fw_resp]

        success = self.bridge.initialize()
        self.assertTrue(success)
        self.assertEqual(self.bridge.protocol_version, 1)
        self.assertEqual(len(self.bridge.slots), 3)
        self.assertEqual(self.bridge.slots[0].vid, 0x1234)
        self.assertEqual(self.bridge.slots[0].pid, 0x5678)
        self.assertTrue(self.bridge.slots[0].connected)
        
        self.assertEqual(self.bridge.slots[1].vid, 0)
        self.assertEqual(self.bridge.slots[1].pid, 0)
        self.assertFalse(self.bridge.slots[1].connected)
        
        self.assertEqual(self.bridge.firmware_version, "1.2.3")
        self.assertTrue(self.bridge.has_connected_device())

    def test_bridge_usb_send_xor_encoding(self):
        # Mock initialization to simulate a connected device
        self.bridge.slots = []
        # Create a mock slot object that has 'connected' attribute
        class MockSlot:
            connected = True
            vid = 0x1111
            pid = 0x2222
        mock_slot = MockSlot()
        self.bridge.slots.append(mock_slot)
        self.bridge.connected_slot = mock_slot

        # Now test usb_send
        via_cmd = b"\x01\x02\x03"
        via_cmd_padded = via_cmd + b"\x00" * (MSG_LEN - len(via_cmd))
        
        # The bridge device should XOR the return value
        mock_response = b"\x04\x05\x06" + b"\x00" * (MSG_LEN - 3)
        xor_response = _xor_encode(mock_response)
        self.mock_hid.responses = [xor_response]

        # Call usb_send
        result = self.bridge.usb_send(via_cmd)

        # 1. Did it send the correct XOR encoded data?
        sent_data = self.mock_hid.written_data[0]
        expected_sent_data = _xor_encode(via_cmd_padded)
        self.assertEqual(sent_data, expected_sent_data)

        # 2. Did it correctly XOR decode the response?
        self.assertEqual(result, mock_response)
        
    def test_bridge_usb_send_fails_without_device(self):
        # Empty slots means no connected device
        self.bridge.connected_slot = None
        with self.assertRaises(RuntimeError) as ctx:
            self.bridge.usb_send(b"\x01")
        self.assertEqual(str(ctx.exception), "No device connected to bridge")

