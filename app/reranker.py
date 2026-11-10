# -*- coding: utf-8 -*-
"""大模型重排序模块
把客户对话上下文 + BM25 召回的候选话术发给大模型（OpenAI 兼容接口，默认 Kimi），
让模型选出最合适的 3 条并给出推荐理由。
未启用时返回 None；调用失败抛出脱敏错误，由上层保留 BM25 结果并提示用户。
"""
import json
import re
import time
from urllib.parse import urlsplit

import requests
from storage import YONGLIANG_LUJING, gengxin_json


class DamoxingCuowu(Exception):
    """可展示给用户的模型错误，不包含请求头、密钥或服务端响应正文。"""

    def __init__(self, daima, xiaoxi):
        super().__init__(xiaoxi)
        self.daima = daima

    def shuju(self):
        return {"code": self.daima, "message": str(self)}

# 模型定价（元/百万 token），可 override
# 默认定价表：输入每百万token 0.8元，输出每百万token 2.0元
# 可通过 config.json 中的 llm.pricing 字段覆盖
MOREN_DINGJIA = {
    "input": 0.8,   # 输入 ¥0.8/1M tokens
    "output": 2.0,  # 输出 ¥2/1M tokens
}


def _jilu_yongliang(moxing, yongliang, dingjia= None):
    """记录一次 LLM 调用用量，返回累计摘要

    参数:
        moxing: 模型名称字符串
        yongliang: API返回的usage字典，包含prompt_tokens/completion_tokens/total_tokens
        dingjia: 定价字典，包含"input"和"output"两个键（元/百万token），为None则用默认定价

    返回:
        本次调用的用量摘要字典 {"call_tokens", "call_cost", "model"}；无数据则返回None

    说明:
        用量记录写入 YONGLIANG_LUJING 对应的JSON文件，保留最近500条记录。
        写入失败不影响主流程，静默忽略异常。
    """
    # 如果没有用量数据或者没有总token数，直接返回None
    if not yongliang or not yongliang.get("total_tokens"):
        return None

    # 从用量数据中提取各项token计数，转为整数
    tishici_tokens = int(yongliang.get("prompt_tokens", 0))
    wancheng_tokens = int(yongliang.get("completion_tokens", 0))
    zong_tokens = int(yongliang.get("total_tokens", 0))

    # 使用传入的定价参数，如果为None则回退到默认定价表
    shiji_dingjia = dingjia or MOREN_DINGJIA

    # 计算输入成本 = 提示词token数 / 100万 * 输入单价
    shuru_chengben = tishici_tokens / 1_000_000 * shiji_dingjia.get("input", 0.8)

    # 计算输出成本 = 完成token数 / 100万 * 输出单价
    shuchu_chengben = wancheng_tokens / 1_000_000 * shiji_dingjia.get("output", 2.0)

    # 总成本 = 输入成本 + 输出成本，保留6位小数
    chengben = round(shuru_chengben + shuchu_chengben, 6)

    # 构建本次调用的单条记录
    jilu_xiang = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": moxing,
        "prompt_tokens": tishici_tokens,
        "completion_tokens": wancheng_tokens,
        "total_tokens": zong_tokens,
        "cost": chengben,
    }

    # 定义文档更新函数：将本次记录追加到用量文件中
    # 这是一个内部辅助函数，用于原子更新用量记录文件
    def gengxin_qi(wendang):
        """更新用量文档：追加新记录、保留最近500条、更新汇总统计"""
        # 确保 records 列表字段存在
        wendang.setdefault("records", [])
        # 将本次调用记录追加到列表末尾
        wendang["records"].append(jilu_xiang)
        # 只保留最近500条记录，防止文件过大
        wendang["records"] = wendang["records"][-500:]

        # 更新累计汇总信息
        hui_zong = wendang.setdefault("summary", {})
        hui_zong["total_prompt_tokens"] = hui_zong.get("total_prompt_tokens", 0) + tishici_tokens
        hui_zong["total_completion_tokens"] = hui_zong.get("total_completion_tokens", 0) + wancheng_tokens
        hui_zong["total_tokens"] = hui_zong.get("total_tokens", 0) + zong_tokens
        hui_zong["total_cost"] = round(hui_zong.get("total_cost", 0) + chengben, 6)
        hui_zong["calls"] = hui_zong.get("calls", 0) + 1
        hui_zong["last_model"] = moxing

    # pywebview 的多个调用可能并发；在同一文件锁内更新累计用量。
    try:
        gengxin_json(YONGLIANG_LUJING, lambda: {"records": [], "summary": {}}, gengxin_qi)
    except Exception:
        # 用量记录写入失败不影响主流程，静默忽略
        pass

    # 返回本次调用的用量摘要信息
    return {
        "call_tokens": zong_tokens,
        "call_cost": chengben,
        "model": moxing,
    }


