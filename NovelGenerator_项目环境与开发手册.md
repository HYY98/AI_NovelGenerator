# NovelGenerator 项目环境与开发手册

> 本文档供 AI 代码生成模型使用。开发前请先通读本文件，再结合具体开发任务生成代码。
>
> 版本：2026-09-16（第二版）
> 相比首版新增：第十一章「本机环境核查结果（实测）」、第十二章「启动与自检清单」、第十三章「已知不一致与风险」；第三、四章按本机实测数据修正。

---

## 一、项目概述

NovelGenerator 是一个 AI 辅助小说创作工具。用户创建小说及世界设定，系统通过可配置的 LLM 工作流生成候选内容，用户审阅后保存为正式数据。

**核心原则：AI 只生成候选，不直接覆盖正式数据。** 所有 AI 产出必须经过用户确认才写入正式表。

- 项目路径：`E:\project\git\NovelGenerator`
- 后端入口：`main.py`
- 前端入口：`frontend/src/app`
- 数据库：MongoDB

---

## 二、技术栈（精确版本）

| 层次 | 技术 | 版本要求 | 本机实测 |
|---|---|---|---|
| 后端语言 | Python | 3.12 | 3.12.10（系统 + `.venv` 一致） |
| 后端框架 | FastAPI + Uvicorn | latest | fastapi 0.141.1 / uvicorn 0.53.0 |
| 数据校验 | Pydantic | latest | 2.13.5 |
| 数据库 | MongoDB + PyMongo Async | pymongo >= 4.16 | pymongo 4.18.1 / mongod 8.3.11 |
| AI SDK | openai / google-genai / anthropic | latest | 3.14.1 / 2.23.0 / 1.6.0 |
| 前端框架 | Next.js | 16.2.10 | 16.2.10 |
| UI 库 | React | 19.2.4 | 19.2.4 |
| 样式 | Tailwind CSS | 4.x | 4.2.2 |
| 组件库 | HeroUI | 3.0.1 | 3.0.1 |
| 国际化 | next-intl | 4.8.3 | 4.8.3 |
| 语言 | TypeScript | 5.x | 5.9.3 |
| 前端测试 | Vitest | 4.1.10 | 4.1.10 |
| 后端测试 | pytest | latest | 9.1.1（**仓库内暂无测试用例**，见第十三章） |
| 前端运行时 | Node.js | **>= 20.9（Next 16 硬性要求）** | 24.19.0 LTS（npm 11.17.0） |

> **注意 1：Next.js 16 有破坏性变更，API 和约定可能与训练数据不同。写前端代码前先读 `frontend/node_modules/next/dist/docs/` 中的相关指南。**
>
> **注意 2：Node.js 是前端唯一的硬性本地依赖，且不能由 Python 环境替代。** 本机此前未安装系统级 Node，只有 IDE 内置的 Node（`C:\Users\yx_zz\.workbuddy\binaries\node\...`，不在 PATH 中），因此手工执行 `npm` / `npm run dev` 会失败。2026-09-16 已安装系统级 Node.js 24.19.0 LTS，见第十一章。

---

## 三、端口与配置

| 服务 | 默认端口 | 本机实际 | 环境变量覆盖 |
|---|---|---|---|
| FastAPI 后端 | 8200 | **8200**（`127.0.0.1`） | `NOVEL_GENERATOR_BACKEND_PORT` |
| Next.js 前端 | 3300 | **3300**（`127.0.0.1`） | `NOVEL_GENERATOR_FRONTEND_PORT` |
| MongoDB | 27017（`config_default.yaml` 默认值） | **27018**（`config.yaml` 显式指定） | `NOVEL_GENERATOR_MONGODB_URL` |

> **MongoDB 端口必须看清是配置文件里的值，不是默认值。** 本机 `backend/config/config.yaml` 写的是 `mongodb://127.0.0.1:27018`，数据库名 `my_database`，与 `config_default.yaml` 的 `localhost:27017` 不同。其它环境（如服务器 17777 实例）同样用 27018，改端口时务必同步。
>
> **前端端口在本机有两套约定**：项目默认 3300，本机脚本 `E:\project\run\ng_frontend.bat` 用 27777（详见 11.5）。二者只能同时存在一个 `next dev`。

