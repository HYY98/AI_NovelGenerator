# NovelGenerator v2.0 后端接口契约（第一批）

> 负责人：后端开发1号（基础契约 + 核心采纳服务）
> 面向：后端2号、后端3号、前端。本文档是第一批的**冻结契约**，落地前先对齐本文。

---

## 1. 核心概念与 ID 边界

### 1.1 MongoDB `_id` 与业务 ID

| 概念 | 说明 | 例子 |
| --- | --- | --- |
| Mongo `_id` | 数据库主键，ObjectId。**不暴露给前端**，前端一律用业务 ID。 | `ObjectId("...")` |
| `novel_id` | 小说业务 ID，前端可见，等价于 Mongo `_id` 的字符串（本项目小说直接以 ObjectId 字符串作业务 ID）。 | `"667f..."` |
| `generation_id` | 生成记录业务 ID，由 `id_sequence` 分配，同一小说内唯一。 | `"gen_000123"` |
| `candidate_id` | 候选业务 ID，形如 `{generation_id}#c{version}`，服务端补齐，稳定不变。 | `"gen_000123#c2"` |
| `card_id` / `chapter_id` / `character_id` | 正式实体业务 ID，带类型前缀。 | `"loc_000045"` |
| `revision_id` | 内容版本业务 ID。 | `"rev_000007"` |
| `operation_id` | 幂等操作业务 ID。 | `"op_000009"` |

原则：**所有写操作只认业务 ID；`_id` 仅存于 DB，接口永不返回、永不接收。**

### 1.2 版本 DTO（VersionedEntity）

所有可变正式实体（卡片/章节/角色/设定）共享以下版本字段：

```json
{
  "version": 3,          // 单调递增，乐观锁基准；并发写入后到者 409
  "updated_at": "ISO8601",
  "updated_by": "user"    // user / ai / system
}
```

并发语义：更新必须携带 `expected_version`，服务端以原子条件更新比对；不匹配返回 `VERSION_STALE`(409)。客户端拿到 409 应刷新后重试。

---

## 2. 统一错误契约

- 响应体 `detail` 为字符串（不破坏既有前端解析）。
- 稳定错误码放响应头 `X-NG-Error-Code`。

| 错误码 | HTTP | 含义 |
| --- | --- | --- |
| `REQUEST_ID_REQUIRED` | 400 | 写操作缺少幂等键 |
| `OPERATION_IN_PROGRESS` | 409 | 同一 request_id 正在处理 |
| `REQUEST_ID_REUSED` | 409 | 同一 request_id 换了请求体 |
| `CANDIDATE_NOT_FOUND` | 404 | 候选不存在 |
| `CANDIDATE_ALREADY_ACCEPTED` | 409 | 候选已被采纳（并发重复） |
| `CANDIDATE_ALREADY_RESOLVED` | 409 | 候选已拒绝/忽略，不可再采纳 |
| `CANDIDATE_SCOPE_MISMATCH` | 400 | 候选与路径/目标不匹配 |
| `CANDIDATE_KIND_UNSUPPORTED` | 422 | 该记录类型暂不支持逐条采纳 |
| `VERSION_STALE` | 409 | 服务端版本已推进 |
| `FIELD_NOT_ALLOWED` | 422 | 字段白名单外或被保护的字段 |
| `REVIEW_REQUIRED` / `REVIEW_INVALID` / `FINALIZE_BLOCKED` / `FINALIZE_FORCE_REMOVED` | 422 | 定稿门禁相关 |
| `TEXT_ANCHOR_INVALID` / `TEXT_ANCHOR_NOT_UNIQUE` | 422/409 | 正文范围定位失败 |

---

## 3. 幂等与候选状态模型（T02 核心）

### 3.1 两层幂等

1. **操作级幂等（request_id）**：同一 `request_id` + 相同请求体只生效一次，重复提交回放首次结果（`replayed: true`）；`request_id` 复用但请求体不同 → `REQUEST_ID_REUSED`。
2. **候选级幂等（candidate_id + action）**：候选状态机保证同一候选只能被**接受一次**（`accepted` 后不可再次接受）。幂等键 = `generation_id + candidate_id + action`。

