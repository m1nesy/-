import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import chatapp_detector
import kb_store
import main
import patent_search
import reranker
import suggester
from test_support import LinshiShujuTests


class EventHook:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self


class AppWorkflowTests(LinshiShujuTests):
    modules = (main, kb_store, patent_search, reranker, suggester)

    def setUp(self):
        super().setUp()
        self.api = main.Api(MagicMock())

    def test_api_recommends_and_patent_search_and_reload_work(self):
        result = self.api.suggest(["专利申请多少钱"])
        self.assertTrue(result["results"])
        self.assertNotIn("error", result)
        result = self.api.search_patents("", dingbu_N=2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["results"]), 2)
        result = self.api.reload_patent_kb()
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["total"], 0)

    def test_patent_directory_can_be_set_and_cleared(self):
        for value in (str(self.root / "certificates"), ""):
            result = self.api.set_patent_dirs(value)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["patent_base"], value)

    def test_clipboard_monitor_survives_multiple_toggles(self):
        monitor = MagicMock()
        self.api._jiantie_jiantingqi = monitor
        for enabled in (True, False, True):
            result = self.api.set_clipboard_listen(enabled)
            self.assertTrue(result["ok"])
            self.assertEqual(result["enabled"], enabled)
            self.assertIs(self.api._jiantie_jiantingqi, monitor)
        self.assertEqual(monitor.set_enabled.call_count, 3)
        with patch("main.autostart_get", return_value=False):
            self.assertTrue(self.api.get_status()["clipboard_listening"])

    def test_clipboard_and_wechat_messages_reach_event_bus(self):
        with patch("main.time.time", return_value=123):
            self.api._fabu_jiantie("第一条客户消息")
            self.api._fabu_jiantie("第二条客户消息")
        self.api._fabu_weixin({"id": "wx-1", "conversation_id": "customer-a", "content": "报价多少？"})
        events = self.api.poll_events()["events"]
        self.assertEqual([item["content"] for item in events], ["第一条客户消息", "第二条客户消息", "报价多少？"])

    def test_setting_key_returns_presence_without_secret(self):
        result = self.api.set_api_key("  sk-workflow-test  ")
        self.assertTrue(result["ok"])
        self.assertTrue(result["api_key_configured"])
        self.assertNotIn("sk-workflow-test", json.dumps(result))
        result = self.api.set_api_key("")
        self.assertFalse(result["api_key_configured"])
        self.assertFalse(result["llm_enabled"])

    def test_components_stop_on_close_instead_of_before_webview_starts(self):
        events = SimpleNamespace(shown=EventHook(), closed=EventHook())
        window = SimpleNamespace(events=events)
        with ExitStack() as stack:
            components = {}
            for name in ("Sender", "AttachManager", "ClipboardMonitor", "WxReader", "WeChatUIAReader"):
                factory = stack.enter_context(patch.object(main, name))
                components[name] = factory.return_value
            stack.enter_context(patch.object(main.webview, "create_window", return_value=window))
            stack.enter_context(patch.object(main, "zhuce_rejian"))

            def start():
                for component in components.values():
                    component.stop.assert_not_called()
                self.assertEqual(len(events.closed.callbacks), 1)
                events.closed.callbacks[0]()

            stack.enter_context(patch.object(main.webview, "start", side_effect=start))
            main.zhuchengxu()
            for name in ("AttachManager", "ClipboardMonitor", "WxReader", "WeChatUIAReader"):
                components[name].stop.assert_called_once()
            components["Sender"].tracker.stop.assert_called_once()


class DetectorTests(unittest.TestCase):
    def test_legacy_and_current_wechat_process_names_are_recognized(self):
        for name in ("WeChat.exe", "Weixin.exe"):
            with self.subTest(name=name), patch("chatapp_detector._process_image_path", return_value="C:/Apps/" + name):
                self.assertTrue(chatapp_detector.is_chatapp_process(123))


if __name__ == "__main__":
    unittest.main()
