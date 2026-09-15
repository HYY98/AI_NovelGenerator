"""旧版文件存储 -> MongoDB 迁移脚本（幂等）。

把停用的 main 分支文件式数据导入新版 Mongo 集合：
  - setting_cards.json（{"version":1,"cards":[...]}）-> setting_cards
  - chapters/*.txt                                       -> chapters

复用后端 Repository，业务ID（loc_/itm_/rul_/chap_）自动分配，字段结构与线上一致。
重复运行安全：同名卡 / 同章节号自动跳过。

用法（在项目根目录、用后端 venv 执行）：
  .venv/bin/python scripts/migrate_legacy.py \
      --novel-id 6aa7c7dffdfd20c73e0685ca \
      --src-dir /home/ubuntu/AI_NovelGenerator_main/Novel_Src \
      [--dry-run]
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# 允许从项目根导入 backend
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.mongo import connect_to_mongo, close_mongo_connection, get_database  # noqa: E402
from backend.db.repositories.setting_card_repository import (  # noqa: E402
    SettingCardRepository,
    CARD_TYPES,
)
from backend.db.repositories.chapter_repository import ChapterRepository  # noqa: E402
from backend.db.utils import to_object_id  # noqa: E402

# 旧版字段 -> 新版字段映射（未覆盖到的旧字段统一并入 notes，避免丢内容）
LEGACY_FIELD_MAP = {
    "location": {
        "region": "region",
        "appearance": "appearance",
        "purpose": "purpose",
        "connections": "related_characters",
        "notes": "notes",
    },
    "item": {
        "category": "category",
        "appearance": "appearance",
        "abilities": "abilities",
        "limitations": "limitations",
        "owner": "owner",
        "notes": "notes",
    },
    "rule": {
        "scope": "scope",
        "definition": "definition",
        "constraints": "constraints",
        "exceptions": "exceptions",
        "consequences": "consequences",
        "notes": "notes",
    },
}


def map_card_fields(card_type: str, legacy_fields: dict) -> dict:
    """把旧字段映射到新字段蓝本，未知字段并入 notes。"""
    valid = list(CARD_TYPES[card_type]["fields"].keys())
    out = {k: "" for k in valid}
    mapping = LEGACY_FIELD_MAP.get(card_type, {})
    extra = []
    for key, val in (legacy_fields or {}).items():
        target = mapping.get(key)
        text = "" if val is None else str(val).strip()
        if target and target in out:
            out[target] = text
        elif text:
            extra.append(f"[{key}] {text}")
    if extra:
        out["notes"] = (out.get("notes", "") + "\n" + "\n".join(extra)).strip()
    return out


async def migrate_cards(card_repo, db, novel_id, src_dir: Path, dry_run: bool):
    path = src_dir / "setting_cards.json"
    if not path.exists():
        print("[卡片] 未找到 setting_cards.json，跳过")
        return 0, 0
    data = json.loads(path.read_text(encoding="utf-8"))
    legacy_cards = data.get("cards", []) if isinstance(data, dict) else data
    created, skipped = 0, 0
    for raw in legacy_cards:
        ctype = raw.get("type")
        name = (raw.get("name") or "").strip()
        if ctype not in CARD_TYPES or not name:
            print(f"[卡片] 跳过非法记录: type={ctype} name={name}")
            continue
        existed = await card_repo.find_one(
            {"novel_id": to_object_id(novel_id), "type": ctype, "name": name}
        )
        if existed:
            skipped += 1
            print(f"[卡片] 已存在，跳过: {ctype}/{name}")
            continue
        payload = {
            "type": ctype,
            "name": name,
            "aliases": raw.get("aliases", []) or [],
            "fields": map_card_fields(ctype, raw.get("fields", {})),
            "enabled": raw.get("enabled", True),
            "importance": int(raw.get("importance", 3) or 3),
            "current_state": raw.get("current_state", "") or "",
            "tags": raw.get("tags", []) or [],
        }
        if dry_run:
            print(f"[卡片][dry-run] 将导入: {ctype}/{name}")
            created += 1
            continue
        await card_repo.create_card(novel_id, payload)
        created += 1
        print(f"[卡片] 已导入: {ctype}/{name}")
    return created, skipped


def parse_chapter_file(path: Path):
    """从章节 txt 文件名和内容解析 (number, title, content)。"""
    stem = path.stem
    m = re.search(r"(\d+)", stem)
    number = int(m.group(1)) if m else None
    # 标题：去掉开头的“第N章/chapter N/纯数字”和分隔符
    title = re.sub(r"^\s*(第\s*\d+\s*章|chapter\s*\d+|\d+)\s*[-_：:、\s]*", "", stem, flags=re.I)
    title = title.strip() or (f"第{number}章" if number else stem)
    content = path.read_text(encoding="utf-8", errors="ignore").strip()
    return number, title, content


async def migrate_chapters(ch_repo, novel_id, src_dir: Path, dry_run: bool):
    ch_dir = src_dir / "chapters"
    if not ch_dir.exists():
        print("[章节] 未找到 chapters 目录，跳过")
        return 0, 0
    files = sorted(ch_dir.glob("*.txt"))
    created, skipped = 0, 0
    auto_number = 0
    for path in files:
        number, title, content = parse_chapter_file(path)
        if number is None:
            auto_number += 1
            number = auto_number
        existed = await ch_repo.find_one(
            {"novel_id": to_object_id(novel_id), "number": number}
        )
        if existed:
            skipped += 1
            print(f"[章节] 第{number}章已存在，跳过")
            continue
        payload = {
            "number": number,
            "title": title,
            "content": content,
            "status": "draft",
        }
        if dry_run:
            print(f"[章节][dry-run] 将导入: 第{number}章 {title}（{len(content)}字）")
            created += 1
            continue
        await ch_repo.create_chapter(novel_id, payload)
        created += 1
        print(f"[章节] 已导入: 第{number}章 {title}")
    return created, skipped


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel-id", required=True)
    ap.add_argument("--src-dir", required=True, help="旧版 Novel_Src 目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = ap.parse_args()

    src_dir = Path(args.src_dir)
    if not src_dir.exists():
        print(f"旧版目录不存在: {src_dir}")
        return

    await connect_to_mongo()
    db = get_database()
    card_repo = SettingCardRepository()
    ch_repo = ChapterRepository()
    try:
        c_new, c_skip = await migrate_cards(card_repo, db, args.novel_id, src_dir, args.dry_run)
        h_new, h_skip = await migrate_chapters(ch_repo, args.novel_id, src_dir, args.dry_run)
        print("=" * 48)
        print(f"卡片：导入 {c_new}，跳过 {c_skip}")
        print(f"章节：导入 {h_new}，跳过 {h_skip}")
        print("模式：" + ("DRY-RUN（未写入）" if args.dry_run else "已写入"))
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
