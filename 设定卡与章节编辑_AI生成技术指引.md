# 设定卡与章节编辑 AI 生成技术指引

> 适用分支：`dev`  
> 适用项目：AI NovelGenerator Web  
> 目标：让不懂小说创作的用户，通过少量输入和多次确认，完成设定、章节规划、正文生成和长篇连续性维护。

## 1. 产品目标

四个模块不能作为相互独立的资料管理页面，必须形成以下闭环：

```text
小说基础信息
    ↓
全书设定 / 角色 / 势力 / 地点 / 物品 / 规则
    ↓
章节目标与章节关联
    ↓
AI 生成章节方案
    ↓
AI 生成正文
    ↓
一致性检查与修改建议
    ↓
用户确认
    ↓
章节定稿
    ↓
更新角色状态、卡片状态、摘要、伏笔和向量库
    ↓
进入下一章
```

核心原则：

1. AI 负责提出候选内容，用户负责确认重要事实。
2. AI 不能未经确认修改正式设定。
3. 章节生成必须读取当前章节可见的设定，不能读取未来章节才会发生的事实。
4. 卡片、章节和角色必须使用稳定业务 ID 关联，不能用 MongoDB `_id` 作为业务关联值。
5. 所有自动更新都必须可追踪到来源章节和原文依据。

## 2. 当前代码基础

当前 `dev` 分支已经具备：

- `setting_cards` 集合；
- `chapters` 集合；
- `location / item / rule` 三类卡片；
- 章节状态：`draft / editing / finalized`；
- 业务 ID：
  - 地点：`loc_000001`
  - 物品：`itm_000001`
  - 规则：`rul_000001`
  - 章节：`chap_000001`
- 回收站；
- 版本号和乐观锁；
- 章节关联角色、地点、物品、规则；
- 旧版文件迁移脚本。

当前还缺少：

- 三类卡片的 AI 生成、提取和补全；
- 章节 AI 生成按钮和生成预览；
- AI 生成时从 MongoDB 读取卡片上下文；
- 定稿后的状态变更确认；
- 结构化一致性检查；
- 章节版本历史；
- AI 生成内容的候选版本管理。

## 3. 数据模型总原则

### 3.1 MongoDB 文档公共字段

所有小说实体建议包含以下字段：

```json
{
  "_id": "Mongo ObjectId",
  "novel_id": "Mongo ObjectId",
  "业务ID": "按实体类型生成",
  "version": 1,
  "is_deleted": false,
  "created_at": "UTC datetime",
  "updated_at": "UTC datetime",
  "deleted_at": null
}
```

说明：

- `_id` 只用于数据库内部定位。
- 章节之间、卡片之间、角色之间的业务关联使用 `character_id`、`card_id`、`chapter_id`。
- `novel_id` 必须作为所有查询和写入的隔离条件。
- 软删除默认不返回，回收站查询显式传 `include_deleted=true`。
- 更新必须带 `expected_version`，服务端使用 Mongo 条件更新保证原子性。

### 3.2 卡片公共结构

集合名称：`setting_cards`

```json
{
  "_id": "ObjectId",
  "card_id": "loc_000001",
  "novel_id": "ObjectId",
  "type": "location",
  "name": "噬渊入口",
  "aliases": ["裂缝入口", "南境裂口"],
  "fields": {},
  "enabled": true,
  "importance": 4,
  "first_appearance_chapter": "chap_000001",
  "current_state": "封锁",
  "state_history": [],
  "tags": ["主线", "高危"],
  "sort_order": 0,
  "version": 1,
  "is_deleted": false,
  "created_at": "UTC datetime",
  "updated_at": "UTC datetime",
  "deleted_at": null
}
```

`type` 只能是：

```text
location
item
rule
```

同一本小说、同一类型下，`name` 必须唯一；别名不强制唯一，但搜索时需要支持别名匹配。

## 4. 地点卡设计

### 4.1 地点卡字段

```json
{
  "type": "location",
  "fields": {
    "location_type": "空间裂缝",
    "region": "南境",
    "parent_location": "南境救援区",
    "appearance": "黑色裂缝悬在废墟上空，边缘不断渗出灰白雾气",
    "atmosphere": "压抑、危险、灵力紊乱",
    "geographic_features": "附近地面无法稳定使用御剑术",
    "purpose": "第一章救援现场，也是噬渊力量首次显露的位置",
    "access_conditions": "需要通过苍生盟安全检查，进入后必须佩戴隔离装备",
    "danger_factors": "噬渊侵蚀、空间坍缩、失联",
    "related_characters": "林嘉豪、苏小满、程贤",
    "related_factions": "苍生盟、太初研究院",
    "related_items": "噬渊核心",
    "notes": ""
  }
}
```

### 4.2 地点卡 AI 能力

地点卡需要三个入口：

