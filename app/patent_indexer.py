# -*- coding: utf-8 -*-
"""专利证书索引器：扫描三个文件夹，从文件名提取元数据，生成 patent_kb.json"""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

# ---- 三个证书文件夹 ----
PATENT_DIRS = [
    Path(r"E:\专利销售智能提词器\1"),
    Path(r"E:\专利销售智能提词器\2"),
    Path(r"E:\专利销售智能提词器\3"),
]

# ---- 证书根目录（路径以此为基准存储相对路径，便于移植） ----
PATENT_BASE = Path(r"E:\专利销售智能提词器")

# ---- 输出路径 ----
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "kb" / "patent_kb.json"

# ---- 文件扩展名 ----
VALID_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}

# ============================================================
# 1. 文件名解析正则
# ============================================================

# 专利号：证书后面跟的数字串（可能带 X 后缀）
CERT_NO_RE = re.compile(r"证书[-\s]*(\d{13,14}X?)", re.IGNORECASE)

# 时间周期：X个月 / X年
TIMELINE_RE = re.compile(r"(\d+\.?\d*)\s*个?\s*(月|年)")

# 日期范围：如 25.6.19-26.4.17 或 2025.6.19-2026.4.17
DATE_RANGE_RE = re.compile(r"(\d{2,4}\.\d{1,2}\.\d{1,2})\s*[-–]\s*(\d{2,4}\.\d{1,2}\.\d{1,2})")

# ============================================================
# 2. 专利类型前缀识别
# ============================================================

# 前缀模式 → (patent_category, exam_type, patent_type_display)
# patent_category: 发明/实用新型/外观设计
# exam_type: 普通/预审/优审
PREFIX_PATTERNS = [
    # === 发明 - 优审 ===
    (re.compile(r"^优审"), "发明", "优审", "发明优审"),
    (re.compile(r"^[A-Z]*优审"), "发明", "优审", "发明优审"),  # A优审, B优审, XK优审, XS优审, KT优审
    # === 发明 - 预审 ===
    (re.compile(r"^预审"), "发明", "预审", "发明预审"),
    (re.compile(r"^[A-Z]*预审"), "发明", "预审", "发明预审"),  # D预审, DD预审, A预审, H预审, XK预审, XS预审, KT预审, Z预审
    # === 发明 - 普通 ===
    (re.compile(r"^普通"), "发明", "普通", "普通发明"),
    (re.compile(r"^[A-Z]发明"), "发明", "普通", "普通发明"),  # B发明
    (re.compile(r"^B普通"), "发明", "普通", "普通发明"),
    # === 实用新型 - 预审 ===
    (re.compile(r"^实用预审"), "实用新型", "预审", "实用预审"),
    (re.compile(r"^[A-Z]实用预审"), "实用新型", "预审", "实用预审"),  # D实用预审
    # === 实用新型 - 普通 ===
    (re.compile(r"^实用(?!预审)"), "实用新型", "普通", "普通实用"),
    (re.compile(r"^[A-Z]+实用(?!预审)"), "实用新型", "普通", "普通实用"),  # B实用, A实用, KT实用, X实用
    # === 外观设计 ===
    (re.compile(r"^外观"), "外观设计", "普通", "普通外观"),
    (re.compile(r"^[A-Z]+外观"), "外观设计", "普通", "普通外观"),  # B外观, A外观, XS外观
]

# 从专利名称关键词推断
NAME_TYPE_PATTERNS = [
    (re.compile(r"实用(新型)?专利"), "实用新型", "普通", "普通实用"),
    (re.compile(r"外观设计(专利)?"), "外观设计", "普通", "普通外观"),
    (re.compile(r"发明(专利)?"), "发明", "普通", "普通发明"),
]

# ============================================================
# 3. 申请人类型推断
# ============================================================

SCHOOL_KW = ["大学", "学院", "学校", "附属中学", "附属小学", "附属幼儿园",
             "职业技术学院", "职业中专", "中学", "小学"]
HOSPITAL_KW = ["医院", "卫生院", "卫生服务中心", "疾控中心", "急救中心",
               "血液中心", "妇幼保健", "社区卫生", "卫生室"]
