# -*- coding: utf-8 -*-
"""专利销售智能提词器 v2.0 - 桌面悬浮窗入口
功能：AI 推荐 + 快捷回复面板 + 一键发送 + 窗口吸附 + 开机自启 + 图片话术
用法: python app/main.py   (或双击 启动提词器.bat)


- 所有
- 
- 推导式全部展开为简单循环
- 
"""
import ctypes  # 系统底层调用库，用于注册热键
import sys  # 系统参数和函数库
import threading  # 多线程库
import time  # 时间库
from ctypes import wintypes  # Windows 数据类型
from pathlib import Path  # 路径处理库

# ===== 开发/打包模式自适应 =====
# 判断当前是否为打包后的可执行文件运行
_shifou_dabao = getattr(sys, "frozen", False)
# 根据运行模式确定基础目录
if _shifou_dabao:
    # 打包模式：基础目录 = exe所在目录/_internal
    JICHU_MULU = Path(sys.executable).parent / "_internal"
else:
    # 开发模式：基础目录 = 当前脚本的上级目录的上级目录
    JICHU_MULU = Path(__file__).resolve().parent.parent
# 将app目录加入搜索路径，以便导入自定义模块
sys.path.insert(0, str(JICHU_MULU / "app"))

import webview  # 桌面悬浮窗框架 # noqa: E402
from attach import AttachManager  # 窗口吸附管理器 # noqa: E402
from clipboard_monitor import ClipboardMonitor  # 粘贴辅助器 # noqa: E402
from event_bus import Shijianzongxian  # 事件总线 # noqa: E402
from kb_store import Zhishiku  # 话术库（知识库） # noqa: E402
from attach import find_chat_window_fallback  # 查找聊天窗口的回退函数 # noqa: E402
from wx_reader import WxReader  # 聊天软件消息接入引擎 # noqa: E402
from wechat_uia_reader import WeChatUIAReader  # 聊天窗口辅助读取引擎 # noqa: E402
from sender import Sender, autostart_get, autostart_set  # 消息发送器及开机自启函数 # noqa: E402
from storage import jiazai_peizhi, baocun_peizhi  # 配置读写函数 # noqa: E402
from storage import DUIHUA_LUJING, YONGLIANG_LUJING, du_json, yuanzixie_json  # 路径常量和JSON操作 # noqa: E402
from suggester import Tuijianqi  # 话术推荐器 # noqa: E402
from patent_search import ZhuanliSousuo  # 专利案例搜索器 # noqa: E402
import os  # 操作系统接口库 # noqa: E402
import subprocess  # 子进程管理库 # noqa: E402

# 窗口标题常量
CHUANGKOU_BIAOTI = "专利销售智能提词器"

# 开机自启用的启动路径（打包后直接用 exe，开发模式用 pythonw.exe）
PYTHONW_LUJING = sys.executable if _shifou_dabao else r"D:\anaconda\pythonw.exe"


