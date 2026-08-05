# -*- coding: utf-8 -*-
"""一键发送引擎 + 开机自启
核心能力：
- WindowTracker：后台线程持续跟踪"最近的聊天窗口"（任何非本程序的前台窗口：聊天软件/企微/QQ/千牛…）
- send_text / send_images：把文字/图片放进剪贴板，切回聊天窗口，模拟 Ctrl+V 粘进输入框，可选自动回车发出
- 长话术按分段标记（单独一行 --- ）拆成多条依次发送
- 开机自启：HKCU Run 注册表读写
"""
import io
import os
import time
import threading

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

# 系统/自身窗口，跟踪时跳过
_SKIP_CLASS = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow",
    "WindowsDashboard", "MultitaskingViewFrame",
}

VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
VK_MENU = 0x12


def _key(vk, down=True):
    flag = 0 if down else win32con.KEYEVENTF_KEYUP
    win32api.keybd_event(vk, 0, flag, 0)


def press_ctrl_v():
    _key(VK_CONTROL, True)
    _key(VK_V, True)
    time.sleep(0.05)
    _key(VK_V, False)
    _key(VK_CONTROL, False)


def press_enter():
    _key(VK_RETURN, True)
    time.sleep(0.03)
    _key(VK_RETURN, False)


def force_foreground(hwnd):
    """绕过 Windows 前台锁定，把目标窗口拉到前台"""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        # 先轻敲一下 Alt，解除 SetForegroundWindow 限制
        _key(VK_MENU, True)
        _key(VK_MENU, False)
        fg = win32gui.GetForegroundWindow()
        fg_tid = win32process.GetWindowThreadProcessId(fg)[0]
        cur_tid = win32api.GetCurrentThreadId()
        win32process.AttachThreadInput(cur_tid, fg_tid, True)
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        finally:
            win32process.AttachThreadInput(cur_tid, fg_tid, False)
        time.sleep(0.15)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


class WindowTracker:
    """轮询记录最近一个"像聊天软件"的前台窗口（排除本程序自己）"""

    def __init__(self, interval=0.3):
        self.interval = interval
        self.last_hwnd = None
        self.last_title = ""
        self.last_class = ""
        self._pid = os.getpid()
        self._running = False
        self._thread = None

    def _is_chat_candidate(self, hwnd):
        try:
            if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return False
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == self._pid:  # 我们自己的窗口
                return False
            cls = win32gui.GetClassName(hwnd)
            if cls in _SKIP_CLASS:
                return False
            title = win32gui.GetWindowText(hwnd)
            if not title.strip():
                return False
            # 排除明显不是聊天窗口的：最大化桌面类、任务管理器等小工具可再补充
            return True
        except Exception:
            return False

    def _poll(self):
        while self._running:
            try:
                hwnd = win32gui.GetForegroundWindow()
                if self._is_chat_candidate(hwnd):
                    self.last_hwnd = hwnd
                    self.last_title = win32gui.GetWindowText(hwnd)
                    self.last_class = win32gui.GetClassName(hwnd)
            except Exception:
                pass
            time.sleep(self.interval)

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

    def target_valid(self):
        return bool(self.last_hwnd and win32gui.IsWindow(self.last_hwnd))

    def status(self):
        if self.target_valid():
            return {"ok": True, "title": self.last_title, "class": self.last_class}
        return {"ok": False, "title": "", "class": ""}