#### AI 生成地点

输入：

```json
{
  "novel_id": "ObjectId",
  "user_prompt": "生成一个适合第一章救援的高危地点",
  "chapter_id": "chap_000001",
  "source_context": {
    "novel_info": true,
    "worldview": true,
    "characters": ["char_000001", "char_000002"],
    "existing_cards": true
  }
}
```

输出候选，不直接写正式卡片：

```json
{
  "type": "location",
  "name": "噬渊入口",
  "aliases": ["南境裂口"],
  "fields": {},
  "current_state": "封锁",
  "importance": 4,
  "reason": "适合作为救援、空间危险和主线秘密的首次交汇地点",
  "conflicts": [],
  "source": "ai_generated"
}
```

#### AI 提取地点

用于从已有小说设定、章节蓝图或正文中识别地点。

必须返回：

- 地点名称；
- 识别依据；
- 是否可能与已有地点重复；
- 建议合并的已有 `card_id`；
- 新发现字段；
- 是否涉及状态变化。

#### AI 补全地点

只补全空字段，默认不能覆盖已有字段。

请求必须支持：

```json
{
  "card_id": "loc_000001",
  "fill_empty_only": true,
  "locked_fields": ["name", "current_state", "access_conditions"],
  "user_prompt": "补充环境细节和剧情用途"
}
```

### 4.3 地点状态变化

地点状态不能只保存当前值，还要记录变化历史：

```json
{
  "from": "开放",
  "to": "封锁",
  "chapter_id": "chap_000004",
  "evidence": "正文第 38 段：苍生盟封锁了南境裂口",
  "note": "用户确认",
  "created_at": "UTC datetime"
}
```

AI 识别到地点状态变化时进入待确认列表，不直接更新卡片。

## 5. 物品卡设计

### 5.1 物品卡字段

```json
{
  "type": "item",
  "fields": {
    "category": "核心神器",
    "appearance": "黑色晶体，内部有灰白色光流",
    "origin": "来源未知",
    "maker": "未知",
    "owner": "林嘉豪",
    "abilities": "打开噬渊通道、反向净化噬渊",
    "limitations": "当前主人无法主动稳定驱动",
    "cost": "待确认",
    "cooldown": "未知",
    "quantity": "1",
    "durability": "完好",
    "secret": "与蛋蛋的真实身份有关，后期揭示",
    "notes": ""
  }
}
```

### 5.2 物品卡 AI 能力

物品卡 AI 必须重点处理：

- 物品从哪里来；
- 谁拥有；
- 谁可以使用；
- 使用需要什么条件；
- 使用后付出什么代价；
- 能力边界是什么；
- 是否已在正文中出现；
- 是否已损坏、遗失、转移或消耗。

推荐入口：

```text
AI 生成物品
AI 补全物品
从本章正文提取物品变化
检查物品能力冲突
```

### 5.3 物品流转记录

建议增加单独的 `item_events` 数组，或者后续拆成 `setting_card_events` 集合：

```json
{
  "event_type": "transfer",
  "item_id": "itm_000001",
  "from_owner": "char_000002",
  "to_owner": "char_000001",
  "chapter_id": "chap_000003",
  "evidence": "苏小满将核心交给林嘉豪保管",
  "confirmed": true,
  "created_at": "UTC datetime"
}
```

事件类型建议：

```text
create
discover
transfer
use
damage
repair
destroy
seal
unseal
lose
recover
```

不能只修改 `fields.owner` 而不保留历史，否则无法解释长篇中的物品去向。

## 6. 设定规则卡设计

### 6.1 规则卡字段

```json
{
  "type": "rule",
  "fields": {
    "rule_category": "世界机制",
    "scope": "全球修仙界",
    "definition": "噬渊每百年入侵一次",
    "trigger": "周期到达或特定空间条件满足时触发",
    "constraints": "入侵期间空间和灵力系统出现异常",
    "exceptions": "特殊封印区域可能延迟入侵",
    "consequences": "被侵蚀者可能转化为噬渊生物",
    "priority": "最高",
    "is_hard_rule": "是",
    "notes": ""
  }
}
```

### 6.2 硬规则和软规则

规则卡必须区分：

```text
硬规则：违反后属于世界逻辑错误，生成时强制检查。
软规则：用于风格、节奏或创作偏好，允许用户在章节级覆盖。
```

建议把 `is_hard_rule` 从文本字段升级为布尔值：

```json
{
  "is_hard_rule": true
}
```

如果为了兼容旧数据暂时保留文本字段，服务层必须统一转换成布尔语义。

### 6.3 规则优先级

规则冲突时按以下顺序处理：

```text
用户明确确认的修订规则
    >
硬规则 priority 高
    >
小说基础世界观
    >
章节蓝图
    >
软规则
    >
AI 临时推断
```

AI 临时推断不能直接写入正式规则卡。