# ============================================================
# 系统提示词：定义重排序模型的角色身份和判断标准
# 这是发送给大模型的 system prompt，指导模型如何筛选话术
# ============================================================
XITONG_TISHICI = """你是专利代理销售团队的话术推荐引擎。客服正在和客户微信聊天，请你根据对话上下文，从候选话术库中选出最适合此刻发给客户的话术。

判断标准：
1. 意图匹配：客户最新一句话的真实意图是什么（嫌贵/担心不过/问流程/问时间/问付款/要优惠/不信任等）
2. 上下文连贯：结合前面对话（聊的产品类型、客户身份、已说过的话）
3. 销售阶段：开场→需求确认→报价→异议处理→逼单→成交后，话术要符合当前阶段
4. 优审/预审/发明/实用等产品词要与对话一致
5. 严格把关：候选话术必须真的能回答客户的问题。如果客户问的是话术库没有覆盖的泛化问题（闲聊、公司介绍、政策咨询、库外话题等），候选都只是沾点边但答非所问，就不要硬选

只输出 JSON，不要输出任何其他内容：
{"picks": [{"id": "候选id", "reason": "一句话推荐理由（给客服看的，20字以内）"}], "need_draft": false}

规则：
- picks 最多3条，按推荐程度降序；只有真正匹配才选，1条合适就只给1条
- 没有任何候选真正合适时：{"picks": [], "need_draft": true}"""


def goujian_yonghu_tishici(xiaoxi_liebiao, houxuan, jieduan_tishi= "") :
    """构建发送给大模型的用户提示词

    将对话记录和候选话术列表拼接成结构化的提示词文本，
    供大模型进行话术重排序时使用。

    参数:
        xiaoxi_liebiao: 对话消息列表，每条是一条消息文本
        houxuan: 候选话术列表，每项为包含id/script/intent的字典
        jieduan_tishi: 销售阶段提示文本，为空则不显示阶段部分

    返回:
        拼接好的用户提示词字符串
    """
    # ---- 第一步：将消息列表拼接为对话记录文本 ----
    # 使用for循环逐一收集每条消息
    duihua_liebiao = []
    for xiaoxi in xiaoxi_liebiao:
        duihua_liebiao.append(xiaoxi)
    duihuajilu = "\n".join(duihua_liebiao)

    # ---- 第二步：构建候选话术文本列表 ----
    # 每条候选话术截取前150字，超出部分用省略号标记
    houxuan_wenben = []
    for houxuan_xiang in houxuan:
        # 提取话术内容
        huashu_neirong = houxuan_xiang["script"]
        # 截取前150字，超出则追加省略号
        huashu = huashu_neirong[:150]
        if len(huashu_neirong) > 150:
            huashu = huashu + "…"
        # 拼接候选条目文本：id + 意图标签 + 话术摘要
        houxuan_wenben.append(
            f'- id={houxuan_xiang["id"]} | 意图={houxuan_xiang["intent"]} | 话术摘要={huashu}'
        )

    # ---- 第三步：构建阶段信息部分 ----
    # 如果有阶段提示文本就拼接，否则为空字符串
    if jieduan_tishi:
        jieduan_bufen = f"【阶段信息】\n{jieduan_tishi}\n\n"
    else:
        jieduan_bufen = ""

    # ---- 第四步：组装完整的用户提示词 ----
    # 格式：【阶段信息】+【对话记录】+【候选话术】+ 选择指令
    houxuan_neirong = "\n".join(houxuan_wenben)
    return f"""{jieduan_bufen}【对话记录】
{duihuajilu}

【候选话术】
{houxuan_neirong}

请选出真正适合此刻发给客户的话术（最多3条）；如果都不合适，返回 need_draft=true。"""