### 3.2 候选状态机

```
pending ──accept──▶ accepted   (终态，不可回退)
   │
   ├────reject──▶ rejected     (终态)
   │
   └────ignore──▶ ignored      (终态)
```

- `pending` 可 accept / reject / ignore；
- 已 `accepted` 的候选再次 accept → `CANDIDATE_ALREADY_ACCEPTED`(409)；
- 已 `rejected` / `ignored` 的候选再次 accept → `CANDIDATE_ALREADY_RESOLVED`(409)；
- 并发 accept 同一候选：先到者原子认领成功，后到者 409。

### 3.3 原子认领（先认领再写）

顺序固定：**认领候选（置 processing）→ 校验 → 写正式实体 → 落事件 → 置 accepted**。正式写入失败时释放认领（回 pending），允许重试。禁止"先写实体再标记"导致的不一致。

---

## 4. 候选采纳接口

### 4.1 统一 DTO

```jsonc
// 请求 CandidateAcceptRequest
{
  "novel_id": "667f...",
  "generation_id": "gen_000123",
  "candidate_id": "gen_000123#c2",   // 多候选必填；单候选可为空
  "decision": "accept | reject | ignore",
  "action": "create | merge | rewrite",  // accept 时必填
  "request_id": "uuid",                  // 必填
  "reason": "",                          // reject/ignore 时建议填写
  "target_card_id": "",                  // merge/rewrite 时必填
  "accepted_fields": {},                 // 采纳字段差异
  "after_text": "",                      // 正文修正用
  "expected_version": 3,
  "expected_chapter_version": 5,
  "expected_content_hash": "",           // 正文修正用
  "operation": "replace | insert | delete", // 正文修正用
  "occurrence": 0                        // 锚点重复时定位第几处
}
```

```jsonc
// 响应 CandidateAcceptResponse
{
  "generation_id": "gen_000123",
  "candidate_id": "gen_000123#c2",
  "status": "accepted",            // 候选终态
  "operation_id": "op_000009",
  "replayed": false,               // true=命中幂等重放
  "action": "create",
  "card": {},                       // 设定卡采纳返回正式卡片
  "chapter": {},                    // 正文修正返回更新后章节
  "events_created": 1,
  "warnings": []
}
```

### 4.2 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/llm/setting-cards/accept` | 设定卡候选采纳（兼容旧入口，等价转发统一服务） |
| POST | `/api/candidates/decision` | 统一候选决定入口（卡片/正文/角色按记录类型分发） |
| GET | `/api/novels/{novel_id}/candidates` | 小说候选列表（逐候选状态 + 分页） |
| POST | `/api/chapters/{chapter_id}/text-revisions/{revision_id}/accept` | 正文修改应用 |

### 4.3 字段白名单（硬编码保护）

下列**结构性字段绝不允许**从 `accepted_fields` / `candidate.fields` 覆盖，只能由后端从候选固定结构提取或后端初始化：

```
type, name, aliases, importance, current_state, is_hard_rule,
card_id, enabled, first_appearance_chapter, version, state_history, tags, sort_order
```

- `name`：只取 `candidate.name`；`accepted_fields.name` 一律忽略并告警。
- `importance`：只取 `candidate.importance`（1..5），白名单外。
- `is_hard_rule`：默认 `false`；只有 `confirm_hard_rule=true` 时才写 `true`。
- `current_state`：**动态状态，不直接落卡片**（见 4.4）。

### 4.4 稳定资料与动态状态分离

- **稳定资料**（name / aliases / fields / importance）→ 直接写入卡片本体。
- **动态状态**（current_state 等会随剧情变化的事实）→ 不写卡片，转成 `story_events`（`event_type=state_change`，`status=pending`），需用户二次确认后才生效。
- 卡片本体的 `current_state` 由后端初始化（空），后续由 story_events 确认流程更新。

