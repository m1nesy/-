# -*- coding: utf-8 -*-
"""本地检索引擎：jieba 分词 + 加权 BM25
对知识库建索引，输入客户问题（可带上下文），返回 top-N 候选话术。

本文件已按照中国软件  政策要求进行修改：
- 所有变量名、函数名、类名均使用中文拼音
- 所有推导式已展开为简单 for 循环
- 添加了详细的中文注释
- 保留 jieba 库的英文导入名称
- BM25 作为专有名称保持不变
"""
# 导入 JSON 库，用于读取知识库文件
import json
# 导入数学库，用于 BM25 中的对数计算
import math
# 导入正则表达式库，用于分词时的字符匹配
import re
# 导入计数器，用于统计词频
from collections import Counter
# 导入路径处理库，用于处理文件路径
from pathlib import Path

# 导入 jieba 分词库（保留英文导入名称）
import jieba

# ============================================================================
# 字段权重常量：定义不同字段在 BM25 检索中的重要性
# 触发问句最像客户原话，权重最高
# ============================================================================
ZIDUAN_QUANZHONG = {
    "triggers": 3.0,   # 触发词字段权重最高，因为最接近客户原话
    "intent": 2.5,     # 意图字段权重次之
    "category": 1.5,   # 类别字段权重较低
    "script": 1.0,     # 话术字段权重最低
}

# ============================================================================
# 场景关键词映射：用于识别对话上下文中的产品/业务场景
# 当上下文中出现这些产品词时，给对应 scenario 的知识块加权
# ============================================================================
CHANGJING_GUANJIANCI = {
    "优审": ["优审", "优先审查"],                       # 优先审查场景
    "预审": ["预审"],                                   # 预审场景
    "发明": ["发明"],                                   # 发明专利场景
    "实用新型": ["实用"],                               # 实用新型场景
    "外观": ["外观"],                                   # 外观设计场景
    "": ["", "软件"],                     # 软件场景
    "转让": ["转让", "买专利", "买一个", "挂靠"],       # 专利转让场景
}

# ============================================================================
# 分词正则表达式：匹配中文汉字、英文字母和数字组成的词语
# ============================================================================
_FENCI_PIPEI = re.compile(r"[一-鿿A-Za-z0-9]+")

# ============================================================================
# 停用词集合：这些常见虚词在检索中不携带有效语义信息，需过滤掉
# ============================================================================
_TINGYONG = set("的了吗呢啊吧嘛么哈呀哦嗯我你他她它这那有没在是和就都也不还很怎么什么怎么样为什么可以能要不要会咱们您们我们一下一个哪个哪些请问")


def fenci(wenben: str) -> list:
    """使用 jieba 对文本进行分词，并去除停用词和标点符号。

    参数:
        wenben: 待分词的文本字符串

    返回:
        过滤后的词语列表
    """
    # 使用 jieba 进行分词，同时将文本转换为小写以便统一处理英文单词
    ciyu_liebiao = jieba.lcut(wenben.lower())

    # 用 for 循环逐个过滤词语：
    # 1. 必须匹配 _FENCI_PIPEI 正则（只保留中文汉字、英文字母和数字）
    # 2. 不能是停用词
    # 3. 去除空白后不能为空字符串
    jieguo = []
    for ci in ciyu_liebiao:
        if _FENCI_PIPEI.fullmatch(ci) and ci not in _TINGYONG and len(ci.strip()) > 0:
            jieguo.append(ci)
    return jieguo


