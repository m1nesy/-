# -*- coding: utf-8 -*-
"""Build the distributable app from the authoritative PyInstaller spec."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "PatentSalesPrompter"
ZIP_PATH = ROOT / "dist" / "PatentSalesPrompter.zip"
SOURCE_UI = ROOT / "app" / "ui" / "index.html"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        str(ROOT / "PatentSalesPrompter.spec"),
    ], cwd=ROOT, check=True)

    internal = DIST / "_internal"
    packaged_ui = internal / "app" / "ui" / "index.html"
    if sha256(SOURCE_UI) != sha256(packaged_ui):
        raise RuntimeError("打包 UI 与源码不一致，构建已停止")

    config_path = internal / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("llm", {})["enabled"] = False
    config["llm"]["api_key"] = ""
    config["llm"]["credential_store"] = ""
    config["patent_dirs"] = ""  # 新机器留空，首次启动自动检测
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    # 验证专利知识库是否打包进去了
    patent_kb = internal / "kb" / "patent_kb.json"
    if patent_kb.exists():
        kb_data = json.loads(patent_kb.read_text(encoding="utf-8"))
        print(f"  专利案例库已打包: {kb_data.get('total', 0)} 条记录")
    else:
        print("  [WARN] 专利案例库未找到，案例库功能将不可用")

    forbidden = ("pytesseract", "imagehash", "rpa_monitor")
    bundled_names = [path.name.lower() for path in internal.rglob("*")]
    found = [name for name in bundled_names if any(item in name for item in forbidden)]
    if found:
        raise RuntimeError(f"发布包包含已禁用的截图/OCR 模块: {found[:5]}")

    manifest = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "ui_sha256": sha256(SOURCE_UI),
        "message_access": ["standard-api", "accessibility-uia"],
        "ocr": False,
    }
    (internal / "build_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    instructions = ROOT / "dist" / "使用说明.txt"
    instructions.write_text(
        "专利销售智能提词器\n\n"
        "1. 双击 PatentSalesPrompter.exe 启动。\n"
        "2. 支持主流聊天软件的客户消息自动接入。\n"
        "3. AI 推荐引擎：根据客户对话上下文推荐最佳话术回复。\n"
        "4. 知识库管理：支持拼音搜索、分类浏览、话术编辑。\n"
        "5. 专利案例库：1876条专利证书索引，多维筛选搜索。\n"
        "6. 配置、话术和日志保存在 %LOCALAPPDATA%\\PatentSalesPrompter。\n"
        "7. 首次使用 AI 时在设置中填入 API Key。\n"
        "8. 案例库：将证书文件夹 1、2、3 复制到程序同目录下即可使用。\n",
        encoding="utf-8",
    )

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    shutil.make_archive(str(ZIP_PATH.with_suffix("")), "zip", ROOT / "dist", DIST.name)
    total_size = sum(path.stat().st_size for path in DIST.rglob("*") if path.is_file())
    print(f"构建完成: {DIST} ({total_size / 1024 / 1024:.1f} MB)")
    print(f"分发文件: {ZIP_PATH} ({ZIP_PATH.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