MILITARY_KW = ["中国人民解放军", "部队", "军区", "武警", "陆军", "海军",
               "空军", "火箭军", "战略支援", "联勤保障", "武装警察"]
COMPANY_KW = ["有限公司", "有限责任公司", "公司", "集团", "研究院",
              "设计院", "中心", "事务所", "工作室", "实验室"]

def infer_applicant_type(name: str, dir_path: str = "") -> str:
    """从申请人名称 + 目录路径推断申请人类型"""
    if name:
        # 部队优先（含"部队医院"的既是部队也是医院，归为部队）
        if any(kw in name for kw in MILITARY_KW):
            return "部队"
        # 医院
        if any(kw in name for kw in HOSPITAL_KW):
            return "医院"
        # 学校
        if any(kw in name for kw in SCHOOL_KW):
            return "学校"
        # 单位
        if any(kw in name for kw in COMPANY_KW):
            return "单位"

    # 从目录路径推断
    dir_path_lower = dir_path.lower()
    if "个人" in dir_path:
        return "个人"
    if "部队" in dir_path or "部队" in dir_path_lower:
        return "部队"
    if "医院" in dir_path or "医院" in dir_path_lower or "医疗" in dir_path:
        return "医院"
    if "学校" in dir_path or "学院" in dir_path or "大学" in dir_path:
        return "学校"
    if "单位" in dir_path:
        return "单位"
    if "国际" in dir_path:
        return "单位"

    # 有申请人名但无法判断 → 单位（多数有组织名的都是单位）
    if name:
        return "单位"

    # 专利-领域下的默认为单位（企业专利为主）
    if "专利-领域" in dir_path:
        return "单位"

    # 各类发明、外观实用 → 默认为单位（混合，以单位为主）
    if "各类发明" in dir_path or "外观实用" in dir_path:
        return "单位"

    # 无法判断的默认个人
    return "个人"


# ============================================================
# 4. 行业领域推断
# ============================================================

# 从目录路径提取领域的关键词映射
DIR_FIELD_MAP = {
    "农业": "农业",
    "工程": "工程",
    "建筑": "建筑",
    "控制": "自动控制",
    "机械": "机械",
    "林业": "林业",
    "档案": "档案管理",
    "电子": "电子信息",
    "医疗": "医疗",
    "医学": "医疗",
    "护理": "医疗",
    "临床": "医疗",
    "外科": "医疗",
    "药": "医疗",
    "国际": "国际",
    "部队": "军事",
    "医院": "医疗",
}

# 从专利名称关键词推断领域
NAME_FIELD_KW = {
    "农业": ["农业", "农用", "种植", "灌溉", "施肥", "育苗", "农药", "收割",
             "农作物", "土壤", "农具", "喷洒", "垄沟", "害虫", "诱捕", "喷灌"],
    "工程": ["工程", "施工", "土木", "水利", "市政", "道路", "桥梁", "隧道",
             "测绘", "勘察", "地基", "混凝土", "钢结构", "脚手架"],
    "建筑": ["建筑", "墙体", "幕墙", "装饰板", "爬梯", "降尘", "通风装置",
             "钢筋", "检测尺", "敲击锤", "防水", "装配式"],
    "机械": ["机械", "加工", "夹具", "挖掘机", "属具", "齿轮", "轴承",
             "焊接", "切削", "打磨", "传动", "液压", "气缸"],
    "电子信息": ["电子", "电气", "电路", "芯片", "半导体", "传感器", "光纤",
               "通信", "信号", "天线", "雷达", "计算机", "软件", "系统",
               "数据", "算法", "图像识别", "机器学习", "人工智能"],
    "自动控制": ["控制", "PLC", "自动化", "机器人", "AGV", "数控",
               "控制器", "控制系统", "监测", "检测系统", "识别系统"],
    "医疗": ["手术", "护理", "临床", "外科", "内科", "眼科", "口腔", "牙科",
             "骨科", "妇科", "儿科", "麻醉", "检验", "诊断", "治疗",
             "康复", "中医", "中药", "针灸", "艾灸", "耳石", "心电",
             "输液", "注射", "导管", "支架", "植入", "假体", "生物",
             "细胞", "基因", "蛋白", "肿瘤", "癌", "药物", "制剂",
             "医学影像", "CT", "MRI", "超声", "内镜", "监护仪"],
    "交通运输": ["交通", "运输", "车辆", "汽车", "航空", "航天", "飞机",
               "无人机", "船舶", "港口", "物流", "仓储", "充电桩"],
    "能源矿业": ["煤矿", "矿井", "煤流", "掘进", "采矿", "光伏", "太阳能",
               "储能", "发电", "新能源", "天然气", "石油", "碳基"],
    "环境": ["环保", "环境", "废气", "废水", "垃圾", "除尘", "净化",
             "生态", "护坡", "绿化", "植草"],
    "教育": ["教学", "教具", "教育", "学习", "培训", "实训", "实验",
             "学前教育", "乐谱", "书签", "笔筒", "画架", "画桌"],
    "体育": ["体育", "运动", "健身", "哑铃", "双杠", "吊环", "弹弓",
             "风筝", "三角铁", "打击乐器"],
    "食品": ["食品", "饮料", "检测", "营养", "膳食", "食品安全"],
    "艺术设计": ["艺术设计", "雕塑", "装饰", "摆件", "胸针", "发簪",
               "蜡染", "纹样", "茶具", "棋盘", "月琴"],
    "法律": ["法律", "法学"],
}

