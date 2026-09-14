"""Novel-scoped structured cards; independent of Tk and generation dependencies."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

CARD_TYPES = {
    "location": ("地点卡", {"region": "所属区域", "appearance": "环境与外观", "purpose": "用途与剧情作用", "connections": "关联人物与地点", "notes": "备注"}),
    "item": ("物品卡", {"category": "物品类别", "appearance": "外观", "abilities": "功能与能力", "limitations": "限制与代价", "owner": "持有者与归属", "notes": "备注"}),
    "rule": ("规则设定卡", {"scope": "适用范围", "definition": "规则内容", "constraints": "约束与代价", "exceptions": "例外条件", "consequences": "违反后果", "notes": "备注"}),
}
STORE_FILENAME = "setting_cards.json"
_LOCK = threading.RLock()


class CardStoreError(ValueError):
    """A user-facing validation, persistence or conflict error."""


def _normalize_cards(cards):
    if not isinstance(cards, list):
        raise CardStoreError("cards 必须是数组")
    result, ids, names = [], set(), set()
    for raw in cards:
        if not isinstance(raw, dict):
            raise CardStoreError("每张卡必须是 JSON 对象")
        kind = raw.get("type")
        if not isinstance(kind, str) or kind not in CARD_TYPES:
            raise CardStoreError("卡片类型必须是 location、item 或 rule")
        name = raw.get("name", "")
        if not isinstance(name, str) or not name.strip():
            raise CardStoreError("名称不能为空")
        name = name.strip()
        key = (kind, name.casefold())
        if key in names:
            raise CardStoreError(f"{CARD_TYPES[kind][0]}名称重复：{name}")
        names.add(key)
        card_id = raw.get("id", uuid.uuid4().hex)
        if not isinstance(card_id, str) or not card_id.strip() or card_id in ids:
            raise CardStoreError("卡片 ID 为空或重复")
        ids.add(card_id)
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CardStoreError(f"{name} 的 enabled 必须是布尔值")
        fields = raw.get("fields", {})
        if not isinstance(fields, dict):
            raise CardStoreError(f"{name} 的 fields 必须是对象")
        unknown = set(fields) - set(CARD_TYPES[kind][1])
        if unknown:
            raise CardStoreError(f"{name} 包含未知字段：{', '.join(sorted(unknown))}")
        normalized = {}
        for field, label in CARD_TYPES[kind][1].items():
            value = fields.get(field, "")
            if not isinstance(value, str):
                raise CardStoreError(f"{name} 的{label}必须是文本")
            normalized[field] = value.strip()
        unknown = set(raw) - {"id", "type", "name", "enabled", "fields"}
        if unknown:
            raise CardStoreError(f"{name} 包含未知属性：{', '.join(sorted(unknown))}")
        result.append({"id": card_id, "type": kind, "name": name, "enabled": enabled, "fields": normalized})
    return result


def _decode(data):
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise CardStoreError(f"JSON 格式错误：{exc}") from exc
    if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
        raise CardStoreError("不支持的卡片文件格式或版本（需要 version: 1）")
    if set(payload) != {"version", "cards"}:
        raise CardStoreError("卡片文件必须且只能包含 version 和 cards")
    return _normalize_cards(payload["cards"])


def _atomic_write(path, cards):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".cards-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump({"version": 1, "cards": cards}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise CardStoreError(f"保存失败：{exc}") from exc
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class CardStore:
    """Snapshot store; reload after external changes instead of silently overwriting.

    Names are unique within each type (trimmed, case-insensitive). Import defaults
    to merge without overwriting; replace=True explicitly replaces the whole store.
    """
    def __init__(self, filepath):
        if not filepath or not str(filepath).strip():
            raise CardStoreError("请先设置小说保存路径")
        self.directory = Path(filepath).expanduser().resolve()
        if self.directory.exists() and not self.directory.is_dir():
            raise CardStoreError("小说保存路径必须是目录")
        self.path = self.directory / STORE_FILENAME
        self._cards = []
        self._revision = None
        self.reload()

    def _read(self):
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CardStoreError(f"读取失败：{exc}") from exc

    def reload(self):
        with _LOCK:
            data = self._read()
            cards = [] if data is None else _decode(data)
            self._cards, self._revision = cards, data
        return self.list_cards()

    def list_cards(self, kind=None, query=""):
        query = query.strip().casefold()
        return copy.deepcopy([card for card in self._cards
                              if (kind is None or card["type"] == kind)
                              and (not query or query in "\n".join([card["name"], *card["fields"].values()]).casefold())])

    def _commit(self, cards):
        cards = _normalize_cards(cards)
        with _LOCK:
            if self._read() != self._revision:
                raise CardStoreError("卡片文件已被其他窗口修改，请重新加载后再保存")
            _atomic_write(self.path, cards)
            self._cards = cards
            self._revision = self._read()

    def save_card(self, card):
        card = copy.deepcopy(card)
        card.setdefault("id", uuid.uuid4().hex)
        cards = self.list_cards()
        index = next((i for i, item in enumerate(cards) if item["id"] == card["id"]), None)
        if index is None:
            cards.append(card)
        else:
            cards[index] = card
        self._commit(cards)
        return next(item for item in self.list_cards() if item["id"] == card["id"])

    def delete_card(self, card_id):
        cards = [card for card in self.list_cards() if card["id"] != card_id]
        if len(cards) == len(self._cards):
            raise CardStoreError("找不到要删除的卡片")
        self._commit(cards)

    def import_json(self, filename, replace=False):
        try:
            incoming = _decode(Path(filename).read_bytes())
        except OSError as exc:
            raise CardStoreError(f"导入读取失败：{exc}") from exc
        self._commit(incoming if replace else self.list_cards() + incoming)
        return len(incoming)

    def export_json(self, filename):
        target = Path(filename).expanduser().resolve()
        if target == self.path:
            raise CardStoreError("导出目标不能是当前卡片存储文件")
        _atomic_write(target, self.list_cards())


def build_card_context(filepath):
    """Return enabled cards as Chinese context; missing file means empty context.

    Invalid/corrupt stores raise CardStoreError for callers to report, not ignore.
    """
    cards = CardStore(filepath).list_cards()
    blocks = []
    for card in cards:
        if not card["enabled"]:
            continue
        title, fields = CARD_TYPES[card["type"]]
        lines = [f"【{title}】{card['name']}"]
        lines.extend(f"{label}：{card['fields'][field]}" for field, label in fields.items() if card["fields"][field])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