def _tiqu_json(wenben):
    """从模型输出文本中宽容地提取 JSON

    大模型有时会在 JSON 前后输出额外文字，本函数用正则匹配
    第一个完整的花括号 JSON 对象并解析。

    参数:
        wenben: 大模型返回的原始文本内容

    返回:
        解析后的Python字典/列表；解析失败会向上抛出异常
    """
    # 去除首尾空白字符
    wenben = wenben.strip()
    # 用正则匹配花括号包围的JSON对象（支持跨行匹配）
    pipei_jieguo = re.search(r"\{.*\}", wenben, re.S)
    if pipei_jieguo:
        # 只取匹配到的JSON部分，丢弃前后无关文字
        wenben = pipei_jieguo.group(0)
    # 解析JSON字符串为Python对象
    return json.loads(wenben)


# ============================================================
# 生成回复的系统提示词
# 用于泛化问题兜底：当候选话术都不匹配时，让大模型基于公司资料
# 模仿话术风格，直接生成一条可发送的微信回复草稿
# ============================================================
SHENGCHENG_XITONG_TISHICI = """你是专利代理公司的资深销售顾问"薛老师"，正在微信上和客户聊天。公司资料在下方给你，请模仿以下话术风格回复客户：
- 亲切口语化，自称"薛老师"，称呼客户"咱们/您"，可适当用"哈/呢/~"和表情
- 先共情/回答问题，再自然引导客户说出：专业方向、用途（评职称/加分/高企）、使用时间
- 严禁承诺"包授权/包下证"（违法）；严禁编造资料里没有的价格、周期、退款政策
- 资料里确实没有的（如具体个案时间承诺），就如实说明需以实际为准，并把话题引回需求确认
- 回复控制在 120 字以内，一条微信消息的长度，直接可发

只输出 JSON：{"reply": "回复内容", "used_facts": "依据了哪些资料，一句话"}"""


