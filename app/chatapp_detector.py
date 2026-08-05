# -*- coding: utf-8 -*-
"""聊天软件客户端的只读发现与兼容性诊断。

本模块只读取进程、程序版本和文件/目录元数据；不读取聊天内容、不导出密钥，
也不会创建、删除或修改聊天软件数据。
"""
import ctypes
import os
import re
from ctypes import wintypes
from pathlib import Path


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def parse_major(version: str):
    """返回 3、4 或 unknown；版本缺失时绝不猜测。"""
    match = re.match(r"\s*(\d+)(?:\.\d+)*", version or "")
    if not match:
        return "unknown"
    major = int(match.group(1))
    return major if major in (3, 4) else "unknown"


def _process_image_path(pid: int):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def is_chatapp_process(pid: int):
    """判断给定进程 ID 是否属于聊天软件进程。"""
    return Path(_process_image_path(pid)).name.lower() in {"chatapp.exe", "weixin.exe"}


def find_chatapp_processes():
    """返回运行中聊天软件进程的 PID 和可执行文件路径。"""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() in {"chatapp.exe", "weixin.exe"}:
                found.append({"pid": int(entry.th32ProcessID), "path": _process_image_path(entry.th32ProcessID)})
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def file_version(path: str):
    if not path:
        return "unknown"
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return ".".join(str(value) for value in (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF))
    except Exception:
        return "unknown"


def documents_dir():
    """优先使用 Windows 用户目录；不包含开发机用户名假设。"""
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(profile) / "Documents"


def find_legacy_layouts():
    """发现旧版消息布局的元数据，不读取数据库内容。"""
    root = documents_dir() / "ChatApp Files"
    layouts = []
    try:
        accounts = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        accounts = []
    for account in accounts:
        msg_dir = account / "Msg"
        multi_dir = msg_dir / "Multi"
        try:
            shards = sorted(p.name for p in multi_dir.glob("MSG*.db") if p.is_file()) if multi_dir.is_dir() else []
        except OSError:
            shards = []
        if msg_dir.is_dir() or shards:
            layouts.append({
                "account_hint": _redact_account(account.name),
                "has_msg_dir": msg_dir.is_dir(),
                "has_multi_dir": multi_dir.is_dir(),
                "shards": shards,
                "has_contacts": (msg_dir / "MicroMsg.db").is_file(),
                "path": str(msg_dir),
            })
    return layouts


def _redact_account(value: str):
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-3:]


def detector():
    """返回可安全展示的聊天软件发现结果和内部可用的消息布局信息。"""
    processes = find_chatapp_processes()
    if not processes:
        return {
            "client_detected": False,
            "client_version": "unknown",
            "client_major": "unknown",
            "legacy_layouts": [],
            "reason_code": "WECHAT_NOT_RUNNING",
            "error": "未检测到正在运行的聊天软件客户端",
        }

    client = processes[0]
    version = file_version(client["path"])
    layouts = find_legacy_layouts()
    return {
        "client_detected": True,
        "client_version": version,
        "client_major": parse_major(version),
        "process_count": len(processes),
        "legacy_layouts": layouts,
        "reason_code": None,
        "error": "",
    }


def public_diagnostics(result: dict):
    """移除本地路径后，可安全复制到问题报告的诊断摘要。"""
    layouts = result.get("legacy_layouts", [])
    return {
        "client_detected": result.get("client_detected", False),
        "client_version": result.get("client_version", "unknown"),
        "client_major": result.get("client_major", "unknown"),
        "process_count": result.get("process_count", 0),
        "legacy_layouts": [{k: v for k, v in item.items() if k != "path"} for item in layouts],
        "reason_code": result.get("reason_code"),
    }