**关键配置文件：**
- 默认配置：`backend/config/config_default.yaml`（可被覆盖，不要直接改）
- 本地运行配置：`backend/config/config.yaml`（含 Provider 与 API 密钥，**已在 `.gitignore` 中忽略，不提交 Git**，已在 Git 中确认为未跟踪状态）
- 前端 API 转发：`frontend/next.config.ts` 将 `/api/*` 转发到 `http://127.0.0.1:8200`（**改后端端口必须同步改这里**）

**环境变量：**
```
NOVEL_GENERATOR_MONGODB_URL=mongodb://127.0.0.1:27018
NOVEL_GENERATOR_MONGO_DATABASE_NAME=my_database
NOVEL_GENERATOR_BACKEND_PORT=8200
NOVEL_GENERATOR_FRONTEND_PORT=3300
NEXT_PUBLIC_API_BASE=（为空时使用相对路径，走 next.config.ts 的 rewrite）
```

---

## 四、环境搭建

### 4.1 后端

```powershell
# 项目根目录
cd E:\project\git\NovelGenerator

# 创建虚拟环境（已存在 .venv 可跳过）
python -m venv .venv

# 安装依赖
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 确保 MongoDB 已启动（见 4.3）
# 确认 backend/config/config.yaml 存在且 Provider/密钥正确

# 启动后端
.venv\Scripts\python.exe main.py
# 需要调试日志时：
.venv\Scripts\python.exe main.py --debug
```

### 4.2 前端

```powershell
# frontend 目录
cd E:\project\git\NovelGenerator\frontend

# 安装依赖（node_modules 已存在且完整时可跳过；本机已安装，见第十一章）
npm install

# 启动开发服务器（端口与文档一致）
npm run dev -- --hostname 127.0.0.1 --port 3300

# 生产模式：先构建再启动
npm run build
npm run start -- --hostname 127.0.0.1 --port 3300
```

> **PowerShell 注意**：Node.js 安装后，**已经打开的终端不会自动刷新 PATH**，会报 `'node' 不是内部或外部命令`。处理方式：新开一个终端窗口，或临时执行 `$env:PATH = "C:\Program Files\nodejs;" + $env:PATH`。
> **Next dev 单实例锁**：同一项目目录下只允许一个 `next dev`。若报 `Another next dev server is already running`，先用 `taskkill /PID <pid> /T /F` 结束旧进程，再启动新的。

### 4.3 MongoDB（本机为便携版，非 Windows 服务）

本机 MongoDB 是解压版，位于 `E:\project\server\mogodb`（目录名拼写就是 `mogodb`），以普通进程运行，**开机不会自动启动**：

```powershell
# 启动（端口 27018，仅本机回环）
E:\project\server\mogodb\bin\mongod.exe `
  --dbpath "E:\project\server\mogodb\data\db" `
  --port 27018 `
  --bind_ip 127.0.0.1 `
  --logpath "E:\project\server\mogodb\logs\mongod.log" `
  --logappend
