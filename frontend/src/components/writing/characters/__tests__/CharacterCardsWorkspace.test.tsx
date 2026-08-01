import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CharacterCardsWorkspace, {
  buildCharacterUpdateRequest,
  buildManualCharacterCreateRequest,
} from "../CharacterCardsWorkspace";
import { ModalLayer } from "../views/CharacterWorkspaceViews";
import type {
  CharacterProfileFields,
  CharacterRelationResponseV1,
  CharacterResponseV1,
} from "@/types/character";

vi.mock("next-intl", () => {
  // 翻译函数必须保持引用稳定，避免测试桩制造不存在于真实 next-intl 中的 effect 重载循环。
  const translate = (key: string) => key;
  return { useTranslations: () => translate };
});

const completeProfile: CharacterProfileFields = {
  name: "林澈",
  aliases: ["阿澈"],
  role_type: "protagonist",
  importance_level: "major",
  gender: "女",
  age_group: "青年",
  race: "人类",
  identity: "调查员",
  appearance: "黑发",
  personality: "谨慎",
  core_desire: "查明真相",
  core_fear: "失去同伴",
  strengths: ["观察"],
  weaknesses: ["多疑"],
  abilities: ["推理"],
  conflict_with_mainline: "被旧案牵制",
  relationship_with_protagonist: "本人",
  initial_state: "独自调查",
  growth_direction: "学会信任",
  story_function: "推动谜案",
  arc_seed: "重新审视旧案",
  tags: ["侦探"],
};

const savedCharacter: CharacterResponseV1 = {
  ...completeProfile,
  novel_id: "507f1f77bcf86cd799439011",
  character_id: "CHR-000001",
  status: "active",
  is_core_character: true,
  first_appearance_volume_id: null,
  first_appearance_chapter_id: null,
  sort_order: 12,
  extra: { source: "test" },
  version: 7,
  is_deleted: false,
  deleted_at: null,
  deletion_sources: [],
  created_at: "2026-07-31T08:00:00Z",
  updated_at: "2026-07-31T08:00:00Z",
};

const relatedCharacter: CharacterResponseV1 = {
  ...savedCharacter,
  name: "顾言",
  aliases: [],
  character_id: "CHR-000002",
  version: 3,
};