### 6.4 规则卡 AI 能力

推荐入口：

```text
从世界观提取规则
AI 生成规则
补充规则约束
检查本章是否违反规则
检测两条规则是否冲突
```

提取结果必须包含原文依据：

```json
{
  "name": "噬渊入侵规则",
  "source_text": "噬渊每百年入侵一次",
  "source_document": "worldview",
  "confidence": 0.96,
  "suggested_action": "create",
  "conflicts": []
}
```

## 7. 章节数据模型

集合名称：`chapters`

```json
{
  "_id": "ObjectId",
  "chapter_id": "chap_000001",
  "novel_id": "ObjectId",
  "volume_id": null,
  "number": 1,
  "title": "第一章 觉醒",
  "content": "",
  "status": "draft",
  "word_count": 0,
  "blueprint_snapshot": {},
  "linked_character_ids": ["char_000001"],
  "linked_location_ids": ["loc_000001"],
  "linked_item_ids": ["itm_000001"],
  "linked_rule_ids": ["rul_000001"],
  "summary": "",
  "unresolved_threads": [],
  "state_change_proposals": [],
  "generation_status": "idle",
  "version": 1,
  "is_deleted": false,
  "created_at": "UTC datetime",
  "updated_at": "UTC datetime"
}
```

### 7.1 章节关联字段

章节只保存业务 ID：

```text
linked_character_ids -> character_id
linked_location_ids  -> card_id，type=location
linked_item_ids      -> card_id，type=item
linked_rule_ids      -> card_id，type=rule
```

保存关联时服务端必须校验：

1. 所有 ID 属于当前 `novel_id`；
2. 卡片类型与关联字段一致；
3. 被软删除的实体不能作为新的章节关联；
4. 重复 ID 自动去重；
5. 不允许保存空字符串或 `null`。

### 7.2 章节状态机

```text
draft -> editing
draft -> finalized
editing -> draft
editing -> finalized
finalized -> editing
```

要求：

- `finalized` 章节正文只读；
- 从 `finalized` 重开到 `editing` 必须二次确认；
- 定稿前必须确保最后一次正文自动保存完成；
- 定稿不是单纯修改状态，而是触发完整的确认流程；
- 章节状态切换必须使用当前版本号；
- 已删除章节不能编辑。

## 8. 章节编辑器功能

### 8.1 普通编辑能力

必须具备：

- 章节列表；
- 新建章节；
- 打开章节；
- 自动保存；
- 手动保存；
- 上一章 / 下一章；
- 查找和替换；
- 字数统计；
- 章节标题编辑；
- 章节摘要；
- 未解决伏笔；
- 关联角色、地点、物品、规则；
- 回收站；
- 版本冲突提示。

### 8.2 AI 入口

章节编辑器至少需要以下按钮：

```text
AI 生成本章
AI 生成剧情方案
AI 续写
AI 改写选段
AI 扩写
AI 压缩
一致性审校
定稿
```

按钮不应直接覆盖正文。所有 AI 输出先进入预览区。

### 8.3 AI 生成本章流程

#### 第一步：收集输入

系统自动收集：

- 小说标题、类型、基调、叙事视角；
- 核心创意；
- 世界观；
- 当前章节编号和标题；
- 当前章节蓝图；
- 上一章摘要；
- 最近章节摘要；
- 当前角色状态；
- 当前章节关联卡片；
- 当前有效硬规则；
- 未解决伏笔；
- 用户额外要求。

#### 第二步：生成章节方案

先生成结构化方案：

```json
{
  "chapter_number": 3,
  "title": "第三章 回收",
  "chapter_goal": "完成第一次战利品回收并建立小队合作",
  "opening": "从上一章结尾直接承接",
  "main_conflict": "队伍需要在危险区域撤离伤员",
  "key_beats": [
    "发现伤员",
    "林嘉豪选择救援对象",
    "苏小满发现地点异常",
    "队伍完成撤离",
    "留下新的伏笔"
  ],
  "character_arcs": [],
  "location_usage": [],
  "item_usage": [],
  "rule_constraints": [],
  "foreshadowing": [],
  "ending_hook": "留下噬渊核心来源的疑问"
}
```

用户确认方案后才生成正文。

#### 第三步：生成正文

正文生成结果：

```json
{
  "content": "章节正文",
  "title": "第三章 回收",
  "summary": "本章摘要",
  "unresolved_threads": ["噬渊核心来源"],
  "used_entities": {
    "characters": ["char_000001", "char_000002"],
    "locations": ["loc_000001"],
    "items": ["itm_000001"],
    "rules": ["rul_000001"]
  },
  "state_change_proposals": []
}
```

## 9. AI 上下文组装

### 9.1 上下文优先级

章节生成上下文按以下优先级排序：