```

- 数据目录：`E:\project\server\mogodb\data\db`
- 日志：`E:\project\server\mogodb\logs\mongod.log`
- 检查是否在跑：`Get-NetTCPConnection -State Listen -LocalPort 27018`
- 本机同时不存在名为 mongo 的 Windows 服务，也没有 Docker，**排障时不要去找服务或容器**。

### 4.4 一键启动（Windows）

双击 `start.bat`，会调用 `.venv\Scripts\pythonw.exe launcher.py` 启动桌面启动器，可管理前后端端口。

- 启动器通过 PATH 调用 `npm.cmd`，因此 **Node.js 必须已安装并在 PATH 中**，否则前端面板会启动失败。
- 启动器拒绝在端口已被占用时启动服务；若后端已由命令行启动（占用 8200），需先停止它或在启动器中改端口。

---

## 五、项目目录结构

### 5.1 后端

```
backend/
├── api/
│   ├── default_routers/          # 常规 CRUD 路由
│   │   ├── novel_router.py
│   │   ├── volume_router.py
│   │   ├── chapter_router.py
│   │   ├── character_router.py
│   │   ├── faction_router.py
│   │   ├── character_relation_router.py
│   │   ├── faction_relation_router.py
│   │   ├── character_faction_binding_router.py
│   │   ├── setting_card_router.py
│   │   ├── setting_card_accept_router.py
│   │   ├── config_router.py
│   │   └── upload_router.py
│   └── llm_routers/              # AI 生成路由
│       ├── create_novel_router.py
│       ├── chapter_generation_router.py
│       ├── character_generation_router.py
│       ├── setting_card_generation_router.py
│       └── novel_blueprint_router.py
├── services/
│   ├── novel/                    # 业务领域服务
│   │   ├── novel_service.py
│   │   ├── volume_service.py
│   │   ├── chapter_service.py
│   │   ├── chapter_finalize_service.py
│   │   ├── chapter_link_validator.py
│   │   ├── character_service.py
│   │   ├── faction_service.py
│   │   ├── character_relation_service.py
│   │   ├── faction_relation_service.py
│   │   ├── character_faction_binding_service.py
│   │   ├── setting_card_service.py
│   │   ├── setting_card_accept_service.py
│   │   └── blueprint_service.py
│   └── llm/                      # AI 生成编排
│       ├── workflow_service.py
│       ├── llm_service.py
│       ├── chapter_generation_service.py
│       ├── chapter_context_service.py
│       ├── character_generation_service.py
│       ├── setting_card_generation_service.py
│       ├── novel_blueprint_service.py
│       ├── text_revision_service.py
│       ├── format_review_service.py
│       ├── generation_support.py
│       └── provider_test_service.py
├── llm/
│   ├── schemas/                  # LLM 输入输出 Pydantic 模型
│   ├── prompts/                  # 提示词模板
│   └── providers/                # Provider 适配层
├── db/
│   ├── mongo.py                  # MongoDB 连接管理
│   ├── base.py                   # 基础仓储（软删除、审计字段）
│   ├── indexes.py                # 索引定义
│   ├── transaction.py            # 事务管理
│   └── repositories/             # 各集合仓储
├── config/
│   ├── config.py                 # 配置加载
│   ├── config_default.yaml       # 默认配置
│   └── config.yaml               # 本地配置（私有）
└── runtime.py                    # 运行时配置（端口等）
```

### 5.2 前端

```
frontend/src/
├── app/
│   ├── layout.tsx
│   ├── page.tsx
│   ├── globals.css
│   └── [locale]/                 # 国际化路由
│       ├── page.tsx
│       ├── layout.tsx
│       ├── providers.tsx
│       ├── settings/             # 设置页
│       └── writing/              # 写作页
├── components/
│   ├── bookshelf/                # 书架/小说管理
│   ├── novel-generation/         # 小说生成向导
│   ├── writing/
│   │   ├── ai/                   # AI 辅助组件
│   │   ├── characters/           # 角色管理
│   │   ├── factions/             # 势力管理
│   │   └── novel-info/
│   ├── settings/                 # 设置页组件
│   ├── shared/                   # 通用组件
│   └── layout/
├── lib/
│   ├── api.ts                    # 后端 API 封装（核心）
│   ├── chapterAiApi.ts           # 章节 AI API
│   ├── relationApi.ts            # 关系 API
│   ├── aiTypes.ts                # AI 相关类型
│   ├── validation.ts
│   ├── saveQueue.ts
│   └── themes.ts
├── types/                        # TypeScript 类型定义
│   ├── novel.ts
│   ├── character.ts
│   ├── config.ts
│   ├── novelBlueprint.ts
│   ├── powerSystem.ts
│   └── textRevision.ts
├── hooks/
│   └── useConfig.ts
└── i18n/                         # 国际化
    ├── messages/en.json
    └── messages/zh.json
