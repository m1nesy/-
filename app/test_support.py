"""回归测试使用独立的数据目录，不读写真实用户配置。"""
import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import storage


class LinshiShujuTests(unittest.TestCase):
    modules = ()

    def setUp(self):
        super().setUp()
        linshi = tempfile.TemporaryDirectory(prefix="prompter-test-")
        self.addCleanup(linshi.cleanup)
        self.root = Path(linshi.name)
        self.resources = self.root / "resources"
        self.resources.mkdir()
        source = Path(__file__).resolve().parent.parent
        shutil.copy2(source / "config.json", self.resources / "config.json")
        shutil.copytree(source / "kb", self.resources / "kb")
        original = storage.YONGHU_MULU
        self.user_dir = self.root / "user"
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (storage,) + self.modules:
            for name, value in list(vars(module).items()):
                if isinstance(value, Path) and value.is_relative_to(original):
                    stack.enter_context(patch.object(module, name, self.user_dir / value.relative_to(original)))
        stack.enter_context(patch.object(storage, "ZIYUAN_MULU", self.resources))

    def write_config(self, path, key, **settings):
        config = {"llm": {"enabled": True, "api_key": key}, **settings}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")
        return config