```text
A. 当前章节用户要求
B. 已确认的章节方案
C. 当前章节硬规则
D. 当前章节关联角色、地点、物品
E. 当前角色状态和已确认历史事实
F. 最近章节摘要
G. 全书世界观
H. 软规则和创作建议
I. 向量检索结果
```

冲突时，高优先级内容覆盖低优先级内容，但必须在审校结果中提示冲突。

### 9.2 卡片筛选

不能把全书所有卡片无条件塞入提示词。优先读取：

1. 当前章节手动关联的卡片；
2. 当前章节方案中提到的卡片；
3. 通过名称、别名、标签检索到的卡片；
4. 当前章节必须遵守的硬规则；
5. 重要性为 4 或 5 的全书核心卡片。

建议上下文格式：

```text
【当前章节地点】
名称：噬渊入口
当前状态：封锁
出入条件：需要安全检查
危险因素：噬渊侵蚀
本章用途：救援现场

【当前章节物品】
名称：噬渊核心
当前持有者：林嘉豪
能力：打开噬渊通道、反向净化噬渊
限制：当前主人无法主动稳定驱动

【必须遵守的硬规则】
规则：虚无之心固定在主体躯干中央
触发条件：虚无生物活动时生效
违反后果：属于世界设定冲突
```

### 9.3 时间可见性

每条设定事实建议增加：

```json
{
  "visible_from_chapter": 1,
  "visible_until_chapter": null,
  "knowledge_scope": "reader",
  "discovery_status": "confirmed"
}
```

至少区分：

```text
reader：读者已知
character：角色已知
author：作者设定但角色未知
secret：尚未揭示
```

生成角色视角内容时，不得把 `author/secret` 事实当作角色已知信息。

## 10. AI 状态变更确认

### 10.1 识别结果

章节生成或审校后，AI 可以提出以下变更：

```json
{
  "entity_type": "item",
  "entity_id": "itm_000001",
  "change_type": "transfer",
  "before": {
    "owner": "苏小满"
  },
  "after": {
    "owner": "林嘉豪"
  },
  "chapter_id": "chap_000003",
  "evidence": "苏小满把噬渊核心交给林嘉豪保管",
  "confidence": 0.91,
  "status": "pending"
}
```

### 10.2 用户操作

每个变更提供：

```text
接受
拒绝
编辑后接受
暂不处理
```

只有“接受”才更新正式卡片或角色状态。

### 10.3 定稿事务

定稿应按照以下顺序执行：

1. 保存正文；
2. 执行一致性检查；
3. 展示阻断级错误；
4. 展示状态变更建议；
5. 用户确认变更；
6. 更新章节状态为 `finalized`；
7. 写入摘要、伏笔和实体事件；
8. 更新角色状态；
9. 更新卡片当前状态；
10. 更新全局摘要；
11. 更新向量库；
12. 记录定稿操作日志。

如果第 5 步之后写入失败，必须支持补偿或重试，不能出现“章节已定稿但实体状态只更新了一半”。

## 11. 推荐 API

### 11.1 现有卡片 API

```text
GET    /api/setting-cards/types
GET    /api/setting-cards/stats/{novel_id}
GET    /api/setting-cards/{novel_id}
POST   /api/setting-cards/{novel_id}
GET    /api/setting-cards/{novel_id}/{card_id}
PUT    /api/setting-cards/{novel_id}/{card_id}?expected_version=N
DELETE /api/setting-cards/{novel_id}/{card_id}
POST   /api/setting-cards/{novel_id}/{card_id}/restore
DELETE /api/setting-cards/{novel_id}/{card_id}/hard
```

### 11.2 现有章节 API

```text
GET    /api/chapters/status-meta
GET    /api/chapters/novel/{novel_id}
POST   /api/chapters/novel/{novel_id}/create
GET    /api/chapters/{chapter_id}
PUT    /api/chapters/{chapter_id}?expected_version=N
POST   /api/chapters/{chapter_id}/finalize
POST   /api/chapters/{chapter_id}/reopen
DELETE /api/chapters/{chapter_id}
POST   /api/chapters/{chapter_id}/restore
GET    /api/chapters/novel/{novel_id}/navigation/{number}
```

### 11.3 建议新增 AI API

#### 卡片生成

```text
POST /api/llm/setting-cards/generate
POST /api/llm/setting-cards/extract
POST /api/llm/setting-cards/{card_id}/complete
POST /api/llm/setting-cards/check-conflicts
```

请求统一包含：

```json
{
  "novel_id": "ObjectId",
  "type": "location",
  "chapter_id": "chap_000001",
  "user_prompt": "",
  "source_text": "",
  "locked_fields": [],
  "provider": "default",
  "generation_params": {}
}
```

#### 章节生成