```

---

## 六、数据库

### 6.1 集合清单

| 集合 | 职责 | 业务 ID 格式 |
|---|---|---|
| `novels` | 小说基本信息 | `_id` |
| `volumes` | 卷 | `_id`, `novel_id`, `order_index` |
| `chapters` | 章节正文、状态 | `chapter_id`, `novel_id`, `volume_id` |
| `characters` | 角色档案 | `character_id`（如 `char_000001`） |
| `factions` | 势力档案 | `faction_id`（如 `fac_000001`） |
| `character_relations` | 角色关系 | `relation_id` |
| `faction_relations` | 势力关系 | `relation_id` |
| `character_faction_bindings` | 角色-势力绑定 | `binding_id` |
| `setting_cards` | 地点/物品/规则卡 | `card_id`，`type`（location/item/rule） |
| `generation_records` | AI 生成记录 | `generation_id`（如 `gen_000001`） |
| `story_events` | 确认的故事事件 | `event_id` |
| `id_sequences` | 业务编号分配 | `novel_id`, `entity_type` |

### 6.2 数据约定

- **软删除**：所有集合有 `is_deleted`、`deleted_at` 字段，基础仓储查询默认排除已删除数据。
- **审计字段**：`created_at`、`updated_at` 由基础仓储自动维护。
- **业务 ID**：在 `novel_id` 作用域内编号，不是全库唯一。
- **双 ID**：MongoDB `_id`（ObjectId）和业务 ID 同时存在，接口层必须明确用哪个。
- **事务**：支持 `disabled`/`auto`/`required`，`auto` 在 MongoDB 不支持事务时降级为顺序写入，**不等于原子回滚**。

### 6.3 连接本机数据库（排障用）

```powershell
# 用项目虚拟环境带的 pymongo（异步库需 await；临时查数脚本建议直接用同步客户端）
.venv\Scripts\python.exe -c "from pymongo import MongoClient; c=MongoClient('mongodb://127.0.0.1:27018'); print(c['my_database'].list_collection_names())"
```

---

## 七、代码规范与约定

### 7.1 后端

- **路由层**：只做参数校验和调用服务，不写业务逻辑。
- **服务层**：业务规则、跨对象校验、事务控制。
- **仓储层**：纯数据库操作，不写业务逻辑。
- **命名**：Python `snake_case`，文件用 `snake_case.py`。
- **错误处理**：接口返回统一格式，不暴露内部异常堆栈。
- **AI 接口**：返回候选结果 + `generation_id`，不直接写正式表。
- **新增接口**：在 `main.py` 中注册路由，路径前缀 `/api`。

### 7.2 前端

- **API 调用**：统一走 `frontend/src/lib/api.ts`，不直接 fetch。
- **组件命名**：React 组件用 `PascalCase.tsx`。
- **类型定义**：放在 `frontend/src/types/`，接口响应必须有 TypeScript 接口。
- **样式**：用 Tailwind CSS，不写全局 CSS 覆盖。
- **国际化**：用户可见文本走 `next-intl`，不硬编码中文。
- **状态管理**：优先用 React hooks，复杂状态用 `saveQueue.ts` 等已有的工具。

### 7.3 通用红线

1. **AI 候选不直接覆盖正式数据**，必须经过用户确认。
2. **跨小说引用无效**，所有关联数据必须校验 `novel_id` 归属。
3. **不提交密钥**：`config.yaml`、API Key、MongoDB URI 不进 Git。
4. **不绕过软删除**：删除操作走软删除，不物理删除。
5. **不复用已删除的业务 ID**。
6. **章节上下文不注入未来章节内容**。
7. **CORS 不是鉴权**，当前无完整登录/JWT/RBAC 体系。

---

## 八、已有功能清单

### 已完成

- 小说、卷、章节的 CRUD 及状态管理
- 角色、势力、角色关系、势力关系、角色-势力绑定的 CRUD
- 设定卡（地点/物品/规则）CRUD
- AI 生成小说（创意扩写→提取→核心种子→元数据）
- AI 生成角色、章节、设定卡
- AI 补全卡片空字段
- 章节定稿（审校+确认提交，记录故事事件）
- 章节上下文生成
- 创作蓝图（novel_blueprint）基础结构
- 文本修订（text_revision）基础接口
- YAML 配置管理、Provider 配置
- 前端：书架、小说创建向导、写作页、设置页

### 开发中/待完善

- 设定卡 AI 字段级差异预览与合并采纳
- 正文与设定卡片双向同步
- 战力体系结构化
- 角色关系图可视化
- 规则卡硬规则校验
- 正文反哺修改建议

---

## 九、排障指南

| 现象 | 检查项 |
|---|---|
| 页面打不开 | 前端进程是否在跑、3300 端口、`node -v` 是否可用、`npm install` 是否执行 |
| `npm`/`node` 找不到 | Node.js 是否安装、当前终端是否为新开（PATH 未刷新） |
| `Another next dev server is already running` | 同目录已有 dev 服务，`taskkill /PID <pid> /T /F` 后再启动 |
| `/api` 请求 404 | 后端是否在 8200 端口、`next.config.ts` rewrite 配置 |
| 后端启动报错 | MongoDB 是否可达（本机 27018）、`config.yaml` 是否存在、密钥是否正确 |
| 后端启动报 `MongoDB client is not initialized` | 仓储 `__init__` 里提前连库了，改为 `@property collection` 延迟取库 |
| AI 请求失败 | Provider 类型、base URL、模型名、密钥、工作流配置 |
| SSE 中断 | 浏览器网络、反向代理缓冲、后端日志 |
| 端口冲突 | `backend/runtime.py` 和 launcher 设置的端口是否有效且不重复；启动器不会杀占用端口的未知进程 |
| 数据查不到 | 是否被软删除（`is_deleted=true`）、`novel_id` 是否匹配、是否连错库（27018 vs 27017） |
| 前端类型报错 | `frontend/src/types/` 是否与后端字段同步 |
| 前端改动不生效（生产模式） | 生产模式跑的是构建产物，必须重新 `npm run build` |

---

## 十、开发任务输入模板

给 AI 分配开发任务时，除了本手册，还应提供：

```
【开发任务】
（具体要做什么）

