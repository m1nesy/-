# -*- coding: utf-8 -*-
"""应用程序路径、原子化 JSON 持久化存储以及 DPAPI 密钥存储。"""

# ============================================================
# 导入标准库模块
# 模块名保持英文原名（Python 标准库要求）
# ============================================================
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path


# ============================================================
# 模块级路径常量定义
# 所有路径变量使用中文拼音命名，符合政策要求
# ============================================================

# 资源目录：打包后（frozen=True）位于可执行文件同级的 _internal 目录；
# 开发模式下（frozen=False）为当前文件的上上级目录（即项目根目录）
ZIYUAN_MULU = (Path(sys.executable).parent / "_internal" if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent.parent)

# 用户数据根目录：位于 Windows 本地应用数据目录（LOCALAPPDATA）下
YONGHU_MULU = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PatentSalesPrompter"

# 主配置文件路径（JSON 格式，存储应用的所有设置项）
PEIZHI_LUJING = YONGHU_MULU / "config.json"

# 知识库目录及其子文件路径
ZHISHIKU_MULU = YONGHU_MULU / "kb"
ZHISHIKU_LUJING = ZHISHIKU_MULU / "knowledge_base.json"          # 通用知识库
ZHUANLI_ZHISHIKU_LUJING = ZHISHIKU_MULU / "patent_kb.json"       # 专利知识库
TONGJI_LUJING = ZHISHIKU_MULU / "stats.json"                     # 统计数据文件
TUPIAN_MULU = ZHISHIKU_MULU / "images"                           # 图片资源目录

# 日志文件存储目录
RIZHI_MULU = YONGHU_MULU / "logs"

# 密钥存储文件路径（LLM API Key 使用 Windows DPAPI 加密后存于此文件）
MIMI_LUJING = YONGHU_MULU / "llm-key.bin"

# 对话记录持久化文件路径（版本 v1）
DUIHUA_LUJING = YONGHU_MULU / "conversations-v1.json"

# LLM 用量统计持久化文件路径（版本 v1）
YONGLIANG_LUJING = YONGHU_MULU / "llm-usage-v1.json"

# 对话数据迁移目录（用于版本升级时的数据格式转换）
QIANYI_MULU = YONGHU_MULU / "migrations" / "conversations"

# ============================================================
# 文件级锁管理
# 使用字典缓存每个文件路径对应的可重入锁（RLock），确保对同一文件
# 的并发读写操作是线程安全的。_SUO 字典本身受 _SUO_BAOHU 保护。
# ============================================================

# 锁缓存字典：键为文件路径字符串，值为 threading.RLock 可重入锁对象
_SUO = {}

# 保护 _SUO 字典自身读写操作的互斥锁，防止并发修改字典导致的数据竞争
_SUO_BAOHU = threading.Lock()


# ============================================================
# 内部辅助函数：获取指定路径对应的文件锁
# 如果该路径还没有分配锁，则在 _SUO_BAOHU 保护下创建一个新的 RLock
# ============================================================

def _huoqu_suo(lujing):
    """获取指定文件路径对应的可重入锁（RLock）。

    参数：
        lujing: 文件路径（字符串或 Path 对象）

    返回：
        threading.RLock 对象，与给定路径一一对应
    """
    # 将路径解析为绝对路径字符串，作为字典键
    jian = str(Path(lujing).resolve())
    # 在 _SUO_BAOHU 的保护下访问 _SUO 字典
    with _SUO_BAOHU:
        # setdefault 确保每个路径只创建一个 RLock 实例
        return _SUO.setdefault(jian, threading.RLock())


# ============================================================
# JSON 读取函数
# 以 UTF-8 编码读取 JSON 文件，若读取失败则返回默认值
# ============================================================

def du_json(lujing, morenzhi=None):
    """读取 JSON 文件并返回解析后的 Python 对象。

    参数：
        lujing:   JSON 文件的路径（字符串或 Path 对象）
        morenzhi: 读取失败时返回的默认值；若为 None 则返回空字典 {}

    返回：
        解析后的字典或列表；读取失败时返回默认值
    """
    try:
        # 以只读模式、UTF-8 编码打开文件
        with open(lujing, "r", encoding="utf-8") as wenjian:
            # 使用 json.load 将文件内容解析为 Python 对象
            return json.load(wenjian)
    except Exception:
        # 任何异常（文件不存在、JSON 格式错误等）都静默处理
        # 返回空字典（如果调用方未指定默认值）
        if morenzhi is None:
            jieguo = {}
            return jieguo
        else:
            return morenzhi