```text
POST /api/llm/chapters/{chapter_id}/plan
POST /api/llm/chapters/{chapter_id}/draft
POST /api/llm/chapters/{chapter_id}/continue
POST /api/llm/chapters/{chapter_id}/rewrite
POST /api/llm/chapters/{chapter_id}/expand
POST /api/llm/chapters/{chapter_id}/review
POST /api/llm/chapters/{chapter_id}/propose-state-changes
POST /api/llm/chapters/{chapter_id}/finalize
```

所有生成接口返回 `generation_id`，便于：

- 查询生成进度；
- 重试；
- 保存候选版本；
- 防止重复提交；
- 追踪使用的上下文版本。

## 12. 生成记录模型

建议新增 `generation_records` 集合，或复用项目现有生成记录结构：

```json
{
  "generation_id": "gen_000001",
  "novel_id": "ObjectId",
  "chapter_id": "chap_000001",
  "kind": "chapter_draft",
  "status": "completed",
  "input_snapshot": {
    "chapter_version": 3,
    "card_versions": {
      "loc_000001": 2,
      "itm_000001": 1,
      "rul_000001": 1
    },
    "prompt_context_hash": "sha256"
  },
  "result": {
    "content": "候选正文",
    "summary": "候选摘要",
    "state_change_proposals": []
  },
  "accepted": false,
  "provider": "provider_alias",
  "model": "model_name",
  "created_at": "UTC datetime"
}
```

候选正文不能直接覆盖当前章节正文。用户点击“采用”后，再调用章节保存接口。

## 13. 一致性检查结果

不要只返回一段自然语言。统一返回：

```json
{
  "review_id": "review_000001",
  "chapter_id": "chap_000003",
  "status": "completed",
  "issues": [
    {
      "severity": "blocking",
      "category": "rule_conflict",
      "entity_type": "rule",
      "entity_id": "rul_000001",
      "message": "本章描述与噬渊入侵周期规则冲突",
      "evidence": "第 12 段",
      "suggestion": "调整为周期触发前兆，或修改规则卡后重新确认",
      "can_ignore": false
    }
  ]
}
```

严重级别：

```text
blocking：不能直接定稿
warning：建议修改，但允许定稿
notice：信息提示
```

检查类别：

```text
character_state
location_state
item_owner
item_ability
rule_conflict
timeline
foreshadowing
duplicate_plot
style
```

## 14. 前端交互要求

### 14.1 三类卡片

页面保留现有列表和编辑器，但增加：

1. `AI 生成`；
2. `AI 补全`；
3. `从小说内容提取`；
4. 生成结果预览；
5. 逐字段采纳；
6. 锁定字段；
7. 与已有卡片合并；
8. 冲突提示；
9. 状态历史；
10. 回收站。

新用户默认看到简化表单：

```text
名称
一句话描述
AI 生成
保存
```

高级字段折叠展开，避免新手面对十几个空文本框。

### 14.2 章节编辑

章节页应突出：

```text
AI 生成本章
AI 生成剧情方案
继续写
审校
定稿
```

正文编辑区域不能被右侧关联面板遮挡。移动端采用上下布局：

```text
章节列表
正文编辑
AI 工具栏
关联设定
摘要与伏笔
```

### 14.3 保存可靠性

必须修复并测试：

- 切换章节前等待当前章节待保存内容完成；
- 定稿前等待正文保存完成；
- 切换卡片前等待当前卡片保存完成；
- 自动保存失败时显示“待保存”；
- 发生版本冲突时保留本地文本；
- 刷新页面不能静默丢失编辑内容。

## 15. 开发任务拆分

### P0：可靠性

- 修复快速切章导致请求串到目标章节的问题；
- 修复自动保存未完成时直接定稿的问题；
- 增加章节和卡片的保存队列；
- 增加前端单元测试和浏览器测试；
- 验证桌面端和移动端布局。

### P1：卡片 AI

- 新增统一卡片生成服务；
- 新增提取、补全、冲突检查；
- 统一结构化输出 Schema；
- 增加候选预览和逐字段采纳；
- 支持空字段补全和字段锁定；
- 记录 AI 生成来源和版本。

### P1：章节 AI

- 新增章节剧情方案生成；
- 新增章节正文生成；
- 新增续写、改写、扩写；
- 新增生成候选版本；
- 生成时读取章节关联卡片；
- 生成时注入硬规则；
- 禁止直接覆盖用户正文。

### P2：长篇连续性

- 章节定稿后提取实体状态变化；
- 增加待确认变更列表；
- 更新角色状态；
- 更新地点和物品事件；
- 更新摘要与未解决伏笔；
- 增加章节影响范围分析；
- 修改早期章节时提示后续章节受影响。

### P3：批量写作

- 批量生成前先生成章节方案；
- 支持逐章审校；
- 支持失败重试；
- 支持暂停和继续；
- 支持批量生成后统一检查；
- 不允许批量流程绕过硬规则和用户确认。

