# -*- coding: utf-8 -*-
"""窗口管理：跟随吸附 / 分屏拼接
- follow 跟随：聊天窗口不改大小，提词器贴其右侧(或左/叠加)
- split 分屏：主动缩小聊天窗口，提词器占右，二者 0 间隙拼接满工作区
"""
import time
import threading

import win32api
import win32con
import win32gui
import win32process

from chatapp_detector import is_chatapp_process

KNOWN_CHAT_CLASSES = {"ChatAppMainWndForPC", "WeworkMainWndForPC"}
CHAT_TITLE_KEYWORDS = ["聊天软件", "企业聊天软件", "Weixin", "ChatApp", "QQ", "千牛", "拼多多商家", "钉钉"]
OWN_TITLE_KEYWORDS = ["专利销售智能提词器", "提词器Agent"]

DEBUG_LOG = None


def _dbg(msg):
    if DEBUG_LOG:
        try:
            with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass


def _is_chatapp_window(hwnd) -> bool:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return is_chatapp_process(pid)
    except Exception:
        return False


def is_chat_like(hwnd) -> bool:
    try:
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
    except Exception:
        return False
    if any(k in title for k in OWN_TITLE_KEYWORDS):
        return False
    if _is_chatapp_window(hwnd):
        return True
    if cls in KNOWN_CHAT_CLASSES:
        return True
    if any(k in title for k in CHAT_TITLE_KEYWORDS):
        return True
    if cls.startswith("Qt") and "QWindow" in cls and title.strip():
        return True
    return False


def find_chat_window_fallback():
    cands = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if is_chat_like(hwnd):
            cands.append(hwnd)

    win32gui.EnumWindows(cb, None)
    for hwnd in cands:
        try:
            r = win32gui.GetWindowRect(hwnd)
            if not win32gui.IsIconic(hwnd) and not (r[0] < -10000 or r[1] < -10000):
                return hwnd
        except Exception:
            continue
    return cands[0] if cands else None


def work_area_for(hwnd):
    """用 SystemParametersInfo 取工作区逻辑坐标（与 SetWindowPos 一致），
    多显示器的聊天窗口用 MonitorFromWindow 修正 X 偏移"""
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    wa = (r.left, r.top, r.right, r.bottom)
    # 多显示器：如果聊天窗口在副屏，偏移到副屏工作区
    try:
        m = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        mi = win32api.GetMonitorInfo(m)
        mw = mi["Work"]
        screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        if mw[0] >= screen_w or mw[2] > screen_w:
            wa = mw
    except Exception:
        pass
    return wa


MODE_OFF = ""
MODE_FOLLOW = "follow"
MODE_SPLIT = "split"
MODE_NAMES = {MODE_OFF: "悬浮", MODE_FOLLOW: "吸附跟随", MODE_SPLIT: "分屏拼接"}