def infer_field(dir_path: str, patent_name: str) -> str:
    """从目录路径 + 专利名称推断行业领域"""
    # 1. 先从目录路径提取
    dir_lower = dir_path.lower()
    for kw, field in DIR_FIELD_MAP.items():
        if kw in dir_path or kw in dir_lower:
            return field
    # 2. 从专利名称关键词匹配
    if patent_name:
        scores = Counter()
        for field, keywords in NAME_FIELD_KW.items():
            for kw in keywords:
                if kw in patent_name:
                    scores[field] += 1
        if scores:
            return scores.most_common(1)[0][0]
    # 3. 默认
    return "其他"


# ============================================================
# 5. 国际辖区推断
# ============================================================

JURISDICTION_MAP = {
    "美国": "美国", "日本": "日本", "德国": "德国", "英国": "英国",
    "法国": "法国", "欧盟": "欧盟", "澳大利亚": "澳大利亚",
    "香港": "香港", "卢森堡": "卢森堡", "尼日利亚": "尼日利亚",
    "韩国": "韩国", "加拿大": "加拿大", "新加坡": "新加坡",
    "国际": "国际",
}


def infer_jurisdiction(dir_path: str) -> str:
    for kw, jur in JURISDICTION_MAP.items():
        if kw in dir_path:
            return jur
    return "中国"


# ============================================================
# 6. 文件名解析核心
# ============================================================

