# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from wechat_uia_reader import WeChatUIAReader


class UIACapabilityTests(unittest.TestCase):
    @patch("wechat_uia_reader.win32gui.GetWindowRect", return_value=(0, 0, 1000, 800))
    @patch("wechat_uia_reader.win32gui.IsIconic", return_value=False)
    @patch("wechat_uia_reader.win32gui.IsWindowVisible", return_value=True)
    @patch("wechat_uia_reader.win32gui.IsWindow", return_value=True)
    @patch("wechat_uia_reader.win32process.GetWindowThreadProcessId", return_value=(1, 2))
    @patch("wechat_uia_reader.is_wechat_process", return_value=True)
    def test_window_without_message_controls_is_unavailable(self, *_mocks):
        reader = WeChatUIAReader()
        reader._uia = object()
        reader._dependency = "available"
        reader.set_target_getter(lambda: 123)
        with patch.object(reader, "_extract", return_value=[]):
            self.assertFalse(reader.available())
        self.assertEqual(reader.status()["reason_code"], "UIA_CHAT_TEXT_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
