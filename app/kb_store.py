# -*- coding: utf-8 -*-
"""话术库浏览/搜索/统计/编辑
- 分类列表 + 全量浏览
- 关键字 / 拼音全拼 / 拼音首字母 模糊搜索
- 使用统计（发送/复制计数，"常用"排序）
- 话术编辑/新增/删除，写回 knowledge_base.json
"""

# 导入标准库：json 用于读写 JSON 文件
import json
# 导入标准库：re 用于正则表达式分割搜索词
import re
# 导入标准库：time 用于时间戳和日期格式化
import time

# 导入第三方库：pypinyin 用于中文转拼音，支持模糊搜索
from pypinyin import lazy_pinyin
# 导入项目内部存储模块：路径常量、原子写入函数、初始化函数
from storage import ZHISHIKU_LUJING, TONGJI_LUJING, TUPIAN_MULU, yuanzixie_json, chushihua_yonghu_shuju

# 调用初始化函数，确保用户数据目录和文件存在
chushihua_yonghu_shuju()

# 最大话术内容长度限制（字符数），防止过长的内容
ZUIDA_HUASHU_CHANGDU = 4000


def _pinyin_ziduan(wenben):
    """计算文本的拼音字段，返回 (全拼连写字符串, 首字母连写字符串)

    参数说明：
        wenben: 待转换的中文文本字符串

    返回值：
        全拼连写：将所有汉字的拼音拼接在一起，如 "专利" -> "zhuanli"
        首字母连写：取每个汉字拼音的首字母拼接，如 "专利" -> "zl"
    """
    # 调用 pypinyin 的 lazy_pinyin 函数，将中文文本转为拼音列表
    pinyin_list = lazy_pinyin(wenben)

    # 全拼：将拼音列表中的所有字符串直接拼接在一起
    quanpin_str = "".join(pinyin_list)

    # 首字母：遍历每个拼音，取其第一个字母连接
    # 注意：展开原生成器表达式，使用简单 for 循环实现
    shouziliao_str = ""
    for py_item in pinyin_list:
        # 跳过空字符串（pypinyin 对非汉字可能返回空串）
        if py_item:
            # 取该拼音的首字母并拼接到结果末尾
            shouziliao_str = shouziliao_str + py_item[0]

    # 返回两个字符串组成的元组
    return quanpin_str, shouziliao_str