def parse_filename(filepath: Path, base_dir: Path = None) -> dict:
    """从单个文件名提取所有元数据。base_dir 用于计算相对路径"""
    stem = filepath.stem  # 去掉扩展名的文件名
    dir_path = str(filepath.parent)

    # 计算相对路径（用于跨机器移植）
    if base_dir:
        try:
            rel_path = filepath.resolve().relative_to(base_dir.resolve())
        except ValueError:
            rel_path = filepath  # 不在 base_dir 下，用绝对路径
    else:
        rel_path = filepath

    # 提取专利号
    cert_no = ""
    cert_match = CERT_NO_RE.search(stem)
    if cert_match:
        cert_no = cert_match.group(1)

    # 提取时间周期
    timeline = ""
    tl_match = TIMELINE_RE.search(stem)
    if tl_match:
        timeline = tl_match.group(1) + tl_match.group(2)

    # 提取日期范围
    date_range = ""
    dr_match = DATE_RANGE_RE.search(stem)
    if dr_match:
        date_range = f"{dr_match.group(1)}-{dr_match.group(2)}"

    # --- 解析文件名段 ---
    # 按 "-" 分割（但注意中文组织名和专利名中可能也有短横）
    # 策略：先找关键分隔符 "-证书" 和已知前缀
    parts = stem.split("-")

    # 识别专利类型
    patent_category = ""
    exam_type = ""
    patent_type_display = ""

    # 先按前缀匹配
    matched_prefix = False
    for pattern_re, cat, exam, display in PREFIX_PATTERNS:
        m = pattern_re.match(stem)
        if m:
            patent_category = cat
            exam_type = exam
            patent_type_display = display
            matched_prefix = True
            break

    # 也检查第一部分是否为已知前缀（包括日期前缀如 10.10预审-）
    if not matched_prefix and parts:
        first = parts[0].strip()
        # 国际专利前缀：如 "卢森堡发明专利—", "尼日利亚发明-", "美国发明——"
        intl_match = re.match(r"^[一-鿿]+(发明|实用|外观)", first)
        if intl_match:
            if "发明" in first:
                patent_category, exam_type, patent_type_display = "发明", "普通", "普通发明"
            elif "实用" in first:
                patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"
            elif "外观" in first:
                patent_category, exam_type, patent_type_display = "外观设计", "普通", "普通外观"
            matched_prefix = True
        # 日期数字开头的：提取第二部分作为类型前缀
        elif re.match(r"^\d+\.\d+", first):
            for i, p in enumerate(parts):
                p_stripped = p.strip()
                # 去掉日期前缀再匹配：如 "10.10预审" → "预审"
                clean_p = re.sub(r"^[\d.]+", "", p_stripped)
                clean_p = clean_p.replace("打码", "")
                for pattern_re, cat, exam, display in PREFIX_PATTERNS:
                    if pattern_re.match(clean_p):
                        patent_category = cat
                        exam_type = exam
                        patent_type_display = display
                        matched_prefix = True
                        break
                if matched_prefix:
                    break
        # 检查第一部分本身：如 "6.13普通" → 普通发明
        else:
            # 去掉日期部分，检查剩余
            clean_first = re.sub(r"^[\d.]+", "", first)
            if clean_first:
                for pattern_re, cat, exam, display in PREFIX_PATTERNS:
                    if pattern_re.match(clean_first):
                        patent_category = cat
                        exam_type = exam
                        patent_type_display = display
                        matched_prefix = True
                        break

    # 从名称关键词推断（后备）
    if not patent_category:
        for pattern_re, cat, exam, display in NAME_TYPE_PATTERNS:
            if pattern_re.search(stem):
                patent_category = cat
                exam_type = exam
                patent_type_display = display
                break

    # 从目录路径推断类型
    if not patent_category:
        dir_name = os.path.basename(dir_path)
        parent_dir = os.path.basename(os.path.dirname(dir_path))
        grandparent_dir = os.path.basename(os.path.dirname(os.path.dirname(dir_path)))
        # 构建完整路径用于匹配
        full_dir_context = f"{grandparent_dir}/{parent_dir}/{dir_name}"

        if "发明" in dir_name or "发明" in parent_dir:
            if "预审" in dir_name or "预审" in parent_dir:
                patent_category, exam_type, patent_type_display = "发明", "预审", "发明预审"
            elif "优审" in dir_name or "优审" in parent_dir:
                patent_category, exam_type, patent_type_display = "发明", "优审", "发明优审"
            else:
                patent_category, exam_type, patent_type_display = "发明", "普通", "普通发明"
        elif "实用" in dir_name or "实用" in parent_dir:
            if "预审" in dir_name or "预审" in parent_dir:
                patent_category, exam_type, patent_type_display = "实用新型", "预审", "实用预审"
            else:
                patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"
        elif "外观" in dir_name or "外观" in parent_dir or "外观" in grandparent_dir:
            patent_category, exam_type, patent_type_display = "外观设计", "普通", "普通外观"
        # "专利-领域"下的子目录 → 根据文件名关键词推断
        elif "专利-领域" in full_dir_context or "专利-领域" in parent_dir:
            # 从文件名判断：含"实用"关键词 → 实用新型
            if any(kw in stem for kw in ["实用", "实用新型"]):
                patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"
            elif any(kw in stem for kw in ["外观"]):
                patent_category, exam_type, patent_type_display = "外观设计", "普通", "普通外观"
            else:
                # 专利-领域下的大部分是实用新型（证书号23开头）
                if cert_no.startswith("2023") or cert_no.startswith("2024") or cert_no.startswith("2025"):
                    patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"
                else:
                    patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"
        # 根目录下文件，无法判断
        elif not patent_category:
            # 最后兜底：从文件名判断
            if "发明" in stem:
                patent_category, exam_type, patent_type_display = "发明", "普通", "普通发明"
            elif "实用" in stem:
                patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"
            elif "外观" in stem:
                patent_category, exam_type, patent_type_display = "外观设计", "普通", "普通外观"
            else:
                patent_category, exam_type, patent_type_display = "实用新型", "普通", "普通实用"

    # --- 提取申请人名称 ---
    applicant = ""
    # 策略：在 parts 中找到包含组织关键词的那一项
    for i, p in enumerate(parts):
        p_stripped = p.strip()
        if not p_stripped:
            continue
        # 跳过明显的非组织名（时间、类型前缀、纯数字等）
        if re.match(r"^[\d.]+$", p_stripped):
            continue
        if re.match(r"^(证书|授权|B?优审|B?预审|D?预审|B?发明|B?普通|实用|外观|A|B|DD?)", p_stripped):
            continue
        if p_stripped in ("", "pdf", "jpg", "png", "证书"):
            continue
        # 检查是否是组织名（含组织关键词）
        all_kw = SCHOOL_KW + HOSPITAL_KW + MILITARY_KW + COMPANY_KW
        if any(kw in p_stripped for kw in all_kw):
            applicant = p_stripped
            break

    # 如果没有找到组织名，尝试用第一个非类型前缀的较长部分作为专利名/申请人
    # 对于个人专利（文件夹在"个人"下），提取专利名
    applicant_type = infer_applicant_type(applicant, dir_path)

    # --- 提取专利名称 ---
    patent_name = ""
    # 去掉前缀、证书号、时间等，剩余的核心部分
    # 从 parts 中提取：跳过已识别的类型前缀、时间、申请人
    name_parts = []
    skip_next = False
    for p in parts:
        p_stripped = p.strip()
        if not p_stripped:
            continue
        # 跳过类型前缀
        if re.match(r"^(B?优审|B?预审|D?预审|B?发明|B?普通|B?外观|B?实用|实用预审|实用[^预]?|外观|A优审|A预审|A实用|A外观|A|B|DD?)$", p_stripped):
            continue
        # 跳过时间周期
        if TIMELINE_RE.match(p_stripped):
            continue
        # 跳过日期
        if re.match(r"^[\d.]+$", p_stripped):
            continue
        # 跳过证书号相关
        if CERT_NO_RE.search(p_stripped) or p_stripped.startswith("证书"):
            continue
        # 跳过已识别的申请人
        if applicant and p_stripped == applicant:
            continue
        name_parts.append(p_stripped)
    patent_name = "-".join(name_parts) if name_parts else stem

    # 清理专利名
    patent_name = re.sub(r"证书[-\s]*\d+X?$", "", patent_name).strip("-").strip()
    patent_name = re.sub(r"\(\d+\)$", "", patent_name).strip()

    # --- 行业领域 ---
    field = infer_field(dir_path, patent_name)

    # --- 国际辖区 ---
    jurisdiction = infer_jurisdiction(dir_path)

    return {
        "name": patent_name or stem,
        "patent_type": patent_type_display or "未知",
        "patent_category": patent_category or "未知",
        "exam_type": exam_type or "普通",
        "applicant": applicant,
        "applicant_type": applicant_type,
        "field": field,
        "timeline": timeline,
        "date_range": date_range,
        "cert_no": cert_no,
        "jurisdiction": jurisdiction,
        "file_path": str(rel_path),  # 相对路径（便于移植）
        "file_path_abs": str(filepath),  # 绝对路径（本机直接使用）
        "file_type": filepath.suffix.lower().lstrip("."),
        "_orig_stem": stem,
        "_dir_path": str(rel_path.parent) if base_dir else dir_path,
    }


