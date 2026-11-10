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

首次使用 AI 时，在设置面板填写服务商的 API Key。默认关闭 AI，未配置密钥时仍可使用本地检索。
启动脚本优先使用项目的 `.venv`，其次使用 PATH 中的 Python；开机自启使用当前解释器对应的 `pythonw.exe`。

## AI 配置与凭据

- 用户配置保存在 `%LOCALAPPDATA%\PatentSalesPrompter\config.json`，首次运行后修改此文件中的 `llm.base_url`、`llm.model` 等设置，并重启或重载应用。
- API Key 通过设置面板保存，使用 Windows DPAPI 加密到同目录的 `llm-key.bin`。普通配置保存不会写入明文密钥；前端只收到是否已配置密钥的状态。
- 旧版资源配置和已有用户配置中的明文密钥会迁移到 DPAPI。已有加密密钥优先；已知的 `config.json.bak`、`legacy-config-*.json` 副本会清理明文密钥字段，其余设置保留。加密失败时保留迁移源文件，避免丢失唯一密钥。
- 清空设置面板中的密钥会删除本地加密文件并关闭 AI，不会从旧备份自动恢复。服务商侧的密钥吊销需要在其控制台完成。
- 模型请求直接发往配置的服务商，通过 `Authorization: Bearer <API Key>` 认证。服务地址必须使用 HTTPS，且不跟随重定向；切换服务商前请核对目标地址。
- AI 开关和“已配置密钥”仅表示本地设置状态，密钥会在实际调用时由服务商验证。401、403、429、超时和网络错误会显示对应提示；有本地候选时保留候选供参考。同一次推荐失败后不会自动追加草稿请求。
- 开启 AI 会发送当前对话上下文，以及重排候选或选出的公司事实资料。应用没有独立的用户登录、角色权限、刷新令牌或自动密钥续期机制。

## 回归测试

在 Windows、Python 3.10+ 环境中执行：

```bash
python -m pip install pywebview pywin32 jieba pypinyin requests Pillow
python -m unittest discover -s app -p "test_*.py" -v
```

测试覆盖真实知识库检索、模型认证失败回退、HTTPS/重定向处理、DPAPI 迁移与删除、并发用量累计、专利查询、消息事件和窗口关闭回调。
测试使用独立临时数据目录；模型 HTTP 请求被替换为测试响应，不使用真实密钥、不产生模型调用费用。微信数据库和 UI Automation 的真实接入需要另行验证。

## 项目结构

```
├── app/                       # 应用源码
│   ├── main.py               # 主入口
│   ├── kb_store.py           # 知识库管理
│   ├── retriever.py          # BM25 检索引擎
│   ├── suggester.py          # 话术推荐引擎
│   ├── reranker.py           # AI 重排序与草稿生成
│   ├── patent_search.py      # 专利案例搜索
│   ├── patent_indexer.py     # 专利文件索引
│   ├── storage.py            # 数据持久化与 DPAPI 密钥存储
│   ├── event_bus.py          # 事件总线
│   ├── sender.py             # 消息发送器
│   ├── attach.py             # 窗口吸附管理
│   ├── clipboard_monitor.py  # 粘贴辅助
│   ├── chatapp_detector.py   # 客户端发现与诊断
│   ├── stage_manager.py      # 销售阶段管理
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