## 16. 验收标准

### 新手创作验收

用户只输入：

```text
题材
一句话创意
篇幅
```

即可完成：

1. AI 生成基础小说设定；
2. AI 生成必要的角色、地点、物品和规则；
3. 用户确认设定；
4. AI 生成第一章方案；
5. AI 生成第一章正文；
6. 一致性检查；
7. 用户修改或接受；
8. 定稿；
9. AI 继续生成第二章；
10. 第二章能够延续第一章已确认事实。

### 数据验收

- 刷新浏览器后数据仍存在；
- 换设备访问数据仍存在；
- 不同小说之间数据不串；
- 业务 ID 不为空、不重复；
- 删除后可恢复；
- 并发编辑不会静默覆盖；
- 版本冲突能保留用户本地修改；
- 定稿不会未经确认修改卡片；
- AI 候选不会直接覆盖正文；
- 所有状态变化能定位到章节和原文依据。

### AI 质量验收

- 不生成违反硬规则的方案；
- 不让角色使用尚未获得的物品；
- 不让物品凭空出现；
- 不让地点状态无故变化；
- 不让角色知道尚未揭示的秘密；
- 不重复最近章节核心事件；
- 每章都有明确目标、冲突和结尾推进；
- 生成失败时可以重试，不产生重复章节。

## 17. 给开发人员的实现顺序

```text
1. 保证章节/卡片保存不丢失、不串写
2. 完成 AI 上下文组装器
3. 接入章节剧情方案生成
4. 接入章节正文生成和候选预览
5. 接入地点/物品/规则卡 AI 生成
6. 接入一致性检查
7. 接入定稿后的状态变更确认
8. 增加事件历史和版本历史
9. 增加移动端优化
10. 最后实现批量章节生成
```

最重要的技术边界：

```text
AI 可以建议事实
用户确认事实
服务端保存事实
章节生成读取事实
定稿后产生新的事实候选
```

任何绕过“候选 → 确认 → 正式保存”的实现，都不应进入正式版本。

## 18. 技术实现蓝图

本节用于把产品要求转换成后端、前端和 AI 服务可以直接拆分的开发任务。

### 18.1 ID 使用规范

系统中同时存在 MongoDB `_id` 和业务 ID，不能混用：

| 对象 | MongoDB 内部字段 | 对外业务字段 | 示例 |
|---|---|---|---|
| 小说 | `_id` | `novel_id` 参数 | `ObjectId` |
| 角色 | `_id` | `character_id` | `char_000001` |
| 势力 | `_id` | `faction_id` | `fac_000001` |
| 地点卡 | `_id` | `card_id` | `loc_000001` |
| 物品卡 | `_id` | `card_id` | `itm_000001` |
| 规则卡 | `_id` | `card_id` | `rul_000001` |
| 章节 | `_id` | `chapter_id` | `chap_000001` |

约束：

1. 前端章节关联字段只能保存业务 ID。
2. 后端保存章节时必须校验业务 ID 属于当前小说。
3. 地点、物品、规则三个字段必须校验卡片 `type`。
4. 软删除实体不能被新章节关联。
5. 查询详情时可以使用 MongoDB `_id`，但响应必须同时返回业务 ID。

### 18.2 推荐的集合关系

```text
novels
  ├── characters
  ├── factions
  ├── setting_cards
  │     ├── type=location
  │     ├── type=item
  │     └── type=rule
  ├── volumes
  ├── chapters
  ├── generation_records
  ├── consistency_reviews
  └── story_events
```

关联方向：

```text
chapter.linked_character_ids ──> characters.character_id
chapter.linked_location_ids  ──> setting_cards.card_id(type=location)
chapter.linked_item_ids      ──> setting_cards.card_id(type=item)
chapter.linked_rule_ids      ──> setting_cards.card_id(type=rule)
story_event.chapter_id       ──> chapters.chapter_id
story_event.entity_id        ──> 角色/卡片业务 ID
generation_record.chapter_id ──> chapters.chapter_id
```

不建议把角色、地点、物品和规则直接复制进章节文档。章节只保存业务 ID 和生成时快照；这样既能保持关联，又能在后续修改设定时追踪影响范围。

### 18.3 章节关联快照

章节生成时必须保存当时使用过的事实版本，避免以后卡片被修改后无法解释旧章节是依据什么生成的：

```json
{
  "context_snapshot": {
    "novel_version": 3,
    "characters": {
      "char_000001": 4
    },
    "cards": {
      "loc_000001": 2,
      "itm_000001": 3,
      "rul_000001": 1
    },
    "previous_chapter_versions": {
      "chap_000001": 8
    },
    "context_hash": "sha256..."
  }
}
```

该快照可以放在 `chapters.generation_context_snapshot`，也可以放在 `generation_records.input_snapshot`。正式版本至少要保留一份。

