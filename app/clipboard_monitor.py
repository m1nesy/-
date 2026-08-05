# -*- coding: utf-8 -*-
"""快捷粘贴辅助模块：检测用户复制操作后自动读取、去重、触发 AI 分析"""
import threading
import time

import win32clipboard
import win32con


def get_clipboard_text():
    """安全读取系统剪贴板文本，失败返回 None"""
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return None


class ClipboardMonitor:
    """定时检测剪贴板，当检测到用户复制新内容时回调 on_text(text)"""

    def __init__(self, interval: float = 0.6):
        self.interval = interval
        self._last = ""
        self._running = False
        self._enabled = False
        self._on_text = None  # callable(text)
        self._suppressed = {}
        self._lock = threading.Lock()
        self._thread = None

    def set_callback(self, fn):
        self._on_text = fn

    def set_enabled(self, flag: bool):
        self._enabled = bool(flag)
        if self._enabled:
            self._last = ""  # 重置去重

    def suppress(self, text: str, ttl: float = 3.0):
        """Temporarily pause paste detection for a specific text value to avoid re-triggering during auto-send."""
        if not text:
            return
        with self._lock:
            now = time.monotonic()
            self._suppressed = {k: v for k, v in self._suppressed.items() if v > now}
            self._suppressed[str(text)] = now + max(0.5, ttl)

    def skip_next(self):
        """Legacy compatibility; temporarily skip the current clipboard content."""
        text = get_clipboard_text()
        if text:
            self.suppress(text)

    def status(self):
        return {"enabled": self._enabled}

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._poll, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.interval * 2))
        self._thread = None

    def _is_suppressed(self, text: str) -> bool:
        with self._lock:
            now = time.monotonic()
            self._suppressed = {k: v for k, v in self._suppressed.items() if v > now}
            return text in self._suppressed

    def _is_chat_message(self, text: str) -> bool:
        """过滤：太短/太长/纯符号/URL 不视为有效聊天内容"""
        t = text.strip()
        if len(t) < 2 or len(t) > 2000:
            return False
        if t.startswith("http://") or t.startswith("https://"):
            return False
        # 至少含一个中文字符或常见聊天标点
        has_cjk = any("一" <= c <= "鿿" for c in t)
        return has_cjk or len(t) > 5

    def _poll(self):
        while self._running:
            try:
                if self._enabled:
                    txt = get_clipboard_text()
                    if txt and txt != self._last:
                        self._last = txt
                        if self._is_suppressed(txt):
                            continue
                        if self._is_chat_message(txt) and self._on_text:
                            self._on_text(txt)
            except Exception:
                pass
            time.sleep(self.interval)