【涉及文件】
- 新增：backend/services/llm/xxx_service.py
- 修改：backend/api/llm_routers/setting_card_generation_router.py

【验收标准】
- （可客观验证的条件）

【注意事项】
- （特殊约束，如不要修改已有函数、复用某个现有工具等）
```

---

## 十一、本机环境核查结果（2026-09-16 实测）

本次按「让项目跑起来」的目标逐项核查，结论如下。

### 11.1 核查表

| 组件 | 要求 | 核查结果 | 结论 |
|---|---|---|---|
| Python | 3.12 | 系统 3.12.10；`.venv` 3.12.10 | 满足 |
| 后端依赖 | `requirements.txt` | `.venv` 中 fastapi / uvicorn / pydantic / pymongo 4.18.1 / openai / google-genai / anthropic / customtkinter / python-multipart / httpx / pytest / PyYAML / aiofiles 全部已安装 | 满足 |
| MongoDB | 可连接 | 便携版 mongod 8.3.11，监听 `127.0.0.1:27018`，dbpath `E:\project\server\mogodb\data\db` | 满足（**非服务，需手工启动**） |
| 后端配置文件 | `backend/config/config.yaml` | 已存在，含 Provider 与密钥；已被 `.gitignore` 忽略且未被 Git 跟踪 | 满足 |
| Node.js / npm | 前端运行必需 | **核查时缺失**：PATH 中无 node/npm，常见安装目录无 node.exe，仅存在 IDE 内置 Node（不在 PATH） | **已修复：安装 Node.js 24.19.0 LTS** |
| 前端依赖 | `frontend/package.json` | `node_modules` 完整（next 16.2.10 / react 19.2.4 / @heroui/react 3.0.1 / tailwindcss 4.2.2 / vitest 4.1.10 / typescript 5.9.3 等），`package-lock.json` 已跟踪，`npm ls --depth=0` 无缺失项 | 满足 |
| 端口 | 27018 / 8200 / 3300 | 三者均按预期监听在 `127.0.0.1` | 满足 |
| 后端测试 | pytest | 可执行，但仓库内 **不存在 `tests/` 目录与 `test_*.py`** | 见第十三章 |

### 11.2 本次修复项（唯一缺失项：Node.js）

```powershell
# 用 winget 安装 Node.js LTS（本机执行，实际安装 24.19.0 / npm 11.17.0）
winget install --id OpenJS.NodeJS.LTS --exact --silent --accept-package-agreements --accept-source-agreements

