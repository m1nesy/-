# -*- coding: utf-8 -*-
"""客户消息接入模块 (Client Message Interface) — 通过标准数据库驱动进行消息轮询。"""
import glob
import hashlib
import os
import re
import threading
import time
from pathlib import Path

from wechat_probe import probe as probe_wechat
from wechat_probe import public_diagnostics

POLL_INTERVAL = 2.0
CONTACT_CACHE_TTL = 600


def _derive_aes_key(key_hex: str, db_path: str) -> bytes:
    with open(db_path, "rb") as f:
        salt = f.read(16)
    return hashlib.pbkdf2_hmac("sha1", bytes.fromhex(key_hex), salt, 64000, 32)


def _resolve_msg_dir(item, wxid):
    candidates = []
    for key in ("wx_dir", "wx_path", "path"):
        value = item.get(key)
        if value:
            root = Path(value)
            candidates.extend((root, root / "Msg", root / wxid / "Msg"))
    candidates.append(Path.home() / "Documents" / "WeChat Files" / wxid / "Msg")
    for candidate in candidates:
        if candidate.name.lower() == "msg" and (candidate / "Multi").is_dir():
            return str(candidate)
        nested = candidate / "Msg"
        if nested.is_dir() and (nested / "Multi").is_dir():
            return str(nested)
    return ""


