# -*- coding: utf-8 -*-
"""
专利知识库搜索模块。
本模块使用 jieba 分词、BM25 算法、拼音匹配和多维度筛选，
为用户提供专利知识库的全文搜索功能。
"""
import json
import math
import re
from collections import Counter
from pathlib import Path

import jieba

from pypinyin import lazy_pinyin

# ============================================================================
# 路径配置
# ============================================================================

# 尝试从 storage 模块导入用户自定义的专利知识库路径。
# 如果导入失败（例如 storage 模块不存在），则设为 None。
try:
    from storage import ZHUANLI_ZHISHIKU_LUJING as _YONGHU_ZHISHIKU
except Exception:
    _YONGHU_ZHISHIKU = None

# 资源目录下的默认专利知识库路径。
# 这是打包时自带的兜底知识库。
_ZIYUAN_ZHISHIKU = Path(__file__).resolve().parent.parent / "kb" / "patent_kb.json"


def _zhaodao_zhishiku():
    """
    查找专利知识库文件。
    优先使用用户数据目录下的知识库（用户可自定义），
    如果用户知识库不存在，则回退到资源目录下的默认知识库。
    """
    # 优先检查用户知识库是否存在
    if _YONGHU_ZHISHIKU and _YONGHU_ZHISHIKU.exists():
        return _YONGHU_ZHISHIKU
    # 其次检查资源目录下的默认知识库
    if _ZIYUAN_ZHISHIKU.exists():
        return _ZIYUAN_ZHISHIKU
    # 都不存在时返回默认路径，让调用方自行处理文件不存在的情况
    return _ZIYUAN_ZHISHIKU


# 全局知识库路径，模块加载时确定
ZHISHIKU_LUJING = _zhaodao_zhishiku()

# ============================================================================
# 分词相关配置
# ============================================================================

# 分词用的正则表达式：匹配中文字符、英文字母和数字组成的连续字符序列
_FENCI_ZHENGZE = re.compile(r"[一-鿿A-Za-z0-9]+")

# 中文停用词集合：这些高频虚词在搜索时不具备区分度，需要过滤掉
_TINGYONGCI = set("的了吗呢啊吧嘛么哈呀哦嗯我你他她它这那有没在是和就都也不还很怎么什么怎么样为什么可以能要不要会咱们您们我们一下一个哪个哪些请问")

# ============================================================================
# 字段权重配置
# ============================================================================
# 不同字段在搜索时的重要性不同。
# 名称(name)权重最高，因为用户最可能按专利名称搜索。
# 申请人和领域次之，其他字段权重较低。
LINGYU_QUANZHONG = {
    "name": 3.0,
    "applicant": 2.0,
    "field": 2.0,
    "patent_type": 1.5,
    "patent_category": 1.5,
    "exam_type": 1.5,
    "applicant_type": 1.0,
    "cert_no": 1.0,
    "timeline": 1.0,
    "jurisdiction": 0.5,
}


def fenci(wenben):
    """
    对输入文本进行中文分词，返回有意义的分词列表。
    分词过程：先转小写，再用 jieba 切词，
    然后过滤掉停用词和非中英文数字的符号。
    参数 wenben: 待分词的文本字符串
    返回值: 分词后的词语列表
    """
    # 将文本转为小写，确保英文大小写统一
    xiaoxie_wenben = wenben.lower()
    # 使用 jieba 进行中文分词（精确模式）
    ci_liebiao = jieba.lcut(xiaoxie_wenben)

    # 用 for 循环逐个过滤分词结果（不使用列表推导式，符合软件  策略）
    jieguo = []
    for ci in ci_liebiao:
        # 判断分词是否匹配中英文数字正则
        pipei_zhengze = _FENCI_ZHENGZE.fullmatch(ci)
        # 判断分词是否在停用词集合中
        bushi_tingyong = ci not in _TINGYONGCI
        # 判断分词去除两端空格后是否非空
        bushi_kong = len(ci.strip()) > 0
        # 只有满足以上三个条件的分词才保留
        if pipei_zhengze and bushi_tingyong and bushi_kong:
            jieguo.append(ci)

    return jieguo


