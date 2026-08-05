# -*- coding: utf-8 -*-
"""销售阶段定义模块。
本模块定义了专利销售的标准阶段流程，为AI上下文增强提供阶段信息。
每个阶段包含名称、目标和推进建议。
"""

# ===== 销售阶段列表 =====
# 每个阶段是一个字典，包含：
#   id: 阶段唯一标识
#   name: 阶段中文名称
#   goal: 本阶段的销售目标
#   color: 阶段对应的显示颜色
JIEDUAN_LIEBIAO = [
    {
        "id": "opening",
        "name": "开场破冰",
        "goal": "确认客户身份、需求来源、加急与否",
        "color": "#3584e4",
    },
    {
        "id": "needs",
        "name": "需求确认",
        "goal": "确定专业方向、使用时间、预算范围、评职称/保研/高企",
        "color": "#2ec27e",
    },
    {
        "id": "quote",
        "name": "报价说明",
        "goal": "报价、说明服务包含内容、流程、时效",
        "color": "#e6a23c",
    },
    {
        "id": "objection",
        "name": "异议处理",
        "goal": "应对价格/授权/时效/信任异议，化解顾虑，推进下一步",
        "color": "#f56c6c",
    },
    {
        "id": "close",
        "name": "逼单/促进",
        "goal": "推动成交：要手机号姓名出合同、收款项、确认课题",
        "color": "#9b51e0",
    },
    {
        "id": "post_sale",
        "name": "成交后",
        "goal": "确认发明人/申请人信息、下发文件、办理登记",
        "color": "#2d6a4f",
    },
    {
        "id": "followup",
        "name": "售后跟进",
        "goal": "跟进审批进度、补正答复提醒、维持长期联系",
        "color": "#6c757d",
    },
]

# ===== 阶段ID到阶段对象的映射表 =====
# 用循环构建映射字典（不使用字典推导式）
JIEDUAN_YINGSE = {}
for jieduan in JIEDUAN_LIEBIAO:
    jieduan_id = jieduan["id"]  # 获取阶段ID
    JIEDUAN_YINGSE[jieduan_id] = jieduan  # 存入映射表


def jieduan_shangxiawen(jieduan_id):
    """构建给AI模型的当前阶段提示文本。
    根据当前所处的销售阶段，生成包含阶段目标、下一步建议的上下文信息。
    参数:
        jieduan_id: 阶段ID字符串，如 "opening", "needs" 等
    返回值:
        阶段提示文本字符串；如果阶段ID为空或不存在，返回空字符串
    """
    # 检查阶段ID是否为空
    if not jieduan_id:
        return ""
    # 从映射表中查找阶段对象
    dangqian_jieduan = JIEDUAN_YINGSE.get(jieduan_id)
    if not dangqian_jieduan:
        return ""  # 阶段ID不存在，返回空
    # 查找当前阶段在列表中的索引位置
    dangqian_weizhi = JIEDUAN_LIEBIAO.index(dangqian_jieduan)
    # 确定下一个阶段的名称
    if dangqian_weizhi + 1 < len(JIEDUAN_LIEBIAO):
        xiage_jieduan = JIEDUAN_LIEBIAO[dangqian_weizhi + 1]
        xiage_ming = xiage_jieduan["name"]
    else:
        # 已是最后一个阶段，下一步目标为维持长期关系
        xiage_ming = "维持长期关系"
    # 构建提示文本
    tishi = (
        "当前销售阶段：" + dangqian_jieduan["name"] + "。"
        "本阶段目标：" + dangqian_jieduan["goal"] + "。"
        "客服需要推进至下一阶段「" + xiage_ming + "」。"
        "请结合当前阶段筛选话术——即使客户问题看起来和阶段不直接相关，"
        "也优先推荐能帮助推进流程的话术。"
    )
    return tishi