# 验证（注意：必须在安装后新开的终端里执行；旧终端 PATH 未刷新）
node -v      # v24.19.0
npm -v       # 11.17.0
```

- 安装位置：`C:\Program Files\nodejs`，已写入**系统级 PATH**。
- 已有终端 / 已登录的桌面会话可能仍使用旧 PATH；**重启终端或重新登录后全局生效**。
- 前端依赖无需重装：`node_modules` 已是完整状态，Node 24 与项目依赖兼容（Next 16 要求 Node >= 20.9）。

### 11.3 实测验证结果

| 验证项 | 命令 / 地址 | 结果 |
|---|---|---|
| 后端存活 | `http://127.0.0.1:8200/docs` | 200 |
| 后端 OpenAPI | `http://127.0.0.1:8200/openapi.json` | title = `Novel Generator API` |
| 数据库连通（读） | `http://127.0.0.1:8200/api/novels/list` | 200，返回 `{"data":[...]}`（含已有小说数据） |
| 配置接口 | `http://127.0.0.1:8200/api/config` | 200 |
| 设定卡元数据 | `http://127.0.0.1:8200/api/setting-cards/types` | 200 |
| 前端首页 | `http://127.0.0.1:3300/` | 200（返回 HTML） |
| 前端 → 后端代理 | `http://127.0.0.1:3300/api/novels/list` | 200（rewrite 生效） |

### 11.4 本次核查中发现并处理的其它问题

1. **残留的旧 dev 服务器**：核查时另有一个在 `127.0.0.1:27777` 启动的 `next dev`（用 IDE 内置 Node 22 拉起，见 11.5 的 `ng_frontend.bat`）。它占有项目级 dev 锁，导致第二次 `next dev` 直接退出并报 `Another next dev server is already running`。已结束该进程树，并在 **3300** 端口用系统 Node 24 重新启动，验证通过。
2. **后端启动来源**：当前运行中的后端由本机脚本 `E:\project\run\ng_backend.bat` 拉起，命令为 `.venv\Scripts\python.exe main.py`（进程树中还会看到一个是子进程、镜像路径为系统 Python 的进程，属同一次启动）。手工启动时保持同样命令即可，不要用系统 Python 直接跑。

### 11.5 本机惯用启动脚本（`E:\project\run\`，不在 Git 仓库内）

| 脚本 | 作用 | 关键点 |
|---|---|---|
| `start_mongo.bat` | 启动便携版 MongoDB | 端口 `27018`，dbpath `E:\project\server\mogodb\data\db` |
| `ng_backend.bat` | 启动后端 | `set NOVEL_GENERATOR_FRONTEND_PORT=27777` 后运行 `.venv\Scripts\python.exe main.py`，日志写入 `E:\project\run\ng_backend.log` |
| `ng_frontend.bat` | 启动前端 | 把 PATH 指到 IDE 内置 Node 22，`npx next dev --port 27777`，日志写入 `ng_frontend.log` |
| `toonflow_proxy.bat` | 另一个项目（Toonflow）的代理 | 与本项目无关，排障时不要混淆 |