# ============================================================
# 内部函数：无锁版本的原子化 JSON 写入
# 使用"写入临时文件 + 原子替换"策略，确保写入过程中不会损坏原文件
# ============================================================

def _xie_json_wei_jiasuo(lujing, shuju, beifen=False):
    """将数据以 JSON 格式原子化地写入文件（不加锁，由调用方负责加锁）。

    原子化写入策略：
    1. 先在目标目录下创建一个临时文件
    2. 将 JSON 数据写入临时文件并 fsync 刷盘
    3. 使用 os.replace 将临时文件原子替换为目标文件
    4. 清理临时文件（正常情况下已被 replace 移走）

    参数：
        lujing: 目标文件路径
        shuju:  要写入的 Python 对象（可被 json.dump 序列化）
        beifen: 是否在写入前创建 .bak 备份文件
    """
    # 统一转为 Path 对象，便于操作
    lujing = Path(lujing)
    # 确保目标目录存在（递归创建父目录）
    lujing.parent.mkdir(parents=True, exist_ok=True)
    # 如果需要备份且目标文件已存在，则复制一份 .bak 文件
    if beifen and lujing.exists():
        shutil.copy2(lujing, lujing.with_suffix(lujing.suffix + ".bak"))
    # 在目标目录下创建临时文件，获取文件描述符和临时文件名
    miaoshuzi, linshi_ming = tempfile.mkstemp(
        prefix=lujing.name + ".",
        suffix=".tmp",
        dir=lujing.parent
    )
    try:
        # 通过文件描述符打开临时文件进行写入
        with os.fdopen(miaoshuzi, "w", encoding="utf-8") as wenjian:
            # 写入 JSON 数据，ensure_ascii=False 保留中文原文
            json.dump(shuju, wenjian, ensure_ascii=False, indent=2)
            # 强制刷新用户态缓冲区到内核
            wenjian.flush()
            # 强制将内核缓冲区写入磁盘（确保数据持久化）
            os.fsync(wenjian.fileno())
        # 原子替换：将临时文件重命名为目标文件（Windows 上近似原子操作）
        os.replace(linshi_ming, lujing)
    finally:
        # 清理：尝试删除临时文件（若 replace 成功则临时文件已不存在）
        try:
            os.unlink(linshi_ming)
        except FileNotFoundError:
            # 文件已被 replace 移走，忽略此异常
            pass


# ============================================================
# 公开函数：原子化 JSON 写入（带锁保护）
# ============================================================

def yuanzixie_json(lujing, shuju, beifen=False):
    """以原子方式将数据写入 JSON 文件（加锁保护，线程安全）。

    参数：
        lujing: 目标文件路径
        shuju:  要写入的 Python 对象
        beifen: 是否在写入前创建 .bak 备份文件
    """
    # 获取该文件对应的锁，并在锁保护下执行无锁写入
    with _huoqu_suo(lujing):
        _xie_json_wei_jiasuo(lujing, shuju, beifen)


# ============================================================
# 原子化的"读取-修改-写入"操作
# 在同一个锁内完成读取、修改、写入三个步骤，保证操作的原子性
# ============================================================

def gengxin_json(lujing, moren_gongchang, bianyiqi, beifen=False):
    """在同一个锁下原子化地读取、修改并替换 JSON 文档。

    执行流程：
    1. 加锁
    2. 读取 JSON 文档（若文件不存在则调用 moren_gongchang 生成默认值）
    3. 调用 bianyiqi 函数对文档进行原地修改
    4. 将修改后的文档写回文件
    5. 释放锁

    参数：
        lujing:          JSON 文件路径
        moren_gongchang: 当文件不存在时，用于生成默认文档的可调用对象
        bianyiqi:        接收文档字典作为参数，对其进行原地修改的函数；
                         返回值将被透传给调用方
        beifen:          写入前是否创建 .bak 备份

    返回：
        bianyiqi 函数的返回值
    """
    # 统一转为 Path 对象
    lujing = Path(lujing)
    # 获取文件对应的锁，确保整个读取-修改-写入过程是原子的
    with _huoqu_suo(lujing):
        # 步骤1：读取或创建文档
        if lujing.exists():
            # 文件存在：读取内容，若读取失败则返回 None
            wendang = du_json(lujing, None)
        else:
            # 文件不存在：使用工厂函数生成默认文档
            wendang = moren_gongchang()
        # 步骤2：类型校验，确保文档是字典类型
        if not isinstance(wendang, dict):
            # 如果不是字典（例如是列表），则回退为默认空字典
            wendang = moren_gongchang()
        # 步骤3：调用修改器函数，同时获取返回值
        jieguo = bianyiqi(wendang)
        # 步骤4：将修改后的文档写回文件
        _xie_json_wei_jiasuo(lujing, wendang, beifen)
        # 步骤5：返回修改器的返回值
        return jieguo