def scan_all(base_dir: Path = None) -> list:
    """扫描三个文件夹所有有效文件。base_dir 用于生成相对路径"""
    if base_dir is None:
        base_dir = PATENT_BASE
    patents = []
    seen = set()  # 去重（基于文件名+大小）
    for sub_dir in PATENT_DIRS:
        if not sub_dir.exists():
            print(f"[WARN] 目录不存在: {sub_dir}")
            continue
        for root, dirs, files in os.walk(sub_dir):
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext not in VALID_EXTS:
                    continue
                # 去重
                try:
                    fsize = fpath.stat().st_size
                except OSError:
                    fsize = 0
                dedup_key = (fname, fsize)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                info = parse_filename(fpath, base_dir)
                info["folder"] = sub_dir.name
                patents.append(info)
    return patents


def build_kb(patents: list) -> dict:
    """构建知识库 JSON"""
    # 构建 facets
    patent_categories = sorted(set(p["patent_category"] for p in patents if p["patent_category"] != "未知"))
    exam_types = sorted(set(p["exam_type"] for p in patents))
    applicant_types = sorted(set(p["applicant_type"] for p in patents))
    fields = sorted(set(p["field"] for p in patents))
    jurisdictions = sorted(set(p["jurisdiction"] for p in patents))

    # 分配 ID 并构建 search_text
    for i, p in enumerate(patents):
        p["id"] = f"pat_{i+1:06d}"
        # 构建搜索文本（用于 BM25 索引）
        search_parts = [
            p["name"],
            p["patent_type"],
            p["patent_category"],
            p["exam_type"],
            p.get("applicant", ""),
            p.get("applicant_type", ""),
            p["field"],
            p.get("timeline", ""),
            p.get("cert_no", ""),
            p.get("jurisdiction", ""),
        ]
        p["search_text"] = " ".join(sp for sp in search_parts if sp)

    kb = {
        "version": "1.0",
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(patents),
        "patent_base": str(PATENT_BASE),
        "facets": {
            "patent_category": patent_categories,
            "exam_type": exam_types,
            "applicant_type": applicant_types,
            "fields": fields,
            "jurisdictions": jurisdictions,
        },
        "patents": patents,
    }
    return kb