const savedRelation: CharacterRelationResponseV1 = {
  novel_id: savedCharacter.novel_id,
  relation_id: "CR-000001",
  source_character_id: savedCharacter.character_id,
  target_character_id: relatedCharacter.character_id,
  source_character_name: savedCharacter.name,
  target_character_name: relatedCharacter.name,
  relation_type: "ally",
  current_state: "共同追查旧案",
  core_conflict: "对调查边界意见不同",
  hidden_tension: "彼此有所隐瞒",
  possible_change: "在危机中建立信任",
  story_value: "串联两条调查线",
  intensity: 4,
  is_active: true,
  user_is_active: true,
  disabled_by_character_ids: [],
  sort_order: 0,
  version: 2,
  is_deleted: false,
  deleted_at: null,
  deletion_sources: [],
  created_at: "2026-07-31T08:00:00Z",
  updated_at: "2026-07-31T08:00:00Z",
};

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("CharacterCardsWorkspace", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse({ data: {} });
      return jsonResponse({ data: [] });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("顶部页头隐藏重复标题与副标题并保持紧凑间距", () => {
    render(<CharacterCardsWorkspace mode="edit" novelId="507f1f77bcf86cd799439011" />);

    expect(screen.getByText("workspaceEyebrow")).toBeInTheDocument();
    const hiddenTitle = screen.getByRole("heading", { name: "title" });
    expect(hiddenTitle).toHaveClass("sr-only");
    expect(screen.queryByText("subtitle")).not.toBeInTheDocument();
    expect(hiddenTitle.closest("header")?.firstElementChild).toHaveClass("px-4", "py-3", "sm:px-6");
  });

  it("手工创建弹窗连续输入时保持输入焦点", async () => {
    const user = userEvent.setup();
    render(<CharacterCardsWorkspace mode="edit" novelId="507f1f77bcf86cd799439011" />);

    await user.click(await screen.findByRole("button", { name: "manualCreateCharacter" }));
    const dialog = await screen.findByRole("dialog");
    const nameInput = within(dialog).getByRole("textbox", { name: "fields.name" });

    await user.type(nameInput, "林澈");

    expect(nameInput).toHaveValue("林澈");
    expect(nameInput).toHaveFocus();
  });

  it("角色档案与人物关系的名册和详情均使用独立滚动区域", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}`)) {
        return jsonResponse({ data: [savedCharacter, relatedCharacter] });
      }
      if (url.endsWith(`/api/character-relations/novel/${savedCharacter.novel_id}`)) {
        return jsonResponse({ data: [savedRelation] });
      }
      return jsonResponse({ data: [] });
    }));

    render(<CharacterCardsWorkspace mode="edit" novelId={savedCharacter.novel_id} />);

    const characterRoster = await screen.findByRole("complementary", { name: "tabs.profiles" });
    const characterDetail = screen.getByRole("region", { name: "tabs.profiles · 林澈" });
    expect(characterRoster).toHaveClass("workspace-scrollbar", "overflow-y-auto", "lg:min-h-0");
    expect(characterDetail).toHaveClass("workspace-scrollbar", "lg:overflow-y-auto", "lg:min-h-0");
    expect(characterRoster).toHaveAttribute("tabindex", "0");
    expect(characterDetail).toHaveAttribute("tabindex", "0");

    await user.click(screen.getByRole("tab", { name: /tabs\.relations/ }));

    const relationRoster = screen.getByRole("complementary", { name: "tabs.relations" });
    const relationDetail = screen.getByRole("region", { name: "tabs.relations · 林澈 → 顾言" });
    expect(relationRoster).toHaveClass("workspace-scrollbar", "overflow-y-auto", "lg:min-h-0");
    expect(relationDetail).toHaveClass("workspace-scrollbar", "lg:overflow-y-auto", "lg:min-h-0");
    expect(relationRoster).toHaveAttribute("tabindex", "0");
    expect(relationDetail).toHaveAttribute("tabindex", "0");
  });

  it("漏填字段时在当前弹窗显示摘要并聚焦首个错误字段", async () => {
    const user = userEvent.setup();
    render(<CharacterCardsWorkspace mode="edit" novelId="507f1f77bcf86cd799439011" />);

    await user.click(await screen.findByRole("button", { name: "manualCreateCharacter" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "actions.create" }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent("errors.profileRequired");
    const nameInput = within(dialog).getByRole("textbox", { name: /^fields\.name/ });
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
    expect(nameInput).toHaveFocus();
  });

  it("关闭手工创建弹窗后将焦点恢复到触发按钮", async () => {
    const user = userEvent.setup();
    render(<CharacterCardsWorkspace mode="edit" novelId="507f1f77bcf86cd799439011" />);

    const trigger = await screen.findByRole("button", { name: "manualCreateCharacter" });
    await user.click(trigger);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("409 字段错误保留编辑草稿并在当前弹窗聚焦冲突字段", async () => {
    const user = userEvent.setup();
    let submittedBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse({ data: {} });
      if (init?.method === "PUT" && url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}/${savedCharacter.character_id}`)) {
        submittedBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return jsonResponse({
          detail: {
            code: "CHARACTER_NAME_CONFLICT",
            message: "角色名称已存在",
            fieldErrors: { name: ["请使用不同的角色姓名"] },
          },
        }, 409);
      }
      if (url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}`)) {
        return jsonResponse({ data: [savedCharacter] });
      }
      return jsonResponse({ data: [] });
    }));

    render(<CharacterCardsWorkspace mode="edit" novelId={savedCharacter.novel_id} />);
    await user.click(await screen.findByRole("button", { name: "actions.edit" }));
    const dialog = await screen.findByRole("dialog");
    const nameInput = within(dialog).getByRole("textbox", { name: "fields.name" });
    await user.clear(nameInput);
    await user.type(nameInput, "林澈重名");
    await user.click(within(dialog).getByRole("button", { name: "actions.save" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("角色名称已存在");
    const invalidNameInput = within(dialog).getByRole("textbox", { name: /^fields\.name/ });
    expect(invalidNameInput).toHaveValue("林澈重名");
    expect(invalidNameInput).toHaveAttribute("aria-invalid", "true");
    await waitFor(() => expect(invalidNameInput).toHaveFocus());
    expect(submittedBody).toMatchObject({ expected_version: 7, name: "林澈重名" });
  });

  it("无字段明细的 409 CAS 冲突保留弹窗草稿并聚焦错误摘要", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse({ data: {} });
      if (init?.method === "PUT" && url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}/${savedCharacter.character_id}`)) {
        return jsonResponse({
          detail: {
            code: "VERSION_CONFLICT",
            message: "角色已被其他编辑更新，请刷新后重试",
          },
        }, 409);
      }
      if (url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}`)) {
        return jsonResponse({ data: [savedCharacter] });
      }
      return jsonResponse({ data: [] });
    }));

    render(<CharacterCardsWorkspace mode="edit" novelId={savedCharacter.novel_id} />);
    await user.click(await screen.findByRole("button", { name: "actions.edit" }));
    const dialog = await screen.findByRole("dialog");
    const nameInput = within(dialog).getByRole("textbox", { name: "fields.name" });
    await user.clear(nameInput);
    await user.type(nameInput, "林澈未保存草稿");
    await user.click(within(dialog).getByRole("button", { name: "actions.save" }));

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("角色已被其他编辑更新，请刷新后重试");
    expect(alert).toHaveAttribute("tabindex", "-1");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(within(dialog).getByRole("textbox", { name: "fields.name" })).toHaveValue("林澈未保存草稿");
  });

  it("FastAPI 422 detail 数组在当前弹窗关联字段并保留草稿", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return jsonResponse({ data: {} });
      if (init?.method === "PUT" && url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}/${savedCharacter.character_id}`)) {
        return jsonResponse({
          detail: [{
            type: "string_too_long",
            loc: ["body", "identity"],
            msg: "身份说明长度超出限制",
            input: "一段仍被保留的身份草稿",
          }],
        }, 422);
      }
      if (url.endsWith(`/api/characters/novel/${savedCharacter.novel_id}`)) {
        return jsonResponse({ data: [savedCharacter] });
      }
      return jsonResponse({ data: [] });
    }));

    render(<CharacterCardsWorkspace mode="edit" novelId={savedCharacter.novel_id} />);
    await user.click(await screen.findByRole("button", { name: "actions.edit" }));
    const dialog = await screen.findByRole("dialog");
    const identityInput = within(dialog).getByRole("textbox", { name: "fields.identity" });
    await user.clear(identityInput);
    await user.type(identityInput, "一段仍被保留的身份草稿");
    await user.click(within(dialog).getByRole("button", { name: "actions.save" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("身份说明长度超出限制");
    const invalidIdentityInput = within(dialog).getByRole("textbox", { name: /^fields\.identity/ });
    expect(invalidIdentityInput).toHaveValue("一段仍被保留的身份草稿");
    expect(invalidIdentityInput).toHaveAttribute("aria-invalid", "true");
    expect(invalidIdentityInput).toHaveAttribute("aria-describedby", "character-identity-error");
    await waitFor(() => expect(invalidIdentityInput).toHaveFocus());
  });
});