## 19. AI 生成接口的统一约定

### 19.1 统一请求结构

卡片生成、章节方案、章节正文和一致性审校都应使用统一的请求元数据：

```json
{
  "novel_id": "小说 ObjectId",
  "chapter_id": "可选的章节业务 ID",
  "request_id": "客户端生成的幂等 ID",
  "user_prompt": "用户额外要求",
  "provider": "provider_alias",
  "generation_params": {
    "temperature": 0.7,
    "max_tokens": 4096,
    "use_stream": true
  }
}
```

后端要求：

1. `request_id` 在相同小说和相同生成类型下幂等。
2. 生成接口默认只产生候选，不修改正式章节或卡片。
3. 生成过程必须记录输入上下文版本。
4. 超时、格式错误和模型失败必须返回可重试的状态。
5. 流式接口只负责进度和文本传输，最终结果仍要经过结构校验。

### 19.2 结构化输出要求

AI 输出不得只返回一段正文。每种生成类型都要有固定 Schema：

```json
{
  "kind": "location_card",
  "candidate_id": "cand_000001",
  "data": {},
  "source": {
    "type": "ai_generated",
    "chapter_id": "chap_000001",
    "evidence": "生成依据摘要"
  },
  "warnings": [],
  "conflicts": []
}
```

前端只能展示 `data`，用户点击采纳后才调用正式保存接口。

### 19.3 生成类型

建议统一使用以下 `kind`：

```text
location_card
item_card
rule_card
extract_cards
complete_card
chapter_plan
chapter_draft
chapter_continue
chapter_rewrite
chapter_expand
chapter_compress
consistency_review
state_change_proposal
```

## 20. 卡片 AI 功能说明

### 20.1 地点卡

输入重点：

```text
用户一句话需求
本章目标
世界观
章节关联角色
已存在地点
```

生成重点：

```text
地点身份
空间特征
氛围
进入和离开条件
危险因素
本章剧情用途
与角色、势力、物品的关系
```

限制：

1. 不得生成与同名地点重复的正式卡片。
2. 已有地点只允许补全空字段，除非用户明确允许改写。
3. 地点状态变化必须生成 `state_change_proposal`。
4. 地点层级不能形成循环引用。

### 20.2 物品卡

输入重点：

```text
物品用途
首次出现章节
可能持有者
已有能力限制
本章剧情需求
```

生成重点：

```text
来源
外观
能力
使用条件
代价
冷却
数量
当前持有者
剧情秘密
```

限制：

1. AI 不得擅自提高已有物品能力。
2. 物品持有者必须是现有角色、势力或明确的未知状态。
3. 物品数量、消耗和转移必须生成事件，不得只改一个文本字段。
4. AI 不能让物品凭空出现在正文中，除非生成“发现/获得”事件。

### 20.3 设定规则卡

输入重点：

```text
世界观原文
已有规则
本章计划
用户希望强化的约束
```

生成重点：

```text
规则名称
适用范围
触发条件
约束和代价
例外条件
违反后果
优先级
是否硬规则
原文依据
```

限制：

1. `is_hard_rule` 应由后端按布尔值保存，不建议继续用“是/否”文本。
2. 规则提取和规则新建必须在 UI 上区分。
3. 规则冲突必须在用户确认前展示。
4. 章节级临时例外不能自动修改全书规则。

## 21. 章节 AI 功能说明

### 21.1 章节页的主操作

章节编辑器顶部建议固定显示：

```text
AI 生成剧情方案
AI 生成本章
继续写
改写选中段落
扩写
压缩
一致性审校
定稿
```

### 21.2 “AI 生成本章”必须分两步

第一步生成章节方案，第二步生成正文。不能一次请求直接覆盖正文。

章节方案最少包含：

```json
{
  "goal": "本章要完成什么",
  "conflict": "本章主要冲突",
  "beats": ["事件一", "事件二", "事件三"],
  "character_changes": [],
  "location_usage": [],
  "item_usage": [],
  "rule_constraints": [],
  "foreshadowing": [],
  "ending_hook": "结尾推进"
}
```

用户确认方案后，生成正文候选。正文候选必须拥有独立 `candidate_id`，不能直接覆盖 `chapters.content`。

### 21.3 “继续写”

继续写必须使用：

```text
当前章节最后 1000~2000 字
当前章节方案
当前章节关联卡片
当前未解决伏笔
下一节目标
用户补充要求
```

不能只把全文再次发送给模型，否则长章节成本高，也容易破坏前文。

### 21.4 “改写选段”

请求必须带选区，而不是让模型猜测修改范围：

```json
{
  "chapter_id": "chap_000001",
  "base_version": 12,
  "selection": {
    "start": 320,
    "end": 780,
    "text": "选中的原文"
  },
  "instruction": "增强紧张感，保持事实不变",
  "locked_facts": ["林嘉豪不会常规战斗术法"]
}
```

