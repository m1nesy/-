# -*- coding: utf-8 -*-
"""聊天窗口消息读取器 — 通过 Windows 标准辅助功能 API (UI Automation) 读取可见聊天窗口中的消息文本。"""
import hashlib
import re
import threading
import time

import win32gui
import win32process

from wechat_probe import is_wechat_process

POLL_INTERVAL = 1.0
# 聊天窗口 UI 元素过滤关键词（匹配实际软件界面文本，用于区分消息内容与界面装饰元素）
UI_SKIP_TEXTS = {
    "微信", "发送", "搜索", "聊天信息", "表情", "文件", "图片", "视频", "语音输入",
    "按住说话", "查看更多消息", "以下为新消息", "朋友圈", "通讯录", "收藏", "设置",
}


def normalize_text(text: str) -> str:
    text = str(text or "").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_message_text(text: str) -> bool:
    text = normalize_text(text)
    if not text or text in UI_SKIP_TEXTS or len(text) > 2000:
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return False
    if re.fullmatch(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}.*", text):
        return False
    return not (len(text) <= 1 and not re.search(r"[一-鿿A-Za-z0-9]", text))


def _intersects(a, b) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def message_area(win_rect):
    left, top, right, bottom = win_rect
    width, height = right - left, bottom - top
    area_left = left + int(width * 0.28) if width >= 800 else left + int(width * 0.04)
    area_top = top + int(height * 0.10)
    area_right = right - int(width * 0.04)
    area_bottom = bottom - int(height * 0.22)
    return win_rect if area_right <= area_left or area_bottom <= area_top else (
        area_left, area_top, area_right, area_bottom)


def infer_role(rect, area) -> str:
    center = (rect[0] + rect[2]) / 2
    return "me" if center > area[0] + (area[2] - area[0]) * 0.62 else "them"


def appended_items(previous, current):
    """Return the appended suffix while tolerating a scrolled/recycled prefix."""
    if not previous:
        return []
    prev_keys = [item["key"] for item in previous]
    cur_keys = [item["key"] for item in current]
    max_overlap = min(len(prev_keys), len(cur_keys))
    for size in range(max_overlap, 0, -1):
        if prev_keys[-size:] == cur_keys[:size]:
            return current[size:]
    previous_ids = {item["identity"] for item in previous if item.get("identity")}
    if previous_ids:
        last_match = -1
        for index, item in enumerate(current):
            if item.get("identity") in previous_ids:
                last_match = index
        if last_match >= 0:
            return current[last_match + 1:]
    return []


