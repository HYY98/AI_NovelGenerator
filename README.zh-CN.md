# AI Novel Generator

AI Novel Generator 是一个用于辅助小说创作的项目，当前仓库内主要包含：

- 基于 FastAPI 的后端接口，用于配置、小说、卷、上传和 AI 工作流相关能力
- 正在重构中的 Next.js 前端
- 可在 Windows 下同时拉起前后端的桌面启动器

## 当前状态

该项目目前仍处于重构过程中，尚未完成，也不应视为稳定版本。

- 接口、目录结构和工作流仍可能继续调整
- 旧功能有一部分可能尚未迁移完成，或处于临时不可用状态
- 如果你需要之前的版本，请查看 `main` 分支

## 运行前准备

需要先准备以下环境：

- Python 3
- MongoDB
- Node.js 与 npm（前端需要）

## 配置说明

项目运行时配置读取自 `backend/config/config.yaml`。

首次运行时，项目会自动确保以下文件存在：

- `backend/config/config_default.yaml`
- `backend/config/config.yaml`

如果要使用 AI 相关功能，请先在 `backend/config/config.yaml` 中补充或修改大模型配置，重点包括：

- `api_key`
- 部分提供商需要的 `base_url`
- `default_provider` 以及各工作流步骤对应的 provider 配置
- 如果你明确需要让 SDK 继承 Windows / 系统代理，可设置 `use_system_proxy: true`；默认值为 `false`

如果使用默认数据库配置，还需要确保本地 MongoDB 已启动。
后端现在直接使用 PyMongo Async。启动时会主动 ping MongoDB 并初始化索引，因此数据库配置错误会在启动阶段暴露，而不是等到第一次请求才失败。

MongoDB 相关配置包括：

- `mongodb_url`：MongoDB 连接串
- `mongo_database_name`：数据库名
- `mongo_timeout_ms`：服务选择超时时间，单位毫秒
- `mongo_transaction_mode`：事务模式，可选 `auto`、`required`、`disabled`；默认 `auto` 会在副本集/分片集启用事务，在单机 MongoDB 上降级为顺序写入

配置编辑可以在前端的设置界面进行，或直接编辑上述 YAML 文件。

阵营 API 现在必须带小说作用域，请使用 `/api/factions/novel/{novel_id}/...` 下的路径；旧的非作用域阵营路径已移除。

## 安装依赖

安装 Python 依赖：

```bash
pip install -r requirements.txt
```

安装前端依赖：

```bash
cd frontend
npm install
```

## 运行方式

### 方式一：Windows 启动器

Windows 下最直接的启动方式是：

```bat
start.bat
```

启动后可通过桌面启动器分别或同时启动：

- 后端：`http://127.0.0.1:8200`
- 接口文档：`http://127.0.0.1:8200/docs`
- 前端：`http://127.0.0.1:3300`

启动器顶部可以设置并持久化前后端端口，默认分别为 8200 和 3300。端口只在两个服务均停止时允许修改；启动器会同步后端 CORS、前端 API 地址和实际监听参数，并在启动前识别 Windows 排除端口。

### 方式二：手动启动后端

```bash
python main.py
```

如果需要输出更完整的后端调试日志，并在控制台打印 AI 原始响应内容，可以这样启动：

```bash
python main.py --debug
```

如果通过 Windows 启动器启动，在启动后端前勾选 `后端调试日志` 即可。

### 方式三：手动启动前端

```bash
cd frontend
npm run dev -- --hostname 127.0.0.1 --port 3300
```

## 测试

数据库测试：

```bash
python -m pytest tests -q
```

按文件运行：

```bash
python -m pytest tests/test_mongo_transaction.py -q
python -m pytest tests/test_volumes.py -q
python -m pytest tests/test_factions.py -q
python -m pytest tests/test_core_factions.py -q
```

API 测试需要 `backend/config/config.yaml` 中的 MongoDB 配置可连接。

## 章节与设定卡 AI 闭环

AI 只产出候选，用户确认后才写入正式数据。任何"AI 直接覆盖正文/直接改设定"的实现都不应进入本分支。

### 使用顺序

```text
小说信息 → 设定/角色/势力 → 章节目标与关联 → AI 生成章节方案（确认）
        → AI 生成本章正文（采用） → 一致性审校 → 定稿确认状态变更 → 进入下一章
```

### 新增接口

章节 AI（前缀 `/api/llm/chapters`，路径中的 `chapter_id` 为章节 ObjectId）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/{chapter_id}/plan` | 生成章节剧情方案候选 |
| POST | `/{chapter_id}/draft` | 生成正文候选（不覆盖 `chapters.content`） |
| POST | `/{chapter_id}/continue` | 续写候选，只携带当前章节尾部 |
| POST | `/{chapter_id}/rewrite` / `expand` / `compress` | 选段改写 / 扩写 / 压缩，必须带 `selection` |
| POST | `/{chapter_id}/review` | 结构化一致性审校（blocking / warning / notice） |
| POST | `/{chapter_id}/propose-state-changes` | 从正文提取状态变更候选 |
| POST | `/{chapter_id}/stream` | 统一 SSE 入口，通过 `kind` 选择生成类型 |
| POST | `/{chapter_id}/finalize` | 定稿两阶段：`stage=review` 审校取候选，`stage=commit` 确认后落库 |
| GET | `/{chapter_id}/pending-state-changes` | 刷新后恢复待确认列表 |

设定卡 AI（前缀 `/api/llm/setting-cards`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/generate` | 生成卡片候选 |
| POST | `/{card_id}/complete` | 只补全空白字段，支持 `locked_fields` |
| POST | `/extract` | 从世界观/小说信息/章节正文提取卡片 |
| POST | `/check-conflicts` | 检查卡片与全书设定冲突 |

请求统一包含 `novel_id`、`request_id`、`user_prompt`、`provider` 与扁平生成参数；响应统一为
`{kind, candidate_id, data, source, warnings, conflicts, provider, model, context_snapshot}`。
**`candidate_id` 只是候选，采用必须再调用正式保存接口。**

### 新增集合

- `generation_records`：每次生成的输入上下文版本快照、候选结果与采纳状态；`request_id` 在同一小说同一生成类型下幂等。
- `story_events`：地点/物品/规则/角色的事实变更事件，只有 `accepted` 的事件会更新正式卡片。

章节生成上下文按"当前章节可见"组装（关联实体 → 硬规则 → 最近章节摘要 → 伏笔 → 全书核心设定），
不会注入未来章节才会发生的事实。

### 验证

```bash
# 端到端验证（不调用大模型，会自建临时小说并自动清理）
.venv/bin/python scripts/verify_chapter_ai_closure.py
```

## 说明

本 README 只描述当前重构中的项目状态。如果你要查看更早期、相对完整的版本，请直接切换到 `main` 分支查看。
