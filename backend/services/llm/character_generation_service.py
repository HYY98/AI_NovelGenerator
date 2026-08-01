"""全书级核心角色与人物关系的直接生成预览服务。"""

from __future__ import annotations

import json
import math
from typing import Any, AsyncGenerator

from backend.db.repositories.faction_repository import faction_repo
from backend.db.repositories.novel_repository import novel_repo
from backend.llm.prompts.prompt_selector import (
    CHARACTER_RELATIONS_PROMPT_NAME,
    CORE_CHARACTERS_PROMPT_NAME,
    load_prompt_config,
)
from backend.llm.schemas.character_pydantic import (
    CoreCharactersGenerateRequestV1,
    CoreCharactersResultSchemaV1,
)
from backend.llm.schemas.character_relation_pydantic import (
    CharacterRelationsGenerateRequestV1,
    CharacterRelationsResultSchemaV1,
)
from backend.services.novel.character_name import (
    GenerationDomainError,
    canonical_json,
    ensure_text_budget,
    normalize_character_name,
)
from backend.services.llm.llm_service import LLMService
from backend.services.llm.workflow_service import (
    build_generation_kwargs,
    generate_structured_result,
    get_llm_service_for_step,
    provider_supports_json_schema,
    resolve_provider_for_step,
    should_use_streaming,
    stream_structured_result_events,
)
from backend.services.novel.character_faction_binding_service import (
    CharacterFactionBindingService,
)
from backend.services.novel.character_relation_service import CharacterRelationService
from backend.services.novel.character_service import CharacterService


CORE_CHARACTER_WORKFLOW_NAME = "create_characters_by_ai"
CORE_CHARACTER_STEP = "create_core_characters"
CHARACTER_RELATION_WORKFLOW_NAME = "create_character_relations_by_ai"
CHARACTER_RELATION_STEP = "create_character_relations"

CHARACTER_RELATION_COUNT_DEFAULT = 20
CORE_CHARACTER_INPUT_MAX_CHARS = 120_000
CHARACTER_RELATION_INPUT_MAX_CHARS = 180_000

NOVEL_PROFILE_FIELDS = (
    "title",
    "genre",
    "tone",
    "target_audience",
    "core_idea",
    "core_seed",
    "plot",
    "summary",
    "worldview",
    "number_of_chapters",
    "words_per_chapter",
    "writing_style",
    "narrative_pov",
    "era_background",
    "tags",
)
FACTION_PROMPT_FIELDS = (
    "faction_id",
    "name",
    "positioning",
    "core_goal",
    "conflict_with_mainline",
    "active_status",
    "level_type",
    "version",
)
CHARACTER_PROMPT_FIELDS = (
    "character_id",
    "name",
    "identity",
    "core_desire",
    "core_fear",
    "story_function",
    "arc_seed",
    "status",
    "is_core_character",
    "version",
)
RELATION_PROMPT_FIELDS = (
    "relation_id",
    "source_character_id",
    "target_character_id",
    "relation_type",
    "current_state",
    "core_conflict",
    "hidden_tension",
    "possible_change",
    "story_value",
    "intensity",
    "is_active",
    "version",
)
BINDING_PROMPT_FIELDS = (
    "binding_id",
    "character_id",
    "faction_id",
    "membership_type",
    "role_title",
    "public_status",
    "loyalty_level",
    "notes",
    "is_active",
    "version",
)


