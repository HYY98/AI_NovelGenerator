/** 全书级角色关系 / 势力关系接口封装，复用后端既有 CRUD 接口。 */

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import type {
  CharacterRelationCreateRequestV1,
  CharacterRelationResponseV1,
  CharacterRelationUpdateRequestV1,
} from "@/types/character";

/** 势力关系公开响应。 */
export interface FactionRelationResponse {
  novel_id?: string;
  relation_id: string;
  source_faction_id: string;
  target_faction_id: string;
  source_faction_name?: string | null;
  target_faction_name?: string | null;
  relation_type: string;
  current_state?: string;
  /** 部分后端版本使用 description 承载说明。 */
  description?: string;
  version?: number;
  is_deleted?: boolean;
}

export const CHARACTER_RELATION_TYPES = [
  "friend",
  "romantic",
  "ally",
  "rival",
  "enemy",
  "parent_of",
  "mentor_of",
  "superior_of",
  "protector_of",
  "debtor_to",
] as const;

export const CHARACTER_RELATION_TYPE_LABEL: Record<string, string> = {
  friend: "朋友",
  romantic: "恋人",
  ally: "盟友",
  rival: "对手",
  enemy: "敌人",
  parent_of: "亲子",
  mentor_of: "师徒",
  superior_of: "上下级",
  protector_of: "守护",
  debtor_to: "债务",
};

export const FACTION_RELATION_TYPE_LABEL: Record<string, string> = {
  hostile: "敌对",
  allied: "同盟",
  cold_war: "冷战",
  dependent: "依附",
  subordinate: "从属",
  trade_partner: "贸易伙伴",
  secret_cooperation: "暗中合作",
  historical_enemy: "世仇",
};

/** 功能：生成创建关系所需的幂等键，避免重复提交产生重复关系。 */
function idempotencyKey(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 功能：列出小说全部有效角色关系。 */
export function listCharacterRelations(
  novelId: string
): Promise<{ data: CharacterRelationResponseV1[] }> {
  return apiGet(`/api/character-relations/novel/${novelId}`);
}

/** 功能：列出小说全部势力关系。 */
export function listFactionRelations(
  novelId: string
): Promise<{ data: FactionRelationResponse[] }> {
  return apiGet(`/api/faction-relations/novel/${novelId}`);
}

/**
 * 功能：创建一条全书级角色关系。
 * Args: novelId: 小说 ObjectId；payload: 关系内容。
 * Returns: 创建后的关系公开响应。
 */
export function createCharacterRelation(
  novelId: string,
  payload: CharacterRelationCreateRequestV1
): Promise<CharacterRelationResponseV1> {
  return apiPost(`/api/character-relations/novel/${novelId}`, payload, {
    headers: { "Idempotency-Key": idempotencyKey() },
  });
}

/**
 * 功能：更新一条角色关系的内容字段（端点不变）。
 * Args:
 *   novelId: 小说 ObjectId。
 *   relationId: 关系业务 ID。
 *   payload: 内容字段与 expected_version。
 * Returns: 更新后的关系公开响应。
 */
export function updateCharacterRelation(
  novelId: string,
  relationId: string,
  payload: CharacterRelationUpdateRequestV1
): Promise<CharacterRelationResponseV1> {
  return apiPut(`/api/character-relations/novel/${novelId}/${relationId}`, payload);
}

/** 功能：软删除一条角色关系。 */
export function deleteCharacterRelation(
  novelId: string,
  relationId: string,
  expectedVersion: number
): Promise<CharacterRelationResponseV1> {
  return apiDelete(
    `/api/character-relations/novel/${novelId}/${relationId}?expected_version=${expectedVersion}`
  );
}
