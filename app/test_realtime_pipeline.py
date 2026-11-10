# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from event_bus import Shijianzongxian
from wechat_uia_reader import appended_items
from wx_reader import _resolve_msg_dir


class EventBusTests(unittest.TestCase):
    def test_structured_content_and_duplicate_guard(self):
        bus = Shijianzongxian(zuidarongliang=2)
        event = Shijianzongxian.xiaoxi_shijian(
            "wechat_db", "wxid_a", "客户A", "them", "报价|流程\n请说明",
            xiaoxi_id="101", chuangjian_shijian=10,
        )
        self.assertTrue(bus.fabu(event))
        self.assertFalse(bus.fabu(dict(event)))
        self.assertEqual(bus.paikong()[0]["content"], "报价|流程\n请说明")

    def test_queue_is_bounded_and_reports_drop(self):
        bus = Shijianzongxian(zuidarongliang=1)
        self.assertTrue(bus.fabu(Shijianzongxian.xiaoxi_shijian("clipboard", "manual", "客户", "them", "消息一", "1")))
        self.assertFalse(bus.fabu(Shijianzongxian.xiaoxi_shijian("clipboard", "manual", "客户", "them", "消息二", "2")))
        self.assertEqual(bus.zhuangtai()["dropped"], 1)


class ReaderDiscoveryTests(unittest.TestCase):
    def test_discovered_wx_dir_has_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "custom" / "wxid_test" / "Msg"
            (root / "Multi").mkdir(parents=True)
            with patch("wx_reader.Path.home", return_value=Path(tmp) / "home"):
                self.assertEqual(_resolve_msg_dir({"wx_dir": str(root.parent)}, "wxid_test"), str(root))


class UIASnapshotTests(unittest.TestCase):
    def test_identical_message_occurrence_is_appended(self):
        previous = [
            {"key": "them|好的|1", "identity": "a"},
        ]
        current = [
            {"key": "them|好的|1", "identity": "a"},
            {"key": "them|好的|2", "identity": "b"},
        ]
        self.assertEqual(appended_items(previous, current), [current[1]])

    def test_scrolled_snapshot_uses_suffix_overlap(self):
        previous = [{"key": key, "identity": ""} for key in ("a", "b", "c")]
        current = [{"key": key, "identity": ""} for key in ("b", "c", "d")]
        self.assertEqual(appended_items(previous, current), [current[2]])


if __name__ == "__main__":
    unittest.main()