def set_clipboard_text(text: str):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_image(path: str):
    """图片转 DIB 放进剪贴板，聊天软件里 Ctrl+V 直接出图"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "BMP")
    dib = buf.getvalue()[14:]  # 去掉 BITMAPFILEHEADER 才是 CF_DIB
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def split_segments(text: str) -> list:
    """按分段标记拆分：单独一行 --- 或 【分段】"""
    lines = text.split("\n")
    segs, cur = [], []
    for ln in lines:
        if ln.strip() in ("---", "【分段】"):
            if cur:
                segs.append("\n".join(cur).strip())
                cur = []
        else:
            cur.append(ln)
    if cur:
        segs.append("\n".join(cur).strip())
    return [s for s in segs if s]


class Sender:
    def __init__(self):
        self.tracker = WindowTracker()
        self.tracker.start()
        self._send_lock = threading.Lock()
        self._clipboard_suppressor = None

    def set_clipboard_suppressor(self, fn):
        self._clipboard_suppressor = fn

    def _set_text(self, text):
        if self._clipboard_suppressor:
            self._clipboard_suppressor(text)
        set_clipboard_text(text)

    def _target_unchanged(self, hwnd, pid, title):
        try:
            if not hwnd or not win32gui.IsWindow(hwnd):
                return False
            _, current_pid = win32process.GetWindowThreadProcessId(hwnd)
            return current_pid == pid and win32gui.GetWindowText(hwnd) == title
        except Exception:
            return False

    def send_text(self, text: str, auto_enter: bool = False) -> dict:
        if not self.tracker.target_valid():
            return {"ok": False, "error": "没找到聊天窗口：请先点一下聊天软件/企微窗口聊一句，再回来点发送"}
        segments = split_segments(text)
        if not segments:
            return {"ok": False, "error": "内容为空"}
        hwnd = self.tracker.last_hwnd
        title = self.tracker.last_title
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return {"ok": False, "error": "发送目标已失效"}
        if not self._send_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有发送任务正在执行", "reason_code": "SEND_BUSY"}
        sent = 0
        try:
            for i, seg in enumerate(segments):
                if not self._target_unchanged(hwnd, pid, title):
                    return {"ok": False, "error": "发送目标已变化，已停止", "segments": sent}
                self._set_text(seg)
                if not force_foreground(hwnd):
                    return {"ok": False, "error": "无法激活聊天窗口", "segments": sent}
                time.sleep(0.1)
                press_ctrl_v()
                time.sleep(0.15)
                if auto_enter:
                    press_enter()
                    time.sleep(0.25 if i < len(segments) - 1 else 0)
                sent += 1
            return {"ok": True, "segments": sent, "target": title}
        except Exception as e:
            return {"ok": False, "error": str(e), "segments": sent}
        finally:
            self._send_lock.release()

    def send_images(self, paths: list, auto_enter: bool = False) -> dict:
        if not self.tracker.target_valid():
            return {"ok": False, "error": "没找到聊天窗口"}
        hwnd = self.tracker.last_hwnd
        title = self.tracker.last_title
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return {"ok": False, "error": "发送目标已失效"}
        if not self._send_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有发送任务正在执行", "reason_code": "SEND_BUSY"}
        sent = 0
        try:
            for p in paths:
                if not os.path.exists(p):
                    continue
                if not self._target_unchanged(hwnd, pid, title):
                    return {"ok": False, "error": "发送目标已变化，已停止", "images": sent}
                set_clipboard_image(p)
                if not force_foreground(hwnd):
                    return {"ok": False, "error": "无法激活聊天窗口", "images": sent}
                time.sleep(0.15)
                press_ctrl_v()
                time.sleep(0.3)
                if auto_enter:
                    press_enter()
                    time.sleep(0.3)
                sent += 1
            return {"ok": True, "images": sent, "target": title}
        except Exception as e:
            return {"ok": False, "error": str(e), "images": sent}
        finally:
            self._send_lock.release()

    def send_all(self, text: str, image_paths: list, auto_enter: bool = False) -> dict:
        """文字 + 图片组合发送"""
        r1 = self.send_text(text, auto_enter=auto_enter) if text.strip() else {"ok": True}
        if not r1.get("ok"):
            return r1
        r2 = self.send_images(image_paths, auto_enter=auto_enter) if image_paths else {"ok": True, "images": 0}
        return {"ok": r2.get("ok", False), "text_segments": r1.get("segments", 0),
                "images": r2.get("images", 0), "target": self.tracker.last_title,
                "error": r2.get("error")}


# ============ 开机自启 ============
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "PatentSalesPrompter"


def autostart_get() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def autostart_set(enable: bool, exe_path: str, script_path: str = "") -> dict:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if enable:
                if script_path:
                    cmd = f'"{exe_path}" "{script_path}"'
                else:
                    cmd = f'"{exe_path}"'
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return {"ok": True, "enabled": enable}
    except Exception as e:
        return {"ok": False, "error": str(e)}