def _project(document: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """从数据库文档提取允许进入 Prompt 的稳定字段。

    Args:
        document: Repository 返回的原始文档。
        fields: 允许进入 Prompt 的字段白名单。

    Returns:
        仅包含白名单字段的独立字典。
    """
    projected = {field: document.get(field) for field in fields}
    if "version" in fields and projected.get("version") is None:
        projected["version"] = 1
    return projected


def _resolve_execution(
    request: Any,
    workflow_name: str,
    step_name: str,
) -> tuple[str, LLMService, bool, bool, dict[str, Any]]:
    """解析一次直接生成所需的 Provider、调用模式和参数。

    Args:
        request: 含扁平七项生成参数的业务请求。
        workflow_name: 工作流配置名称。
        step_name: 工作流步骤名称。

    Returns:
        Provider、LLMService、JSON Schema 能力、流式决策和生成参数。
    """
    provider = resolve_provider_for_step(workflow_name, step_name)
    if not provider:
        raise GenerationDomainError(
            "LLM_PROVIDER_UNAVAILABLE",
            f"工作流 {workflow_name} 没有可用的 Provider",
            status_code=400,
        )
    return (
        provider,
        get_llm_service_for_step(workflow_name, step_name),
        provider_supports_json_schema(provider),
        should_use_streaming(request, provider),
        build_generation_kwargs(request),
    )


class CharacterGenerationService:
    """读取当前小说事实并直接生成角色或关系候选预览。"""

    @staticmethod
    async def _load_core_context(
        request: CoreCharactersGenerateRequestV1,
    ) -> dict[str, Any]:
        """读取核心角色生成所需的当前小说事实。

        Args:
            request: 已校验的直接生成请求。

        Returns:
            Prompt 输入、允许引用的势力 ID 和已占用角色名称。
        """
        novel_id = request.novel_id
        novel = await novel_repo.get_novel_by_id(novel_id)
        factions = [
            item
            for item in await faction_repo.get_factions_by_level_type(novel_id, "core")
            if item.get("active_status") == "active"
        ]
        all_characters = await CharacterService.get_characters_by_novel(novel_id)
        core_characters = [
            item
            for item in all_characters
            if item.get("is_core_character") is True and item.get("status") == "active"
        ]
        if len(factions) > 100:
            raise GenerationDomainError(
                "CHARACTER_GENERATION_INPUT_BUDGET_EXCEEDED",
                "核心势力数量超过 100，无法在不截断事实的情况下生成角色",
            )
        if len(core_characters) > 200:
            raise GenerationDomainError(
                "CHARACTER_GENERATION_INPUT_BUDGET_EXCEEDED",
                "已有核心角色数量超过 200，无法在不截断事实的情况下继续生成",
            )

        novel_profile = _project(novel, NOVEL_PROFILE_FIELDS)
        for field, value in novel_profile.items():
            if isinstance(value, str) and len(value) > 8000:
                raise GenerationDomainError(
                    "CHARACTER_GENERATION_INPUT_BUDGET_EXCEEDED",
                    f"小说字段 {field} 超过 8000 字符",
                )

        faction_projections = [_project(item, FACTION_PROMPT_FIELDS) for item in factions]
        character_projections = [
            _project(item, CHARACTER_PROMPT_FIELDS) for item in core_characters
        ]
        for index, projection in enumerate(
            (*faction_projections, *character_projections)
        ):
            if len(canonical_json(projection)) > 2000:
                raise GenerationDomainError(
                    "CHARACTER_GENERATION_INPUT_BUDGET_EXCEEDED",
                    f"核心势力或角色摘要第 {index + 1} 项超过 2000 字符",
                )

        prompt_input = {
            "novel_profile": novel_profile,
            "core_factions": faction_projections,
            "existing_core_characters": character_projections,
            "character_count": request.character_count,
            "user_guidance": request.user_guidance,
        }
        ensure_text_budget(
            prompt_input,
            max_chars=CORE_CHARACTER_INPUT_MAX_CHARS,
            code="CHARACTER_GENERATION_INPUT_BUDGET_EXCEEDED",
            label="核心角色生成输入",
        )
        return {
            "prompt_input": prompt_input,
            "allowed_faction_ids": {
                str(item["faction_id"]) for item in faction_projections
            },
            "reserved_character_names": {
                normalize_character_name(str(item.get("name") or ""))[1]
                for item in all_characters
                if str(item.get("name") or "").strip()
            },
        }

    @staticmethod
    async def _load_relation_context(
        request: CharacterRelationsGenerateRequestV1,
    ) -> dict[str, Any]:
        """读取角色关系追加生成所需的当前小说事实。

        Args:
            request: 已校验的关系直接生成请求。

        Returns:
            Prompt 输入、最终数量上限和关系业务校验上下文。
        """
        novel_id = request.novel_id
        novel = await novel_repo.get_novel_by_id(novel_id)
        characters = await CharacterService.get_characters_by_ids(
            novel_id,
            request.character_ids,
        )
        characters_by_id = {
            str(item["character_id"]): item for item in characters
        }
        missing_ids = [
            character_id
            for character_id in request.character_ids
            if character_id not in characters_by_id
        ]
        if missing_ids:
            raise GenerationDomainError(
                "CHARACTER_RELATION_ENDPOINT_INVALID",
                f"角色不存在或不属于当前小说: {', '.join(missing_ids)}",
            )
        for character in characters:
            if (
                character.get("status") != "active"
                or character.get("is_core_character") is not True
            ):
                raise GenerationDomainError(
                    "CHARACTER_RELATION_ENDPOINT_INVALID",
                    f"角色 {character['character_id']} 必须是 active 核心角色",
                )

        selected_ids = set(request.character_ids)
        bindings = [
            item
            for item in await CharacterFactionBindingService.list_bindings(
                novel_id,
                is_active=True,
            )
            if item.get("character_id") in selected_ids
        ]
        relations = [
            item
            for item in await CharacterRelationService.list_relations(
                novel_id,
                is_active=True,
            )
            if item.get("source_character_id") in selected_ids
            and item.get("target_character_id") in selected_ids
        ]
        if len(bindings) > 300 or len(relations) > 1000:
            raise GenerationDomainError(
                "CHARACTER_RELATION_INPUT_BUDGET_EXCEEDED",
                "角色绑定或既有活动关系超过工作流预算，禁止静默截断",
            )

        character_projections = [
            _project(characters_by_id[item], CHARACTER_PROMPT_FIELDS)
            for item in request.character_ids
        ]
        binding_projections = [
            _project(item, BINDING_PROMPT_FIELDS) for item in bindings
        ]
        relation_projections = [
            _project(item, RELATION_PROMPT_FIELDS) for item in relations
        ]
        for index, projection in enumerate(
            (*character_projections, *relation_projections)
        ):
            if len(canonical_json(projection)) > 2000:
                raise GenerationDomainError(
                    "CHARACTER_RELATION_INPUT_BUDGET_EXCEEDED",
                    f"角色或关系摘要第 {index + 1} 项超过 2000 字符",
                )

        character_count = len(request.character_ids)
        maximum_edge_count = character_count * (character_count - 1)
        minimum_cover_count = math.ceil(character_count / 2)
        normalized_limit = (
            request.relation_count_limit
            if request.relation_count_limit is not None
            else min(
                maximum_edge_count,
                max(CHARACTER_RELATION_COUNT_DEFAULT, minimum_cover_count),
            )
        )
        novel_profile = _project(novel, NOVEL_PROFILE_FIELDS)
        for field in ("plot", "core_seed"):
            value = novel_profile.get(field)
            if isinstance(value, str) and len(value) > 8000:
                raise GenerationDomainError(
                    "CHARACTER_RELATION_INPUT_BUDGET_EXCEEDED",
                    f"小说字段 {field} 超过 8000 字符",
                )

        prompt_input = {
            "novel_profile": novel_profile,
            "characters": character_projections,
            "faction_bindings": binding_projections,
            "existing_relations": relation_projections,
            "allow_isolated_characters": request.allow_isolated_characters,
            "relation_count_limit": normalized_limit,
            "user_guidance": request.user_guidance,
        }
        ensure_text_budget(
            prompt_input,
            max_chars=CHARACTER_RELATION_INPUT_MAX_CHARS,
            code="CHARACTER_RELATION_INPUT_BUDGET_EXCEEDED",
            label="角色关系生成输入",
        )
        return {
            "prompt_input": prompt_input,
            "relation_count_limit": normalized_limit,
            "character_ids": list(request.character_ids),
            "allow_isolated_characters": request.allow_isolated_characters,
        }

    @staticmethod
    def _build_core_prompt(
        context: dict[str, Any],
        *,
        use_json_schema: bool,
    ) -> str:
        """构造核心角色直接生成 Prompt。

        Args:
            context: `_load_core_context` 返回的当前事实上下文。
            use_json_schema: 是否使用 Provider 原生 Schema 输出。

        Returns:
            完整核心角色生成 Prompt。
        """
        prompt_input = context["prompt_input"]
        section = load_prompt_config()[CORE_CHARACTERS_PROMPT_NAME]
        base = section["create_core_characters_prompt_base"].format(
            novel_profile_json=json.dumps(
                prompt_input["novel_profile"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            core_factions_json=json.dumps(
                prompt_input["core_factions"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            existing_core_characters_json=json.dumps(
                prompt_input["existing_core_characters"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            character_count=prompt_input["character_count"],
            user_guidance=prompt_input.get("user_guidance") or "无",
        )
        suffix_key = (
            "create_core_characters_prompt_with_schema_suffix"
            if use_json_schema
            else "create_core_characters_prompt_without_schema_suffix"
        )
        return f"{base.rstrip()}\n\n{section[suffix_key].strip()}"

    @staticmethod
    def _build_relation_prompt(
        context: dict[str, Any],
        *,
        use_json_schema: bool,
    ) -> str:
        """构造仅追加的角色关系直接生成 Prompt。

        Args:
            context: `_load_relation_context` 返回的当前事实上下文。
            use_json_schema: 是否使用 Provider 原生 Schema 输出。

        Returns:
            完整角色关系生成 Prompt。
        """
        prompt_input = context["prompt_input"]
        section = load_prompt_config()[CHARACTER_RELATIONS_PROMPT_NAME]
        base = section["create_character_relations_prompt_base"].format(
            novel_profile_json=json.dumps(
                prompt_input["novel_profile"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            characters_json=json.dumps(
                prompt_input["characters"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            faction_bindings_json=json.dumps(
                prompt_input["faction_bindings"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            existing_relations_json=json.dumps(
                prompt_input["existing_relations"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            allow_isolated_characters=json.dumps(
                prompt_input["allow_isolated_characters"]
            ),
            relation_count_limit=prompt_input["relation_count_limit"],
            user_guidance=prompt_input.get("user_guidance") or "无",
        )
        suffix_key = (
            "create_character_relations_prompt_with_schema_suffix"
            if use_json_schema
            else "create_character_relations_prompt_without_schema_suffix"
        )
        return f"{base.rstrip()}\n\n{section[suffix_key].strip()}"

    @staticmethod
    def _validate_core_result(
        result: CoreCharactersResultSchemaV1,
        request: CoreCharactersGenerateRequestV1,
        context: dict[str, Any],
    ) -> None:
        """校验角色数量、名称和势力引用是否符合当前请求事实。

        Args:
            result: 已通过结构 Schema 的角色候选。
            request: 当前直接生成请求。
            context: 当前小说事实上下文。

        Returns:
            校验通过时不返回内容。
        """
        errors: list[str] = []
        if len(result.core_characters) != request.character_count:
            errors.append(
                f"候选数量必须严格等于 {request.character_count}"
            )
        normalized_names = [
            normalize_character_name(item.name)[1]
            for item in result.core_characters
        ]
        if len(normalized_names) != len(set(normalized_names)):
            errors.append("候选角色名称在 Unicode 规范化后重复")
        if set(normalized_names) & context["reserved_character_names"]:
            errors.append("候选角色与已有角色重名")

        allowed_faction_ids = context["allowed_faction_ids"]
        for binding in result.binding_candidates:
            if (
                binding.faction_id is not None
                and binding.faction_id not in allowed_faction_ids
            ):
                errors.append(f"势力 {binding.faction_id} 不在当前核心势力中")
        if errors:
            raise GenerationDomainError(
                "CHARACTER_GENERATION_OUTPUT_INVALID",
                "; ".join(errors),
                status_code=502,
            )

    @staticmethod
    async def _validate_relation_result(
        novel_id: str,
        result: CharacterRelationsResultSchemaV1,
        context: dict[str, Any],
    ) -> None:
        """校验关系数量、端点覆盖和仅追加语义冲突。

        Args:
            novel_id: 当前小说公开 ID。
            result: 已通过结构 Schema 的关系候选。
            context: 当前关系生成事实上下文。

        Returns:
            校验通过时不返回内容。
        """
        errors: list[str] = []
        if len(result.relations) > context["relation_count_limit"]:
            errors.append("关系候选数量超过本次请求上限")

        service_errors = await CharacterRelationService.validate_generation_candidates(
            novel_id,
            result.relations,
            context["character_ids"],
        )
        errors.extend(service_errors)
        if not context["allow_isolated_characters"]:
            covered = {
                endpoint
                for relation in result.relations
                for endpoint in (
                    relation.source_character_id,
                    relation.target_character_id,
                )
            }
            missing = sorted(set(context["character_ids"]) - covered)
            if missing:
                errors.append(
                    f"以下角色未参与任何候选关系: {', '.join(missing)}"
                )
        if errors:
            raise GenerationDomainError(
                "CHARACTER_RELATION_OUTPUT_INVALID",
                "; ".join(errors),
                status_code=502,
            )

    @staticmethod
    async def generate_core_characters_preview(
        request: CoreCharactersGenerateRequestV1,
    ) -> CoreCharactersResultSchemaV1:
        """非流式生成核心角色与势力绑定候选预览。

        Args:
            request: 扁平七参数的核心角色生成请求。

        Returns:
            已通过结构和当前事实校验的核心角色结果。
        """
        context = await CharacterGenerationService._load_core_context(
            request,
        )
        _, service, use_json_schema, _, gen_kwargs = _resolve_execution(
            request,
            CORE_CHARACTER_WORKFLOW_NAME,
            CORE_CHARACTER_STEP,
        )
        prompt = CharacterGenerationService._build_core_prompt(
            context,
            use_json_schema=use_json_schema,
        )
        result = await generate_structured_result(
            service,
            prompt,
            CoreCharactersResultSchemaV1,
            CORE_CHARACTER_STEP,
            gen_kwargs=gen_kwargs,
            use_json_schema=use_json_schema,
        )
        CharacterGenerationService._validate_core_result(result, request, context)
        return result

    @staticmethod
    async def stream_core_characters_preview(
        request: CoreCharactersGenerateRequestV1,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """准备核心角色生成上下文并返回 progress/done 事件流。

        Args:
            request: 扁平七参数的核心角色生成请求。

        Returns:
            已完成小说事实与 Provider 准备的事件生成器；不含原始 JSON 分片。
        """
        context = await CharacterGenerationService._load_core_context(
            request,
        )
        _, service, use_json_schema, should_stream, gen_kwargs = _resolve_execution(
            request,
            CORE_CHARACTER_WORKFLOW_NAME,
            CORE_CHARACTER_STEP,
        )
        prompt = CharacterGenerationService._build_core_prompt(
            context,
            use_json_schema=False if should_stream else use_json_schema,
        )

        async def event_stream() -> AsyncGenerator[dict[str, Any], None]:
            """在已准备上下文上执行核心角色生成并输出事件。

            Args:
                无。

            Yields:
                不含原始 JSON 分片的进度事件和最终结构化结果事件。
            """
            if not should_stream:
                result = await generate_structured_result(
                    service,
                    prompt,
                    CoreCharactersResultSchemaV1,
                    CORE_CHARACTER_STEP,
                    gen_kwargs=gen_kwargs,
                    use_json_schema=use_json_schema,
                )
                CharacterGenerationService._validate_core_result(
                    result,
                    request,
                    context,
                )
                yield {"status": "done", "result": result}
                return

            async for event in stream_structured_result_events(
                service,
                prompt,
                CoreCharactersResultSchemaV1,
                CORE_CHARACTER_STEP,
                gen_kwargs=gen_kwargs,
            ):
                if event["status"] == "progress":
                    yield event
                    continue
                result = CoreCharactersResultSchemaV1.model_validate(
                    event["result"].model_dump()
                )
                CharacterGenerationService._validate_core_result(
                    result,
                    request,
                    context,
                )
                yield {"status": "done", "result": result}

        return event_stream()

    @staticmethod
    async def generate_character_relations_preview(
        request: CharacterRelationsGenerateRequestV1,
    ) -> CharacterRelationsResultSchemaV1:
        """非流式生成仅追加的角色关系候选预览。

        Args:
            request: 扁平七参数的关系生成请求。

        Returns:
            已通过结构和当前事实校验的关系结果。
        """
        context = await CharacterGenerationService._load_relation_context(
            request,
        )
        _, service, use_json_schema, _, gen_kwargs = _resolve_execution(
            request,
            CHARACTER_RELATION_WORKFLOW_NAME,
            CHARACTER_RELATION_STEP,
        )
        prompt = CharacterGenerationService._build_relation_prompt(
            context,
            use_json_schema=use_json_schema,
        )
        result = await generate_structured_result(
            service,
            prompt,
            CharacterRelationsResultSchemaV1,
            CHARACTER_RELATION_STEP,
            gen_kwargs=gen_kwargs,
            use_json_schema=use_json_schema,
        )
        await CharacterGenerationService._validate_relation_result(
            request.novel_id,
            result,
            context,
        )
        return result

    @staticmethod
    async def stream_character_relations_preview(
        request: CharacterRelationsGenerateRequestV1,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """准备角色关系生成上下文并返回 progress/done 事件流。

        Args:
            request: 扁平七参数的关系生成请求。

        Returns:
            已完成小说事实与 Provider 准备的事件生成器；不含原始 JSON 分片。
        """
        context = await CharacterGenerationService._load_relation_context(
            request,
        )
        _, service, use_json_schema, should_stream, gen_kwargs = _resolve_execution(
            request,
            CHARACTER_RELATION_WORKFLOW_NAME,
            CHARACTER_RELATION_STEP,
        )
        prompt = CharacterGenerationService._build_relation_prompt(
            context,
            use_json_schema=False if should_stream else use_json_schema,
        )

        async def event_stream() -> AsyncGenerator[dict[str, Any], None]:
            """在已准备上下文上执行角色关系生成并输出事件。

            Args:
                无。

            Yields:
                不含原始 JSON 分片的进度事件和最终结构化结果事件。
            """
            if not should_stream:
                result = await generate_structured_result(
                    service,
                    prompt,
                    CharacterRelationsResultSchemaV1,
                    CHARACTER_RELATION_STEP,
                    gen_kwargs=gen_kwargs,
                    use_json_schema=use_json_schema,
                )
                await CharacterGenerationService._validate_relation_result(
                    request.novel_id,
                    result,
                    context,
                )
                yield {"status": "done", "result": result}
                return

            async for event in stream_structured_result_events(
                service,
                prompt,
                CharacterRelationsResultSchemaV1,
                CHARACTER_RELATION_STEP,
                gen_kwargs=gen_kwargs,
            ):
                if event["status"] == "progress":
                    yield event
                    continue
                result = CharacterRelationsResultSchemaV1.model_validate(
                    event["result"].model_dump()
                )
                await CharacterGenerationService._validate_relation_result(
                    request.novel_id,
                    result,
                    context,
                )
                yield {"status": "done", "result": result}

        return event_stream()


__all__ = [
    "CHARACTER_RELATION_STEP",
    "CHARACTER_RELATION_WORKFLOW_NAME",
    "CORE_CHARACTER_STEP",
    "CORE_CHARACTER_WORKFLOW_NAME",
    "CharacterGenerationService",
]