def _pinyin_lingyu(wenben):
    """
    对输入文本生成全拼和首字母缩写，用于拼音模糊搜索。
    例如输入"医疗"，生成全拼 "yiliao" 和首字母 "yl"。
    参数 wenben: 输入文本字符串
    返回值: (全拼字符串, 首字母字符串) 的元组
    """
    # 使用 pypinyin 获取每个汉字的拼音
    pinyin_liebiao = lazy_pinyin(wenben)
    # 将拼音列表用空字符串连接成完整的全拼字符串
    quanpin = "".join(pinyin_liebiao)

    # 用 for 循环收集每个拼音的首字母（不使用列表推导式）
    shouzimu_liebiao = []
    for py in pinyin_liebiao:
        # 有些字符可能没有拼音，需要跳过空字符串
        if py:
            shouzimu_liebiao.append(py[0])

    # 将所有首字母连接成一个字符串
    shouzimu = "".join(shouzimu_liebiao)

    return quanpin, shouzimu


# ============================================================================
# BM25 算法实现
# ============================================================================
class BMSuanfa:
    """
    Okapi BM25 信息检索排序算法。
    使用默认参数 k1=1.5, b=0.75。
    BM25 用于计算查询词与文档之间的相关性得分。
    """

    def __init__(self, can_k1=1.5, can_b=0.75):
        """
        初始化 BM25 算法实例。
        参数 can_k1: 词频饱和度参数，控制词频对得分的影响程度
        参数 can_b: 文档长度归一化参数，控制文档长度惩罚的强度
        """
        # 词频饱和度参数
        self.can_k1 = can_k1
        # 文档长度归一化参数
        self.can_b = can_b
        # 存储所有文档的词频字典
        self.wendang_liebiao = []
        # 存储每个文档的总词数（长度）
        self.wendang_changdu = []
        # 所有文档的平均长度
        self.pingjun_changdu = 0.0
        # 文档频率计数器：记录每个词出现在多少个文档中
        self.wendang_pinlv = Counter()
        # 文档总数
        self.wendang_shu = 0
        # 逆文档频率字典：记录每个词的 IDF 值
        self.nixiang_pinlv = {}

    def suoyin(self, wendang_liebiao):
        """
        为文档列表建立 BM25 索引。
        计算每个词的文档频率和逆文档频率，为后续打分做准备。
        参数 wendang_liebiao: 文档列表，每个文档是一个 {词: 词频} 的字典
        """
        # 保存文档列表
        self.wendang_liebiao = wendang_liebiao
        # 记录文档总数
        self.wendang_shu = len(wendang_liebiao)
        # 重置文档频率计数器
        self.wendang_pinlv = Counter()
        # 清空文档长度列表，准备重新计算
        self.wendang_changdu = []

        # 遍历每个文档，计算文档长度和词的文档频率
        for wendang in wendang_liebiao:
            # 计算当前文档的总词数（所有词的词频之和）
            zongcishu = sum(wendang.values())
            self.wendang_changdu.append(zongcishu)

            # 遍历当前文档中的每个词，更新文档频率
            for ci in wendang:
                self.wendang_pinlv[ci] += 1

        # 计算平均文档长度（防止除零错误）
        if self.wendang_shu:
            self.pingjun_changdu = sum(self.wendang_changdu) / self.wendang_shu
        else:
            self.pingjun_changdu = 0.0

        # 用 for 循环计算每个词的逆文档频率 IDF（不使用字典推导式）
        self.nixiang_pinlv = {}
        for ci, pinlv in self.wendang_pinlv.items():
            # IDF 公式: log(1 + (N - df + 0.5) / (df + 0.5))
            # N 是文档总数，df 是包含该词的文档数
            idf_zhi = math.log(1 + (self.wendang_shu - pinlv + 0.5) / (pinlv + 0.5))
            self.nixiang_pinlv[ci] = idf_zhi

    def defe(self, chaxun_ci_liebiao, wendang_suoyin):
        """
        计算查询词对指定文档的 BM25 得分。
        参数 chaxun_ci_liebiao: 查询词列表
        参数 wendang_suoyin: 文档在 wendang_liebiao 中的索引位置
        返回值: BM25 得分（浮点数）
        """
        # 如果没有文档被索引，返回 0 分
        if not self.wendang_shu:
            return 0.0

        # 获取当前要打分的文档
        wendang = self.wendang_liebiao[wendang_suoyin]
        # 获取当前文档的长度（防止除零错误，使用极小值）
        wendang_len = self.wendang_changdu[wendang_suoyin] or 1e-9
        # 防止平均长度为零导致除零
        pingjun_len = self.pingjun_changdu or 1e-9

        # 累加得分
        zong_defen = 0.0

        # 遍历查询中的每个词，计算该词对文档的贡献分
        for ci in chaxun_ci_liebiao:
            # 查询词在当前文档中的词频
            ci_pin = wendang.get(ci)
            # 如果该词不在文档中，跳过
            if not ci_pin:
                continue

            # 获取该词的逆文档频率
            idf_zhi = self.nixiang_pinlv.get(ci, 0.0)

            # BM25 公式的分母部分
            # 包含词频、k1 参数、b 参数和文档长度归一化
            fenmu = ci_pin + self.can_k1 * (1 - self.can_b + self.can_b * wendang_len / pingjun_len)

            # BM25 公式计算该词的得分并累加
            zong_defen += idf_zhi * ci_pin * (self.can_k1 + 1) / fenmu

        return zong_defen