class WxReader:
    """客户消息接入读取器 — 通过数据库驱动以只读方式轮询消息存储分片，实现新消息实时接入。"""

    def __init__(self, interval=POLL_INTERVAL):
        self.interval = interval
        self._running = False
        self._enabled = False
        self._thread = None
        self._on_message = None
        self._key = None
        self._wxid = None
        self._wx_info = None
        self._msg_dir = None
        self._multi_dir = None
        self._mode = None  # compatibility: None | live
        self._contact_conn = None
        self._contacts = {}
        self._contacts_ts = 0
        self._shards = {}
        self._saved_cursors = {}
        self._state_lock = threading.RLock()
        self._next_retry = 0.0
        self._capability = {
            "db_access": "not-attempted", "read_mode": "disabled", "state": "stopped",
            "reason_code": None, "last_error": "", "consecutive_failures": 0,
            "reconnect_count": 0, "last_poll_at": 0, "last_message_at": 0,
        }

    def set_callback(self, fn):
        self._on_message = fn

    @property
    def enabled(self):
        return self._enabled

    def diagnostics(self):
        with self._state_lock:
            result = public_diagnostics(self._capability)
            result.update({
                "db_access": self._capability.get("db_access"),
                "read_mode": self._capability.get("read_mode"),
                "state": self._capability.get("state"),
                "reason_code": self._capability.get("reason_code"),
                "last_error": self._capability.get("last_error", ""),
                "consecutive_failures": self._capability.get("consecutive_failures", 0),
                "reconnect_count": self._capability.get("reconnect_count", 0),
                "last_poll_at": self._capability.get("last_poll_at", 0),
                "last_message_at": self._capability.get("last_message_at", 0),
                "shards": [os.path.basename(path) for path in self._shards],
            })
            return result

    def init(self):
        """初始化消息接入模块：探测聊天软件运行状态，配置数据库驱动访问参数。"""
        self._set_state("initializing")
        discovery = probe_wechat()
        with self._state_lock:
            self._capability.update(discovery)
        if not discovery.get("client_detected"):
            return self._fail("WECHAT_NOT_RUNNING", discovery.get("error", "未检测到聊天软件"))
        if discovery.get("client_major") == 4:
            return self._fail("VERSION_4_DB_UNSUPPORTED", "新版本聊天软件使用可见窗口兼容模式，不通过消息接口读取")
        try:
            # 数据库工具：用于访问消息存储的配置信息
            import pywxdump
            info = pywxdump.get_wx_info()
            items = info if isinstance(info, list) else [info] if info else []
            selected = None
            for item in items:
                key, wxid = item.get("key", ""), item.get("wxid", "")
                msg_dir = _resolve_msg_dir(item, wxid) if key and wxid else ""
                if len(key) == 64 and msg_dir:
                    selected = (item, key, wxid, msg_dir)
                    break
            if not selected:
                return self._fail("KEY_OR_PROFILE_UNAVAILABLE", "未能取得可验证的聊天软件登录会话或消息目录")
            item, key, wxid, msg_dir = selected
            same_session = self._wxid == wxid and bool(self._saved_cursors)
            self._key, self._wxid, self._msg_dir = key, wxid, msg_dir
            self._wx_info = item
            self._multi_dir = os.path.join(self._msg_dir, "Multi")
            self._close_connections()
            self._discover_shards(baseline=not same_session)
            if not self._shards:
                return self._fail("MSG_SHARD_NOT_FOUND", "未找到可读取的消息分片")
            self._open_contacts()
            self._mode = "live"
            with self._state_lock:
                self._capability.update({
                    "db_access": "supported", "read_mode": "live", "state": "ready",
                    "reason_code": None, "last_error": "", "consecutive_failures": 0,
                })
            return {"ok": True, "wxid": self._wxid, "mode": "live", "capability": self.diagnostics()}
        except ImportError:
            return self._fail("READER_DEPENDENCY_MISSING", "消息接入组件未安装；请使用完整发布包")
        except Exception as exc:
            self._close_connections()
            return self._fail("DB_PROFILE_MISMATCH", f"消息库无法按兼容参数读取（{type(exc).__name__}）")

    def _fail(self, reason_code, error):
        self._mode = None
        with self._state_lock:
            self._capability.update({
                "db_access": "unsupported", "read_mode": "disabled", "state": "error",
                "reason_code": reason_code, "last_error": str(error), "error": str(error),
            })
        return {"ok": False, "error": str(error), "reason_code": reason_code,
                "capability": self.diagnostics()}

    def _set_state(self, state, reason_code=None, error=""):
        with self._state_lock:
            self._capability["state"] = state
            if reason_code is not None:
                self._capability["reason_code"] = reason_code
            if error:
                self._capability["last_error"] = str(error)

    def set_enabled(self, flag):
        self._enabled = bool(flag)
        if self._enabled and not self._shards and time.monotonic() >= self._next_retry:
            self._next_retry = 0

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(2.0, self.interval * 2))
        self._thread = None
        self._close_connections()
        self._mode = None
        self._set_state("stopped")

    def _open_encrypted_ro(self, path):
        # 数据库驱动：以只读方式打开加密消息存储，使用配置的访问参数
        from sqlcipher3 import dbapi2 as sqlcipher
        key = _derive_aes_key(self._key, path)
        uri = "file:" + path.replace("\\", "/") + "?mode=ro"
        conn = sqlcipher.connect(uri, uri=True, check_same_thread=False, isolation_level=None, timeout=2)
        # 数据库驱动参数配置
        conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        conn.execute("PRAGMA cipher_page_size = 4096")
        conn.execute("PRAGMA kdf_iter = 64000")
        conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1")
        conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
        conn.execute("PRAGMA busy_timeout = 1500")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn

    @staticmethod
    def _message_table(conn):
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "MSG" in tables:
            return "MSG"
        for table in tables:
            safe = table.replace('"', '""')
            cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{safe}")')]
            if {"localId", "CreateTime", "StrContent"}.issubset(cols):
                return table
        raise RuntimeError("消息分片中未找到消息表")

    @staticmethod
    def _file_identity(path):
        stat = os.stat(path)
        return f"{stat.st_dev}:{stat.st_ino}:{stat.st_ctime_ns}"

    def _paths(self):
        paths = []
        for path in glob.glob(os.path.join(self._multi_dir or "", "MSG*.db")):
            match = re.fullmatch(r"MSG(\d+)\.db", os.path.basename(path), re.I)
            if match:
                paths.append((int(match.group(1)), path))
        return [path for _, path in sorted(paths)]

    def _open_shard(self, path, baseline):
        conn = self._open_encrypted_ro(path)
        table = self._message_table(conn)
        safe = table.replace('"', '""')
        identity = self._file_identity(path)
        saved = self._saved_cursors.get(identity)
        if saved is not None:
            cursor = saved
        elif baseline:
            cursor = conn.execute(f'SELECT max(localId) FROM "{safe}"').fetchone()[0] or 0
        else:
            cursor = 0
        return {"conn": conn, "table": table, "cursor": int(cursor), "identity": identity}

    def _discover_shards(self, baseline):
        current = set(self._paths())
        for path in list(self._shards):
            if path not in current:
                self._close_shard(path)
        for path in current:
            identity = self._file_identity(path)
            old = self._shards.get(path)
            if old and old["identity"] == identity:
                continue
            if old:
                self._close_shard(path)
            self._shards[path] = self._open_shard(path, baseline=baseline)

    def _close_shard(self, path):
        shard = self._shards.pop(path, None)
        if shard:
            self._saved_cursors[shard["identity"]] = shard["cursor"]
            try:
                shard["conn"].close()
            except Exception:
                pass

    def _open_contacts(self):
        path = os.path.join(self._msg_dir, "MicroMsg.db")
        if os.path.isfile(path):
            try:
                self._contact_conn = self._open_encrypted_ro(path)
            except Exception:
                self._contact_conn = None

    def _fetch_new(self, path, shard):
        table = shard["table"].replace('"', '""')
        cur = shard["conn"].execute(
            f'SELECT * FROM "{table}" WHERE localId > ? ORDER BY localId', (shard["cursor"],))
        cols = [desc[0] for desc in cur.description]
        out = []
        for row in cur.fetchall():
            item = dict(zip(cols, row))
            local_id = int(item.get("localId", 0) or 0)
            shard["cursor"] = max(shard["cursor"], local_id)
            formatted = self._format_msg(item, path, shard["identity"])
            if formatted:
                out.append(formatted)
        return out

    def _format_msg(self, item, path, identity):
        sender = int(item.get("IsSender", 0) or 0)
        msg_type = int(item.get("Type", 0) or 0)
        sub_type = int(item.get("SubType", 0) or 0)
        room = str(item.get("StrTalker", "") or "")
        content = item.get("StrContent") or ""
        if room.endswith("@chatroom") and ":\n" in content:
            content = content.split(":\n", 1)[1]
        if (msg_type, sub_type) == (49, 57) or (msg_type == 49 and not content):
            content = self._parse_app_msg(item.get("CompressContent")) or content
        elif msg_type == 3:
            content = "[图片]"
        elif msg_type == 34:
            content = "[语音]"
        elif msg_type == 43:
            content = "[视频]"
        elif msg_type == 47:
            content = "[表情]"
        elif msg_type == 42:
            content = "[名片]"
        elif msg_type == 48:
            content = "[位置]"
        elif msg_type == 50:
            content = "[语音/视频通话]"
        elif not content:
            content = f"[消息类型{msg_type}]"
        local_id = int(item.get("localId", 0) or 0)
        server_id = str(item.get("MsgSvrID", "") or "")
        message_id = server_id if server_id and server_id != "0" else f"{identity}:{local_id}"
        name = self._contact_name(room)
        created = int(item.get("CreateTime", 0) or 0)
        return {
            "source": "wechat_db", "conversation_id": room, "conversation_name": name,
            "role": "me" if sender == 1 else "them", "content": str(content),
            "message_id": message_id, "created_at": created,
            "id": local_id, "is_sender": sender, "talker": name, "room": room,
            "type": msg_type, "CreateTime": created, "shard": os.path.basename(path),
        }

    @staticmethod
    def _parse_app_msg(compress):
        if not compress or not isinstance(compress, bytes):
            return ""
        try:
            # 数据库工具：消息内容解压与XML解析
            from pywxdump.db.dbMSG import decompress_CompressContent
            from pywxdump.db.utils import xml2dict
            xml = decompress_CompressContent(compress)
            appmsg = xml2dict(xml).get("appmsg", {}) if xml else {}
            title = appmsg.get("title", "")
            refer = appmsg.get("refermsg", {}) or {}
            if refer:
                display = refer.get("content", "")
                if str(display).startswith("<?xml"):
                    display = "[图片/链接]"
                return f"{title}\n[引用]{refer.get('displayname', '')}: {display}"
            return "\n".join(value for value in (title, appmsg.get("des", "")) if value)
        except Exception:
            return ""

    def _contact_name(self, wxid):
        if not wxid or wxid.endswith("@chatroom"):
            return wxid
        now = time.time()
        if now - self._contacts_ts > CONTACT_CACHE_TTL:
            self._contacts.clear()
            self._contacts_ts = now
        if wxid in self._contacts:
            return self._contacts[wxid]
        name = wxid
        if self._contact_conn:
            try:
                row = self._contact_conn.execute(
                    "SELECT Remark, NickName FROM Contact WHERE UserName=?", (wxid,)).fetchone()
                if row:
                    name = row[0] or row[1] or wxid
            except Exception:
                pass
        self._contacts[wxid] = name
        return name

    def _poll_loop(self):
        """消息轮询主循环：定期扫描消息分片，将新消息通过回调输出。"""
        while self._running:
            if self._enabled:
                try:
                    if not self._shards:
                        if time.monotonic() >= self._next_retry:
                            self.init()
                    else:
                        self._discover_shards(baseline=False)
                        messages = []
                        for path, shard in list(self._shards.items()):
                            messages.extend(self._fetch_new(path, shard))
                        messages.sort(key=lambda msg: (msg.get("created_at", 0), msg.get("id", 0)))
                        self._emit(messages)
                        with self._state_lock:
                            self._capability.update({
                                "state": "ready", "last_poll_at": time.time(),
                                "consecutive_failures": 0, "reason_code": None, "last_error": "",
                            })
                except Exception as exc:
                    self._handle_poll_error(exc)
            time.sleep(self.interval)

    def _handle_poll_error(self, exc):
        with self._state_lock:
            failures = int(self._capability.get("consecutive_failures", 0)) + 1
            self._capability.update({
                "state": "reconnecting", "reason_code": "DB_READ_FAILED",
                "last_error": type(exc).__name__, "consecutive_failures": failures,
                "reconnect_count": int(self._capability.get("reconnect_count", 0)) + 1,
            })
        self._close_connections()
        self._next_retry = time.monotonic() + min(30.0, 2 ** min(failures, 5))

    def _emit(self, messages):
        for message in messages or []:
            if not str(message.get("content", "")).strip():
                continue
            if self._on_message:
                self._on_message(message.get("conversation_name", ""), message)
            with self._state_lock:
                self._capability["last_message_at"] = time.time()

    def _close_connections(self):
        for path in list(self._shards):
            self._close_shard(path)
        if self._contact_conn:
            try:
                self._contact_conn.close()
            except Exception:
                pass
        self._contact_conn = None