class AttachManager:
    def __init__(self, find_own, tracker, width: int, interval: float = 0.7):
        self._find_own = find_own
        self.tracker = tracker
        self.width = width
        self.interval = interval
        self.mode = MODE_OFF
        self.chat_title = ""
        self._own_hwnd = None
        self._chat_hwnd = None
        self._running = False
        self._orig_chat_rect = None  # 分屏前聊天窗口的原始坐标，退出时恢复
        self._saved_pos = None  # 吸附前我们窗口的原始位置
        self._target_source = ""
        self._target_class = ""

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        if self.mode == MODE_SPLIT:
            self._restore_chat()
        if self.mode == MODE_FOLLOW:
            self._restore_our_pos()
        self.mode = mode
        if mode not in (MODE_SPLIT, MODE_FOLLOW):
            self._chat_hwnd = None
            self.chat_title = ""

    def status(self):
        return {"enabled": self.mode != MODE_OFF, "mode": self.mode,
                "mode_name": MODE_NAMES.get(self.mode, ""), "chat_title": self.chat_title,
                "target_source": self._target_source, "target_class": self._target_class}

    def start(self):
        if not self._running:
            self._running = True
            threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False
        if self.mode == MODE_SPLIT:
            self._restore_chat()
        elif self.mode == MODE_FOLLOW:
            self._restore_our_pos()
        self.mode = MODE_OFF

    # ---- 内部 ----

    def _pick_target_hwnd(self):
        # 候选池：个体聊天窗口（Qt 类，联系人名字）优先于主面板（标题固定词）
        cands = []
        t = self.tracker
        if t.target_valid() and is_chat_like(t.last_hwnd) and t.last_hwnd != self._own_hwnd:
            cands.append(t.last_hwnd)
        if (self._chat_hwnd and win32gui.IsWindow(self._chat_hwnd)
                and is_chat_like(self._chat_hwnd) and self._chat_hwnd != self._own_hwnd
                and self._chat_hwnd not in cands):
            cands.append(self._chat_hwnd)
        fb = find_chat_window_fallback()
        if fb and fb != self._own_hwnd and fb not in cands:
            cands.append(fb)
        # 优先个体聊天窗口（Qt 类、联系人名为标题、不大到满屏）
        for hwnd in cands:
            try:
                cls = win32gui.GetClassName(hwnd)
                r = win32gui.GetWindowRect(hwnd)
                w = r[2] - r[0]
                screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
                # 个体聊天窗特征：Qt 类 + 宽度 < 85% 屏幕
                if cls.startswith("Qt") and "QWindow" in cls and w < screen_w * 0.85:
                    self._target_source = "individual-qt"
                    self._target_class = cls
                    return hwnd
            except Exception:
                continue
        # 其次：聊天软件进程归属，适配 4.x 窗口类名和标题变化
        for hwnd in cands:
            if _is_chatapp_window(hwnd):
                self._target_source = "chatapp-process"
                try:
                    self._target_class = win32gui.GetClassName(hwnd)
                except Exception:
                    self._target_class = ""
                return hwnd
        # 再次：已知聊天类名（聊天软件3/企微）
        for hwnd in cands:
            try:
                if win32gui.GetClassName(hwnd) in KNOWN_CHAT_CLASSES:
                    self._target_source = "known-class"
                    self._target_class = win32gui.GetClassName(hwnd)
                    return hwnd
            except Exception:
                continue
        # 最后：标题关键字匹配
        for hwnd in cands:
            try:
                self._target_source = "title-fallback"
                self._target_class = win32gui.GetClassName(hwnd)
                return hwnd
            except Exception:
                continue
        return None

    def _own_rect(self):
        try:
            return win32gui.GetWindowRect(self._own_hwnd)
        except Exception:
            return None

    def _restore_chat(self):
        if self._chat_hwnd and self._orig_chat_rect and win32gui.IsWindow(self._chat_hwnd):
            l, t, r, b = self._orig_chat_rect
            win32gui.SetWindowPos(self._chat_hwnd, None, l, t, r - l, b - t,
                                  win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
        self._orig_chat_rect = None

    def _restore_our_pos(self):
        if self._own_hwnd and win32gui.IsWindow(self._own_hwnd) and self._saved_pos:
            x, y, w, h = self._saved_pos
            win32gui.SetWindowPos(self._own_hwnd, win32con.HWND_TOPMOST, x, y, w, h,
                                  win32con.SWP_SHOWWINDOW)
        self._saved_pos = None

    def _loop(self):
        import traceback as _tb
        while self._running:
            try:
                if self.mode == MODE_SPLIT:
                    self._tick_split()
                elif self.mode == MODE_FOLLOW:
                    self._tick_follow()
            except Exception:
                _dbg("TICK EXCEPTION:\n" + _tb.format_exc())
            time.sleep(self.interval)

    def _ensure_own(self):
        if not self._own_hwnd or not win32gui.IsWindow(self._own_hwnd):
            self._own_hwnd = self._find_own()
        return self._own_hwnd

    # ---------- 分屏模式 ----------
    def _tick_split(self):
        if not self._ensure_own():
            return
        hwnd = self._pick_target_hwnd()
        if not hwnd:
            self.chat_title = ""
            return
        self._chat_hwnd = hwnd
        try:
            self.chat_title = win32gui.GetWindowText(hwnd)
        except Exception:
            self.chat_title = ""
        if win32gui.IsIconic(hwnd):
            return
        cr = win32gui.GetWindowRect(hwnd)
        if cr[0] < -10000 or cr[1] < -10000:
            return

        wa = work_area_for(hwnd)
        wa_l, wa_t, wa_r, wa_b = wa
        # 提词器右侧留 2px 余量防止窗体边缘被屏幕截掉
        right_margin = 2
        chat_w = wa_r - wa_l - self.width - right_margin
        chat_h = wa_b - wa_t

        # 首次切换到分屏时记下聊天窗口原始位置（用于退出时恢复）
        if self._orig_chat_rect is None:
            self._orig_chat_rect = cr
        if self._saved_pos is None:
            r = self._own_rect()
            if r:
                self._saved_pos = r

        # 尝试缩放聊天窗口填满左侧
        win32gui.SetWindowPos(
            hwnd, None, wa_l, wa_t, chat_w, chat_h,
            win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        # 读回实际位置：如果聊天软件拒绝缩放，退回到跟贴模式
        actual = win32gui.GetWindowRect(hwnd)
        a_left, a_top, a_right, a_bottom = actual
        a_w = a_right - a_left
        accepted = abs(a_left - wa_l) < 40 and abs(a_w - chat_w) < 40

        if accepted:
            # 分屏成功：提词器贴工作区右侧
            win32gui.SetWindowPos(
                self._own_hwnd, win32con.HWND_TOPMOST,
                a_left + a_w, a_top, self.width, a_bottom - a_top,
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
            )
        else:
            # 拒绝缩放 → 跟贴：紧贴聊天窗口实际右边缘
            x = a_right if a_right + self.width <= wa_r else max(0, a_left - self.width)
            win32gui.SetWindowPos(
                self._own_hwnd, win32con.HWND_TOPMOST,
                x, max(0, a_top), self.width, a_bottom - a_top,
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
            )

    # ---------- 跟随模式 ----------
    def _tick_follow(self):
        if not self._ensure_own():
            return
        hwnd = self._pick_target_hwnd()
        if not hwnd:
            self.chat_title = ""
            return
        self._chat_hwnd = hwnd
        try:
            self.chat_title = win32gui.GetWindowText(hwnd)
        except Exception:
            self.chat_title = ""
        rect = win32gui.GetWindowRect(hwnd)
        if win32gui.IsIconic(hwnd) or rect[0] < -10000 or rect[1] < -10000:
            return

        if self._saved_pos is None:
            r = self._own_rect()
            if r:
                self._saved_pos = r

        left, top, right, bottom = rect
        w, h = self.width, bottom - top
        screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)

        # 放置策略：右侧 / 左侧 / 右侧叠加
        if right + w <= screen_w:
            x = right
        elif left - w >= 0:
            x = left - w
        else:
            x = screen_w - w
        y = max(0, top)
        _dbg(f"follow: target={hwnd}({self.chat_title}) rect={rect} -> SetWindowPos({x},{y},{w},{h})")
        win32gui.SetWindowPos(
            self._own_hwnd, win32con.HWND_TOPMOST, x, y, w, h,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
