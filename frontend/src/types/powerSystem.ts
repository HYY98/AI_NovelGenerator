/** 结构化战力体系的数据契约（对应后端 power_system_pydantic）。 */

/** 战力等级 / 境界。 */
export interface PowerLevel {
  name: string;
  order: number;
  description: string;
  requirements: string[];
  abilities: string[];
  limitations: string[];
}

/** 能量资源定义。 */
export interface PowerResource {
  name: string;
  source: string;
  consumption: string;
  recovery: string;
}

/** 克制关系。 */
export interface PowerCounter {
  source: string;
  target: string;
  description: string;
}

/** 战力体系版本状态。 */
export type PowerSystemStatus = "draft" | "confirmed" | "archived";

/** 完整战力体系。 */
export interface PowerSystem {
  power_system_id?: string | null;
  name: string;
  description: string;
  levels: PowerLevel[];
  power_dimensions: string[];
  resource: PowerResource | null;
  restrictions: string[];
  special_rules: string[];
  counters: PowerCounter[];
  status?: PowerSystemStatus;
  version?: number;
}

/** 功能：构造一份空的战力体系，供新增或重置使用。
 * Args: 无。
 * Returns: 字段齐全且为空的 PowerSystem。
 */
export function createEmptyPowerSystem(): PowerSystem {
  return {
    power_system_id: null,
    name: "",
    description: "",
    levels: [],
    power_dimensions: [],
    resource: null,
    restrictions: [],
    special_rules: [],
    counters: [],
    status: "draft",
    version: 1,
  };
}

/** 功能：构造一个空的战力等级，order 由调用方指定。
 * Args: order: 该等级在体系中的顺序。
 * Returns: 字段齐全且为空的 PowerLevel。
 */
export function createEmptyPowerLevel(order: number): PowerLevel {
  return {
    name: "",
    order,
    description: "",
    requirements: [],
    abilities: [],
    limitations: [],
  };
}

/** 战力体系校验问题。 */
export interface PowerSystemIssue {
  /** 出问题的字段路径，例如 `levels.0.name`。 */
  field: string;
  message: string;
}

/** 功能：校验战力体系的等级名称与顺序是否合法。
 *
 * 只做前端可判定的结构性校验：等级名必填且不重复、order 不重复且非负。
 * 内容类约束由后端在蓝图确认时再次校验。
 *
 * Args:
 *   value: 待校验的战力体系。
 *
 * Returns:
 *   校验问题列表；为空表示结构合法。
 */
export function validatePowerSystem(value: PowerSystem): PowerSystemIssue[] {
  const issues: PowerSystemIssue[] = [];

  value.levels.forEach((level, index) => {
    if (!level.name.trim()) {
      issues.push({ field: `levels.${index}.name`, message: `第 ${index + 1} 个等级缺少名称` });
    }
    if (!Number.isFinite(level.order) || level.order < 0) {
      issues.push({ field: `levels.${index}.order`, message: `第 ${index + 1} 个等级的顺序必须是不小于 0 的整数` });
    }
  });

  const names = value.levels.map((level) => level.name.trim()).filter(Boolean);
  const duplicateName = names.find((name, index) => names.indexOf(name) !== index);
  if (duplicateName) {
    issues.push({ field: "levels", message: `等级名称「${duplicateName}」重复` });
  }

  const orders = value.levels.map((level) => level.order);
  const duplicateOrder = orders.find((order, index) => orders.indexOf(order) !== index);
  if (duplicateOrder !== undefined) {
    issues.push({ field: "levels", message: `等级顺序 ${duplicateOrder} 重复` });
  }

  return issues;
}

/** 功能：按 order 升序重排等级，并把 order 重写为连续序号。
 * Args: levels: 待整理等级列表。
 * Returns: 排序并编号后的新数组，不修改入参。
 */
export function normalizePowerLevels(levels: PowerLevel[]): PowerLevel[] {
  return [...levels]
    .sort((a, b) => a.order - b.order)
    .map((level, index) => ({ ...level, order: index }));
}

const LIST_SEPARATOR = /[\n,，、;；]+/;

/** 功能：把多行文本拆成字符串数组，用于维度、限制等列表字段。
 * Args: text: 用户输入的原始文本。
 * Returns: 去空白后的非空字符串数组。
 */
export function splitLines(text: string): string[] {
  return text
    .split(LIST_SEPARATOR)
    .map((item) => item.trim())
    .filter(Boolean);
}