# ============================================================
# DPAPI 密钥存储（Windows 平台专用）
# 使用 Windows DPAPI（数据保护 API）加密敏感信息后存储到文件
# ============================================================

def baocun_mimi(zhi):
    """使用 Windows DPAPI 加密并保存密钥到文件。

    参数：
        zhi: 要保存的密钥字符串；若为空字符串则删除密钥文件
    """
    # 将值转为字符串并去除首尾空白
    zhi = str(zhi or "").strip()
    # 确保密钥文件所在目录存在
    MIMI_LUJING.parent.mkdir(parents=True, exist_ok=True)
    # 如果值为空，则删除已存在的密钥文件（表示清除密钥）
    if not zhi:
        try:
            MIMI_LUJING.unlink()
        except FileNotFoundError:
            # 文件本来就不存在，无需处理
            pass
        return
    # 导入 Windows DPAPI 加密模块（仅在需要时导入）
    import win32crypt
    # 调用 CryptProtectData 对 UTF-8 编码后的密钥进行加密
    # 第二个参数 "PatentSalesPrompter" 为可选熵值，增加安全性
    jiami = win32crypt.CryptProtectData(
        zhi.encode("utf-8"),
        "PatentSalesPrompter",
        None,
        None,
        None,
        0
    )
    # 先写入临时文件，再原子替换，避免写入过程中断电导致文件损坏
    linshi = MIMI_LUJING.with_suffix(".tmp")
    linshi.write_bytes(jiami)
    os.replace(linshi, MIMI_LUJING)


def jiazai_mimi():
    """从 DPAPI 加密文件中读取并解密密钥。

    返回：
        解密后的密钥字符串；若文件不存在或解密失败则返回空字符串 ""
    """
    try:
        # 导入 Windows DPAPI 解密模块
        import win32crypt
        # 读取加密文件内容（二进制模式）
        jiami_shuju = MIMI_LUJING.read_bytes()
        # 调用 CryptUnprotectData 解密
        # 返回值是一个元组，索引 [1] 为解密后的字节数据
        jiemi_byte = win32crypt.CryptUnprotectData(
            jiami_shuju,
            None,
            None,
            None,
            0
        )[1]
        # 将解密后的字节数据以 UTF-8 解码为字符串并返回
        jiemi_str = jiemi_byte.decode("utf-8")
        return jiemi_str
    except Exception:
        # 任何异常（文件不存在、解密失败等）都返回空字符串
        return ""


# ============================================================
# 用户数据初始化
# 首次运行时创建所有必需的目录，并从资源目录复制默认数据文件
# ============================================================