---

## 5. 版本快照接口（T10 版本基础）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/chapters/{chapter_id}/versions?limit=&skip=` | 章节版本列表 |
| POST | `/api/chapters/{chapter_id}/versions/{revision_id}/restore` | 还原（写新快照，不删历史） |
| GET | `/api/novels/{novel_id}/entities/{entity_type}/{entity_id}/versions` | 通用实体版本列表 |
| POST | `/api/novels/{novel_id}/entities/{entity_type}/{entity_id}/versions/{revision_id}/restore` | 通用实体还原 |

还原请求体：`{"request_id": "uuid", "reason": "..."}`。

---

## 6. 任务接口（T04）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/llm/generation-jobs/{job_id}` | 任务快照（含 `resumable/checkpoint/request_hash`） |
| POST | `/api/llm/generation-jobs/{job_id}/resume` | 续跑（仅 interrupted/failed，校验 novel_id/request_hash） |
| GET | `/api/llm/generation-jobs/history?novel_id=&workflow=&status=&limit=&skip=` | 历史列表 |
| GET | `/api/llm/generation-jobs/active?novel_id=&workflow=` | 重连接口 |

任务状态新增 `interrupted`（中断可续跑，保留检查点）。

---

## 7. 定稿接口（T03）

`POST /api/chapters/{chapter_id}/finalize`

```jsonc
{
  "review_generation_id": "gen_000012",  // 必填：服务端保存的审校记录
  "exceptions": [                         // 阻断项逐项裁决，force 已下线
    {"issue_id": "iss_ab12cd34", "reason": "设定允许", "scope": "this_chapter"}
  ],
  "request_id": "uuid",
  "accepted_event_ids": [],
  "rejected_event_ids": []
}
```

- 无 `review_generation_id` → `REVIEW_REQUIRED`；
- 阻断项未逐项裁决 → `FINALIZE_BLOCKED`（返回 `blocking_issues`）；
- `PUT /api/chapters/{id}` 带 `status=finalized` 不再直接生效。

---

## 8. generation_records 历史形态与兼容读取

历史上存在两条写入路径，第一批兼容读取同时覆盖：

| 形态 | 候选数组位置 | 单候选 |
| --- | --- | --- |
| 模块3 统一链路 `create_generation_record` | `payload.result.data.candidates[]` | 无 candidates 数组，整记录即候选（`accept_status`） |
| 旧链路 `create_record` | `result.data.candidates[]` | 同上 |

兼容读取逻辑（`candidate_accept_service`）：

1. `_candidate_container`：依次探测 `payload.result.data.candidates` → `result.data.candidates`；
2. 数组候选缺失 `candidate_id` 时按 `{generation_id}#c{version}` 补齐并回写；
3. 无候选数组时按单候选处理，状态取 `accept_status`（默认 `pending`）。

---

## 9. 路由 / 集合差异清单（T00 核对结论）

新增集合（需在 indexes.py 建索引，统一合并阶段落地）：

| 集合 | 用途 | 关键索引 |
| --- | --- | --- |
| `operation_records` | 统一提交协议幂等 | `(novel_id, operation_type, request_id)` 唯一 |
| `entity_versions` | 内容版本快照 | `(novel_id, revision_id)` 唯一、`(novel_id, entity_type, entity_id, created_at)` |

新增路由（统一合并阶段注册到 main.py）：

- `POST /api/candidates/decision`、`GET /api/novels/{novel_id}/candidates`
- `GET/POST /api/chapters/{id}/versions[/restore]`
- `GET/POST /api/novels/{novel_id}/entities/{type}/{id}/versions[/restore]`
- `POST /api/llm/generation-jobs/{id}/resume`、`GET /api/llm/generation-jobs/history`

> 说明：路由注册与 indexes.py 属公共文件，第一批先以 `# TODO(v2 backend1)` 标注，待三位后端统一合并时再集中落地，避免并行改动冲突。