class BM25:
    """Okapi BM25 检索模型。

    BM25 是一种经典的词袋检索算法，用于计算查询与文档之间的相关性得分。
    参数 k1=1.5, b=0.75 是业界标准默认值。
    BM25 作为专有算法名称保持不变。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """初始化 BM25 模型。

        参数:
            k1: 词频饱和度参数，控制词频对得分的影响程度
            b: 文档长度归一化参数，控制文档长度对得分的影响
        """
        self.k1 = k1
        self.b = b
        # 文档列表，每个文档是一个 {词语: 加权词频} 的字典
        self.wendang_liebiao = []
        # 每个文档的长度（即词语总数）
        self.wendang_changdu = []
        # 所有文档的平均长度
        self.pingjun_changdu = 0.0
        # 文档频率计数器：每个词语在多少个文档中出现过
        self.wendang_pinlv = Counter()
        # 文档总数
        self.wendang_shu = 0
        # 逆向文档频率字典：{词语: IDF值}
        self.nixiang_pinlv = {}

    def suoyin(self, wendang_liebiao: list):
        """对文档集合建立索引。

        参数:
            wendang_liebiao: 文档列表，每个元素是 {词语: 加权词频} 的字典
        """
        # 保存文档列表
        self.wendang_liebiao = wendang_liebiao
        # 记录文档总数
        self.wendang_shu = len(wendang_liebiao)
        # 重置文档频率计数器
        self.wendang_pinlv = Counter()
        # 重置文档长度列表
        self.wendang_changdu = []

        # 遍历每个文档，计算文档长度和文档频率
        for wendang in wendang_liebiao:
            # 文档长度 = 该文档中所有词语的总词频
            self.wendang_changdu.append(sum(wendang.values()))
            # 统计每个词语出现在多少个文档中（文档频率）
            for ci in wendang:
                self.wendang_pinlv[ci] += 1

        # 计算所有文档的平均长度
        if self.wendang_shu:
            self.pingjun_changdu = sum(self.wendang_changdu) / self.wendang_shu
        else:
            self.pingjun_changdu = 0.0

        # 计算每个词语的 IDF（逆向文档频率）值
        # 用 for 循环替代字典推导式
        self.nixiang_pinlv = {}
        for ci, df_zhi in self.wendang_pinlv.items():
            # IDF 公式：log(1 + (N - df + 0.5) / (df + 0.5))
            # 其中 N 是文档总数，df 是包含该词语的文档数
            self.nixiang_pinlv[ci] = math.log(1 + (self.wendang_shu - df_zhi + 0.5) / (df_zhi + 0.5))

    def defen(self, chaxun_ci: list, wendang_bianhao: int) -> float:
        """计算查询词语对指定文档的 BM25 得分。

        参数:
            chaxun_ci: 查询词语列表
            wendang_bianhao: 文档在文档列表中的索引编号

        返回:
            BM25 相关性得分（浮点数）
        """
        # 如果没有建立索引（文档数为0），返回 0 分
        if not self.wendang_shu:
            return 0.0

        # 获取文档的词语频率字典
        wendang = self.wendang_liebiao[wendang_bianhao]
        # 获取文档长度，如果为 0 则使用极小值避免除零错误
        changdu = self.wendang_changdu[wendang_bianhao]
        if changdu == 0:
            changdu = 1e-9

        # 初始化总得分
        defenzhi = 0.0

        # 对查询中的每个词语，累加其 BM25 得分
        for ci in chaxun_ci:
            # 获取该词语在当前文档中的词频
            cipin = wendang.get(ci)
            if not cipin:
                # 如果词语不在文档中，跳过
                continue

            # 获取该词语的 IDF 值
            nixiang = self.nixiang_pinlv.get(ci, 0.0)

            # BM25 分母部分：词频 + k1 * (1 - b + b * 文档长度 / 平均文档长度)
            fenmu = cipin + self.k1 * (1 - self.b + self.b * changdu / (self.pingjun_changdu if self.pingjun_changdu else 1e-9))

            # 累加：IDF * 词频 * (k1 + 1) / 分母
            defenzhi += nixiang * cipin * (self.k1 + 1) / fenmu

        return defenzhi


class Jiansuoqi:
    """检索引擎类，封装了知识库加载、索引建立和多种搜索策略。

    功能包括：
    - 加载 JSON 格式的知识库文件
    - 对知识库内容建立 BM25 索引
    - 支持关键词搜索、场景加权搜索和智能分支搜索
    - 支持按路径前缀限定搜索范围
    """

    def __init__(self, zhishiku_lujing: str):
        """初始化检索引擎。

        参数:
            zhishiku_lujing: 知识库 JSON 文件的路径字符串
        """
        # 将路径字符串转换为 Path 对象
        self.zhishiku_lujing = Path(zhishiku_lujing)
        # 知识块列表，每个块包含意图、触发词、话术等信息
        self.kuaizi = []
        # BM25 索引实例
        self.bm25 = BM25()
        # 树结构的节点列表（从知识库 JSON 中读取）
        self._shu_jiedian = []
        # 路径索引字典：路径前缀字符串 -> 包含的块索引列表
        self._lujing_suoyin = {}
        # 类别到树根节点的映射字典：类别名称 -> 树根节点名称
        self._leibie_dao_shu = {}
        # 加载知识库并建立索引
        self.chongxin_jiazai()

    def chongxin_jiazai(self):
        """重新加载知识库文件并重建所有索引。

        这个方法会：
        1. 读取 JSON 格式的知识库文件
        2. 对每个知识块的各字段分别分词并加权
        3. 建立 BM25 索引
        4. 预计算场景集合
        5. 构建路径索引和类别到树的映射
        """
        # 打开知识库文件并读取 JSON 数据
        with open(self.zhishiku_lujing, "r", encoding="utf-8") as wenjian:
            shuju = json.load(wenjian)

        # 获取知识块列表
        self.kuaizi = shuju["chunks"]
        # 获取树结构节点（如果存在）
        self._shu_jiedian = shuju.get("tree", {}).get("nodes", [])

        # 初始化文档列表，用于 BM25 索引
        wendang_liebiao = []
        # 重置路径索引
        self._lujing_suoyin = {}
        # 重置类别到树的映射
        self._leibie_dao_shu = {}

        # 遍历每个知识块，提取各字段的加权词频
        for kuai in self.kuaizi:
            # 创建计数器，记录该知识块中各词语的加权总词频
            cipin = Counter()

            # 对意图字段分词，按意图权重进行加权
            for ciyu in fenci(kuai.get("intent", "")):
                cipin[ciyu] += ZIDUAN_QUANZHONG["intent"]

            # 对类别字段分词，按类别权重进行加权
            for ciyu in fenci(kuai.get("category", "")):
                cipin[ciyu] += ZIDUAN_QUANZHONG["category"]

            # 对每个触发词逐个分词，按触发词权重进行加权
            for chufaqi in kuai.get("triggers", []):
                for ciyu in fenci(chufaqi):
                    cipin[ciyu] += ZIDUAN_QUANZHONG["triggers"]

            # 对话术字段分词，按话术权重进行加权
            for ciyu in fenci(kuai.get("script", "")):
                cipin[ciyu] += ZIDUAN_QUANZHONG["script"]

            # 将该知识块的加权词频字典添加到文档列表
            wendang_liebiao.append(dict(cipin))

        # 使用文档列表建立 BM25 索引
        self.bm25.suoyin(wendang_liebiao)

        # 预计算每个知识块的场景集合（用 for 循环替代列表推导式）
        self._changjing = []
        for kuai in self.kuaizi:
            self._changjing.append(set(kuai.get("scenario", [])))

        # 构建路径索引：路径前缀 -> [块索引, ...]
        # 用 for 循环遍历所有知识块，同时获取索引号和块内容
        for suoyin, kuai in enumerate(self.kuaizi):
            # 获取知识块的路径（优先使用 _path 字段，否则使用 category 字段）
            lujing = kuai.get("_path", kuai.get("category", ""))
            # 获取知识块的树根节点（优先使用 _tree 字段，否则使用 category 字段）
            shugen = kuai.get("_tree", kuai.get("category", ""))

            # 将路径按 "/" 分割为各部分
            bufen = lujing.split("/")
            # 为每个层级的前缀建立索引
            # 例如 "异议处理/报价流程" 会索引 "异议处理" 和 "异议处理/报价流程"
            for weizhi in range(len(bufen)):
                qianzhui = "/".join(bufen[:weizhi + 1])
                # 如果该前缀不在索引中，先创建空列表
                if qianzhui not in self._lujing_suoyin:
                    self._lujing_suoyin[qianzhui] = []
                # 将当前块的索引添加到该前缀的列表中
                self._lujing_suoyin[qianzhui].append(suoyin)

            # 为树根节点建立索引
            if shugen not in self._lujing_suoyin:
                self._lujing_suoyin[shugen] = []
            # 避免重复添加同一个索引
            if suoyin not in self._lujing_suoyin[shugen]:
                self._lujing_suoyin[shugen].append(suoyin)

            # 记录类别到树根节点的映射关系
            self._leibie_dao_shu[kuai.get("category", "")] = shugen

    def huoqu_shu(self) -> list:
        """返回树结构信息，包含每个节点的条目数量。

        返回:
            树节点列表，每个节点包含名称、图标和包含的知识块数量
        """
        # 内部递归函数，用于构建树结构
        def goujian(jiedian_liebiao):
            """递归构建树节点的函数。

            参数:
                jiedian_liebiao: 当前层级的节点列表

            返回:
                构建好的节点信息列表
            """
            shuchu = []
            # 遍历当前层级的所有节点
            for jiedian in jiedian_liebiao:
                # 创建节点信息字典
                jiedian_info = {
                    "name": jiedian["name"],
                    "icon": jiedian.get("icon", ""),
                    "count": 0
                }

                # 如果节点定义了 categories 字段，统计属于这些类别的知识块数量
                if "categories" in jiedian:
                    leibies = jiedian["categories"]
                    # 用 for 循环替代生成器表达式进行计数
                    jishu = 0
                    for kuai in self.kuaizi:
                        if kuai.get("category") in leibies:
                            jishu += 1
                    jiedian_info["count"] = jishu

                # 如果节点有子节点，递归构建子节点
                if "children" in jiedian:
                    jiedian_info["children"] = goujian(jiedian["children"])
                    # 汇总所有子节点的 count 值，用 for 循环替代生成器表达式
                    zijishu = 0
                    for zijiedian in jiedian_info["children"]:
                        zijishu += zijiedian.get("count", 0)
                    jiedian_info["count"] = zijishu

                shuchu.append(jiedian_info)
            return shuchu

        # 从根节点开始构建整个树结构
        return goujian(self._shu_jiedian)

    @staticmethod
    def _shangxiawen_changjing(xiaoxi_liebiao: list) -> set:
        """从对话消息中识别相关的业务场景。

        通过匹配场景关键词，判断当前对话涉及哪些产品/业务场景。

        参数:
            xiaoxi_liebiao: 对话消息列表

        返回:
            识别到的场景名称集合
        """
        # 将所有消息拼接为一个字符串以便匹配
        wenben = " ".join(xiaoxi_liebiao)
        # 存储命中场景的集合
        mingzhong = set()

        # 遍历所有场景关键词
        for changjing, guanjianci_liebiao in CHANGJING_GUANJIANCI.items():
            # 检查该场景的任一关键词是否出现在文本中（用 for 循环替代 any() 生成器）
            shifou_pipei = False
            for guanjianci in guanjianci_liebiao:
                if guanjianci in wenben:
                    shifou_pipei = True
                    break  # 只要有一个关键词匹配就足够了
            if shifou_pipei:
                mingzhong.add(changjing)

        return mingzhong

    def _shangxiawen_shu(self, xiaoxi_liebiao: list) -> set:
        """从对话中识别相关的树分支。

        通过匹配知识块的触发词和意图，判断当前对话属于哪个树分支。

        参数:
            xiaoxi_liebiao: 对话消息列表

        返回:
            匹配到的树根节点名称集合
        """
        # 将所有消息拼接为一个字符串
        wenben = " ".join(xiaoxi_liebiao)
        # 存储命中树根节点的集合
        mingzhong = set()

        # 遍历所有知识块，检查是否与对话内容相关
        for kuai in self.kuaizi:
            # 获取知识块的类别
            leibie = kuai.get("category", "")
            # 获取该类别对应的树根节点名称
            shugen = self._leibie_dao_shu.get(leibie, leibie)

            # 检查知识块的触发词是否与对话文本匹配（用 for 循环替代 any() 生成器）
            chufaqi_pipei = False
            for guanjianci in kuai.get("triggers", []):
                if guanjianci in wenben:
                    chufaqi_pipei = True
                    break
            if chufaqi_pipei:
                mingzhong.add(shugen)

            # 检查知识块的意图字段是否恰好出现在对话文本中
            if kuai.get("intent", "") in wenben:
                mingzhong.add(shugen)

        return mingzhong

    def sousuo(self, xiaoxi_liebiao: list, qian_k: int = 8, lujing_qianzhui: str = "") -> list:
        """执行关键词搜索，返回最相关的知识块。

        搜索策略：
        - 最新消息权重 0.7，全部上下文拼接权重 0.3
        - 再按上下文里的产品场景词做 1.2 倍加权
        - 支持通过路径前缀限定搜索范围

        参数:
            xiaoxi_liebiao: 客户消息列表（时间顺序），最后一条是最新问题
            qian_k: 返回的候选结果数量上限
            lujing_qianzhui: 限定搜索路径（如 "异议处理" 或 "报价与付款/报价流程"），空则全库搜索

        返回:
            [(块索引, 得分), ...] 按得分降序排列的列表
        """
        # 清洗消息：去除空白和空消息（用 for 循环替代列表推导式）
        qingxi_xiaoxi = []
        for m in xiaoxi_liebiao:
            if m and m.strip():
                qingxi_xiaoxi.append(m.strip())
        xiaoxi_liebiao = qingxi_xiaoxi

        # 如果没有有效消息，直接返回空列表
        if not xiaoxi_liebiao:
            return []

        # 最新消息分词（最后一条消息权重最高）
        zuixin_chaxun = fenci(xiaoxi_liebiao[-1])
        # 全部上下文拼接后分词（提供背景信息）
        quanbu_chaxun = fenci(" ".join(xiaoxi_liebiao))

        # 从对话上下文中识别业务场景
        changjing = self._shangxiawen_changjing(xiaoxi_liebiao)

        # 确定候选搜索范围
        if lujing_qianzhui and lujing_qianzhui in self._lujing_suoyin:
            # 如果提供了路径前缀且存在于索引中，使用路径索引限定范围
            houxuan_suoyin = self._lujing_suoyin[lujing_qianzhui]
        else:
            # 否则使用全库所有知识块（用 for 循环替代 list(range(...))）
            houxuan_suoyin = []
            for suoyin in range(len(self.kuaizi)):
                houxuan_suoyin.append(suoyin)

        # 对每个候选知识块计算综合得分
        jieguo = []
        for suoyin in houxuan_suoyin:
            # 综合得分 = 最新消息得分 * 0.7 + 全部上下文得分 * 0.3
            defenzhi = 0.7 * self.bm25.defen(zuixin_chaxun, suoyin) + 0.3 * self.bm25.defen(quanbu_chaxun, suoyin)

            # 如果得分为 0 或负值，跳过该块
            if defenzhi <= 0:
                continue

            # 场景加权：如果知识块的场景与上下文场景有交集，得分乘以 1.2
            if changjing and self._changjing[suoyin] & changjing:
                defenzhi *= 1.2

            # 将结果添加到列表中
            jieguo.append((suoyin, defenzhi))

        # 按得分从高到低排序
        jieguo.sort(key=lambda x: x[1], reverse=True)
        # 返回前 qian_k 个结果
        return jieguo[:qian_k]

    def zhineng_sousuo(self, xiaoxi_liebiao: list, qian_k: int = 8) -> tuple:
        """智能搜索：先识别相关树分支，优先在该分支内搜索。

        搜索策略：
        1. 分析对话上下文，识别可能相关的树分支
        2. 如果有明确的分支匹配且分支内条目数足够，优先在该分支搜索
        3. 如果分支搜索结果不够（少于3条），回退到全库搜索

        参数:
            xiaoxi_liebiao: 客户消息列表
            qian_k: 返回的候选结果数量上限

        返回:
            (结果列表, 匹配到的分支名称列表) 的元组
        """
        # 识别对话中涉及的树分支
        shu_liebiao = self._shangxiawen_shu(xiaoxi_liebiao)

        # 如果有明确的树分支匹配，尝试优先在分支内搜索
        if shu_liebiao:
            # 取第一个匹配的分支作为搜索范围
            lujing = list(shu_liebiao)[0]
            # 获取该分支下的所有知识块索引
            fenzhi_suoyin = self._lujing_suoyin.get(lujing, [])

            # 如果分支内有足够多的条目（至少3个），在该分支内搜索
            if len(fenzhi_suoyin) >= 3:
                jieguo = self.sousuo(xiaoxi_liebiao, qian_k=qian_k, lujing_qianzhui=lujing)
                # 如果分支搜索结果足够（至少3条），返回分支搜索结果
                if len(jieguo) >= 3:
                    return (jieguo, [lujing])

        # 回退到全库搜索（分支内结果不够或没有匹配分支）
        jieguo = self.sousuo(xiaoxi_liebiao, qian_k=qian_k, lujing_qianzhui="")

        # 用 for 循环构建返回的分支名称列表
        fanhui_shu = []
        if shu_liebiao:
            for shu_ming in shu_liebiao:
                fanhui_shu.append(shu_ming)

        return (jieguo, fanhui_shu)

    def huoqu_kuaizi(self, suoyin: int) -> dict:
        """根据索引获取单个知识块的内容。

        参数:
            suoyin: 知识块在列表中的索引编号

        返回:
            知识块字典，包含 intent、triggers、script、category 等字段
        """
        return self.kuaizi[suoyin]

    def lujing_kuaizi(self, lujing_qianzhui: str) -> list:
        """获取指定路径前缀下的所有知识块。

        参数:
            lujing_qianzhui: 路径前缀字符串

        返回:
            该路径下所有知识块的列表
        """
        # 从路径索引中获取该前缀对应的块索引列表
        suoyin_liebiao = self._lujing_suoyin.get(lujing_qianzhui, [])

        # 用 for 循环收集对应的知识块（替代列表推导式）
        jieguo = []
        for suoyin in suoyin_liebiao:
            jieguo.append(self.kuaizi[suoyin])

        return jieguo
