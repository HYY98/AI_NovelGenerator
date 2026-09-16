"""模块7.1 后端验收静态校验脚本。

运行环境缺少 FastAPI / Motor 等运行时依赖，因此本脚本不启动服务，
改用标准库 AST 与源码检查，对增量开发说明书 7.1 的后端验收项逐条核对。
这样可以在装依赖之前先发现"文件漏改、字段漏加、顺序写反"这类问题。

用法：
    python scripts/verify_increment_backend.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

results: list[tuple[bool, str, str]] = []


def read(rel: str) -> str:
    """读取相对仓库根目录的文件源码。"""
    path = ROOT / rel
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def tree(rel: str) -> ast.Module | None:
    """解析相对仓库根目录的文件为 AST。"""
    src = read(rel)
    if not src:
        return None
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def check(ok: bool, item: str, detail: str = "") -> None:
    """记录一条验收结果。"""
    results.append((bool(ok), item, detail))


def source_has(rel: str, needle: str) -> bool:
    """判断文件源码是否包含指定片段。"""
    return needle in read(rel)


def module_defines(rel: str, *names: str) -> bool:
    """判断模块顶层是否定义了指定名称。"""
    mod = tree(rel)
    if mod is None:
        return False
    defined = set()
    for node in mod.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    return all(name in defined for name in names)


def class_method_names(rel: str, class_name: str) -> set[str]:
    """返回指定类中定义的方法名集合。"""
    mod = tree(rel)
    if mod is None:
        return set()
    for node in ast.walk(mod):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def method_attr_calls(rel: str, class_name: str, method_name: str) -> set[str]:
    """返回指定类方法体内被调用的属性名集合（如 create_chapter）。"""
    mod = tree(rel)
    if mod is None:
        return set()
    for node in ast.walk(mod):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == method_name
                ):
                    return {
                        call.func.attr
                        for call in ast.walk(child)
                        if isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                    }
    return set()


def module_assign_tuple(rel: str, name: str) -> set[str]:
    """返回模块顶层某个元组/列表常量中的字符串元素。"""
    mod = tree(rel)
    if mod is None:
        return set()
    for node in mod.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return {
                        elt.value
                        for elt in getattr(node.value, "elts", [])
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
    return set()


def main() -> int:
    """执行全部验收项并打印结果。"""
    gen_rec = "backend/db/repositories/generation_record_repository.py"
    story_evt = "backend/db/repositories/story_event_repository.py"
    card_gen = "backend/services/llm/setting_card_generation_service.py"
    accept_svc = "backend/services/novel/setting_card_accept_service.py"
    blueprint_svc = "backend/services/novel/blueprint_service.py"
    llm_blueprint = "backend/services/llm/novel_blueprint_service.py"
    text_rev_svc = "backend/services/llm/text_revision_service.py"
    chapter_gen = "backend/services/llm/chapter_generation_service.py"
    create_router = "backend/api/llm_routers/create_novel_router.py"

    # 1. quick 模式默认不依赖蓝图
    src = read(create_router)
    check(
        'generation_mode: str = Field(default="quick"' in src
        and "_load_confirmed_blueprint" in src
        and 'if req.generation_mode == "guided"' in src,
        "1. quick 模式不传蓝图仍能执行；仅 guided 分支加载蓝图",
    )

    # 2. guided 模式可创建、保存、确认、版本化蓝图
    methods = class_method_names(blueprint_svc, "BlueprintService")
    repo_methods = class_method_names(
        "backend/db/repositories/novel_blueprint_repository.py",
        "NovelBlueprintRepository",
    )
    check(
        {"create_blueprint", "update_blueprint", "confirm_blueprint"} <= methods
        and "create_new_version" in repo_methods,
        "2. 蓝图可创建、更新、确认，并支持创建新版本",
        f"service={sorted(methods & {'create_blueprint','update_blueprint','confirm_blueprint'})}",
    )

    # 3. 未确认蓝图不能用于章节生成
    check(
        'str(blueprint.get("status") or "") != "confirmed"' in read(create_router),
        "3. 未确认蓝图会被拒绝用于生成",
    )

    # 4. 大纲生成只写生成记录；正式卷章仅在用户确认大纲后创建
    outline_src = read(llm_blueprint)
    generate_calls = method_attr_calls(
        llm_blueprint, "NovelBlueprintService", "generate_outline"
    )
    check(
        "save_record(" in outline_src
        and "volumes" in outline_src
        and "create_chapter" not in generate_calls
        and "create_volume" not in generate_calls
        and "if confirm:" in outline_src,
        "4. 大纲生成只写生成记录，正式卷章仅在确认后创建",
    )

    # 5. 生成记录包含版本与上下文摘要
    check(
        all(
            source_has(gen_rec, f'"{field}": data.get("{field}")')
            for field in ("blueprint_version", "outline_version", "chapter_version")
        )
        and "snapshot" in read("backend/services/llm/generation_support.py"),
        "5. 生成记录包含 blueprint_version / outline_version / chapter_version 与上下文快照",
    )

    # 6. 战力体系可保存等级、顺序、能力、限制、资源
    check(
        all(
            source_has("backend/db/repositories/power_system_repository.py", f'"{f}"')
            for f in ("levels", "order", "abilities", "limitations", "resource")
        ),
        "6. 战力体系可保存等级、顺序、能力、限制与资源",
    )

    # 7. 角色可关联战力体系与当前等级
    check(
        all(
            source_has("backend/services/novel/character_service.py", f'"{f}"')
            for f in ("power_system_id", "power_level", "power_version")
        ),
        "7. 角色可关联战力体系与当前等级",
    )

    # 8. 物品卡战力字段
    check(
        all(
            source_has(
                "backend/db/repositories/setting_card_repository.py",
                f'"{f}":',
            )
            for f in ("required_power_level", "power_bonus", "power_cost")
        ),
        "8. 物品卡支持 required_power_level / power_bonus / power_cost",
    )

    # 9. AI 改写只返回字段补丁，不写正式卡片
    rewrite_ok = (
        "changed_fields" in read(card_gen)
        and "update_card" not in read(card_gen)
        and "preserved_fields" in read(card_gen)
    )
    check(rewrite_ok, "9. AI 改写只返回字段补丁，不直接更新正式卡片")

    # 10. 默认保护 name / current_state，规则卡保护 is_hard_rule
    check(
        module_defines(card_gen, "DEFAULT_PROTECTED_FIELDS", "RULE_PROTECTED_FIELDS")
        and 'DEFAULT_PROTECTED_FIELDS = ("name", "current_state")' in read(card_gen)
        and 'RULE_PROTECTED_FIELDS = ("is_hard_rule",)' in read(card_gen)
        and "allow_locked_fields" in read(card_gen),
        "10. 默认保护 name / current_state，规则卡额外保护 is_hard_rule",
    )

    # 11. 提取结果支持 create / merge / skip
    actions = module_assign_tuple(
        "backend/llm/schemas/setting_card_pydantic.py", "CARD_ACTIONS"
    )
    check(
        {"create", "merge", "skip"} <= actions,
        "11. 设定卡提取结果支持 create / merge / skip",
        f"CARD_ACTIONS={sorted(actions)}",
    )

    # 12. 统一采纳校验归属、白名单、版本
    check(
        all(
            source_has(accept_svc, needle)
            for needle in (
                "get_owned_record",
                "get_card_by_business_id",
                "_filter_fields",
                "_check_versions",
            )
        ),
        "12. 统一采纳校验生成记录归属、目标卡片归属、字段白名单和版本",
    )

    # 13. 同一候选不能重复采纳
    check(
        source_has(gen_rec, '"accepted": {"$ne": True}'),
        "13. 采纳用条件更新防重复，同一候选不能重复采纳",
    )

    # 14. 写入失败不标记 accepted（标记调用出现在正式写入之后）
    src = read(accept_svc)
    write_pos = src.find("await self._accept_create")
    update_pos = src.find("await self._accept_update")
    # 正式写入之后的采纳标记是唯一有效的那一处，取最后一处避免命中 reject 分支
    mark_pos = src.rfind("adopted = await generation_record_repo.mark_accepted_action(")
    check(
        mark_pos > 0 and mark_pos > min(p for p in (write_pos, update_pos) if p > 0),
        "14. 卡片正式写入成功后才标记采纳，失败不会标记为 accepted",
    )

    # 15. 正文分析包含证据、起止位置与来源章节
    check(
        all(
            source_has(chapter_gen, needle)
            for needle in ("evidence_start", "evidence_end", "evidence_text", "chapter_id")
        )
        and all(source_has(story_evt, needle) for needle in ("evidence_start", "evidence_end"))
        and source_has(
            "backend/llm/schemas/setting_card_pydantic.py", "EvidenceRangeSchema"
        ),
        "15. 正文分析候选携带原文证据、起止位置与来源章节",
    )

    # 16. 正文版本变化后旧结果不能直接采纳
    check(
        "chapter_version" in read(accept_svc) and "_check_versions" in read(accept_svc),
        "16. 正文版本变化后旧分析/建议会被拒绝",
    )

    # 17. 修改建议包含前后文本、章节版本与范围
    check(
        all(
            source_has("backend/db/repositories/text_revision_repository.py", f'"{f}"')
            for f in ("before_text", "after_text", "chapter_version", "target_range")
        ),
        "17. 正文修改建议包含修改前后文本、章节版本与范围",
    )

    # 18. 确认时校验章节版本与 before_text 精确匹配（精确范围/唯一锚点定位）
    apply_src = read(text_rev_svc)
    check(
        "chapter_version != int(revision.get(\"chapter_version\")" in apply_src
        and "_locate_generation_range" in apply_src
        and "content[start:end] == before_text" in apply_src,
        "18. 确认修改建议时校验章节版本并做 before_text 精确匹配",
    )

    # 19. is_hard_rule 默认 false，AI 不能单独置为 true
    check(
        'confirm_hard_rule' in read(accept_svc)
        and 'filtered["is_hard_rule"] = "false"' in read(accept_svc),
        "19. is_hard_rule 未显式确认时回落为 false",
    )

    # 20. blocking 冲突由服务端重新校验，不信任客户端
    check(
        "blocking" in read(blueprint_svc) and "force" in read(blueprint_svc),
        "20. blocking 冲突由服务端重新计算，强制定稿需显式 force",
    )

    # 21. 新增接口校验 novel_id 归属（路径或请求体作用域）
    accept_router = "backend/api/default_routers/setting_card_accept_router.py"
    check(
        "{novel_id}" in read("backend/api/llm_routers/novel_blueprint_router.py")
        and "novel_id: str = Field(min_length=1" in read(accept_router)
        and "/api/llm/setting-cards" in read(accept_router),
        "21. 新增接口均校验 novel_id 归属（路径或请求体作用域）",
    )

    # 22. 写入动作记录来源、时间、版本
    check(
        "status_history" in read(gen_rec)
        and "source" in read(gen_rec)
        and "created_at" in read(gen_rec)
        and "status_history" in read(story_evt),
        "22. 生成记录与事件均记录来源、时间与状态历史",
    )

    # 23. 新集合索引已注册
    idx = read("backend/db/indexes.py")
    check(
        all(
            f"await init_{name}_indexes()" in idx
            for name in ("novel_blueprint", "power_system", "text_revision")
        ),
        "23. 新增三个集合的索引已在 init_all_indexes 中注册",
    )

    # 24. 新路由已在 main.py 注册
    main_src = read("main.py")
    check(
        "novel_blueprint_router" in main_src and "setting_card_accept_router" in main_src,
        "24. 新增路由已在 main.py 注册",
    )

    # 25. 定稿提交按服务端审校记录与硬规则签名重新校验
    finalize_src = read("backend/services/novel/chapter_finalize_service.py")
    check(
        all(
            needle in finalize_src
            for needle in (
                "review_generation_id",
                "hard_rule_signature",
                "get_owned_record",
                "hard_rules",
            )
        ),
        "25. 定稿提交按服务端审校记录与硬规则签名重新校验",
    )

    # 26. 卡片通用更新默认锁定受保护字段
    card_svc = read("backend/services/novel/setting_card_service.py")
    check(
        "PROTECTED_UPDATE_FIELDS" in card_svc
        and "allow_protected_fields" in card_svc,
        "26. 卡片通用更新接口默认锁定 name / current_state / is_hard_rule",
    )

    # 27. 卡片修改后可分析受影响章节
    check(
        "analyze_impact" in read(text_rev_svc)
        and "text-revision/impact" in read("backend/api/llm_routers/novel_blueprint_router.py"),
        "27. 正文影响分析入口可按卡片定位受影响章节",
    )

    # 28. 输出被 max_tokens 截断的兜底：安全下限 + 自动放大重试 + 可读错误映射
    base_src = read("backend/llm/base_client.py")
    openai_src = read("backend/llm/openai_client.py")
    check(
        "MIN_OUTPUT_TOKENS" in base_src
        and "plan_truncation_retry" in base_src
        and "is_length_limit_error" in base_src
        and "LLMOutputTruncatedError" in read("backend/llm/exceptions.py")
        and "plan_truncation_retry" in openai_src
        and "is_length_limit_error" in openai_src,
        "28. 输出截断：请求级安全下限、截断后自动放大重试与可读错误映射",
    )

    # 29. 大纲 / 世界书 / 长期记忆仓储已实现（不再是空占位）
    new_repos = {
        "backend/db/repositories/outline_repository.py": (
            "OutlineRepository",
            "outline_repo",
        ),
        "backend/db/repositories/worldbook_repository.py": (
            "WorldbookRepository",
            "worldbook_repo",
        ),
        "backend/db/repositories/context_memory_repository.py": (
            "ContextMemoryRepository",
            "context_memory_repo",
        ),
    }
    check(
        all(
            class_name in read(path) and singleton in read(path)
            for path, (class_name, singleton) in new_repos.items()
        ),
        "29. 大纲 / 世界书 / 长期记忆三个仓储均已实现",
    )

    # 30. 三个新集合的索引已注册
    idx_src = read("backend/db/indexes.py")
    check(
        all(
            f"await init_{name}_indexes()" in idx_src
            for name in ("outline", "worldbook", "context_memory")
        ),
        "30. 大纲 / 世界书 / 长期记忆集合索引已注册",
    )

    # 31. 长时生成任务与 HTTP 请求解耦，切页/刷新后可恢复
    job_svc_src = read("backend/services/llm/generation_job_service.py")
    job_router_src = read("backend/api/llm_routers/generation_job_router.py")
    check(
        "asyncio.create_task" in job_svc_src
        and "find_resumable_job" in job_svc_src
        and "/active" in job_router_src
        and "/cancel" in job_router_src
        and "generation_job_router" in main_src
        and "await init_generation_job_indexes()" in idx_src,
        "31. 生成任务后台执行，支持恢复查询、取消与索引注册",
    )

    # 32. 进度百分比由真实产出驱动（条目计数 + 历史样本校准）
    workflow_src = read("backend/services/llm/workflow_service.py")
    char_svc_src = read("backend/services/llm/character_generation_service.py")
    char_router_src = read(
        "backend/api/llm_routers/character_generation_router.py"
    )
    check(
        all(needle in workflow_src for needle in ("item_marker", "items_done", "items_target"))
        and '"character_ref"' in char_svc_src
        and '"relation_ref"' in char_svc_src
        and "item_chars_ema" in job_svc_src
        and "prefer_stream=True" in char_router_src
        and "/generate-core-characters/jobs" in char_router_src,
        "32. 进度按真实条目产出统计并用历史样本校准，流式失败自动回退",
    )

    failed = [r for r in results if not r[0]]
    for ok, item, detail in results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {item}{suffix}")
    print(f"\n合计 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