def main():
    print("=" * 60)
    print("  专利证书索引器 - Patent Certificate Indexer")
    print("=" * 60)

    print(f"\n[1/3] 扫描文件夹（基准目录: {PATENT_BASE}）...")
    patents = scan_all(PATENT_BASE)
    print(f"  找到 {len(patents)} 个文件（去重后）")

    print(f"\n[2/3] 分类统计...")
    # 统计
    cat_cnt = Counter(p["patent_category"] for p in patents)
    exam_cnt = Counter(p["exam_type"] for p in patents)
    app_cnt = Counter(p["applicant_type"] for p in patents)
    field_cnt = Counter(p["field"] for p in patents)
    jur_cnt = Counter(p["jurisdiction"] for p in patents)

    print(f"  专利类别: {dict(cat_cnt)}")
    print(f"  审查方式: {dict(exam_cnt)}")
    print(f"  申请人类型: {dict(app_cnt)}")
    print(f"  行业领域 ({len(field_cnt)}个):")
    for fld, cnt in field_cnt.most_common(25):
        print(f"    {fld}: {cnt}")
    print(f"  辖区: {dict(jur_cnt)}")

    # 显示一些样本
    print(f"\n  样本（前10条）:")
    for p in patents[:10]:
        print(f"    [{p['patent_type']}] {p['name'][:50]}...")
        print(f"      申请人: {p.get('applicant', 'N/A')} | 类型: {p['applicant_type']} | 领域: {p['field']}")

    print(f"\n[3/3] 写入知识库: {OUTPUT_PATH}")
    kb = build_kb(patents)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)

    file_size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    print(f"  完成! 文件大小: {file_size_mb:.1f} MB")
    print(f"  共 {kb['total']} 条专利记录")
    print("=" * 60)


if __name__ == "__main__":
    main()
