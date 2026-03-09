import struct
import unittest

from protocol.keyboard_comm import Keyboard
from test.test_keyboard import SimulatedDevice, LAYOUT_2x2, s

class TestKeychron(unittest.TestCase):
    def prepare_keyboard(self, layout, keymap, keychron_features=0, keychron_misc=0):
        dev = SimulatedDevice()
        
        # Override sim_send to handle Keychron commands
        original_sim_send = dev.sim_send
        def keychron_sim_send(device, data, retries=1):
            if data[0] == 0xA0: # KC_GET_PROTOCOL_VERSION
                return struct.pack("BBH", 0xA0, 0x01, 0x0100) + b"\x00" * 28
            if data[0] == 0xA1: # KC_GET_FIRMWARE_VERSION
                return struct.pack("BB", 0xA1, 6) + b"1.0.0\x00" + b"\x00" * 24
            if data[0] == 0xA2: # KC_GET_SUPPORT_FEATURE
                # Features are at data[2] and data[3] (data[1] is unused padding)
                return struct.pack("<BBI", 0xA2, 0x00, keychron_features) + b"\x00" * 26
            if data[0] == 0xA7 and data[1] == 0x01: # MISC_GET_PROTOCOL_VER
                # data[3] and data[4] = version, data[5] and data[6] = misc features
                return struct.pack("<BBBHB", 0xA7, 0x01, 0x00, 0x0100, keychron_misc) + b"\x00" * 26
            
            # Additional stubs for reloads
            if data[0] == 0xA7 and data[1] == 0x02: # DFU_INFO_GET
                return struct.pack("<BBB", 0xA7, 0x02, 1) + b"\x00" * 29
            if data[0] == 0xA7 and data[1] == 0x05: # DEBOUNCE_GET
                return struct.pack("<BBBB", 0xA7, 0x05, 5, 0) + b"\x00" * 28
            if data[0] == 0xA7 and data[1] == 0x0D: # REPORT_RATE_GET
                return struct.pack("<BBH", 0xA7, 0x0D, 1000) + b"\x00" * 28
            if data[0] == 0xA7 and data[1] == 0x0B: # WIRELESS_LPM_GET
                return struct.pack("<BBB", 0xA7, 0x0B, 1) + b"\x00" * 29
            if data[0] == 0xA8 and data[1] == 0x01: # KC_KEYCHRON_RGB / RGB_GET_PROTOCOL_VER
                return struct.pack("<BBBH", 0xA8, 0x01, 0, 0x0100) + b"\x00" * 27
            if data[0] == 0xA8 and data[1] == 0x05: # RGB_GET_LED_COUNT
                return struct.pack("<BBB", 0xA8, 0x05, 1) + b"\x00" * 29
            if data[0] == 0xA8 and data[1] == 0x06: # RGB_GET_LED_INDEX
                # Return 0 for all indices
                return struct.pack("<BBB", 0xA8, 0x06, 0) + b"\x00" * 29
            if data[0] == 0xA8 and data[1] == 0x07: # PER_KEY_RGB_GET_TYPE
                return struct.pack("<BBB", 0xA8, 0x07, 1) + b"\x00" * 29
            if data[0] == 0xA8 and data[1] == 0x0B: # GET_INDICATORS_CONFIG
                return struct.pack("<BBB", 0xA8, 0x0B, 1) + b"\x00" * 29
            if data[0] == 0xA8 and data[1] == 0x03: # RGB_GET_MIXED_RGB_REGIONS
                return struct.pack("<BB", 0xA8, 0x03) + b"\x00" * 30
            if data[0] == 0xA9 and data[1] == 0x01: # KC_ANALOG_MATRIX / ANALOG_GET
                return struct.pack("<BBBBBBH", 0xA9, 0x01, 1, 0, 0, 0, 0) + b"\x00" * 25
            if data[0] == 0xA9 and data[1] == 0x0C: # ANALOG_SOCD_GET
                return struct.pack("<BBB", 0xA9, 0x0C, 0) + b"\x00" * 29
            if data[0] == 0xA9 and data[1] == 0x11: # ANALOG_CALIBRATION_GET
                return struct.pack("<BBB", 0xA9, 0x11, 0) + b"\x00" * 29
            if data[0] == 0xA9 and data[1] == 0x10: # ANALOG_PROFILE_GET_NAME
                return struct.pack("<BB", 0xA9, 0x10) + b"Profile\x00" + b"\x00" * 22
            if data[0] == 0xA9 and data[1] == 0x1B: # ANALOG_GET_REALTIME_TRAVEL
                return struct.pack("<BBH", 0xA9, 0x1B, 0) + b"\x00" * 28
            if data[0] == 0xA9 and data[1] == 0x20: # ANALOG_GET_ADC_CONFIG
                return struct.pack("<BBH", 0xA9, 0x20, 0) + b"\x00" * 28
            if data[0] == 0xA9 and data[1] == 0x22: # ANALOG_GET_ADVANCE_MODE_CLEAR
                return struct.pack("<BBB", 0xA9, 0x22, 0) + b"\x00" * 29
            
            # For Keychron SET commands (0xA7, 0xA8, 0xA9), simulate success by echoing cmd + subcmd + status 0
            # This prevents them from falling through to original_sim_send which might expect specific responses
            # or cause expect_idx mismatches for non-Keychron commands.
            if 0xA0 <= data[0] <= 0xA9:
                # For SET commands, a simple success response is often sufficient for testing the call itself
                # This assumes the command doesn't require a specific data payload back for success.
                # If a specific Keychron SET command needs a custom response, it should be added above.
                return struct.pack("BBB", data[0], data[1], 0) + b"\x00" * 29

            return original_sim_send(device, data, retries)
        
        dev.sim_send = keychron_sim_send
        
        dev.expect_via_protocol(9)
        dev.expect_keyboard_id(0)
        dev.expect_layout(layout)
        dev.expect_layers(len(keymap))

        # macro count
        dev.expect("0C", "0C00")
        # macro buffer size
        dev.expect("0D", "0D0000")

        dev.expect_keymap(keymap)

        kb = Keyboard(dev, dev.sim_send)
        # We attach a list of sent packets to kb for testing assertions
        kb._test_sent_pkts = []
        
        orig_usb_send = kb.usb_send
        def capturing_usb_send(device, data, retries=1):
            kb._test_sent_pkts.append(data)
            return orig_usb_send(device, data, retries)
        kb.usb_send = capturing_usb_send

        kb.reload()

        return kb, dev

    def test_keychron_feature_detection(self):
        # FEATURE_BLUETOOTH | FEATURE_ANALOG_MATRIX | FEATURE_KEYCHRON_RGB
        features = 0x02 | 0x08 | 0x80
        
        # MISC_DEBOUNCE | MISC_REPORT_RATE | MISC_DFU_INFO
        misc = 0x04 | 0x20 | 0x01
        
        kb, dev = self.prepare_keyboard(LAYOUT_2x2, [[[1, 2], [3, 4]]], features, misc)
        
        self.assertTrue(kb.has_keychron_features())
        self.assertTrue(kb.has_keychron_wireless())
        self.assertTrue(kb.has_keychron_analog())
        self.assertTrue(kb.has_keychron_rgb())
        
        self.assertTrue(kb.has_keychron_debounce())
        self.assertTrue(kb.has_keychron_report_rate())
        
        self.assertFalse(kb.has_keychron_snap_click())
        self.assertFalse(kb.has_keychron_nkro())


    def test_keychron_feature_detection_none(self):
        # No features
        features = 0
        misc = 0
        
        kb, dev = self.prepare_keyboard(LAYOUT_2x2, [[[1, 2], [3, 4]]], features, misc)
        
        self.assertFalse(kb.has_keychron_features())
        self.assertFalse(kb.has_keychron_wireless())
        self.assertFalse(kb.has_keychron_analog())
        self.assertFalse(kb.has_keychron_rgb())
        self.assertFalse(kb.has_keychron_report_rate())

    def test_keychron_misc_commands(self):
        features = 0x04 | 0x20 | 0x02 | 0x01 # DEBOUNCE, REPORT_RATE, WIRELESS, NKRO
        kb, dev = self.prepare_keyboard(LAYOUT_2x2, [[[1, 2], [3, 4]]], features, 0)
        dev.expect_data = [] # clear expectations so it doesn't try to match DUMMY
        
        kb._test_sent_pkts.clear()
        
        self.assertTrue(kb.set_keychron_debounce(2, 15))
        self.assertEqual(kb._test_sent_pkts[-1][:4], struct.pack("BBBB", 0xA7, 0x06, 2, 15))
        
        self.assertTrue(kb.set_keychron_nkro(True))
        self.assertEqual(kb._test_sent_pkts[-1][:3], struct.pack("BBB", 0xA7, 0x13, 1))
        
        self.assertTrue(kb.set_keychron_report_rate(2))
        self.assertEqual(kb._test_sent_pkts[-1][:3], struct.pack("BBB", 0xA7, 0x0E, 2))
        
        self.assertTrue(kb.set_keychron_wireless_lpm(600, 1800))
        self.assertEqual(kb._test_sent_pkts[-1][:6], struct.pack("<BBHH", 0xA7, 0x0C, 600, 1800))

    def test_keychron_snap_click(self):
        kb, dev = self.prepare_keyboard(LAYOUT_2x2, [[[1, 2], [3, 4]]], 0, 0x02) # MISC_SNAP_CLICK
        dev.expect_data = []
        # Force a dummy count (normally fetched on reload)
        kb.keychron_snap_click_count = 1
        
        kb._test_sent_pkts.clear()
        
        # entry_idx, snap_type, key1, key2
        self.assertTrue(kb.set_keychron_snap_click(0, 1, 0x0004, 0x0005))
        # pack fmt: "BBBBBBB"
        self.assertEqual(kb._test_sent_pkts[-1][:7], struct.pack("BBBBBBB", 0xA7, 0x09, 0, 1, 1, 0x04, 0x05))

    def test_keychron_rgb(self):
        features = 0x80 # KEYCHRON_RGB
        kb, dev = self.prepare_keyboard(LAYOUT_2x2, [[[1, 2], [3, 4]]], features, 0)
        dev.expect_data = []
        
        kb._test_sent_pkts.clear()
        
        self.assertTrue(kb.set_keychron_per_key_rgb_type(2))
        self.assertEqual(kb._test_sent_pkts[-1][:3], struct.pack("BBB", 0xA8, 0x08, 2))
        
        self.assertTrue(kb.set_keychron_per_key_color(0, 255, 128, 64))
        # Protocol packs "BBBBBBB" (CMD, SUBCMD, index, valid, h, s, v) -> (0xA8, 0x0A, 0, 1, 255, 128, 64)
        self.assertEqual(kb._test_sent_pkts[-1][:7], struct.pack("BBBBBBB", 0xA8, 0x0A, 0, 1, 255, 128, 64))
        
        self.assertTrue(kb.set_keychron_os_indicator_config(0x01, 128, 255, 128))
        self.assertEqual(kb._test_sent_pkts[-1][:6], struct.pack("BBBBBB", 0xA8, 0x04, 0x01, 128, 255, 128))
        
        self.assertTrue(kb.save_keychron_rgb())
        self.assertEqual(kb._test_sent_pkts[-1][:2], struct.pack("BB", 0xA8, 0x02))

    def test_keychron_analog_matrix(self):
        features = 0x08 # KEYCHRON_ANALOG
        kb, dev = self.prepare_keyboard(LAYOUT_2x2, [[[1, 2], [3, 4]]], features, 0)
        dev.expect_data = []
        
        kb._test_sent_pkts.clear()
        
        # Test global analog travel setting
        # profile (0), mode (1 - Regular), act_pt (20), sens (3), rls_sens (3), entire (True=1)
        self.assertTrue(kb.set_keychron_analog_travel(0, 1, 20, 3, 3, entire=True))
        self.assertEqual(kb._test_sent_pkts[-1][:9], struct.pack("BBBBBBBBB", 0xA9, 0x14, 0, 1, 20, 3, 3, 1, 0))
        
        # Test DKS advance mode setting
        # profile, row, col, okmc_index, shallow_act, shallow_deact, deep_act, deep_deact, keycodes, actions
        keycodes = [0x0004, 0x0005, 0x0006, 0x0007]
        actions = [{"shallow_act": 1, "shallow_deact": 2, "deep_act": 3, "deep_deact": 4}] * 4
        self.assertTrue(kb.set_keychron_analog_advance_mode_dks(0, 0, 0, 0, 10, 8, 30, 28, keycodes, actions))
        
        pkt = kb._test_sent_pkts[-1]
        self.assertEqual(pkt[0:4], struct.pack("BBBB", 0xA9, 0x15, 0, 1)) # ADV_MODE_OKMC=1
        
        # Test analog SOCD pair setting
        self.assertTrue(kb.set_keychron_analog_socd(0, 0, 1, 0, 2, 0, 1)) # SOCD_PRI_LAST=1
        self.assertEqual(kb._test_sent_pkts[-1][:9], struct.pack("BBBBBBBBB", 0xA9, 0x16, 0, 0, 1, 0, 2, 0, 1))

        # Test selecting a profile
        self.assertTrue(kb.select_keychron_analog_profile(1))
        self.assertEqual(kb._test_sent_pkts[-1][:3], struct.pack("BBB", 0xA9, 0x11, 1))