def chushihua_yonghu_shuju():
    """初始化用户数据目录结构，首次运行时从资源目录复制默认文件。

    功能：
    1. 创建用户数据目录、知识库目录、图片目录、日志目录
    2. 如果配置文件不存在，从资源目录复制默认配置
    3. 处理旧版配置中的明文 API Key 迁移（明文 → DPAPI 加密存储）
    4. 复制默认知识库、专利知识库、统计数据文件
    5. 复制默认图片资源

    返回：
        用户数据根目录路径（Path 对象）
    """
    # ---- 步骤1：创建必要的目录结构 ----
    YONGHU_MULU.mkdir(parents=True, exist_ok=True)
    ZHISHIKU_MULU.mkdir(parents=True, exist_ok=True)
    TUPIAN_MULU.mkdir(parents=True, exist_ok=True)
    RIZHI_MULU.mkdir(parents=True, exist_ok=True)

    # ---- 步骤2：初始化主配置文件 ----
    # 资源目录中的默认配置文件路径
    moren_peizhi = ZIYUAN_MULU / "config.json"
    # 只有当用户配置文件不存在时才进行初始化
    if not PEIZHI_LUJING.exists():
        # 从资源目录读取默认配置
        peizhi = du_json(moren_peizhi, {})
        # 检查旧版配置中是否包含明文 API Key（需要迁移到 DPAPI 加密存储）
        yiliu_miyao = str(peizhi.get("llm", {}).get("api_key", "")).strip()
        if yiliu_miyao:
            # 存在旧版明文密钥：先备份旧配置文件
            beifen_wenjian = YONGHU_MULU / f"legacy-config-{time.strftime('%Y%m%d-%H%M%S')}.json"
            shutil.copy2(moren_peizhi, beifen_wenjian)
            # 将明文密钥迁移到 DPAPI 加密存储
            baocun_mimi(yiliu_miyao)
            # 清除配置文件中的明文密钥字段
            peizhi.setdefault("llm", {})["api_key"] = ""
            # 标记密钥存储方式为 dpapi
            peizhi["llm"]["credential_store"] = "dpapi"
        # 将处理后的配置写入用户配置文件
        yuanzixie_json(PEIZHI_LUJING, peizhi)

    # ---- 步骤3：复制默认知识库文件 ----
    moren_zhishiku = ZIYUAN_MULU / "kb" / "knowledge_base.json"
    if not ZHISHIKU_LUJING.exists() and moren_zhishiku.exists():
        # 用户知识库不存在且默认知识库存在，则复制
        shutil.copy2(moren_zhishiku, ZHISHIKU_LUJING)

    # ---- 步骤4：复制默认专利知识库文件 ----
    moren_zhuanli_zhishiku = ZIYUAN_MULU / "kb" / "patent_kb.json"
    if not ZHUANLI_ZHISHIKU_LUJING.exists() and moren_zhuanli_zhishiku.exists():
        # 用户专利知识库不存在且默认专利知识库存在，则复制
        shutil.copy2(moren_zhuanli_zhishiku, ZHUANLI_ZHISHIKU_LUJING)

    # ---- 步骤5：复制默认统计数据文件 ----
    moren_tongji = ZIYUAN_MULU / "kb" / "stats.json"
    if not TONGJI_LUJING.exists() and moren_tongji.exists():
        # 用户统计数据文件不存在且默认统计数据存在，则复制
        shutil.copy2(moren_tongji, TONGJI_LUJING)

    # ---- 步骤6：复制默认图片资源 ----
    moren_tupian = ZIYUAN_MULU / "kb" / "images"
    if moren_tupian.is_dir():
        # 遍历默认图片目录中的每一个条目
        for laiyuan in moren_tupian.iterdir():
            # 确定目标文件路径
            mubiao = TUPIAN_MULU / laiyuan.name
            # 仅复制文件（跳过子目录），且不覆盖已存在的文件
            if laiyuan.is_file() and not mubiao.exists():
                shutil.copy2(laiyuan, mubiao)

    # 返回用户数据根目录路径
    return YONGHU_MULU


# ============================================================
# 加载配置（公开接口）
# 供应用其他模块调用，获取完整的应用配置
# ============================================================

def jiazai_peizhi():
    """加载完整的应用配置。

    执行流程：
    1. 确保用户数据目录已初始化
    2. 从磁盘读取配置文件
    3. 从 DPAPI 加密存储中加载 API Key 并注入到配置字典中

    返回：
        包含所有配置项的字典，其中 llm.api_key 已从加密存储中加载
    """
    # 确保用户数据目录和默认文件已就绪
    chushihua_yonghu_shuju()
    # 读取配置文件
    peizhi = du_json(PEIZHI_LUJING, {})
    # 从 DPAPI 加密存储中加载 API 密钥，注入到配置字典
    peizhi.setdefault("llm", {})["api_key"] = jiazai_mimi()
    # 返回完整配置
    return peizhi


# ============================================================
# 保存配置（公开接口）
# 供应用其他模块调用，持久化应用配置
# ============================================================

def baocun_peizhi(peizhi):
    """保存应用配置到磁盘。

    执行流程：
    1. 确保用户数据目录已初始化
    2. 深拷贝配置（避免修改原始对象）
    3. 提取并单独保存 API Key 到 DPAPI 加密存储
    4. 将剩余配置写入 JSON 文件

    参数：
        peizhi: 包含所有配置项的字典
    """
    # 确保用户数据目录和默认文件已就绪
    chushihua_yonghu_shuju()
    # 通过 JSON 序列化/反序列化实现深拷贝，避免修改调用方传入的原始字典
    qingli = json.loads(json.dumps(peizhi))
    # 从配置中提取 API 密钥（取出后从配置字典中删除）
    miyao = str(qingli.setdefault("llm", {}).pop("api_key", "") or "").strip()
    # 将密钥单独保存到 DPAPI 加密存储
    baocun_mimi(miyao)
    # 更新 credential_store 字段，标记密钥的存储方式
    if miyao:
        qingli["llm"]["credential_store"] = "dpapi"
    else:
        qingli["llm"]["credential_store"] = ""
    # 将清理后的配置（不含明文密钥）原子化写入磁盘，同时创建备份
    yuanzixie_json(PEIZHI_LUJING, qingli, beifen=True)
