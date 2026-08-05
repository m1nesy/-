# 专利销售智能提词器

AI 驱动的专利代理销售辅助工具，帮助客服人员在客户沟通中快速匹配话术、参考历史案例、提升响应效率。

## 核心功能

### 1. 智能话术推荐
- 根据客户对话上下文，自动从话术知识库中匹配最合适的回复
- 支持 BM25 本地检索 + 大模型 AI 重排序双引擎
- 销售阶段感知：开场→需求→报价→异议→成交，话术随阶段变化

### 2. 知识库管理
- 内置标准话术库，覆盖完整销售流程
- 支持中文关键字、拼音全拼、拼音首字母三种搜索方式
- 树状分类浏览，话术可新增、编辑、删除，使用频率统计

### 3. 专利案例库
- 索引 1,876 份专利证书，覆盖 20 个行业领域
- 多维度筛选：专利类别、审查方式、申请人类型、行业领域
- 搜索结果可直接打开原始 PDF 证书文件作为参考

### 4. 客户消息接入
- 通过 Windows 标准辅助功能 API 读取聊天窗口消息
- 快捷粘贴辅助：检测用户复制操作后自动加入分析上下文
- 多客户对话管理，支持对话历史保存与恢复

### 5. 便捷操作
- 一键发送话术到当前聊天窗口
- 窗口吸附模式（跟随/分屏）
- 全局热键唤起/隐藏

## 技术架构

| 层级 | 技术 |
|------|------|
| 前端 | HTML/CSS/JavaScript (pywebview 桌面窗口) |
| 后端 | Python |
| 搜索 | jieba 中文分词 + BM25 排序算法 |
| AI   | LLM 大模型重排序与草稿生成（OpenAI 兼容接口） |
| 存储 | JSON 文件持久化，原子写入 |

## 运行环境

- Windows 10/11
- Python 3.10+
- 依赖见 `requirements.txt`

## 快速开始

```bash
pip install -r requirements.txt
python app/main.py
# 或双击 启动提词器.bat
```

## 项目结构

```
├── app/                       # 应用源码
│   ├── main.py               # 主入口
│   ├── zhishiku.py           # 知识库管理
│   ├── jiansuoqi.py          # BM25 检索引擎
│   ├── tuijianqi.py          # 话术推荐引擎
│   ├── chongpaiqi.py         # AI 重排序模块
│   ├── zhuanli_sousuo.py     # 专利案例搜索
│   ├── zhuanli_bianmu.py     # 专利文件索引
│   ├── cunchu.py             # 数据持久化
│   ├── shijianzongxian.py    # 事件总线
│   ├── fasongqi.py           # 消息发送器
│   ├── xifu.py               # 窗口吸附管理
│   ├── zhantie_fuzhu.py      # 粘贴辅助
│   ├── jieduan_guanli.py     # 销售阶段管理
│   └── ui/index.html         # 用户界面
├── kb/                        # 知识库数据
│   ├── knowledge_base.json   # 话术库
│   └── patent_kb.json        # 专利索引
└── config.json                # 应用配置
```

## 打包发布

```bash
python _build.py
# 输出：dist/PatentSalesPrompter.zip
```

## 许可证

内部工具，仅供授权人员使用。