# ============================================================================
# 专利搜索引擎
# ============================================================================
class ZhuanliSousuo:
    """
    专利知识库搜索引擎。
    功能包括：
    - 加载专利知识库 JSON 文件
    - 支持多维度筛选（专利类别、审查类型、申请人类型、领域、管辖地）
    - 支持中文关键词搜索（基于 BM25 算法）
    - 支持拼音模糊搜索（全拼和首字母匹配）
    - 解析证书文件的相对路径和绝对路径
    """

    def __init__(self, zhuanli_genmulu=""):
        """
        初始化搜索引擎。
        参数 zhuanli_genmulu: 证书文件的根目录路径（用于跨机器移植时解析相对路径）
        """
        # 专利数据列表，每个元素是一个包含专利信息的字典
        self.zhuanli_liebiao = []
        # 筛选维度配置字典
        self.shaixuan_weidu = {}
        # BM25 算法实例
        self.bm25_suanfa = BMSuanfa()
        # 拼音索引：每个元素是 (原始文本小写, 全拼, 首字母) 的元组
        self._pinyin_suoyin = []
        # 每个筛选维度的值及其出现次数计数器
        self._shaixuan_jishu = {}
        # 证书文件根目录（可配置，用于跨机器移植）
        self._zhuanli_genmulu = zhuanli_genmulu
        # 加载知识库数据
        self.chongxinzairu()

    def shezhi_zhuanli_genmulu(self, genmulu):
        """
        设置证书文件的根目录路径。
        当程序被移植到另一台机器时，可通过此方法更新根目录。
        参数 genmulu: 新的证书根目录路径字符串
        """
        self._zhuanli_genmulu = genmulu

    def _jiexi_lujing(self, xiangdui_lujing, juedui_lujing=""):
        """
        解析专利证书文件的真实路径。
        优先使用绝对路径（如果文件实际存在），
        否则尝试用 base+相对路径拼接（用于跨机器移植）。
        参数 xiangdui_lujing: 证书文件的相对路径
        参数 juedui_lujing: 证书文件的绝对路径（可能来自旧机器）
        返回值: 解析后的文件路径字符串
        """
        # 情况1：有绝对路径且文件在磁盘上存在，直接使用
        if juedui_lujing and Path(juedui_lujing).exists():
            return juedui_lujing

        # 情况2：有绝对路径但文件不存在（已移植到新机器），尝试使用 base+相对路径
        if self._zhuanli_genmulu and xiangdui_lujing:
            jiexi_hou = Path(self._zhuanli_genmulu) / xiangdui_lujing
            # 如果拼接后的路径存在，返回该路径
            if jiexi_hou.exists():
                return str(jiexi_hou)
            # 即使不存在也返回路径字符串，让调用方处理
            return str(jiexi_hou)

        # 情况3：没有绝对路径，仅使用相对路径 + base
        if juedui_lujing:
            return juedui_lujing

        # 情况4：使用 base 拼接相对路径
        if self._zhuanli_genmulu and xiangdui_lujing:
            return str(Path(self._zhuanli_genmulu) / xiangdui_lujing)

        # 兜底：直接返回相对路径
        return xiangdui_lujing

    def chongxinzairu(self):
        """
        重新加载专利知识库。
        从 JSON 文件读取专利数据，重建 BM25 索引、
        拼音索引和筛选维度计数。
        """
        # 检查知识库文件是否存在
        if not ZHISHIKU_LUJING.exists():
            print("[ZhuanliSousuo] 专利知识库不存在: {}".format(ZHISHIKU_LUJING))
            return

        # 打开并解析 JSON 知识库文件
        with open(ZHISHIKU_LUJING, "r", encoding="utf-8") as f:
            shuju = json.load(f)

        # 读取专利列表
        self.zhuanli_liebiao = shuju.get("patents", [])
        # 读取筛选维度配置
        self.shaixuan_weidu = shuju.get("facets", {})

        # 如果 JSON 里包含了 patent_base 且外部未设置，则使用 JSON 里的值
        json_genmulu = shuju.get("patent_base", "")
        if json_genmulu and not self._zhuanli_genmulu:
            self._zhuanli_genmulu = json_genmulu

        # 准备 BM25 索引所需的数据
        wendang_liebiao = []
        # 清空拼音索引
        self._pinyin_suoyin = []

        # 初始化各筛选维度的计数器
        self._shaixuan_jishu = {}
        self._shaixuan_jishu["patent_category"] = Counter()
        self._shaixuan_jishu["exam_type"] = Counter()
        self._shaixuan_jishu["applicant_type"] = Counter()
        self._shaixuan_jishu["field"] = Counter()
        self._shaixuan_jishu["jurisdiction"] = Counter()

        # 遍历每条专利数据，建立索引
        for zhuanli in self.zhuanli_liebiao:
            # 当前专利的词频计数器
            ci_pinlv = Counter()

            # 对专利名称进行加权分词
            mingcheng = zhuanli.get("name", "")
            mingcheng_fenci = fenci(mingcheng)
            for fenci_danyuan in mingcheng_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["name"]

            # 对专利申请人进行加权分词
            shenqingren = zhuanli.get("applicant", "")
            shenqingren_fenci = fenci(shenqingren)
            for fenci_danyuan in shenqingren_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["applicant"]

            # 对专利领域进行加权分词
            lingyu = zhuanli.get("field", "")
            lingyu_fenci = fenci(lingyu)
            for fenci_danyuan in lingyu_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["field"]

            # 对专利类型进行加权分词
            zhuanli_leixing = zhuanli.get("patent_type", "")
            zhuanli_leixing_fenci = fenci(zhuanli_leixing)
            for fenci_danyuan in zhuanli_leixing_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["patent_type"]

            # 对专利类别进行加权分词
            zhuanli_bie = zhuanli.get("patent_category", "")
            zhuanli_bie_fenci = fenci(zhuanli_bie)
            for fenci_danyuan in zhuanli_bie_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["patent_category"]

            # 对审查类型进行加权分词
            shencha_leixing = zhuanli.get("exam_type", "")
            shencha_leixing_fenci = fenci(shencha_leixing)
            for fenci_danyuan in shencha_leixing_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["exam_type"]

            # 对申请人类型进行加权分词
            shenqingren_leixing = zhuanli.get("applicant_type", "")
            shenqingren_leixing_fenci = fenci(shenqingren_leixing)
            for fenci_danyuan in shenqingren_leixing_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["applicant_type"]

            # 对证书编号进行加权分词
            zhengshu_bianhao = zhuanli.get("cert_no", "")
            zhengshu_bianhao_fenci = fenci(zhengshu_bianhao)
            for fenci_danyuan in zhengshu_bianhao_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["cert_no"]

            # 对时间线进行加权分词
            shijianxian = zhuanli.get("timeline", "")
            shijianxian_fenci = fenci(shijianxian)
            for fenci_danyuan in shijianxian_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["timeline"]

            # 对管辖地进行加权分词
            guanxiadi = zhuanli.get("jurisdiction", "")
            guanxiadi_fenci = fenci(guanxiadi)
            for fenci_danyuan in guanxiadi_fenci:
                ci_pinlv[fenci_danyuan] += LINGYU_QUANZHONG["jurisdiction"]

            # 将当前专利的词频字典加入文档列表
            wendang_liebiao.append(dict(ci_pinlv))

            # 构建拼音索引：合并名称、申请人和领域
            yuanshi_wenben = "{} {} {}".format(
                zhuanli.get('name', ''),
                zhuanli.get('applicant', ''),
                zhuanli.get('field', '')
            )
            # 生成全拼和首字母
            quanpin, shouzimu = _pinyin_lingyu(yuanshi_wenben)
            # 将 (原始文本小写, 全拼, 首字母) 存入拼音索引
            self._pinyin_suoyin.append((yuanshi_wenben.lower(), quanpin, shouzimu))

            # 更新各筛选维度的计数
            weidu_mingcheng_liebiao = [
                "patent_category",
                "exam_type",
                "applicant_type",
                "field",
                "jurisdiction",
            ]
            for weidu_mingcheng in weidu_mingcheng_liebiao:
                # 获取当前专利在该维度下的值
                zhi = zhuanli.get(weidu_mingcheng, "")
                # 如果值非空，增加计数
                if zhi:
                    self._shaixuan_jishu[weidu_mingcheng][zhi] += 1

        # 使用文档列表构建 BM25 索引
        self.bm25_suanfa.suoyin(wendang_liebiao)

    def huode_shaixuan_weidu(self):
        """
        获取所有筛选维度及其可选值（带计数）。
        用于前端展示筛选面板，让用户知道每个维度有哪些选项及各选项的专利数量。
        返回值: 字典，键为维度名称，值为 [{"value": 选项值, "count": 数量}] 列表
        """
        jieguo = {}
        # 遍历每个筛选维度及其计数器
        for weidu, jishuqi in self._shaixuan_jishu.items():
            # 用 for 循环构建选项列表（不使用列表推导式）
            xuanxiang_liebiao = []
            # 按计数从高到低排列，获取最常见的选项
            pinfan_paixu = jishuqi.most_common()
            for zhi, shuliang in pinfan_paixu:
                xuanxiang = {"value": zhi, "count": shuliang}
                xuanxiang_liebiao.append(xuanxiang)

            jieguo[weidu] = xuanxiang_liebiao

        return jieguo

    def sousuo(self, chaxun="", shaixuan=None, zuiduo_jieguo=20):
        """
        执行专利搜索。
        支持中文关键词搜索（BM25 打分）和拼音模糊搜索（全拼/首字母匹配）。
        同时支持按多个维度筛选结果。
        参数 chaxun: 搜索关键词，支持中文、全拼和首字母
        参数 shaixuan: 筛选条件字典，如 {"patent_category": "发明", "exam_type": "预审"}
        参数 zuiduo_jieguo: 最多返回的结果条数
        返回值: 搜索结果列表，每条结果是一个包含专利信息的字典
        """
        # 如果 shaixuan 参数为 None，设为空字典
        if shaixuan is None:
            shaixuan = {}

        # ================================================================
        # 第一步：根据筛选条件确定候选集
        # ================================================================
        houxuan_liebiao = []
        houxuan_suoyin = []

        # 遍历所有专利，检查是否满足筛选条件
        for i, zhuanli in enumerate(self.zhuanli_liebiao):
            pipei = True
            # 检查当前专利是否满足每个筛选维度的条件
            for weidu, zhi in shaixuan.items():
                # 如果筛选值非空，且专利在该维度的值不匹配，则排除
                if zhi and zhuanli.get(weidu) != zhi:
                    pipei = False
                    # 一旦不满足某个条件，跳出内层循环
                    break

            # 如果满足所有筛选条件，加入候选集
            if pipei:
                houxuan_liebiao.append(zhuanli)
                houxuan_suoyin.append(i)

        # 如果没有符合条件的专利，直接返回空列表
        if not houxuan_liebiao:
            return []

        # ================================================================
        # 第二步：如果有搜索词，进行 BM25 打分和拼音匹配
        # ================================================================
        # 去除搜索词两端的空格
        chaxun = (chaxun or "").strip()

        if chaxun:
            # 按空白字符拆分搜索词，用于拼音匹配的 AND 条件
            ci_liebiao = []
            chaihou = re.split(r"\s+", chaxun.lower())
            # 用 for 循环过滤空字符串（不使用列表推导式）
            for t in chaihou:
                if t:
                    ci_liebiao.append(t)

            if ci_liebiao:
                # 对搜索词进行分词，用于 BM25 打分
                chaxun_fenci = fenci(chaxun)

                # 用 for 循环计算每个候选专利的 BM25 得分（不使用列表推导式）
                defen_liebiao = []
                for suoyin_hao in houxuan_suoyin:
                    # 调用 BM25 算法计算得分
                    defe_zhi = self.bm25_suanfa.defe(chaxun_fenci, suoyin_hao)

                    if defe_zhi <= 0:
                        # BM25 得分为 0 时，尝试拼音模糊匹配作为兜底
                        yuanshi, quanpin, shouzimu = self._pinyin_suoyin[suoyin_hao]

                        # 用 for 循环实现 all() 的逻辑：检查每个搜索词是否匹配
                        pinyin_pipei = True
                        for t in ci_liebiao:
                            # 检查搜索词是否出现在原始文本、全拼或首字母中
                            zaix_yuanshi = t in yuanshi
                            zaix_quanpin = t in quanpin
                            zaix_shouzimu_kaishi = shouzimu.startswith(t)
                            zaix_shouzimu = t in shouzimu
                            # 任何一个条件成立则该词判定为匹配
                            pipei_ci = zaix_yuanshi or zaix_quanpin or zaix_shouzimu_kaishi or zaix_shouzimu
                            if not pipei_ci:
                                pinyin_pipei = False
                                break

                        # 如果拼音匹配成功，给一个很小的正分（0.01）
                        if pinyin_pipei:
                            defe_zhi = 0.01

                    # 如果得分大于 0，加入得分列表
                    if defe_zhi > 0:
                        defen_liebiao.append((suoyin_hao, defe_zhi))

                # 按得分从高到低排序
                defen_liebiao.sort(key=lambda x: x[1], reverse=True)

                # 取前 zuiduo_jieguo 条
                caijian_hou = defen_liebiao[:zuiduo_jieguo]

                # 用 for 循环提取索引列表（不使用列表推导式）
                houxuan_suoyin = []
                for suoyin_hao, _ in caijian_hou:
                    houxuan_suoyin.append(suoyin_hao)

                # 用 for 循环根据索引重新构建候选列表（不使用列表推导式）
                houxuan_liebiao = []
                for suoyin_hao in houxuan_suoyin:
                    houxuan_liebiao.append(self.zhuanli_liebiao[suoyin_hao])
            else:
                # 搜索词拆分后为空，直接截取前 N 条
                houxuan_liebiao = houxuan_liebiao[:zuiduo_jieguo]
        else:
            # 没有搜索词时，直接截取前 N 条候选专利
            houxuan_liebiao = houxuan_liebiao[:zuiduo_jieguo]

        # ================================================================
        # 第三步：格式化返回结果
        # ================================================================
        jieguo_liebiao = []

        # 遍历候选列表，构建格式化的结果
        for i, zhuanli in enumerate(houxuan_liebiao):
            # 解析文件路径：优先用绝对路径，否则用 base+相对路径
            xiangdui_lujing = zhuanli.get("file_path", "")
            juedui_lujing = zhuanli.get("file_path_abs", "")
            jiexi_hou_lujing = self._jiexi_lujing(xiangdui_lujing, juedui_lujing)

            # 构建单条搜索结果字典
            jieguo = {}
            jieguo["rank"] = i + 1
            jieguo["id"] = zhuanli["id"]
            jieguo["name"] = zhuanli.get("name", "")
            jieguo["patent_type"] = zhuanli.get("patent_type", "")
            jieguo["patent_category"] = zhuanli.get("patent_category", "")
            jieguo["exam_type"] = zhuanli.get("exam_type", "")
            jieguo["applicant"] = zhuanli.get("applicant", "")
            jieguo["applicant_type"] = zhuanli.get("applicant_type", "")
            jieguo["field"] = zhuanli.get("field", "")
            jieguo["timeline"] = zhuanli.get("timeline", "")
            jieguo["date_range"] = zhuanli.get("date_range", "")
            jieguo["cert_no"] = zhuanli.get("cert_no", "")
            jieguo["jurisdiction"] = zhuanli.get("jurisdiction", "")
            jieguo["file_path"] = jiexi_hou_lujing
            jieguo["file_type"] = zhuanli.get("file_type", "")
            jieguo["folder"] = zhuanli.get("folder", "")

            # 将本条结果加入结果列表
            jieguo_liebiao.append(jieguo)

        return jieguo_liebiao

    def an_id_huode(self, zhuanli_id):
        """
        根据专利 ID 获取单条专利的详细信息。
        同时解析证书文件的真实路径。
        参数 zhuanli_id: 专利的唯一标识符
        返回值: 专利信息字典，如果找不到则返回 None
        """
        # 遍历专利列表查找匹配的 ID
        for zhuanli in self.zhuanli_liebiao:
            if zhuanli["id"] == zhuanli_id:
                # 复制专利数据字典
                jieguo = dict(zhuanli)
                # 解析并设置文件路径
                jieguo["file_path"] = self._jiexi_lujing(
                    zhuanli.get("file_path", ""),
                    zhuanli.get("file_path_abs", "")
                )
                return jieguo

        # 未找到匹配的专利，返回 None
        return None

    @property
    def zongshu(self):
        """
        返回知识库中专利的总数量。
        这是一个只读属性，方便外部快速获取专利总数。
        """
        return len(self.zhuanli_liebiao)
