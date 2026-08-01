import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FactionCardsWorkspace, {
  buildFactionUpdateRequest,
  buildFactionVersionedDeletePath,
  buildFactionVersionRequest,
  filterFactionProfiles,
} from "../FactionCardsWorkspace";
import type { CoreFaction } from "@/types/novel";

vi.mock("next-intl", () => {
  // 翻译桩保持稳定引用，避免组件 effect 因测试函数反复创建而重载。
  const translate = (key: string) => key;
  return { useTranslations: () => translate };
});

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const savedFaction: CoreFaction = {
  _id: "507f1f77bcf86cd799439012",
  novel_id: "507f1f77bcf86cd799439011",
  faction_id: "fac_000001",
  name: "晨星局",
  alias: ["晨星"],
  faction_type: "official",
  level_type: "core",
  parent_faction_id: null,
  positioning: "守护边境秩序",
  public_stance: "公开维持中立",
  core_goal: "阻止边境战争",
  hidden_goal: "调查旧日失踪案",
  resources_and_advantages: ["情报网"],
  organization_style: "议事制",
  core_values: ["秩序"],
  conflict_with_mainline: "秘密调查牵动主线",
  is_public: true,
  influence_scope: "北境",
  active_status: "active",
  expandability: "可扩展为跨境联盟",
  tags: ["中立"],
  sort_order: 10,
  version: 7,
};

describe("势力工作台页头与边距", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ data: [] })));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("隐藏重复标题与副标题，并与角色工作台共用内容边距", () => {
    const { container } = render(createElement(FactionCardsWorkspace, {
      mode: "edit",
      novelId: savedFaction.novel_id,
    }));

    expect(screen.getByText("workspaceEyebrow")).toBeInTheDocument();
    const hiddenTitle = screen.getByRole("heading", { name: "title" });
    expect(hiddenTitle).toHaveClass("sr-only");
    expect(screen.queryByText("subtitle")).not.toBeInTheDocument();
    expect(hiddenTitle.closest("header")?.firstElementChild).toHaveClass("px-4", "py-3", "sm:px-6");
    expect(screen.getByRole("main")).toHaveClass("px-4", "py-5", "sm:px-6");
    expect(screen.getByRole("main")).not.toHaveClass("lg:px-8", "lg:py-6");
    expect(container.querySelector(".max-w-7xl")).toBeNull();
  });
});

describe("势力档案乐观锁请求", () => {
  it("更新载荷携带当前版本且不泄露服务端 version 字段", () => {
    const payload = buildFactionUpdateRequest(savedFaction);

    expect(payload.expected_version).toBe(7);
    expect(payload.name).toBe("晨星局");
    expect(payload).not.toHaveProperty("version");
  });

  it("恢复请求仅提交当前 expected_version", () => {
    expect(buildFactionVersionRequest(savedFaction)).toEqual({ expected_version: 7 });
  });

  it.each([
    "/api/factions/novel/507f1f77bcf86cd799439011/fac_000001",
    "/api/factions/novel/507f1f77bcf86cd799439011/fac_000001/hard",
  ])("删除路径 %s 携带当前 expected_version", (path) => {
    expect(buildFactionVersionedDeletePath(path, savedFaction)).toBe(
      `${path}?expected_version=7`,
    );
  });
});

describe("势力档案名册筛选", () => {
  const volumeFaction: CoreFaction = {
    ...savedFaction,
    _id: "507f1f77bcf86cd799439013",
    faction_id: "fac_000002",
    name: "渡鸦商会",
    alias: ["黑羽商队"],
    level_type: "volume",
    core_goal: "控制海路贸易",
    tags: ["商贸"],
  };

  it("按势力类别筛选且不改变原始列表", () => {
    const source = [savedFaction, volumeFaction];
    const filtered = filterFactionProfiles(source, "", "volume");

    expect(filtered).toEqual([volumeFaction]);
    expect(source).toHaveLength(2);
  });

  it.each(["渡鸦", "黑羽", "海路", "商贸"])("关键词 %s 可命中档案语义字段", (query) => {
    expect(filterFactionProfiles([savedFaction, volumeFaction], query, "all")).toEqual([volumeFaction]);
  });

  it("同时应用类别与关键词条件", () => {
    expect(filterFactionProfiles([savedFaction, volumeFaction], "晨星", "volume")).toEqual([]);
  });
});