class Api:
    """暴露给前端 JS 的桥接接口类
    前端通过 window.pywebview.api 调用此类的方法
    """

    def __init__(self, fasongqi):  # fasongqi: Sender 发送器实例
        # ----- 初始化话术推荐器 -----
        self.tuijianqi = Tuijianqi()
        # ----- 初始化话术库 -----
        self.huashuku = Zhishiku()
        # ----- 初始化发送器 -----
        self.fasongqi = fasongqi
        # ----- 专利案例库：自动检测证书目录 -----
        peizhi = jiazai_peizhi()  # 加载配置文件
        zhuanli_jichu = peizhi.get("patent_dirs", "").strip()  # 读取配置的专利目录
        if not zhuanli_jichu:
            # 配置为空时自动检测
            if _shifou_dabao:
                # 打包模式：使用exe所在目录
                zhuanli_jichu = str(Path(sys.executable).parent)
            else:
                # 开发模式：使用项目根目录
                zhuanli_jichu = str(JICHU_MULU)
        # 创建专利搜索器实例
        self.zhuanli_sousuo = ZhuanliSousuo(zhuanli_jichu)
        # ----- 窗口吸附管理器（稍后在 main() 中注入）-----
        self.xifu_guanliqi = None
        # ----- 事件总线（消息队列）-----
        self.shijian_zongxian = Shijianzongxian()
        # ----- 聊天消息读取器（稍后初始化）-----
        self.weixin_duquqi = None  # 聊天消息读取器
        self.weixin_uia_duquqi = None  # 聊天窗口读取器
        # ----- 消息接入状态标记 -----
        self._weixin_zhunbei = False  # 消息接入是否就绪
        self._weixin_moshi = "disabled"  # 消息读取模式
        self._weixin_chushihua_suo = threading.Lock()  # 初始化锁，防止重复初始化消息接入
        # 消息接入能力诊断
        self._weixin_nengli = {
            "db_access": "not-attempted",  # 数据库访问状态
            "read_mode": "disabled",  # 读取模式
            "message_access": "not-attempted",  # 消息访问状态
            "message_read_mode": "disabled",  # 消息读取模式
        }

    # ===== 状态查询方法 =====
    def get_status(self):
        """获取当前所有状态信息，供前端轮询显示"""
        zhuangtai = self.tuijianqi.status()  # 获取推荐器状态
        zhuangtai["autostart"] = autostart_get()  # 获取开机自启状态
        # 获取窗口吸附状态
        if self.xifu_guanliqi:
            zhuangtai["attach"] = self.xifu_guanliqi.status()
        else:
            zhuangtai["attach"] = {"enabled": False, "chat_title": ""}
        zhuangtai["send_target"] = self.fasongqi.tracker.status()  # 获取发送目标状态
        # 粘贴辅助状态
        if hasattr(self, "_jiantie_tingzhe"):
            zhuangtai["clipboard_listening"] = self._jiantie_tingzhe
        else:
            zhuangtai["clipboard_listening"] = False
        zhuangtai["wx_ready"] = self._weixin_zhunbei  # 消息接入就绪状态
        zhuangtai["wx_mode"] = self._weixin_moshi  # 消息读取模式
        zhuangtai["wx_listening"] = self._weixin_qiyong()  # 消息是否在读取
        zhuangtai["wx_capability"] = self._weixin_nengli  # 消息接入能力
        # 专利案例库状态
        if self.zhuanli_sousuo:
            zhuangtai["patent_total"] = self.zhuanli_sousuo.zongshu
        else:
            zhuangtai["patent_total"] = 0
        if self.zhuanli_sousuo:
            zhuangtai["patent_base"] = self.zhuanli_sousuo._zhuanli_genmulu
        else:
            zhuangtai["patent_base"] = ""
        return zhuangtai

    def get_send_target(self):
        """获取当前消息发送目标窗口信息"""
        return self.fasongqi.tracker.status()

    # ===== AI 推荐方法 =====
    def suggest(self, xiaoxi_liebiao, jieduan=""):
        """根据客户消息推荐话术
        参数:
            xiaoxi_liebiao: 客户消息列表（时间顺序）
            jieduan: 当前销售阶段ID
        """
        try:
            jieguo = self.tuijianqi.tuijian(xiaoxi_liebiao, jieduan=jieduan or "")
            return jieguo
        except Exception as cuowu:
            # 任何异常都返回空结果，由上层降级处理
            return {"results": [], "llm_used": False, "draft": None, "error": str(cuowu)}

    def ai_draft(self, xiaoxi_liebiao, jieduan=""):
        """让AI生成一条草稿回复
        参数:
            xiaoxi_liebiao: 客户消息列表
            jieduan: 销售阶段ID
        """
        try:
            jieguo = self.tuijianqi.shengcheng_caogao(xiaoxi_liebiao, jieduan=jieduan or "")
            return jieguo
        except Exception as cuowu:
            return {"draft": None, "error": str(cuowu)}

    def set_llm_enabled(self, qiyong):
        """开关AI增强模式
        参数:
            qiyong: True启用AI，False切回纯本地模式
        """
        try:
            jieguo = self.tuijianqi.shezhi_AI(bool(qiyong))
            jieguo["ok"] = True
            return jieguo
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def set_api_key(self, miyao):
        """设置API密钥并保存到配置
        参数:
            miyao: API密钥字符串
        """
        try:
            peizhi = jiazai_peizhi()  # 加载当前配置
            # 设置密钥
            peizhi.setdefault("llm", {})["api_key"] = (miyao or "").strip()
            # 根据密钥是否为空决定启用状态
            peizhi["llm"]["enabled"] = bool(peizhi["llm"]["api_key"])
            baocun_peizhi(peizhi)  # 保存配置
            self.tuijianqi.chongxin_jiazai()  # 重新加载推荐器
            zhuangtai = self.tuijianqi.status()
            zhuangtai["ok"] = True
            return zhuangtai
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    # ===== 消息自动获取方法 =====
    def _weixin_qiyong(self):
        """检查消息自动读取是否启用"""
        # 如果是UIA模式且有UIA读取器，返回UIA状态
        if self._weixin_moshi == "uia" and self.weixin_uia_duquqi:
            return self.weixin_uia_duquqi.enabled
        # 如果有数据库读取器，返回其状态
        if self.weixin_duquqi:
            return self.weixin_duquqi.enabled
        return False

    def _uia_chenggong(self, shujuku_jieguo):
        """UIA模式初始化成功的处理"""
        # 复制数据库检测结果中的能力信息
        nengli = dict(shujuku_jieguo.get("capability") or {})
        # 获取UIA状态
        if self.weixin_uia_duquqi:
            uia_zhuangtai = self.weixin_uia_duquqi.status()
        else:
            uia_zhuangtai = {}
        # 更新能力信息
        nengli.update({
            "db_access": nengli.get("db_access", "unsupported"),
            "message_access": "available",
            "message_read_mode": "uia",
            "read_mode": nengli.get("read_mode", "disabled"),
            "state": "ready",
            "reason_code": None,
            "last_error": "",
            "uia": uia_zhuangtai,
        })
        self._weixin_nengli = nengli  # 保存能力信息
        self._weixin_zhunbei = True  # 标记为就绪
        self._weixin_moshi = "uia"  # 设置为UIA模式
        return {
            "ok": True,
            "mode": "uia",
            "message": "微信窗口读取可用",
            "capability": self._weixin_nengli,
        }

    def _uia_shibai(self, shujuku_jieguo, yuanyin_daima="WECHAT_WINDOW_NOT_FOUND"):
        """UIA模式初始化失败的处理
        参数:
            shujuku_jieguo: 数据库检测结果
            yuanyin_daima: 失败原因代码
        """
        nengli = dict(shujuku_jieguo.get("capability") or {})
        # 获取UIA状态
        if self.weixin_uia_duquqi:
            uia_zhuangtai = self.weixin_uia_duquqi.status()
        else:
            uia_zhuangtai = {"dependency": "missing"}
        # 如果依赖缺失，更新原因代码
        if uia_zhuangtai.get("dependency") == "missing":
            yuanyin_daima = "UIA_DEPENDENCY_MISSING"
        nengli.update({
            "message_access": "unavailable",
            "message_read_mode": "disabled",
            "uia": uia_zhuangtai,
            "reason_code": yuanyin_daima,
        })
        self._weixin_nengli = nengli
        self._weixin_zhunbei = False
        self._weixin_moshi = "disabled"
        # 获取错误信息
        cuowu_xinxi = uia_zhuangtai.get("last_error") or shujuku_jieguo.get("error") or "消息自动获取当前不可用"
        return {
            "ok": False,
            "error": cuowu_xinxi,
            "reason_code": yuanyin_daima,
            "capability": self._weixin_nengli,
        }

    def wx_init(self):
        """初始化聊天消息接入"""
        # 检查消息读取器是否已创建
        if not self.weixin_duquqi:
            return {"ok": False, "error": "消息读取器未初始化"}
        # 尝试获取锁，防止并发初始化
        if not self._weixin_chushihua_suo.acquire(blocking=False):
            return {
                "ok": False,
                "error": "消息接入检测正在进行",
                "reason_code": "READER_INITIALIZING",
                "capability": self._weixin_nengli,
            }
        try:
            jieguo = self.weixin_duquqi.init()  # 初始化聊天消息读取器
            self._weixin_nengli = jieguo.get("capability", self.weixin_duquqi.diagnostics())
            if jieguo.get("ok"):
                # 初始化成功
                self._weixin_nengli.update({
                    "message_access": "available",
                    "message_read_mode": jieguo.get("mode", "db"),
                })
                self._weixin_zhunbei = True
                self._weixin_moshi = jieguo.get("mode", "db")
                return jieguo
            # 如果数据库版本不支持且UIA读取器可用，尝试UIA模式
            if jieguo.get("reason_code") == "VERSION_4_DB_UNSUPPORTED" and self.weixin_uia_duquqi:
                if self.weixin_uia_duquqi.available():
                    return self._uia_chenggong(jieguo)
                yuanyin = self.weixin_uia_duquqi.status().get("reason_code") or "WECHAT_WINDOW_NOT_FOUND"
                return self._uia_shibai(jieguo, yuanyin)
            # 初始化完全失败
            self._weixin_zhunbei = False
            self._weixin_moshi = "disabled"
            return jieguo
        finally:
            self._weixin_chushihua_suo.release()  # 释放锁

    def wx_listen(self, qiyong):
        """开启或关闭消息自动读取"""
        if not self._weixin_zhunbei:
            yuanyin = self._weixin_nengli.get("reason_code", "WECHAT_READER_NOT_READY")
            return {
                "ok": False,
                "error": "微信消息自动获取当前不可用",
                "reason_code": yuanyin,
                "capability": self._weixin_nengli,
            }
        # UIA模式
        if self._weixin_moshi == "uia":
            self.weixin_uia_duquqi.set_enabled(bool(qiyong))
            return {
                "ok": True,
                "enabled": self.weixin_uia_duquqi.enabled,
                "mode": "uia",
                "capability": self._weixin_nengli,
            }
        # 数据库读取器未初始化
        if not self.weixin_duquqi:
            return {
                "ok": False,
                "error": "消息读取器未初始化",
                "capability": self._weixin_nengli,
            }
        # 数据库读取模式
        self.weixin_duquqi.set_enabled(bool(qiyong))
        return {
            "ok": True,
            "enabled": self.weixin_duquqi.enabled,
            "mode": self._weixin_moshi,
            "capability": self._weixin_nengli,
        }

    def wx_status(self):
        """获取消息接入连接状态"""
        nengli = dict(self._weixin_nengli)  # 复制一份能力信息
        # 根据模式补充状态
        if self._weixin_moshi == "uia" and self.weixin_uia_duquqi:
            nengli["uia"] = self.weixin_uia_duquqi.status()
        elif self.weixin_duquqi:
            nengli.update(self.weixin_duquqi.diagnostics())
        self._weixin_nengli = nengli
        return {
            "ready": self._weixin_zhunbei,
            "enabled": self._weixin_qiyong(),
            "mode": self._weixin_moshi,
            "capability": nengli,
        }

    # ===== 快捷回复：浏览/搜索 =====
    def get_categories(self):
        """获取话术库的所有分类列表"""
        return self.huashuku.fenlei()

    def get_kb_tree(self):
        """返回知识库树状结构"""
        try:
            return {"ok": True, "tree": self.tuijianqi.huoqu_shu()}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu), "tree": []}

    def search_scripts(self, chaxun="", fenlei=""):
        """搜索话术
        参数:
            chaxun: 搜索关键字
            fenlei: 分类筛选
        """
        return self.huashuku.sousuo(chaxun, fenlei)

    # ===== 专利案例库 =====
    def search_patents(self, chaxun="", shaixuan=None, dingbu_N=30):
        """搜索专利案例
        参数:
            chaxun: 搜索关键字
            shaixuan: 筛选条件字典 {patent_category, exam_type, applicant_type, field}
            dingbu_N: 返回前N条
        """
        try:
            if shaixuan is None:
                shaixuan = {}
            jieguo = self.zhuanli_sousuo.sousuo(chaxun, shaixuan, dingbu_N=dingbu_N)
            return {"ok": True, "results": jieguo, "total": len(jieguo)}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu), "results": []}

    def get_patent_facets(self):
        """返回所有筛选维度及选项计数"""
        try:
            weidu = self.zhuanli_sousuo.huode_shaixuan_weidu()
            return {"ok": True, "facets": weidu, "total": self.zhuanli_sousuo.zongshu}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu), "facets": {}}

    def open_patent_file(self, zhuanli_ID):
        """用系统默认程序打开专利证书文件
        参数:
            zhuanli_ID: 专利ID
        """
        try:
            shuju = self.zhuanli_sousuo.an_id_huode(zhuanli_ID)
            if not shuju:
                return {"ok": False, "error": "专利不存在"}
            wenjian_lujing = shuju.get("file_path", "")
            if not wenjian_lujing or not os.path.exists(wenjian_lujing):
                return {"ok": False, "error": "文件不存在: " + wenjian_lujing}
            os.startfile(wenjian_lujing)  # 用系统默认程序打开
            return {"ok": True, "file": wenjian_lujing}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def open_patent_folder(self, zhuanli_ID):
        """打开专利文件所在文件夹
        参数:
            zhuanli_ID: 专利ID
        """
        try:
            shuju = self.zhuanli_sousuo.an_id_huode(zhuanli_ID)
            if not shuju:
                return {"ok": False, "error": "专利不存在"}
            wenjian_lujing = shuju.get("file_path", "")
            if not wenjian_lujing:
                return {"ok": False, "error": "路径为空"}
            mulu = os.path.dirname(wenjian_lujing)
            if not os.path.isdir(mulu):
                return {"ok": False, "error": "文件夹不存在: " + mulu}
            # 打开资源管理器并选中文件
            subprocess.Popen('explorer /select,"' + wenjian_lujing + '"')
            return {"ok": True, "folder": mulu}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def reload_patent_kb(self):
        """重新加载专利知识库"""
        try:
            self.zhuanli_sousuo.chongxinzairu()
            return {"ok": True, "total": self.zhuanli_sousuo.zongshu()}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def set_patent_dirs(self, jichu_mulu):
        """设置专利证书根目录并重新加载
        参数:
            jichu_mulu: 证书根目录路径
        """
        try:
            jichu_mulu = (jichu_mulu or "").strip()
            peizhi = jiazai_peizhi()
            peizhi["patent_dirs"] = jichu_mulu
            baocun_peizhi(peizhi)
            if jichu_mulu:
                self.zhuanli_sousuo.shezhi_jichu(jichu_mulu)
            self.zhuanli_sousuo.chongxin_jiazai()
            return {"ok": True, "patent_base": jichu_mulu, "total": self.zhuanli_sousuo.zongshu}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def get_patent_status(self):
        """返回专利库状态信息"""
        try:
            return {
                "ok": True,
                "total": self.zhuanli_sousuo.zongshu,
                "patent_base": self.zhuanli_sousuo._zhuanli_genmulu,
            }
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    # ===== 一键发送 =====
    def send_script(self, kuaizi_ID, zidong_huiche=False):
        """发送话术到目标窗口
        参数:
            kuaizi_ID: 话术块ID
            zidong_huiche: 是否发送后自动按回车
        """
        kuaizi = self.huashuku.huoqu(kuaizi_ID)
        if not kuaizi:
            return {"ok": False, "error": "话术不存在"}
        tupian_liebiao = self.huashuku.jiexi_tupian(kuaizi)  # 解析关联图片
        jieguo = self.fasongqi.send_all(kuaizi.get("script", ""), tupian_liebiao, auto_enter=bool(zidong_huiche))
        if jieguo.get("ok"):
            self.huashuku.jilu_shiyong(kuaizi_ID)  # 记录使用次数
        return jieguo

    def send_text(self, wenben, zidong_huiche=False, shiyong_ID=""):
        """发送任意文本（AI草稿/自定义）
        参数:
            wenben: 要发送的文本
            zidong_huiche: 是否自动回车
            shiyong_ID: 用于统计的使用ID
        """
        jieguo = self.fasongqi.send_text(wenben, auto_enter=bool(zidong_huiche))
        if jieguo.get("ok") and shiyong_ID:
            self.huashuku.jilu_shiyong(shiyong_ID)
        return jieguo

    # ===== 复制到剪贴板（兜底方案）=====
    def copy_to_clipboard(self, wenben, shiyong_ID=""):
        """把文本复制到系统剪贴板
        参数:
            wenben: 要复制的文本
            shiyong_ID: 使用统计ID
        """
        try:
            # 先抑制粘贴辅助，防止自己复制的内容被读取
            if hasattr(self, "_jiantie_tingzhe") and self._jiantie_tingzhe:
                self._jiantie_tingzhe.suppress(wenben)
            import win32clipboard  # Windows剪贴板API
            import win32con  # Windows常量
            win32clipboard.OpenClipboard()  # 打开剪贴板
            try:
                win32clipboard.EmptyClipboard()  # 清空剪贴板
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, wenben)  # 写入文本
            finally:
                win32clipboard.CloseClipboard()  # 关闭剪贴板
            if shiyong_ID:
                self.huashuku.jilu_shiyong(shiyong_ID)
            return True
        except Exception:
            return False

    # ===== 话术编辑 =====
    def save_script(self, shuju):
        """保存或更新话术
        参数:
            shuju: 话术数据字典，含id则更新，否则新增
        """
        try:
            if shuju.get("id"):
                # 有ID = 更新已有话术
                jieguo = self.huashuku.gengxin_huashu(shuju["id"], shuju)
            else:
                # 无ID = 新增话术
                jieguo = self.huashuku.tianjia_huashu(shuju)
            if jieguo.get("ok"):
                # 保存成功后重新加载
                self.tuijianqi.chongxin_jiazai()
                self.huashuku.chongxin_jiazai()
            return jieguo
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def delete_script(self, kuaizi_ID):
        """删除话术
        参数:
            kuaizi_ID: 话术块ID
        """
        try:
            jieguo = self.huashuku.shanchu_huashu(kuaizi_ID)
            if jieguo.get("ok"):
                self.tuijianqi.chongxin_jiazai()
                self.huashuku.chongxin_jiazai()
            return jieguo
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def reload_kb(self):
        """重新加载话术知识库"""
        try:
            self.tuijianqi.chongxin_jiazai()
            self.huashuku.chongxin_jiazai()
            zhuangtai = self.tuijianqi.status()
            zhuangtai["ok"] = True
            return zhuangtai
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    # ===== 剪贴板自动监听 =====
    def set_clipboard_listen(self, qiyong):
        """开启或关闭剪贴板自动监听"""
        if hasattr(self, "_jiantie_tingzhe") and self._jiantie_tingzhe:
            self._jiantie_tingzhe.set_enabled(bool(qiyong))
            self._jiantie_tingzhe = bool(qiyong)
            return {"ok": True, "enabled": self._jiantie_tingzhe}
        return {"ok": False, "error": "粘贴辅助未初始化"}

    def poll_events(self, xianzhi=200):
        """轮询获取事件和读取器健康状态
        返回读取的事件列表和状态信息
        """
        return {
            "events": self.shijian_zongxian.paikong(xianzhi),  # 获取事件
            "event_bus": self.shijian_zongxian.zhuangtai(),  # 事件总线状态
            "wechat": self.wx_status(),  # 微信状态
        }

    def poll_clipboard(self):
        """兼容旧版UI的剪贴板轮询方法"""
        shijian_liebiao = self.poll_events()["events"]  # 获取所有事件
        jiurong_liebiao = []  # 构建兼容格式的列表
        for shijian in shijian_liebiao:
            if shijian.get("source") == "clipboard":
                # 粘贴来源
                jiurong_liebiao.append(shijian.get("content", ""))
            else:
                # 聊天消息来源，格式化为字符串
                jiurong_liebiao.append("__WX__|{role}|{name}|{content}".format(
                    role=shijian.get("role", "them"),
                    name=shijian.get("conversation_name", ""),
                    content=shijian.get("content", ""),
                ))
        return jiurong_liebiao

    def _fabu_jiantie(self, wenben):
        """发布粘贴消息到事件总线"""
        xianzai = time.time()  # 当前时间戳
        shijian = Shijianzongxian.xiaoxi_shijian(
            "clipboard",  # 来源：粘贴
            "manual",  # 对话ID
            "当前客户",  # 对话名称
            "them",  # 角色：对方
            wenben,  # 消息内容
            message_id="clipboard:" + str(xianzai)[:10],  # 消息ID
            created_at=int(xianzai),  # 创建时间
        )
        self.shijian_zongxian.fabu(shijian)

    def _fabu_weixin(self, xiaoxi):
        """发布聊天消息到事件总线"""
        if not isinstance(xiaoxi, dict):
            return  # 非字典格式，忽略
        shijian = Shijianzongxian.xiaoxi_shijian(
            xiaoxi.get("source", "wechat_db"),  # 来源
            xiaoxi.get("conversation_id") or xiaoxi.get("room"),  # 对话ID
            xiaoxi.get("conversation_name") or xiaoxi.get("talker"),  # 对话名称
            xiaoxi.get("role", "them"),  # 角色
            xiaoxi.get("content", ""),  # 消息内容
            message_id=xiaoxi.get("message_id") or xiaoxi.get("id", ""),  # 消息ID
            created_at=xiaoxi.get("created_at") or xiaoxi.get("CreateTime", 0),  # 创建时间
        )
        self.shijian_zongxian.fabu(shijian)

    # ===== 对话历史持久化 =====
    def save_conversations(self, kehu_shuju):
        """前端调用：把多客户对话数据保存到磁盘
        参数:
            kehu_shuju: 客户对话数据列表
        """
        try:
            shuju = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),  # 更新时间
                "clients": kehu_shuju if isinstance(kehu_shuju, list) else [],
            }
            yuanzixie_json(DUIHUA_LUJING, shuju)  # 原子写入
            return {"ok": True, "count": len(shuju["clients"])}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def load_conversations(self):
        """前端调用：从磁盘恢复多客户对话数据"""
        try:
            shuju = du_json(DUIHUA_LUJING, None)
            if shuju and isinstance(shuju.get("clients"), list):
                return {"ok": True, "clients": shuju["clients"], "updated": shuju.get("updated", "")}
            return {"ok": True, "clients": [], "updated": ""}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu), "clients": []}

    def delete_conversation(self, duihua_ID):
        """删除磁盘上的某个对话记录"""
        try:
            shuju = du_json(DUIHUA_LUJING, {"clients": []})
            zhiqian_shuliang = len(shuju.get("clients", []))
            # 展开循环：过滤掉要删除的对话
            xin_kehu_liebiao = []
            for kehu in shuju.get("clients", []):
                if kehu.get("conversationId") != duihua_ID:
                    xin_kehu_liebiao.append(kehu)
            shuju["clients"] = xin_kehu_liebiao
            shuju["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            yuanzixie_json(DUIHUA_LUJING, shuju)
            return {"ok": True, "deleted": zhiqian_shuliang - len(shuju["clients"])}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu)}

    def get_usage_stats(self):
        """返回累计 token 用量和费用摘要"""
        try:
            wendang = du_json(YONGLIANG_LUJING, {"records": [], "summary": {}})
            zhaiyao = wendang.get("summary", {})
            # 获取最近20条调用记录
            jilu = wendang.get("records") or []
            if len(jilu) > 20:
                zuijin = jilu[-20:]  # 取最后20条
            else:
                zuijin = list(jilu)  # 不足20条就全取
            zuijin.reverse()  # 反转使最新在前
            return {"ok": True, "summary": zhaiyao, "recent": zuijin}
        except Exception as cuowu:
            return {"ok": False, "error": str(cuowu), "summary": {}, "recent": []}

    def set_autostart(self, qiyong):
        """设置开机自启动
        参数:
            qiyong: True启用，False禁用
        """
        if _shifou_dabao:
            jiaoben = ""  # 打包模式无需脚本路径
            pyw = sys.executable
        else:
            jiaoben = JICHU_MULU / "app" / "main.py"  # 开发模式的脚本路径
            # 检查pythonw是否存在
            if Path(PYTHONW_LUJING).exists():
                pyw = PYTHONW_LUJING
            else:
                pyw = sys.executable.replace("python.exe", "pythonw.exe")
        jieguo = autostart_set(bool(qiyong), pyw, str(jiaoben))
        jieguo["autostart"] = autostart_get()
        return jieguo

    def set_attach_mode(self, moshi):
        """设置窗口吸附模式"""
        if self.xifu_guanliqi:
            self.xifu_guanliqi.set_mode(moshi or "")
            try:
                peizhi = jiazai_peizhi()
                peizhi.setdefault("window", {})["attach_mode"] = moshi or ""
                baocun_peizhi(peizhi)
            except Exception:
                pass  # 配置保存失败不阻塞
            zhuangtai = self.xifu_guanliqi.status()
            zhuangtai["ok"] = True
            return zhuangtai
        return {"ok": False, "error": "attach 未初始化"}


