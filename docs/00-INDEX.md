# NovelGenerator 项目文档

## 文档用途

本目录面向开发、维护、联调和交接，记录 `NovelGenerator` 当前代码实现。文档中的结论分为：

- **代码已确认**：可由当前源码、配置或依赖文件直接追溯。
- **尚未运行验证**：静态检查得到的结论，尚未在真实环境启动或联调。
- **建议要求**：面向安全、运维或后续开发的约束，不代表当前系统已经具备该能力。

## 推荐阅读顺序

| 顺序 | 文档 | 适合了解的问题 |
| ---: | --- | --- |
| 1 | [01-项目概览](./01-项目概览.md) | 项目做什么，边界在哪里 |
| 2 | [02-架构与技术栈](./02-架构与技术栈.md) | 请求、AI 生成和数据库如何连接 |
| 3 | [03-数据模型](./03-数据模型.md) | MongoDB 集合、ID、软删除和一致性 |
| 4 | [04-业务规则与红线](./04-业务规则与红线.md) | 候选、定稿、上下文和安全边界 |
| 5 | [05-运行与部署](./05-运行与部署.md) | 如何准备环境、运行和排障 |
| 6 | [06-术语表](./06-术语表.md) | 统一领域词汇和易混概念 |

## 既有资料

以下文件继续保留，分别承担背景说明或专项指引：

- [`README.zh-CN.md`](../README.zh-CN.md)：项目中文介绍。
- [`README.md`](../README.md)：项目原始说明。
- [`NovelGenerator_项目环境与开发手册.md`](../NovelGenerator_项目环境与开发手册.md)：AI 开发手册 + 本机（Windows）环境实测核查结果与启动自检清单。
- [`设定卡与章节编辑_AI生成技术指引.md`](../设定卡与章节编辑_AI生成技术指引.md)：AI 生成相关技术指引；其中的蓝图和建议需与当前实现区分。
- [`测试环境注意事项.md`](../测试环境注意事项.md)：测试环境专项信息，不在本目录复制具体实例地址或敏感配置。
- [`frontend/README.md`](../frontend/README.md)：前端专项说明。

## 事实依据

文档主要依据以下代码位置：

- 后端启动和路由：[`main.py`](../main.py)
- 运行参数：[`backend/runtime.py`](../backend/runtime.py)
- 配置管理：[`backend/config/config.py`](../backend/config/config.py)
- MongoDB 连接：[`backend/db/mongo.py`](../backend/db/mongo.py)
- 仓储和索引：[`backend/db/repositories`](../backend/db/repositories)、[`backend/db/indexes.py`](../backend/db/indexes.py)
- 事务与补偿：[`backend/db/transaction.py`](../backend/db/transaction.py)、[`backend/db/created_write_compensation.py`](../backend/db/created_write_compensation.py)
- AI 生成入口：[`backend/api/llm_routers/create_novel_router.py`](../backend/api/llm_routers/create_novel_router.py)
- 小说业务：[`backend/services/novel`](../backend/services/novel)
- 前端 API：[`frontend/src/lib/api.ts`](../frontend/src/lib/api.ts)

## 维护约定

1. 先修改代码，再同步更新受影响文档；不要仅依据旧文档推断现状。
2. 新增集合、字段、状态或 API 时，同时更新数据模型、规则和术语表。
3. 配置示例只能使用占位符；禁止提交或复制真实 API Key、密码、MongoDB URI、用户数据和生产地址。
4. 明确标注静态核对与运行验证，不把设计蓝图、类型快照或空模块描述为已实现功能。
5. 破坏性操作、迁移和备份恢复必须单独说明风险，并在隔离环境演练。
6. 本目录只描述 `NovelGenerator`，不要混入 `Toonflow` 等其他项目的数据库、端口或服务结论。

> 本次文档依据当前工作区静态代码核对生成，未启动服务、连接真实 MongoDB、调用外部模型或执行部署验证。
