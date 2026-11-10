# -*- coding: utf-8 -*-
"""意图识别主逻辑模块。
本模块实现了话术推荐的核心流程：
1. 使用检索引擎进行BM25召回
2. 使用重排器进行大模型重排序（可选，可降级）
3. AI草稿兜底生成

主要类:
    Tuijianqi: 推荐器，协调检索、重排和生成
"""
import json
from retriever import Jiansuoqi
from reranker import Chongpaiqi, DamoxingCuowu
from stage_manager import jieduan_shangxiawen
from storage import ZHISHIKU_LUJING, jiazai_peizhi, baocun_peizhi

# ===== 全局阈值和常量 =====

# BM25召回的最高分低于此值时，视为"没有匹配到"，触发AI兜底生成
ZUIDI_DEFEN = 4.0

# 用于AI兜底生成的事实资料ID列表
# 这些是话术库中存储事实性信息的条目（如价格、流程、保障等）
SHISHI_KUAIZI_ID = [
    "quote_03",   # 服务包含内容
    "flow_01",    # 流程说明
    "prod_01",    # 发明参数
    "prod_02",    # 实用新型参数
    "prod_03",    # 价格总表
    "prod_04",    # 成功率数据
    "prod_05",    # 软件
    "prod_06",    # 买专利的三种方式
    "prod_07",    # 费减条件
    "prod_08",    # 时间无法保证的说明
    "pay_01",     # 一次性支付/分期说明
    "pay_02",     # 先收费的原因
    "auth_01",    # 包授权违法的说明
    "auth_03",    # 没通过的保障
    "quality_02", # 未授权补救措施
    "trust_01",   # 避坑指南/公司资质
    "need_01",    # 职称保研要求
    "prod_10",    # 对公账户信息
]