# ===== 全局热键相关常量 =====
# 修饰键映射表
REJIAN_XIUSHI = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
# 禁止按键重复标志
XIU_SHI_BUCHONGFU = 0x4000
# Windows 热键消息ID
WM_REJIAN = 0x0312


def zhuce_rejian(chuangkou, rejian_zifuchuan):
    """注册全局热键
    参数:
        chuangkou: 要控制显示/隐藏的窗口对象
        rejian_zifuchuan: 热键字符串，如 "ctrl+alt+q"
    """
    try:
        # 解析热键字符串
        bufen_liebiao = []
        for bufen in rejian_zifuchuan.split("+"):
            bufen_qingxi = bufen.strip().lower()
            if bufen_qingxi:
                bufen_liebiao.append(bufen_qingxi)
        xiu_shi = 0  # 修饰键累加值
        anjian = None  # 虚拟键码
        for bufen in bufen_liebiao:
            if bufen in REJIAN_XIUSHI:
                xiu_shi = xiu_shi | REJIAN_XIUSHI[bufen]  # 按位或添加修饰键
            elif len(bufen) == 1:
                anjian = ord(bufen.upper())  # 单字符转为虚拟键码
        if anjian is None:
            return  # 没有有效的按键，退出

        yonghu32 = ctypes.windll.user32  # Windows用户32 API

        class XIAOXI(ctypes.Structure):
            """Windows消息结构体"""
            _fields_ = [
                ("hwnd", wintypes.HWND),  # 窗口句柄
                ("message", wintypes.UINT),  # 消息类型
                ("wParam", wintypes.WPARAM),  # 消息参数
                ("lParam", wintypes.LPARAM),  # 消息参数
                ("time", wintypes.DWORD),  # 时间
                ("pt", wintypes.POINT),  # 光标位置
            ]

        def xunhuan():
            """热键消息循环（在独立线程中运行）"""
            # 注册热键
            if not yonghu32.RegisterHotKey(None, 1, xiu_shi | XIU_SHI_BUCHONGFU, anjian):
                return  # 注册失败退出
            try:
                xiaoxi = XIAOXI()  # 创建消息对象
                kejian = True  # 窗口可见状态
                # 循环等待消息
                while yonghu32.GetMessageW(ctypes.byref(xiaoxi), None, 0, 0) > 0:
                    if xiaoxi.message == WM_REJIAN:
                        try:
                            if kejian:
                                chuangkou.hide()  # 隐藏窗口
                            else:
                                chuangkou.show()  # 显示窗口
                            kejian = not kejian  # 切换状态
                        except Exception:
                            pass  # 忽略切换异常
            finally:
                yonghu32.UnregisterHotKey(None, 1)  # 注销热键

        # 在守护线程中启动消息循环
        threading.Thread(target=xunhuan, daemon=True).start()
    except Exception:
        pass  # 热键注册失败不影响主流程


