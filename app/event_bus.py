# -*- coding: utf-8 -*-
"""用于捕获对话的线程安全结构化事件传输。"""

# ============================================================
# 导入标准库模块
# 模块名保持英文原名（Python 标准库要求）
# ============================================================
import hashlib
import queue
import threading
import time
from collections import OrderedDict


# ============================================================
# 事件总线类 —— Shijianzongxian
# 提供线程安全的事件发布、去重、消费与状态查询功能
# 用于在多个采集源（微信、QQ 等）与 UI 消费端之间传递结构化事件
# ============================================================

class Shijianzongxian:
    """线程安全的结构化事件总线。

    核心功能：
    1. 事件发布与自动去重（基于 event_id 指纹）
    2. 内部使用 queue.Queue 暂存事件，支持最大容量限制
    3. 使用 OrderedDict 记录已见事件指纹，支持容量上限与 LRU 淘汰
    4. 提供 drain（排空）方法供消费端批量拉取事件
    5. 提供 status（状态）方法查询队列深度与丢弃计数
    """

    def __init__(self, zuidarongliang=1000, yijian_xianzhi=3000):
        """初始化事件总线实例。

        参数：
            zuidarongliang: 内部事件队列的最大容量（默认 1000）。
                            当队列满时，新发布的事件将被静默丢弃。
            yijian_xianzhi: 已见事件指纹字典（OrderedDict）的最大条目数（默认 3000）。
                            超过此上限时，最早插入的条目将被淘汰。
        """
        # ---- 内部事件队列 ----
        # 使用 queue.Queue 实现线程安全的生产者-消费者队列
        # maxsize 限制队列深度，防止内存无限增长
        self._duilie = queue.Queue(maxsize=zuidarongliang)

        # ---- 已见事件指纹字典 ----
        # 使用 OrderedDict 存储已发布事件的 event_id 及其时间戳
        # OrderedDict 保持插入顺序，用于实现 LRU 淘汰策略
        self._yijian = OrderedDict()

        # ---- 已见指纹字典的最大容量 ----
        self._yijian_xianzhi = yijian_xianzhi

        # ---- 发布操作互斥锁 ----
        # 保护 _yijian 字典和 _duilie 队列的并发访问
        self._suo = threading.Lock()

        # ---- 排空操作互斥锁 ----
        # 确保同一时间只有一个线程在消费（drain）事件，
        # 防止多个消费者抢到重复或乱序的事件
        self._paikong_suo = threading.Lock()

        # ---- 丢弃计数器 ----
        # 记录因队列满而被丢弃的事件总数，用于监控和调试
        self._diushi = 0

    @staticmethod
    def xiaoxi_shijian(laiyuan, duihua_id, duihua_mingcheng, jiaose, neirong,
                       xiaoxi_id="", chuangjian_shijian=0):
        """构造一条标准化的消息事件字典。

        该静态方法将原始采集数据组装为统一的事件格式，供发布到总线。
        所有字段值都会被规范化处理（字符串化、去空白）。

        参数：
            laiyuan:           事件来源标识，如 "wechat"、"qq"、"manual"
            duihua_id:         对话的唯一标识符
            duihua_mingcheng:  对话的显示名称
            jiaose:            消息角色，必须是 "me"（我方）或 "them"（对方）
            neirong:           消息正文内容
            xiaoxi_id:         消息的唯一标识符（可选）。若为空则自动基于内容生成
            chuangjian_shijian: 消息创建时间戳（Unix 秒，可选）

        返回：
            一个包含所有事件字段的字典，字段包括：
            event_id, type, source, conversation_id, conversation_name,
            role, content, message_id, created_at, received_at
        """
        # ---- 字段规范化 ----
        # 内容字段：转为字符串并去除首尾空白
        neirong = str(neirong or "").strip()

        # 对话ID字段：使用传入值，若同时为空则回退为对话名称，最终回退为 "manual"
        duihua_id = str(duihua_id or duihua_mingcheng or "manual")

        # 消息ID字段：转为字符串，若为空则后续自动生成
        xiaoxi_id = str(xiaoxi_id or "")

        # ---- 生成事件指纹标识 ----
        # 如果有消息ID则直接用消息ID作为标识；
        # 否则使用 SHA1 哈希（基于 对话ID|角色|创建时间|内容 的组合）作为标识
        if xiaoxi_id:
            # 有显式消息ID，直接使用
            biaoshi = xiaoxi_id
        else:
            # 无消息ID，通过内容组合计算 SHA1 哈希作为唯一标识
            yuanwen = f"{duihua_id}|{jiaose}|{chuangjian_shijian}|{neirong}"
            yuanwen_bianma = yuanwen.encode("utf-8", "ignore")
            biaoshi = hashlib.sha1(yuanwen_bianma).hexdigest()

        # ---- 角色校验 ----
        # 仅接受 "me"（我方）和 "them"（对方）两种角色，其他值统一视为 "unknown"
        if jiaose in {"me", "them"}:
            xiaoyan_hou_jiaose = jiaose
        else:
            xiaoyan_hou_jiaose = "unknown"

        # ---- 组装并返回标准化事件字典 ----
        shijian_shuju = {
            "event_id": f"{laiyuan}:{biaoshi}",
            "type": "message",
            "source": laiyuan,
            "conversation_id": duihua_id,
            "conversation_name": str(duihua_mingcheng or duihua_id),
            "role": xiaoyan_hou_jiaose,
            "content": neirong,
            "message_id": xiaoxi_id,
            "created_at": int(chuangjian_shijian or 0),
            "received_at": time.time(),
        }
        return shijian_shuju

    def fabu(self, shijian):
        """发布一个事件到总线。

        发布前会进行去重检查：如果该 event_id 已经被发布过，则跳过本次发布。
        如果队列已满，事件将被丢弃，丢弃计数器加一。

        参数：
            shijian: 事件字典（通常由 xiaoxi_shijian() 方法构造）

        返回：
            True  表示事件成功入队
            False 表示事件被跳过（已去重）或被丢弃（队列满）
        """
        # ---- 步骤1：提取并校验事件ID ----
        # 安全获取 event_id（如果 shijian 为 None 则返回空字典）
        shijian_id = str((shijian or {}).get("event_id", ""))

        # 没有 event_id 或没有 content 字段的事件直接丢弃
        if not shijian_id:
            return False
        if not (shijian or {}).get("content"):
            return False

        # ---- 步骤2：加锁进行去重检查和入队操作 ----
        with self._suo:
            # 去重检查：如果 event_id 已在 _yijian 字典中，说明该事件已发布过
            if shijian_id in self._yijian:
                # 将已有条目移动到 OrderedDict 末尾（更新 LRU 顺序）
                self._yijian.move_to_end(shijian_id)
                # 跳过本次发布
                return False

            # 将新的 event_id 和时间戳记录到 _yijian 字典
            self._yijian[shijian_id] = time.time()

            # 如果已见指纹字典超过容量上限，淘汰最旧的条目（LRU 策略）
            # 循环淘汰，每次淘汰一个，直到条目数不超过上限
            while len(self._yijian) > self._yijian_xianzhi:
                # popitem(last=False) 移除并返回 OrderedDict 中的第一个条目（最早插入的）
                self._yijian.popitem(last=False)

            # ---- 步骤3：尝试将事件放入队列 ----
            try:
                # put_nowait 是非阻塞入队；如果队列满则抛出 queue.Full 异常
                self._duilie.put_nowait(shijian)
            except queue.Full:
                # 队列已满，丢弃计数器加一
                self._diushi = self._diushi + 1
                return False

        # 成功入队，返回 True
        return True

    def paikong(self, xianzhi=200):
        """批量消费（排空）事件队列中的事件。

        使用专用的排空锁（_paikong_suo），确保同一时间只有一个消费者在拉取事件。
        每次最多拉取 xianzhi 条事件，且不超过 500 条的上限。

        参数：
            xianzhi: 单次最多拉取的事件数量（默认 200，范围 1-500）

        返回：
            事件字典列表（可能为空列表）
        """
        # 用于收集本次拉取到的事件
        shijian_liebiao = []

        # ---- 计算实际拉取数量 ----
        # 确保 xianzhi 在 1 到 500 之间
        shiji_xianzhi = int(xianzhi or 200)
        if shiji_xianzhi < 1:
            shiji_xianzhi = 1
        if shiji_xianzhi > 500:
            shiji_xianzhi = 500

        # ---- 加排空锁，开始消费 ----
        with self._paikong_suo:
            # 循环拉取，最多拉取 shiji_xianzhi 次
            for _ in range(shiji_xianzhi):
                try:
                    # get_nowait 是非阻塞出队；
                    # 如果队列为空则抛出 queue.Empty 异常
                    shijian = self._duilie.get_nowait()
                    shijian_liebiao.append(shijian)
                except queue.Empty:
                    # 队列已空，提前结束循环
                    break

        # 返回本次拉取到的所有事件
        return shijian_liebiao

    def zhuangtai(self):
        """查询事件总线的当前状态。

        返回：
            包含以下字段的字典：
            - queue_depth: 当前队列中待消费的事件数量
            - dropped:     累计被丢弃的事件数量
        """
        # 加锁读取状态，保证数据一致性
        with self._suo:
            zhuangtai_shuju = {
                "queue_depth": self._duilie.qsize(),
                "dropped": self._diushi,
            }
        return zhuangtai_shuju
