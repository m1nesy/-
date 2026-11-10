import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import storage
from test_support import LinshiShujuTests


class CredentialTests(LinshiShujuTests):
    def test_dpapi_save_load_replace_and_delete(self):
        storage.baocun_peizhi({"llm": {"enabled": True, "api_key": "sk-test-first"}})
        self.assertEqual(storage.jiazai_peizhi()["llm"]["api_key"], "sk-test-first")
        self.assertNotIn(b"sk-test-first", storage.MIMI_LUJING.read_bytes())
        self.assertNotIn("api_key", storage.du_json(storage.PEIZHI_LUJING)["llm"])
        storage.baocun_peizhi({"llm": {"enabled": True, "api_key": "sk-test-second"}})
        self.assertEqual(storage.jiazai_mimi(), "sk-test-second")
        storage.baocun_peizhi({"llm": {"enabled": False, "api_key": ""}})
        self.assertFalse(storage.MIMI_LUJING.exists())
        self.assertEqual(storage.jiazai_peizhi()["llm"]["api_key"], "")
        for path in self.user_dir.glob("*.json*"):
            self.assertNotIn("sk-test-", path.read_text(encoding="utf-8"))

    def test_first_run_migrates_and_scrubs_resource_and_legacy_backups(self):
        self.write_config(self.resources / "config.json", "legacy-resource", window={"width": 720})
        backup = self.user_dir / "legacy-config-20200101.json"
        self.write_config(backup, "old-backup", window={"width": 600})
        self.write_config(storage.PEIZHI_LUJING.with_suffix(".json.bak"), "old-config")
        config = storage.jiazai_peizhi()
        self.assertEqual(config["llm"]["api_key"], "legacy-resource")
        self.assertEqual(config["window"]["width"], 720)
        self.assertEqual(storage.du_json(backup)["window"]["width"], 600)
        for path in list(self.user_dir.glob("*.json*")) + [self.resources / "config.json"]:
            self.assertNotIn("api_key", storage.du_json(path).get("llm", {}))

    def test_existing_plaintext_user_config_is_migrated(self):
        self.write_config(storage.PEIZHI_LUJING, "existing-user")
        self.assertEqual(storage.jiazai_peizhi()["llm"]["api_key"], "existing-user")
        self.assertNotIn("api_key", storage.du_json(storage.PEIZHI_LUJING)["llm"])

    def test_existing_encrypted_key_has_priority_over_old_plaintext(self):
        storage.baocun_mimi("current-encrypted")
        self.write_config(storage.PEIZHI_LUJING, "obsolete-key")
        self.assertEqual(storage.jiazai_peizhi()["llm"]["api_key"], "current-encrypted")
        self.assertNotIn("obsolete-key", storage.PEIZHI_LUJING.read_text())

    def test_backup_does_not_restore_a_deleted_key(self):
        storage.chushihua_yonghu_shuju()
        self.write_config(self.user_dir / "legacy-config-old.json", "revoked-key")
        self.assertEqual(storage.jiazai_peizhi()["llm"]["api_key"], "")
        self.assertFalse(storage.MIMI_LUJING.exists())

    def test_encryption_failure_preserves_original_config(self):
        self.write_config(storage.PEIZHI_LUJING, "recoverable-key")
        original = storage.PEIZHI_LUJING.read_bytes()
        with patch("win32crypt.CryptProtectData", side_effect=RuntimeError("DPAPI unavailable")):
            with self.assertRaises(RuntimeError):
                storage.jiazai_peizhi()
        self.assertEqual(storage.PEIZHI_LUJING.read_bytes(), original)
        self.assertFalse(storage.MIMI_LUJING.exists())

    def test_failed_atomic_replace_keeps_previous_key_and_removes_temp_file(self):
        storage.baocun_mimi("previous-key")
        previous = storage.MIMI_LUJING.read_bytes()
        with patch("storage.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                storage.baocun_mimi("replacement")
        self.assertEqual(storage.MIMI_LUJING.read_bytes(), previous)
        self.assertEqual(list(self.user_dir.glob("llm-key.*.tmp")), [])

    def test_concurrent_secret_saves_leave_a_decryptable_complete_key(self):
        keys = [f"concurrent-{i}" for i in range(8)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(storage.baocun_mimi, keys))
        self.assertIn(storage.jiazai_mimi(), keys)
        self.assertEqual(list(self.user_dir.glob("*.tmp")), [])

    def test_empty_resource_config_is_not_rewritten(self):
        with patch("storage.yuanzixie_json", wraps=storage.yuanzixie_json) as write:
            storage.chushihua_yonghu_shuju()
        self.assertNotIn(self.resources / "config.json", [call.args[0] for call in write.call_args_list])


if __name__ == "__main__":
    unittest.main()