def zhuchengxu():
    """主函数：创建窗口并启动应用"""
    # 加载配置
    peizhi = jiazai_peizhi()
    chuangkou_peizhi = peizhi.get("window", {})

    # 创建发送器
    fasongqi = Sender()
    # 创建API桥接对象
    jiekou = Api(fasongqi)

    # HTML页面路径
    html_lujing = JICHU_MULU / "app" / "ui" / "index.html"
    # 创建悬浮窗
    chuangkou = webview.create_window(
        CHUANGKOU_BIAOTI,  # 窗口标题
        str(html_lujing),  # HTML文件路径
        js_api=jiekou,  # JS桥接对象
        width=int(chuangkou_peizhi.get("width", 500)),  # 窗口宽度
        height=int(chuangkou_peizhi.get("height", 780)),  # 窗口高度
        on_top=bool(chuangkou_peizhi.get("on_top", True)),  # 置顶
        resizable=True,  # 可调整大小
        min_size=(200, 400),  # 最小尺寸
    )

    def chazhao_ziji():
        """查找本窗口的Windows句柄"""
        import os as _os
        import win32gui
        import win32process
        jincheng_ID = _os.getpid()  # 获取当前进程ID
        jieguo = {"hwnd": None}

        def huidiao(hwnd, _):
            """Windows窗口枚举回调"""
            if jieguo["hwnd"]:
                return  # 已找到，跳过
            try:
                _, chuang_jincheng = win32process.GetWindowThreadProcessId(hwnd)
                if chuang_jincheng == jincheng_ID and CHUANGKOU_BIAOTI in win32gui.GetWindowText(hwnd):
                    jieguo["hwnd"] = hwnd  # 找到匹配窗口
            except Exception:
                pass

        win32gui.EnumWindows(huidiao, None)  # 枚举所有窗口
        return jieguo["hwnd"]

    # 创建窗口吸附管理器
    xifu = AttachManager(
        chazhao_ziji,  # 查找自身窗口的函数
        fasongqi.tracker,  # 发送目标追踪器
        width=int(chuangkou_peizhi.get("width", 700)),  # 吸附宽度
    )
    xifu.start()  # 启动吸附
    jiekou.xifu_guanliqi = xifu  # 注入到API对象
    # 读取吸附模式配置
    xifu_moshi = chuangkou_peizhi.get("attach_mode", "")
    if xifu_moshi:
        xifu.set_mode(xifu_moshi)

    # ===== 粘贴辅助 =====
    # 用途：用户复制微信消息 → 自动读取 → 进入上下文 → AI分析
    jiantie = ClipboardMonitor()  # 创建粘贴辅助器
    jiantie.set_callback(jiekou._fabu_jiantie)  # 设置回调
    jiantie.start()  # 启动监听
    jiekou._jiantie_tingzhe = jiantie
    jiekou._jiantie_tingzhe = False  # 默认关闭监听
    fasongqi.set_clipboard_suppressor(jiantie.suppress)  # 设置抑制器

    def huoqu_weixin_mubiao():
        """获取当前聊天窗口句柄"""
        import win32gui
        import win32process
        from wechat_probe import is_wechat_process

        # 候选聊天窗口列表
        houxuan_liebiao = []
        if xifu:
            houxuan_liebiao.append(xifu._chat_hwnd)
        houxuan_liebiao.append(fasongqi.tracker.last_hwnd)
        houxuan_liebiao.append(find_chat_window_fallback())
        # 遍历候选，找到聊天窗口
        for hwnd in houxuan_liebiao:
            try:
                if not hwnd:
                    continue
                if not win32gui.IsWindow(hwnd):
                    continue
                if not win32gui.IsWindowVisible(hwnd):
                    continue
                _, jincheng = win32process.GetWindowThreadProcessId(hwnd)
                if is_wechat_process(jincheng):
                    return hwnd
            except Exception:
                continue
        return None

    # ===== 聊天窗口辅助读取引擎（UI Automation）=====
    weixin_uia = WeChatUIAReader()  # 创建窗口读取器
    # 设置回调：lambda包装以匹配参数签名
    weixin_uia.set_callback(lambda _talker, xiaoxi: jiekou._fabu_weixin(xiaoxi))
    weixin_uia.set_target_getter(huoqu_weixin_mubiao)  # 设置目标获取函数
    weixin_uia.start()  # 启动窗口读取
    jiekou.weixin_uia_duquqi = weixin_uia

    # ===== 聊天消息接入引擎（消息接口模式）=====
    weixin = WxReader()  # 创建消息读取器
    weixin.set_callback(lambda _talker, xiaoxi: jiekou._fabu_weixin(xiaoxi))
    weixin.start()  # 启动
    jiekou.weixin_duquqi = weixin

    chushihua_suo = threading.Lock()  # 初始化锁

    def chushihua_weixin():
        """在后台线程中初始化消息接入"""
        if not chushihua_suo.acquire(blocking=False):
            return  # 已在初始化中
        try:
            # 设置初始化中状态
            jiekou._weixin_nengli = {
                "db_access": "initializing",
                "read_mode": "disabled",
                "message_access": "initializing",
                "message_read_mode": "disabled",
            }
            jieguo = jiekou.wx_init()  # 执行初始化
            jiekou._weixin_nengli = jieguo.get("capability", weixin.diagnostics())
            jiekou._weixin_zhunbei = jieguo.get("ok", False)
        except Exception:
            # 初始化失败
            jiekou._weixin_nengli = {
                "db_access": "unsupported",
                "read_mode": "disabled",
                "message_access": "unavailable",
                "message_read_mode": "disabled",
                "reason_code": "READER_INIT_FAILED",
            }
        finally:
            chushihua_suo.release()  # 释放锁

    def dang_xianshi():
        """窗口显示时的回调"""
        threading.Thread(target=chushihua_weixin, daemon=True).start()

    def dang_guanbi():
        """窗口关闭时的回调：停止所有组件"""
    # 遍历所有组件列表
    zujian_liebiao = [weixin, weixin_uia, jiantie, xifu, fasongqi.tracker]
    for zujian in zujian_liebiao:
        try:
            zujian.stop()  # 停止组件
        except Exception:
            pass  # 忽略停止异常

    # 绑定事件回调
    chuangkou.events.shown += dang_xianshi  # 窗口显示
    chuangkou.events.closed += dang_guanbi  # 窗口关闭

    # 注册全局热键
    rejian = chuangkou_peizhi.get("hotkey", "")
    if rejian:
        zhuce_rejian(chuangkou, rejian)

    # 启动webview主循环
    webview.start()


# 程序入口
if __name__ == "__main__":
    zhuchengxu()