class WeChatUIAReader:
    """聊天窗口消息读取器 — 利用 Windows UI Automation 标准辅助功能接口，
    从聊天窗口的可访问性树中提取可见消息文本，无需截图或 OCR。"""

    def __init__(self, interval=POLL_INTERVAL):
        self.interval = interval
        self._running = False
        self._enabled = False
        self._thread = None
        self._target_getter = None
        self._on_message = None
        self._last_hwnd = None
        self._last_conversation = ""
        self._snapshot = []
        self._last_count = 0
        self._last_error = ""
        self._reason_code = None
        self._dependency = "unknown"
        self._last_poll_at = 0
        self._last_message_at = 0
        self._uia = None

    @property
    def enabled(self):
        return self._enabled

    def set_callback(self, fn):
        self._on_message = fn

    def set_target_getter(self, fn):
        self._target_getter = fn

    def available(self):
        """检查辅助功能读取是否可用：加载 UIA 组件并验证聊天窗口可访问性。"""
        if not self._load_uia():
            return False
        hwnd = self._validate_target(self._get_target(silent=True), silent=False)
        if not hwnd:
            return False
        try:
            area = message_area(win32gui.GetWindowRect(hwnd))
            items = self._extract(hwnd, area)
            self._last_count = len(items)
            if not items:
                self._set_error("UIA_CHAT_TEXT_UNAVAILABLE", "新版本聊天软件未向 UI Automation 暴露聊天文本")
                return False
            self._reason_code = None
            self._last_error = ""
            return True
        except Exception as exc:
            self._set_error("UIA_TREE_UNAVAILABLE", str(exc))
            return False

    def status(self):
        hwnd = self._validate_target(self._get_target(silent=True), silent=True)
        return {
            "enabled": self._enabled, "target": bool(hwnd), "read_mode": "uia",
            "dependency": self._dependency, "last_count": self._last_count,
            "last_error": self._last_error, "reason_code": self._reason_code,
            "last_poll_at": self._last_poll_at, "last_message_at": self._last_message_at,
            "constraints": "current-visible-chat-only; no-screenshot; no-ocr",
        }

    def set_enabled(self, flag):
        was_off = not self._enabled
        self._enabled = bool(flag)
        if self._enabled and was_off:
            self._reset_snapshot()
            self._scan(emit=False)

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.interval * 2))
        self._thread = None

    def _load_uia(self):
        if self._uia is not None:
            return True
        try:
            import uiautomation as uia
            self._uia = uia
            self._dependency = "available"
            return True
        except Exception as exc:
            self._dependency = "missing"
            self._set_error("UIA_DEPENDENCY_MISSING", f"UIA 依赖不可用: {exc}")
            return False

    def _get_target(self, silent=False):
        try:
            return self._target_getter() if self._target_getter else None
        except Exception as exc:
            if not silent:
                self._set_error("WECHAT_WINDOW_NOT_FOUND", str(exc))
            return None

    def _validate_target(self, hwnd, silent=False):
        try:
            if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                if not silent:
                    self._set_error("WECHAT_WINDOW_NOT_FOUND", "未找到可见聊天窗口")
                return None
            if win32gui.IsIconic(hwnd):
                if not silent:
                    self._set_error("WECHAT_WINDOW_MINIMIZED", "聊天窗口已最小化")
                return None
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not is_wechat_process(pid):
                if not silent:
                    self._set_error("WECHAT_WINDOW_NOT_FOUND", "当前目标不是聊天窗口")
                return None
            return hwnd
        except Exception as exc:
            if not silent:
                self._set_error("WECHAT_WINDOW_NOT_FOUND", str(exc))
            return None

    def _loop(self):
        while self._running:
            try:
                if self._enabled:
                    self._scan(emit=True)
            except Exception as exc:
                self._set_error("UIA_TREE_UNAVAILABLE", str(exc))
            time.sleep(self.interval)

    def _scan(self, emit):
        if not self._load_uia():
            return []
        hwnd = self._validate_target(self._get_target())
        if not hwnd:
            self._last_count = 0
            return []
        title = normalize_text(win32gui.GetWindowText(hwnd)) or "聊天窗口"
        conversation_id = f"uia:{hwnd}:{title}"
        changed = hwnd != self._last_hwnd or conversation_id != self._last_conversation
        area = message_area(win32gui.GetWindowRect(hwnd))
        items = self._with_occurrences(self._extract(hwnd, area), area)
        self._last_count = len(items)
        self._last_poll_at = time.time()
        self._reason_code = None
        self._last_error = ""
        if changed or not self._snapshot:
            self._last_hwnd = hwnd
            self._last_conversation = conversation_id
            self._snapshot = items
            return []
        new_items = appended_items(self._snapshot, items)
        self._snapshot = items
        emitted = []
        if emit:
            for item in new_items:
                message_id = item.get("identity") or hashlib.sha1(
                    f"{conversation_id}|{item['key']}|{time.time_ns()}".encode()).hexdigest()
                message = {
                    "source": "chatapp_uia", "conversation_id": conversation_id,
                    "conversation_name": title, "role": item["role"], "content": item["text"],
                    "message_id": message_id, "created_at": int(time.time()),
                }
                if self._on_message:
                    self._on_message(title, message)
                emitted.append(message)
                self._last_message_at = time.time()
        return emitted

    def _reset_snapshot(self):
        self._last_hwnd = None
        self._last_conversation = ""
        self._snapshot = []

    def _set_error(self, reason_code, message):
        self._reason_code = reason_code
        self._last_error = str(message)

    @staticmethod
    def _with_occurrences(items, area):
        counts = {}
        result = []
        for item in items:
            role = infer_role(item["rect"], area)
            base = f"{role}|{item['text']}"
            counts[base] = counts.get(base, 0) + 1
            copy = dict(item)
            copy["role"] = role
            copy["key"] = f"{base}|{counts[base]}"
            result.append(copy)
        return result

    def _extract(self, hwnd, area):
        root = self._uia.ControlFromHandle(hwnd)
        out = []

        def visit(ctrl, depth=0):
            if depth > 8:
                return
            try:
                name = normalize_text(getattr(ctrl, "Name", ""))
                rect = self._rect_tuple(getattr(ctrl, "BoundingRectangle", None))
                control_type = str(getattr(ctrl, "ControlTypeName", "") or "")
                if rect and _intersects(rect, area) and is_message_text(name):
                    if self._is_message_control(control_type, rect, area, name):
                        identity = self._control_identity(ctrl)
                        out.append({"text": name, "rect": rect, "type": control_type,
                                    "identity": identity})
                for child in ctrl.GetChildren() or []:
                    visit(child, depth + 1)
            except Exception:
                return

        visit(root)
        out.sort(key=lambda item: (item["rect"][1], item["rect"][0], item["text"]))
        deduped, seen = [], set()
        for item in out:
            signature = (item["identity"] or item["text"], item["rect"][1] // 8, item["rect"][0] // 8)
            if signature not in seen:
                seen.add(signature)
                deduped.append(item)
        return deduped

    @staticmethod
    def _control_identity(ctrl):
        for attr in ("RuntimeId", "AutomationId"):
            try:
                value = getattr(ctrl, attr, None)
                if callable(value):
                    value = value()
                if value:
                    return f"{attr}:{value}"
            except Exception:
                continue
        return ""

    @staticmethod
    def _rect_tuple(rect):
        if not rect:
            return None
        try:
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:
            try:
                return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
            except Exception:
                return None

    @staticmethod
    def _is_message_control(control_type, rect, area, text):
        lowered = control_type.lower()
        if any(value in lowered for value in ("window", "pane", "edit", "button", "menu", "tab")):
            return False
        if text in UI_SKIP_TEXTS:
            return False
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        area_width, area_height = area[2] - area[0], area[3] - area[1]
        return width < area_width * 0.95 and height < area_height * 0.8
