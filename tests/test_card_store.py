import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from card_store import CARD_TYPES, CardStore, CardStoreError, build_card_context


class CardStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CardStore(self.root / "novel-a")

    def card(self, name="城门", kind="location", **kwargs):
        return {"type": kind, "name": name, "fields": {}, **kwargs}

    def fixture(self, cards):
        path = self.root / "import.json"
        path.write_text(json.dumps({"version": 1, "cards": cards}), encoding="utf-8")
        return path

    def test_crud_all_types_and_isolation(self):
        for kind in CARD_TYPES:
            card = self.store.save_card(self.card(kind, kind))
            self.assertTrue(card["enabled"])
            self.assertEqual(set(card["fields"]), set(CARD_TYPES[kind][1]))
            card["name"] += " changed"
            self.store.save_card(card)
        self.assertEqual(len(CardStore(self.store.directory).list_cards()), 3)
        self.assertEqual(CardStore(self.root / "novel-b").list_cards(), [])
        self.store.delete_card(self.store.list_cards()[0]["id"])
        self.assertEqual(len(self.store.list_cards()), 2)

    def test_name_validation_and_type_scoped_duplicates(self):
        for name in ("", "  ", None, 123):
            with self.assertRaises(CardStoreError):
                self.store.save_card(self.card(name))
        self.store.save_card(self.card(" ABC "))
        with self.assertRaises(CardStoreError):
            self.store.save_card(self.card("abc"))
        self.store.save_card(self.card("abc", "item"))
        self.assertEqual(self.store.list_cards()[0]["name"], "ABC")

    def test_search_and_defensive_copy(self):
        self.store.save_card(self.card(fields={"region": "Northern Sea"}))
        found = self.store.list_cards(query="northern")
        self.assertEqual(len(found), 1)
        self.assertEqual(self.store.list_cards("item", "northern"), [])
        found[0]["fields"]["region"] = "mutated"
        self.assertEqual(self.store.list_cards()[0]["fields"]["region"], "Northern Sea")

    def test_import_export_roundtrip(self):
        self.store.save_card(self.card())
        export = self.root / "export.json"
        self.store.export_json(export)
        other = CardStore(self.root / "novel-b")
        self.assertEqual(other.import_json(export), 1)
        self.assertEqual(other.list_cards(), self.store.list_cards())
        with self.assertRaises(CardStoreError):
            other.import_json(export)
        self.assertEqual(other.import_json(export, replace=True), 1)
        with self.assertRaises(CardStoreError):
            self.store.export_json(self.store.path)

    def test_import_is_transactional(self):
        self.store.save_card(self.card())
        original = self.store.path.read_bytes()
        for cards in ([self.card("new"), self.card()], [self.card("new"), self.card(" ")],
                      [self.card("a", enabled="false")], [self.card("a", fields={"unknown": "x"})]):
            with self.assertRaises(CardStoreError):
                self.store.import_json(self.fixture(cards))
            self.assertEqual(self.store.path.read_bytes(), original)
            self.assertEqual(len(self.store.list_cards()), 1)

    def test_atomic_failure_preserves_disk_and_memory(self):
        self.store.save_card(self.card())
        original = self.store.path.read_bytes()
        with patch("card_store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(CardStoreError):
                self.store.save_card(self.card("new"))
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(len(self.store.list_cards()), 1)
        self.assertEqual(list(self.store.directory.glob(".cards-*.tmp")), [])

    def test_corrupt_store_not_overwritten(self):
        self.store.directory.mkdir()
        self.store.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(CardStoreError):
            CardStore(self.store.directory)
        with self.assertRaises(CardStoreError):
            self.store.save_card(self.card())
        self.assertEqual(self.store.path.read_text(), "not json")

    def test_stale_writer_detected(self):
        other = CardStore(self.store.directory)
        self.store.save_card(self.card())
        with self.assertRaises(CardStoreError):
            other.save_card(self.card("new"))
        other.reload()
        other.save_card(self.card("new"))
        self.assertEqual(len(other.list_cards()), 2)

    def test_context_default_enabled_and_disabled(self):
        self.assertEqual(build_card_context(self.store.directory), "")
        self.store.save_card(self.card(fields={"region": "北境"}))
        self.store.save_card(self.card("secret", "rule", enabled=False))
        context = build_card_context(self.store.directory)
        self.assertIn("【地点卡】城门", context)
        self.assertIn("所属区域：北境", context)
        self.assertNotIn("secret", context)

    def test_schema_rejects_malformed_inputs(self):
        for raw in ([], {"version": True, "cards": []}, {"version": 2, "cards": []},
                    {"version": 1, "cards": {}}, {"version": 1, "cards": [42]},
                    {"version": 1, "cards": [self.card(kind=[])]}):
            path = self.root / "invalid.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(CardStoreError):
                self.store.import_json(path)
        with self.assertRaises(CardStoreError):
            CardStore("")


if __name__ == "__main__":
    unittest.main()