describe("buildManualCharacterCreateRequest", () => {
  it("将核心开关作为独立字段写入创建载荷", () => {
    const payload = buildManualCharacterCreateRequest(completeProfile, true);

    expect(payload.is_core_character).toBe(true);
    expect(payload.importance_level).toBe("major");
    expect(payload.sort_order).toBe(0);
    expect(payload.extra).toEqual({});
  });

  it("角色编辑载荷严格排除核心标记、正式字段和审计字段", () => {
    const payload = buildCharacterUpdateRequest(savedCharacter, {
      ...completeProfile,
      name: "林澈·修订",
    });

    expect(payload.expected_version).toBe(7);
    expect(payload.name).toBe("林澈·修订");
    const forbiddenFields = [
      "novel_id",
      "character_id",
      "is_core_character",
      "status",
      "sort_order",
      "extra",
      "first_appearance_volume_id",
      "first_appearance_chapter_id",
      "is_deleted",
      "deleted_at",
      "deletion_sources",
      "created_at",
      "updated_at",
    ];
    for (const field of forbiddenFields) {
      expect(payload).not.toHaveProperty(field);
    }
  });
});

describe("ModalLayer", () => {
  it("保存中阻止 Escape、遮罩状态变更和右上关闭按钮卸载弹窗", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <ModalLayer
        title="保存中"
        description="正在保存角色草稿"
        busy
        onClose={onClose}
        footer={null}
      >
        <p>请稍候</p>
      </ModalLayer>,
    );

    const dialog = await screen.findByRole("dialog");
    const closeButton = within(dialog).getByRole("button", { name: "保存中" });
    expect(closeButton).toBeDisabled();

    await user.keyboard("{Escape}");
    await user.click(closeButton);

    expect(onClose).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
  });
});