返回候选片段和影响提示，用户采纳后再合并到正文。

## 22. 状态事件模型

地点、物品和角色变化不应直接覆盖当前字段，建议统一写入 `story_events`：

```json
{
  "event_id": "evt_000001",
  "novel_id": "ObjectId",
  "chapter_id": "chap_000003",
  "entity_type": "item",
  "entity_id": "itm_000001",
  "event_type": "transfer",
  "before": {
    "owner": "苏小满"
  },
  "after": {
    "owner": "林嘉豪"
  },
  "evidence": {
    "text": "苏小满把噬渊核心交给林嘉豪保管",
    "start": 120,
    "end": 142
  },
  "confidence": 0.91,
  "status": "pending",
  "confirmed_by": null,
  "confirmed_at": null,
  "created_at": "UTC datetime"
}
```

事件状态：

```text
pending
accepted
rejected
edited
```

只有 `accepted` 事件可以更新卡片的 `current_state` 或角色状态。更新时要保留事件 ID，便于撤销和审计。

## 23. 定稿服务的事务边界

推荐新增 `FinalizeChapterService`，不要把所有逻辑塞进章节 Repository。

定稿步骤：

```text
1. 校验章节版本
2. 等待或确认正文已保存
3. 执行一致性审校
4. 阻断级问题不允许直接定稿
5. 生成状态变更候选
6. 用户确认状态变更
7. 开启数据库事务
8. 更新章节为 finalized
9. 写入 story_events
10. 更新角色/卡片当前状态
11. 更新章节摘要和未解决伏笔
12. 更新小说统计和向量索引
13. 提交事务
```

如果 Mongo 事务不可用，应采用“定稿任务记录 + 可重试补偿”的方式，不能只更新章节状态后把其他步骤放在后台无记录执行。

## 24. 前端状态和保存队列

章节和卡片编辑器都需要独立保存队列，不能只使用一个共享的 `setTimeout`。

推荐状态：

```text
clean
dirty
saving
saved
conflict
error
```

切换章节、切换卡片、点击定稿前必须执行：

```text
flushPendingSave()
```

保存队列必须携带：

```json
{
  "entity_id": "chap_000001",
  "base_version": 12,
  "patch": {
    "content": "..."
  }
}
```

服务端返回新版本后，前端才更新本地 `base_version`。如果返回 409，保留本地编辑内容，提供：

```text
加载服务器版本
保留本地版本并重新提交
查看差异
```

## 25. 本阶段开发任务单

### P0：先修可靠性

1. 章节切换前刷新当前章节保存队列。
2. 定稿前刷新正文保存队列。
3. 卡片切换和页面离开前刷新卡片保存队列。
4. 增加保存中、未保存、冲突、失败状态。
5. 修复移动端正文区域被关联面板遮挡。

### P1：做章节 AI 闭环

1. 上下文组装服务。
2. 章节方案生成。
3. 正文候选生成。
4. 候选预览和采纳。
5. 续写、改写、扩写。
6. 一致性审校。
7. 定稿前状态变更确认。

### P1：做卡片 AI

1. AI 生成卡片。
2. 从小说信息提取卡片。
3. AI 补全空字段。
4. 卡片冲突检测。
5. 逐字段采纳和字段锁定。

### P2：做长篇记忆

1. `story_events`。
2. 章节事实时间线。
3. 角色知情范围。
4. 章节影响分析。
5. 版本历史和回滚。

## 26. 技术验收用例

至少要自动化验证以下场景：

| 用例 | 期望结果 |
|---|---|
| 编辑第一章后立即切到第二章 | 第一章修改保存到第一章，不能串到第二章 |
| 编辑正文后立即点击定稿 | 先保存最新正文，再进入审校和定稿 |
| 两个窗口同时编辑同一张卡 | 后提交者收到 409，不静默覆盖 |
| AI 生成卡片 | 只出现候选，不自动写入正式卡片 |
| AI 采纳卡片 | 正式卡片版本增加，来源可追踪 |
| 章节引用被删除卡片 | 后端拒绝新关联 |
| 角色未知秘密 | 角色视角不能使用 `secret` 事实 |
| 章节违反硬规则 | 审校返回 `blocking` 问题 |
| 定稿后物品转移 | 先生成事件，确认后更新持有者 |
| 重复点击生成 | 同一 `request_id` 不产生重复候选或重复章节 |

最终判断标准：

```text
新手只输入创意
系统生成设定候选
用户确认关键事实
系统生成章节方案
用户确认方向
系统生成正文候选
系统自动审校
用户确认状态变化
章节定稿
下一章自动继承已确认事实
```

只要其中任何一步绕过候选、确认、正式保存这三个阶段，就不能认为长篇写作闭环完成。