class Zhishiku:
    """知识库类：管理话术的加载、搜索、统计和编辑操作

    主要功能：
        1. 从 JSON 文件加载话术数据
        2. 支持分类浏览和关键词搜索
        3. 记录话术使用次数（发送/复制计数）
        4. 话术的新增、修改和删除
        5. 拼音模糊搜索（全拼和首字母匹配）
    """

    def __init__(self):
        """初始化知识库实例，加载数据并构建搜索索引"""
        # 话术列表：存储所有话术条目，每个条目是一个字典
        self.huashu_liebiao = []
        # 使用统计字典：key 为话术 ID，value 为 {"uses": N, "last": "..."}
        self.tongji = {}
        # 执行初始数据加载
        self.chongxin_jiazai()

    def chongxin_jiazai(self):
        """重新从磁盘加载知识库数据和搜索索引

        这个方法在初始化时调用，也在每次修改话术后调用，
        确保内存中的数据与文件保持一致。
        """
        # 打开知识库 JSON 文件，以 UTF-8 编码读取全部内容
        with open(ZHISHIKU_LUJING, "r", encoding="utf-8") as f:
            shuju = json.load(f)
        # 从 JSON 数据中提取话术列表字段
        self.huashu_liebiao = shuju["chunks"]

        # 预构建搜索索引：为每条话术提前计算好原文、全拼和首字母
        # 避免每次搜索时重复计算拼音，大幅提升搜索性能
        self._suoyin = []
        for h in self.huashu_liebiao:
            # 提取话术的各个可搜索字段
            yitu_wenben = h.get("intent", "")
            fenlei_wenben = h.get("category", "")
            # 处理触发词列表：用空格连接所有触发词
            chufa_liebiao = h.get("triggers", [])
            if chufa_liebiao:
                # 如果有触发词，用空格连接成一个字符串
                chufa_wenben = " ".join(chufa_liebiao)
            else:
                # 如果没有触发词，使用空字符串
                chufa_wenben = ""
            huashu_wenben = h.get("script", "")
            # 将所有字段用空格拼接成一个完整文本字符串
            # 并转为小写，实现不区分大小写的搜索
            yuanwen = (yitu_wenben + " " + fenlei_wenben + " " + chufa_wenben + " " + huashu_wenben).lower()
            # 调用拼音转换函数，计算全拼和首字母
            quanpin, shouziliao = _pinyin_ziduan(yuanwen)
            # 将索引元组 (原文, 全拼, 首字母) 添加到索引列表
            self._suoyin.append((yuanwen, quanpin, shouziliao))

        # 尝试加载使用统计数据文件
        try:
            with open(TONGJI_LUJING, "r", encoding="utf-8") as f:
                self.tongji = json.load(f)
        except Exception:
            # 如果文件不存在或读取失败，使用空字典作为默认值
            self.tongji = {}

    def _baocun_zhishiku(self):
        """保存知识库数据到 JSON 文件，使用原子写入确保数据安全"""
        # 先读取当前文件内容，保留原始结构中的其他字段
        with open(ZHISHIKU_LUJING, "r", encoding="utf-8") as f:
            shuju = json.load(f)
        # 更新话术列表字段为内存中的最新数据
        shuju["chunks"] = self.huashu_liebiao
        # 更新修改日期字段为当天日期
        shuju["updated"] = time.strftime("%Y-%m-%d")
        # 调用原子写入函数，启用备份机制防止数据丢失
        yuanzixie_json(ZHISHIKU_LUJING, shuju, backup=True)

    def _baocun_tongji(self):
        """保存使用统计数据到 JSON 文件"""
        # 将统计数据字典直接写入统计文件
        yuanzixie_json(TONGJI_LUJING, self.tongji)

    # ---------- 浏览/搜索 ----------

    def fenlei(self):
        """返回分类列表，每个分类包含名称和该分类下的话术数量

        返回值格式：
            [{"name": "分类名", "count": 话术数量}, ...]
        保持分类在数据中的原始出现顺序。
        """
        shunxu = []  # 记录分类名称的首次出现顺序
        jishu = {}   # 字典：key 为分类名，value 为该分类的话术计数

        # 遍历所有话术，统计每个分类的数量并记录顺序
        for h in self.huashu_liebiao:
            # 获取该话术的分类名称，如果未设置则默认为"其他"
            fenlei_ming = h.get("category", "其他")
            # 更新该分类的计数
            if fenlei_ming in jishu:
                # 如果该分类已存在，计数加一
                jishu[fenlei_ming] = jishu[fenlei_ming] + 1
            else:
                # 如果该分类首次出现，计数初始化为 1
                jishu[fenlei_ming] = 1
            # 如果是新分类名，将其加入顺序列表
            if fenlei_ming not in shunxu:
                shunxu.append(fenlei_ming)

        # 构建返回结果列表
        # 展开原列表推导式为简单 for 循环
        jieguo = []
        for fenlei_ming in shunxu:
            # 每个元素包含分类名称和对应的话术数量
            jieguo.append({"name": fenlei_ming, "count": jishu[fenlei_ming]})
        return jieguo

    def sousuo(self, chaxun="", fenlei_name=""):
        """AND 语义搜索：每个查询词都必须命中（原文/全拼/首字母中任一匹配即可）

        参数说明：
            chaxun: 搜索关键词字符串，多个词用空格分隔，AND 语义
            fenlei_name: 分类过滤条件
                - 空字符串表示不过滤分类
                - 普通字符串表示精确匹配分类名称
                - "__tree__:路径名" 格式表示按树形路径过滤

        返回值格式：
            [{"id": "xxx", "intent": "...", "category": "...", ...}, ...]
            按使用次数降序排列（常用优先），同次数保持原始顺序。
        """
        # 第一步：将查询字符串按空白字符分割为单个词汇
        # 展开原列表推导式为简单 for 循环
        yuanshi_wenben = (chaxun or "").strip().lower()
        fenkai_cihui = re.split(r"\s+", yuanshi_wenben)
        cihui_liebiao = []
        for cihui in fenkai_cihui:
            # 跳过空字符串（连续空格会产生空项）
            if cihui:
                cihui_liebiao.append(cihui)

        # 第二步：处理树形过滤参数
        shu_guolv = ""
        if fenlei_name and fenlei_name.startswith("__tree__:"):
            # 提取 "__tree__:" 前缀后的实际路径值
            shu_guolv = fenlei_name[len("__tree__:"):]
            # 树形过滤和分类过滤互斥，清空分类过滤
            fenlei_name = ""

        # 第三步：遍历所有话术，逐一检查是否符合过滤条件
        jieguo = []
        # 使用索引同步遍历话术列表和搜索索引
        for i in range(len(self.huashu_liebiao)):
            h = self.huashu_liebiao[i]
            yuanwen, quanpin, shouziliao = self._suoyin[i]

            # 按分类名称过滤：如果指定了分类，只保留匹配的话术
            if fenlei_name and h.get("category") != fenlei_name:
                # 分类不匹配，跳过该话术
                continue

            # 按树形路径过滤
            if shu_guolv:
                # 获取话术的路径和树字段
                h_lujing = h.get("_path", "")
                h_shu = h.get("_tree", h.get("category", ""))
                # 筛选条件：树字段完全匹配，或路径以 "树路径/" 开头
                if shu_guolv != h_shu and not h_lujing.startswith(shu_guolv + "/"):
                    # 树路径不匹配，跳过该话术
                    continue

            # 按搜索词过滤：AND 语义，每个词都必须命中至少一种匹配方式
            if cihui_liebiao:
                quanbu_mingzhong = True
                for cihui in cihui_liebiao:
                    # 检查该词是否命中：原文包含 / 全拼包含 / 首字母以之开头 / 首字母包含
                    mingzhong = (cihui in yuanwen) or (cihui in quanpin) or shouziliao.startswith(cihui) or (cihui in shouziliao)
                    if not mingzhong:
                        # 只要有一个词不命中，整个话术就不符合条件
                        quanbu_mingzhong = False
                        break
                # 如果有任何词未命中，跳过该话术
                if not quanbu_mingzhong:
                    continue

            # 获取该话术的使用统计数据
            jilu = self.tongji.get(h["id"], {})
            # 构建搜索结果条目字典
            sousuo_xiang = {}
            sousuo_xiang["id"] = h["id"]
            sousuo_xiang["intent"] = h.get("intent", "")
            sousuo_xiang["category"] = h.get("category", "")
            sousuo_xiang["script"] = h.get("script", "")
            sousuo_xiang["tips"] = h.get("tips", "")
            sousuo_xiang["triggers"] = h.get("triggers", [])
            sousuo_xiang["images"] = h.get("images", [])
            sousuo_xiang["uses"] = jilu.get("uses", 0)
            # 将条目添加到结果列表
            jieguo.append(sousuo_xiang)

        # 第四步：按使用次数降序排序，次数相同保持原顺序（稳定排序）
        jieguo.sort(key=lambda x: -x["uses"])
        return jieguo

    def huoqu(self, huashu_id):
        """根据话术 ID 获取完整的话术条目字典

        参数说明：
            huashu_id: 话术的唯一标识字符串

        返回值：
            如果找到，返回的话术字典（直接引用，修改会影响内存数据）
            如果未找到，返回 None
        """
        # 遍历话术列表查找匹配的 ID
        for h in self.huashu_liebiao:
            if h["id"] == huashu_id:
                # 找到匹配的话术，直接返回
                return h
        # 遍历完毕未找到，返回 None
        return None

    def jiexi_tupian(self, huashu_xiang):
        """解析话术关联的图片，返回实际存在的图片文件路径列表

        参数说明：
            huashu_xiang: 话术条目字典，需包含 "images" 字段

        返回值：
            实际存在于磁盘上的图片文件绝对路径列表
        """
        lujing_liebiao = []
        for wenjianming in huashu_xiang.get("images", []):
            # 拼接图片目录和文件名，得到完整路径
            lujing = TUPIAN_MULU / wenjianming
            # 仅当文件实际存在时才加入结果列表
            if lujing.exists():
                lujing_liebiao.append(str(lujing))
        return lujing_liebiao

    # ---------- 统计 ----------

    def jilu_shiyong(self, huashu_id):
        """记录话术的一次使用，更新使用计数和最后使用时间

        每次用户发送或复制话术时调用此方法，
        统计数据用于"常用"排序和展示。

        参数说明：
            huashu_id: 被使用的话术的唯一标识 ID
        """
        # 获取或创建该话术的统计记录
        if huashu_id in self.tongji:
            # 该话术已有统计记录，直接获取引用
            jilu = self.tongji[huashu_id]
        else:
            # 该话术没有统计记录，创建一个新的
            jilu = {"uses": 0, "last": ""}
            self.tongji[huashu_id] = jilu
        # 使用次数加一
        jilu["uses"] = int(jilu.get("uses", 0)) + 1
        # 更新最后使用时间为当前日期时间
        jilu["last"] = time.strftime("%Y-%m-%d %H:%M")
        # 将更新后的统计数据写入文件
        self._baocun_tongji()

    # ---------- 编辑 ----------

    @staticmethod
    def _qingjie(huashu_neirong):
        """清洗话术文本内容：去除首尾空白字符，并截断到最大长度限制

        参数说明：
            huashu_neirong: 待清洗的话术文本字符串

        返回值：
            清洗后的文本，长度不超过 ZUIDA_HUASHU_CHANGDU
        """
        # 如果输入为 None，使用空字符串代替
        # 去除首尾空白后截断到最大长度
        return (huashu_neirong or "").strip()[:ZUIDA_HUASHU_CHANGDU]

    def tianjia_huashu(self, shuju_bao):
        """新增一条话术到知识库

        参数说明：
            shuju_bao: 包含话术各字段的字典，字段包括：
                id (可选): 话术唯一标识，不提供则自动生成
                category: 分类名称
                intent: 意图/标题
                triggers: 触发词列表
                script: 话术正文内容
                tips: 使用提示
                scenario: 适用场景列表
                images: 关联图片文件名列表

        返回值：
            {"ok": True, "id": "xxx"} 表示成功
            {"ok": False, "error": "原因"} 表示失败
        """
        # 获取或生成话术的唯一 ID
        bianhao = (shuju_bao.get("id") or "").strip()
        if not bianhao:
            # 如果未提供 ID，使用 "custom_" 前缀加时间戳生成
            bianhao = "custom_" + str(int(time.time()))

        # 检查 ID 是否与已有话术冲突
        if self.huoqu(bianhao):
            # 如果 ID 已存在，返回错误信息
            return {"ok": False, "error": "id 已存在: " + bianhao}

        # 处理触发词列表：过滤掉空白项
        # 展开原列表推导式为简单 for 循环
        chufaci_liebiao = []
        for chufaci in shuju_bao.get("triggers", []):
            t_trim = chufaci.strip()
            # 只保留非空的触发词
            if t_trim:
                chufaci_liebiao.append(t_trim)

        # 构建话术条目字典，逐一设置每个字段
        huashu_xiang = {}
        huashu_xiang["id"] = bianhao
        # 设置分类名称：默认值"自定义"，确保不为空
        huashu_xiang["category"] = (shuju_bao.get("category") or "自定义").strip() or "自定义"
        # 设置意图名称：默认值"未命名话术"，确保不为空
        huashu_xiang["intent"] = (shuju_bao.get("intent") or "未命名话术").strip() or "未命名话术"
        huashu_xiang["triggers"] = chufaci_liebiao
        # 清洗并设置话术正文内容
        huashu_xiang["script"] = self._qingjie(shuju_bao.get("script", ""))
        huashu_xiang["tips"] = (shuju_bao.get("tips") or "").strip()
        # 设置适用场景：默认值为 ["通用"]
        huashu_xiang["scenario"] = shuju_bao.get("scenario") or ["通用"]
        # 设置关联图片列表
        huashu_xiang["images"] = shuju_bao.get("images") or []

        # 校验：话术内容不能为空
        if not huashu_xiang["script"]:
            return {"ok": False, "error": "话术内容不能为空"}

        # 将新话术添加到列表末尾
        self.huashu_liebiao.append(huashu_xiang)
        # 保存到文件
        self._baocun_zhishiku()
        # 重新加载以确保索引同步
        self.chongxin_jiazai()
        return {"ok": True, "id": bianhao}

    def gengxin_huashu(self, huashu_id, shuju_bao):
        """更新一条已有话术的字段

        参数说明：
            huashu_id: 要更新的话术的唯一标识
            shuju_bao: 包含要更新字段的字典
                只有 shuju_bao 中包含的字段才会被更新
                字段值为 None 时跳过该字段的更新

        返回值：
            {"ok": True} 表示成功
            {"ok": False, "error": "原因"} 表示失败
        """
        # 查找要更新的话术
        h = self.huoqu(huashu_id)
        if not h:
            # 话术不存在，返回错误
            return {"ok": False, "error": "话术不存在"}

        # 更新文本类字段：分类、意图、提示
        for ziduan in ("category", "intent", "tips"):
            # 只有当字段在请求中存在且不为 None 时才更新
            if ziduan in shuju_bao and shuju_bao[ziduan] is not None:
                # 去除首尾空白后赋值
                h[ziduan] = str(shuju_bao[ziduan]).strip()

        # 更新话术正文内容字段
        if "script" in shuju_bao and shuju_bao["script"] is not None:
            # 清洗内容：去空白 + 截断
            neirong = self._qingjie(shuju_bao["script"])
            # 清洗后如果为空，返回错误
            if not neirong:
                return {"ok": False, "error": "话术内容不能为空"}
            h["script"] = neirong

        # 更新触发词字段
        if "triggers" in shuju_bao and shuju_bao["triggers"] is not None:
            # 处理触发词列表：过滤掉空白项
            # 展开原列表推导式为简单 for 循环
            chufaci_liebiao = []
            for chufaci in shuju_bao["triggers"]:
                t_trim = chufaci.strip()
                # 只保留非空的触发词
                if t_trim:
                    chufaci_liebiao.append(t_trim)
            h["triggers"] = chufaci_liebiao

        # 更新图片字段
        if "images" in shuju_bao and shuju_bao["images"] is not None:
            # 直接替换图片列表
            h["images"] = shuju_bao["images"]

        # 保存修改并重新加载
        self._baocun_zhishiku()
        self.chongxin_jiazai()
        return {"ok": True}

    def shanchu_huashu(self, huashu_id):
        """删除一条话术

        参数说明：
            huashu_id: 要删除的话术的唯一标识

        返回值：
            {"ok": True} 表示删除成功
            {"ok": False, "error": "话术不存在"} 表示未找到该话术
        """
        # 记录删除前的话术总数，用于判断是否真的删除了
        zhiqian_shumu = len(self.huashu_liebiao)

        # 构建新列表：保留所有 ID 不匹配的话术
        # 展开原列表推导式为简单 for 循环
        xin_liebiao = []
        for h in self.huashu_liebiao:
            if h["id"] != huashu_id:
                # ID 不匹配，保留该话术
                xin_liebiao.append(h)
        # 用新列表替换旧列表
        self.huashu_liebiao = xin_liebiao

        # 检查是否真的删除了话术
        if len(self.huashu_liebiao) == zhiqian_shumu:
            # 长度未变，说明没有匹配到要删除的话术
            return {"ok": False, "error": "话术不存在"}

        # 保存修改并重新加载
        self._baocun_zhishiku()
        self.chongxin_jiazai()
        return {"ok": True}