class Tuijianqi:
    """话术推荐器类。
    负责协调本地检索（BM25）和大模型重排（AI），
    最终输出最适合当前客户对话的话术列表。
    """

    def __init__(self):
        """初始化推荐器：加载配置、创建检索引擎和重排器"""
        # 从配置文件加载设置
        self.peizhi = jiazai_peizhi()
        # 创建检索引擎实例
        self.jiansuoqi = Jiansuoqi(str(ZHISHIKU_LUJING))
        # 创建重排器实例
        self.chongpaiqi = Chongpaiqi(self.peizhi)

    def chongxin_jiazai(self):
        """重新加载配置和检索引擎的索引数据。
        当话术库内容发生变化后调用此方法刷新内存中的数据。
        """
        self.peizhi = jiazai_peizhi()
        self.jiansuoqi.chongxin_jiazai()
        self.chongpaiqi = Chongpaiqi(self.peizhi)

    def shezhi_AI(self, qiyong):
        """开关AI重排功能并持久化到配置文件。
        参数:
            qiyong: True 启用AI重排，False 关闭AI仅用本地BM25
        返回值:
            当前状态字典
        """
        # 加载最新配置
        self.peizhi = jiazai_peizhi()
        # 设置LLM启用状态
        self.peizhi.setdefault("llm", {})["enabled"] = bool(qiyong)
        # 保存到配置文件
        baocun_peizhi(self.peizhi)
        # 重新创建重排器以应用新设置
        self.chongpaiqi = Chongpaiqi(self.peizhi)
        return self.status()

    def status(self):
        """返回推荐器的状态信息。
        包含知识库条目数、更新时间、AI启用状态、模型名称、API密钥配置状态。
        """
        miyao = self.chongpaiqi.miyao
        zhuangtai = {
            "kb_count": len(self.jiansuoqi.kuaizi),
            "kb_updated": self._zhishiku_gengxin(),
            "llm_enabled": self.chongpaiqi.qiyong,
            "llm_model": self.chongpaiqi.moxing if self.chongpaiqi.qiyong else None,
            "api_key_configured": bool(miyao),
        }
        return zhuangtai

    def _zhishiku_gengxin(self):
        """获取知识库文件的上次更新时间字符串"""
        try:
            with open(ZHISHIKU_LUJING, "r", encoding="utf-8") as wenjian:
                shuju = json.load(wenjian)
                return shuju.get("updated", "")
        except Exception:
            return ""

    def _goujian_shishi(self, xiaoxi_liebiao, dingbu_N=5, pipei_fenzhi=None):
        """按对话上下文和树分支智能筛选最相关的事实资料。
        大幅节省发送给AI的token数量。
        参数:
            xiaoxi_liebiao: 客户消息列表
            dingbu_N: 返回前N条事实
            pipei_fenzhi: 匹配到的知识库树分支列表
        返回值:
            拼接好的事实资料文本
        """
        # 构建ID到话术块的映射
        ID_duiying = {}
        for kuaizi in self.jiansuoqi.kuaizi:
            kuaizi_id = kuaizi["id"]
            ID_duiying[kuaizi_id] = kuaizi

        # 收集事实块
        shishi_kuaizi = []
        for kuaizi_id in SHISHI_KUAIZI_ID:
            kuaizi = ID_duiying.get(kuaizi_id)
            if kuaizi:
                shishi_kuaizi.append(kuaizi)

        if not shishi_kuaizi:
            return ""

        # 如果已知匹配的树分支，优先取该分支下的事实块
        if pipei_fenzhi:
            fenzhi_shishi = []
            for kuaizi in shishi_kuaizi:
                kuaizi_shu = kuaizi.get("_tree")
                kuaizi_lujing = kuaizi.get("_path", "").split("/")[0]
                if kuaizi_shu in pipei_fenzhi or kuaizi_lujing in pipei_fenzhi:
                    fenzhi_shishi.append(kuaizi)
            if len(fenzhi_shishi) >= 2:
                shishi_kuaizi = fenzhi_shishi

        # 用BM25对事实块排序
        from collections import Counter
        from retriever import BM25, fenci

        linshi_BM25 = BM25()
        wendang_liebiao = []
        for kuaizi in shishi_kuaizi:
            pinlv = Counter()
            for ci in fenci(kuaizi.get("intent", "")):
                pinlv[ci] += 2.0
            for ci in fenci(kuaizi.get("script", "")):
                pinlv[ci] += 1.0
            wendang_liebiao.append(dict(pinlv))
        linshi_BM25.suoyin(wendang_liebiao)

        chaxun_ci = fenci(" ".join(xiaoxi_liebiao))
        defen_jieguo = []
        for suoyin in range(len(shishi_kuaizi)):
            fen = linshi_BM25.defen(chaxun_ci, suoyin)
            defen_jieguo.append((suoyin, fen))
        # 按分数降序排序
        defen_jieguo.sort(key=lambda x: x[1], reverse=True)

        # 必须保留的关键事实块ID
        yongyuan_baoliu = {"prod_03", "flow_01"}
        yixuan_ID = set()
        hang_liebiao = []

        for suoyin, fen in defen_jieguo[:dingbu_N]:
            kuaizi = shishi_kuaizi[suoyin]
            yixuan_ID.add(kuaizi["id"])
            neirong = kuaizi["script"]
            if len(neirong) > 300:
                neirong = neirong[:300] + "…"
            hang_liebiao.append("【" + kuaizi["intent"] + "】" + neirong)

        # 确保永远保留的关键事实被包含
        for baoliu_id in yongyuan_baoliu:
            if baoliu_id not in yixuan_ID and baoliu_id in ID_duiying:
                kuaizi = ID_duiying[baoliu_id]
                neirong = kuaizi["script"]
                if len(neirong) > 300:
                    neirong = neirong[:300] + "…"
                hang_liebiao.append("【" + kuaizi["intent"] + "】" + neirong)

        return "\n".join(hang_liebiao)

    def _shengcheng_caogao(self, xiaoxi_liebiao, jieduan="", pipei_fenzhi=None):
        """调用AI生成兜底回复草稿。
        当话术库中没有合适的话术时，让AI根据公司资料生成一条回复。
        参数:
            xiaoxi_liebiao: 客户消息列表
            jieduan: 销售阶段ID
            pipei_fenzhi: 匹配到的知识库分支
        返回值:
            AI生成的草稿字典，失败返回None
        """
        if not self.chongpaiqi.qiyong:
            return None
        # 构建事实资料文本
        shishi = self._goujian_shishi(xiaoxi_liebiao, pipei_fenzhi=pipei_fenzhi)
        # 获取阶段提示
        jieduan_tishi = jieduan_shangxiawen(jieduan)
        # 调用重排器的生成函数
        shengcheng_jieguo = self.chongpaiqi.shengcheng_huifu(
            xiaoxi_liebiao, shishi, jieduan_tishi)
        if not shengcheng_jieguo:
            return None
        caogao = {
            "reply": shengcheng_jieguo["reply"],
            "used_facts": shengcheng_jieguo.get("used_facts", ""),
        }
        if shengcheng_jieguo.get("usage"):
            caogao["usage"] = shengcheng_jieguo["usage"]
        return caogao

    def shengcheng_caogao(self, xiaoxi_liebiao, jieduan=""):
        """手动触发AI草稿生成（对外API）。
        用于标准话术都不合适的场景，让用户手动请求AI起草回复。
        """
        # 过滤空消息
        guolv_hou = []
        for xiaoxi in (xiaoxi_liebiao or []):
            if xiaoxi and xiaoxi.strip():
                guolv_hou.append(xiaoxi)
        if not guolv_hou:
            return {"draft": None}
        if not self.chongpaiqi.qiyong:
            return {"draft": None, "llm_error": {"code": "disabled", "message": "请先在设置中配置 API Key 并开启 AI。"}}
        try:
            caogao = self._shengcheng_caogao(guolv_hou, jieduan)
        except DamoxingCuowu as cuowu:
            return {"draft": None, "llm_error": cuowu.shuju()}
        jieguo = {"draft": caogao, "llm_used": bool(caogao)}
        if caogao and caogao.get("usage"):
            jieguo["usage"] = caogao["usage"]
        return jieguo

    def huoqu_shu(self):
        """返回知识库的树状结构，供前端展示分类树"""
        return self.jiansuoqi.huoqu_shu()

    def tuijian(self, xiaoxi_liebiao, dingbu_N=3, jieduan=""):
        """推荐话术的主入口方法。
        流程：
        1. 树感知智能搜索（优先在匹配的分支内搜索）
        2. BM25本地检索召回top-8候选
        3. 大模型重排（可选），选出top-3
        4. 如果最高BM25分低于阈值，触发AI草稿兜底

        参数:
            xiaoxi_liebiao: 客户消息列表（时间顺序）
            dingbu_N: 返回的话术数量
            jieduan: 销售阶段ID

        返回值:
            {"results": [...], "llm_used": bool, "draft": dict|None}
        """
        # 过滤空消息
        guolv_hou = []
        for xiaoxi in (xiaoxi_liebiao or []):
            if xiaoxi and xiaoxi.strip():
                guolv_hou.append(xiaoxi)
        if not guolv_hou:
            return {"results": [], "llm_used": False, "draft": None}

        # 获取阶段提示文本
        jieduan_tishi = jieduan_shangxiawen(jieduan)

        # ===== 第一步：树感知智能搜索 =====
        jieguo = self.jiansuoqi.zhineng_sousuo(guolv_hou, qian_k=8)
        mingzhong_liebiao = jieguo[0]  # BM25召回结果
        pipei_fenzhi = jieguo[1]        # 匹配到的树分支

        # 如果没有召回结果，直接生成AI草稿
        if not mingzhong_liebiao:
            shuchu = self.shengcheng_caogao(guolv_hou, jieduan)
            shuchu["results"] = []
            shuchu.setdefault("llm_used", False)
            return shuchu

        # ===== 第二步：收集候选话术 =====
        houxuan = []
        for suoyin, fen in mingzhong_liebiao:
            kuaizi = self.jiansuoqi.huoqu_kuaizi(suoyin)
            houxuan.append(kuaizi)

        # 构建分数映射
        defen_duiying = {}
        for (suoyin, fen), kuaizi in zip(mingzhong_liebiao, houxuan):
            kuaizi_id = kuaizi["id"]
            defen_duiying[kuaizi_id] = round(fen, 2)

        # ===== 第三步：大模型重排序（可选）=====
        llm_cuowu = None
        try:
            chongpai_jieguo = self.chongpaiqi.chongpai(guolv_hou, houxuan, zuidao_shu=dingbu_N, jieduan_tishi=jieduan_tishi)
        except DamoxingCuowu as cuowu:
            chongpai_jieguo = None
            llm_cuowu = cuowu.shuju()
        AI_shiyong = chongpai_jieguo is not None

        if chongpai_jieguo is None:
            # AI重排不可用，使用BM25排序
            xuanzhong_jieguo = []
            for kuaizi in houxuan[:dingbu_N]:
                xuanzhong_jieguo.append({"id": kuaizi["id"], "reason": ""})
            xuyao_caogao = mingzhong_liebiao[0][1] < ZUIDI_DEFEN
        else:
            xuanzhong_jieguo = chongpai_jieguo["picks"]
            xuyao_caogao = chongpai_jieguo["need_draft"]

        caogao = None
        # 同一次推荐中模型已失败时，不再发第二次草稿请求。
        if xuyao_caogao and not llm_cuowu:
            try:
                caogao = self._shengcheng_caogao(guolv_hou, jieduan, pipei_fenzhi=pipei_fenzhi)
            except DamoxingCuowu as cuowu:
                llm_cuowu = cuowu.shuju()
        if llm_cuowu and not xuanzhong_jieguo:
            xuanzhong_jieguo = [{"id": x["id"], "reason": ""} for x in houxuan[:dingbu_N]]

        # ===== 第四步：构建返回结果 =====
        # 构建ID到候选的映射
        ID_duiying = {}
        for kuaizi in houxuan:
            ID_duiying[kuaizi["id"]] = kuaizi

        jieguo_liebiao = []
        paiming = 1
        for xuanxiang in xuanzhong_jieguo:
            kuaizi = ID_duiying.get(xuanxiang["id"])
            if not kuaizi:
                continue
            jieguo_liebiao.append({
                "rank": paiming,
                "id": kuaizi["id"],
                "intent": kuaizi["intent"],
                "category": kuaizi["category"],
                "script": kuaizi["script"],
                "tips": kuaizi.get("tips", ""),
                "reason": xuanxiang.get("reason", ""),
                "score": defen_duiying.get(kuaizi["id"], 0),
            })
            paiming = paiming + 1

        shuchu = {
            "results": jieguo_liebiao,
            "llm_used": AI_shiyong or bool(caogao),
            "draft": caogao,
            "matched_branches": pipei_fenzhi,
        }
        if llm_cuowu:
            shuchu["llm_error"] = llm_cuowu
        # 汇总重排和草稿用量，避免界面漏掉第二次调用。
        yongliang_liebiao = []
        for jieguo_xiang in (chongpai_jieguo, caogao):
            if jieguo_xiang and jieguo_xiang.get("usage"):
                yongliang_liebiao.append(jieguo_xiang["usage"])
        if yongliang_liebiao:
            shuchu["usage"] = {
                "call_tokens": sum(x.get("call_tokens", 0) for x in yongliang_liebiao),
                "call_cost": round(sum(x.get("call_cost", 0) for x in yongliang_liebiao), 6),
                "model": yongliang_liebiao[-1].get("model"),
            }
        return shuchu