class Chongpaiqi:
    """重排器类：调用大模型对BM25召回的候选话术进行智能重排序

    核心职责：
    1. chongpai() - 从候选话术中选出最匹配当前对话的几条
    2. shengcheng_huifu() - 当候选都不合适时，基于公司资料生成回复草稿

    两种方法都调用 OpenAI 兼容接口，失败时抛出 DamoxingCuowu 由上层降级处理。
    """

    def __init__(self, peizhi):
        """初始化重排器

        从配置字典中读取LLM相关设置，构建可用的API客户端。

        参数:
            peizhi: 配置字典，包含 "llm" 键，其下包含：
                - enabled: 是否启用大模型重排序
                - api_key: API密钥
                - base_url: API基础URL
                - model: 模型名称
                - timeout: 请求超时秒数
                - extra_body: 额外请求体参数
                - pricing: 定价覆盖
        """
        # 提取LLM配置子字典
        damoxing_peizhi = peizhi.get("llm", {})

        # 是否启用重排器：需要 enabled 为真 且 api_key 不为空
        self.qiyong = bool(damoxing_peizhi.get("enabled")) and bool(damoxing_peizhi.get("api_key"))

        # API基础地址，去除末尾斜杠以便拼接路径
        self.jichu_url = damoxing_peizhi.get("base_url", "https://api.moonshot.cn/v1").rstrip("/")

        # API密钥，用于Bearer认证
        self.miyao = damoxing_peizhi.get("api_key", "")

        # 使用的模型名称，默认 Kimi K2
        self.moxing = damoxing_peizhi.get("model", "kimi-k2-0905-preview")

        # HTTP请求超时时间（秒）
        self.chaoshi = int(damoxing_peizhi.get("timeout", 20))

        # 追加到请求体的额外参数，如方舟/豆包关闭深度思考 {"thinking": {"type": "disabled"}}
        self.ewai_canshu = dict(damoxing_peizhi.get("extra_body", {}))

        # 模型定价（元/百万 token），可通过 config.json 中 llm.pricing 字段覆盖
        self.dingjia = dict(damoxing_peizhi.get("pricing", MOREN_DINGJIA))

    def _qingqiu(self, qingqiuti):
        """统一处理传输和认证错误；重定向时不转发 Bearer 密钥。"""
        try:
            dizhi = urlsplit(self.jichu_url)
            if (dizhi.scheme != "https" or not dizhi.hostname or dizhi.username
                    or dizhi.password or dizhi.query or dizhi.fragment):
                raise ValueError
            dizhi.port  # 校验端口格式
        except ValueError:
            raise DamoxingCuowu("invalid_url", "模型服务地址必须是有效的 HTTPS 地址，且不能包含账号、查询参数或片段。") from None

        try:
            xiangying = requests.post(
                f"{self.jichu_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.miyao}", "Content-Type": "application/json"},
                json=qingqiuti,
                timeout=self.chaoshi,
                allow_redirects=False,
            )
            if 300 <= xiangying.status_code < 400:
                raise DamoxingCuowu("redirect", "模型服务地址发生重定向，请在配置中填写最终的 HTTPS 地址。")
            xiangying.raise_for_status()
        except requests.HTTPError as cuowu:
            daima = cuowu.response.status_code if cuowu.response is not None else 0
            if daima == 401:
                raise DamoxingCuowu("authentication", "API Key 验证失败，请在设置中更新密钥。") from None
            if daima == 403:
                raise DamoxingCuowu("permission", "当前 API Key 无权使用该模型，请检查服务商的权限配置。") from None
            if daima == 429:
                raise DamoxingCuowu("rate_limit", "模型服务调用受限，请稍后重试或检查额度。") from None
            raise DamoxingCuowu("http_error", "模型服务请求失败，请稍后重试并检查服务配置。") from None
        except requests.Timeout:
            raise DamoxingCuowu("timeout", "模型服务响应超时，请稍后重试。") from None
        except requests.RequestException:
            raise DamoxingCuowu("network", "暂时无法连接模型服务，请检查网络。") from None

        try:
            xiangyingti = xiangying.json()
            neirong = xiangyingti["choices"][0]["message"]["content"]
            yongliang = _jilu_yongliang(self.moxing, xiangyingti.get("usage"), self.dingjia)
            shuju = _tiqu_json(neirong)
            if not isinstance(shuju, dict):
                raise ValueError
            return shuju, yongliang
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise DamoxingCuowu("invalid_response", "模型返回格式异常，请重试；本地检索仍可使用。") from None

    def chongpai(self, xiaoxi_liebiao, houxuan, zuidao_shu= 3, jieduan_tishi= ""):
        """对候选话术进行智能重排序

        将对话上下文和候选话术一起发送给大模型，让模型选出最合适的几条。

        参数:
            xiaoxi_liebiao: 对话消息列表，每条为一条消息文本
            houxuan: BM25召回的候选话术列表 [{"id", "script", "intent"}, ...]
            zuidao_shu: 最多返回几条推荐话术
            jieduan_tishi: 当前销售阶段的提示文本，为空则不提供阶段信息

        返回:
            成功: {"picks": [{"id","reason"},...], "need_draft", "usage": {...}}
            未启用时返回 None；失败时抛出 DamoxingCuowu，由上层降级为本地排序
        """
        # 如果重排器未启用或没有候选话术，直接返回None
        if not self.qiyong or not houxuan:
            return None

        try:
            # ---- 第一步：构建API请求体 ----
            # 组装system消息和user消息
            qingqiuti = {
                "model": self.moxing,
                "messages": [
                    {"role": "system", "content": XITONG_TISHICI},
                    {"role": "user", "content": goujian_yonghu_tishici(xiaoxi_liebiao, houxuan, jieduan_tishi)},
                ],
                "temperature": 0.2,
            }
            # 合并额外参数（如关闭深度思考等平台特有参数）
            qingqiuti.update(self.ewai_canshu)

            jiexi_shuju, yongliang_xinxi = self._qingqiu(qingqiuti)

            # ---- 第四步：构建有效候选ID集合 ----
            # 从所有候选话术中提取ID，用于校验模型返回的picks
            # 注意：这里使用for循环展开，不使用集合推导式
            youxiao_bianhao = set()
            for houxuan_xiang in houxuan:
                youxiao_bianhao.add(houxuan_xiang["id"])

            # ---- 第五步：筛选并整理推荐结果 ----
            # 遍历模型返回的picks，只保留ID在候选列表中的项
            # 注意：这里使用for循环展开，不使用列表推导式
            xuanzhong = []
            for xuan_xiang in jiexi_shuju.get("picks", []):
                # 检查该项的id是否在有效候选ID集合中
                if xuan_xiang.get("id") in youxiao_bianhao:
                    # 截取推荐理由到60字以内，防止过长
                    tuijian_liyou = str(xuan_xiang.get("reason", ""))[:60]
                    # 构建选中项
                    xuanzhong.append({"id": xuan_xiang["id"], "reason": tuijian_liyou})
            # 限制返回数量不超过 top_k
            xuanzhong = xuanzhong[:zuidao_shu]

            # ---- 第六步：判断是否需要AI生成回复草稿 ----
            # 如果模型标记 need_draft 或没有任何选中项，则需要兜底生成
            xuyao_caogao = bool(jiexi_shuju.get("need_draft")) or not xuanzhong

            # ---- 第七步：构建并返回结果 ----
            jieguo = {"picks": xuanzhong, "need_draft": xuyao_caogao}
            # 如果有用量信息，附加到结果中
            if yongliang_xinxi:
                jieguo["usage"] = yongliang_xinxi
            return jieguo

        except DamoxingCuowu:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DamoxingCuowu("invalid_response", "模型返回的推荐格式异常，已使用本地检索。") from None

    def shengcheng_huifu(self, xiaoxi_liebiao, shishi_ziliao, jieduan_tishi= ""):
        """泛化问题兜底：基于话术库事实资料，生成一条微信回复草稿

        当候选话术库无法匹配客户问题时，调用此方法让大模型基于公司资料
        生成一条符合话术风格的回复。

        参数:
            xiaoxi_liebiao: 对话消息列表
            shishi_ziliao: 话术库中的事实资料文本（公司介绍、服务内容、价格等）
            jieduan_tishi: 当前销售阶段提示文本

        返回:
            成功: {"reply": "回复内容", "used_facts": "依据说明", "usage": {...}}
            未启用时返回 None；失败时抛出 DamoxingCuowu"""
        # 未启用或没有消息时直接返回None
        if not self.qiyong or not xiaoxi_liebiao:
            return None

        # ---- 第一步：拼接对话记录 ----
        # 使用for循环将消息列表逐一收集后换行拼接
        # 注意：不使用生成器表达式，展开为显式循环
        duihua_liebiao = []
        for xiaoxi in xiaoxi_liebiao:
            duihua_liebiao.append(xiaoxi)
        duihuajilu = "\n".join(duihua_liebiao)

        # ---- 第二步：构建阶段信息部分 ----
        if jieduan_tishi:
            jieduan_bufen = f"【阶段信息】\n{jieduan_tishi}\n\n"
        else:
            jieduan_bufen = ""

        # ---- 第三步：组装用户提示词 ----
        # 包含阶段信息、公司资料、对话记录和生成指令
        yonghu_tishici = (
            f"{jieduan_bufen}"
            f"【公司资料】\n{shishi_ziliao}\n\n"
            f"【对话记录】\n{duihuajilu}\n\n"
            f"请针对客户最新一句话生成回复。"
        )

        try:
            # ---- 第四步：构建API请求体 ----
            qingqiuti = {
                "model": self.moxing,
                "messages": [
                    {"role": "system", "content": SHENGCHENG_XITONG_TISHICI},
                    {"role": "user", "content": yonghu_tishici},
                ],
                "temperature": 0.4,
            }
            # 合并额外参数
            qingqiuti.update(self.ewai_canshu)

            jiexi_shuju, yongliang_xinxi = self._qingqiu(qingqiuti)

            # ---- 第七步：提取回复内容 ----
            # 获取reply字段，去除首尾空白
            huifu = str(jiexi_shuju.get("reply", "")).strip()

            # 如果回复内容为空，返回None表示生成失败
            if not huifu:
                return None

            # ---- 第八步：构建并返回结果 ----
            # 提取依据说明，截取80字
            yiju_shuoming = str(jiexi_shuju.get("used_facts", ""))[:80]
            jieguo = {"reply": huifu, "used_facts": yiju_shuoming}
            # 附加用量信息
            if yongliang_xinxi:
                jieguo["usage"] = yongliang_xinxi
            return jieguo

        except DamoxingCuowu:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DamoxingCuowu("invalid_response", "模型返回的草稿格式异常，请重试。") from None