> **端口约定差异（重要）**：项目默认/本文档用的是前端 **3300**，而本机脚本 `ng_frontend.bat` 用的是 **27777**。两者都能跑；后端 CORS 目前是 `allow_origins=["*"]`（`main.py`），因此前端端口不影响接口调用。
> **同一项目目录只能有一个 `next dev`**，切端口前必须先停掉在跑的实例：找到端口对应进程后 `taskkill /PID <pid> /T /F`。
> `ng_frontend.bat` 里硬编码的 IDE 内置 Node 路径已无必要——系统级 Node 24 装好后，直接把该行删除或改为 `npm run dev` 亦可。

---

## 十二、启动与自检清单

### 12.1 启动顺序（本机）

```powershell
# ① MongoDB（若 27018 未监听）
E:\project\server\mogodb\bin\mongod.exe --dbpath "E:\project\server\mogodb\data\db" --port 27018 --bind_ip 127.0.0.1 --logpath "E:\project\server\mogodb\logs\mongod.log" --logappend

# ② 后端（项目根目录，新开终端）
cd E:\project\git\NovelGenerator
.venv\Scripts\python.exe main.py

# ③ 前端（新开终端）
cd E:\project\git\NovelGenerator\frontend
npm run dev -- --hostname 127.0.0.1 --port 3300
```

或者直接双击 `start.bat` 用桌面启动器统一管理（要求 Node.js 已在 PATH 中，且 8200 / 3300 未被占用）。

### 12.2 一键自检命令（PowerShell）

```powershell
# 端口是否都在监听
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in 27018,8200,3300 } |
  Select-Object LocalAddress,LocalPort,OwningProcess | Sort-Object LocalPort

# 后端 + 数据库
Invoke-WebRequest http://127.0.0.1:8200/api/novels/list -UseBasicParsing | Select-Object StatusCode

# 前端 + 代理
Invoke-WebRequest http://127.0.0.1:3300/ -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest http://127.0.0.1:3300/api/novels/list -UseBasicParsing | Select-Object StatusCode

# Node 工具链
node -v; npm -v
```

### 12.3 后端 AI 闭环自检脚本（本机同样可用）

```powershell
.venv\Scripts\python.exe scripts\verify_chapter_ai_closure.py      # 章节/卡片 AI 闭环，自建临时小说，不调用大模型
.venv\Scripts\python.exe scripts\verify_increment_backend.py       # 增量后端校验
.venv\Scripts\python.exe scripts\migrate_legacy.py --help          # 旧数据迁移（带 --dry-run 空跑）
```

---

## 十三、已知不一致与风险

| 项 | 说明 | 建议 |
|---|---|---|
| `README.md` 的测试章节 | 写了 `python -m pytest tests -q`，但仓库当前**没有 `tests/` 目录和 `test_*.py`** 文件 | 以 `scripts/verify_*.py` 自检脚本为准；如需要，另行补齐测试用例 |
| MongoDB 端口表述 | 文档旧版写 27017；本机 `config.yaml` 实际是 **27018**（便携版实例） | 以 `config.yaml` 为准，迁移配置时勿照抄默认值 |
| Node.js 依赖 | 首版手册未把 Node 列为硬性本地依赖，导致环境缺项 | 已在本版第二章/第四章/第十一章补齐 |
| 前端生产模式 | 生产模式跑构建产物，改代码不 build 不生效 | 开发用 `npm run dev`；生产前必须 `npm run build` |
| 本机 MongoDB 为便携版 | 开机不自启、无服务、无 Docker | 重启机器后先手工启动 mongod，再启后端 |
| 前端端口两套约定 | 项目默认 3300；本机脚本 `ng_frontend.bat` 用 27777 | 统一其一；切换前必须先停掉在跑的 `next dev`（dev 锁互斥） |
| 事务模式 | 单机 MongoDB 下 `auto` 降级为顺序写入 | 不要把“启动成功”当作事务能力证明 |

---

> 本文档基于项目静态代码与本机实测（2026-09-16）整理。如代码结构、端口、依赖有变更，请同步更新本文档。
