"""Offline storage and editor tests, with real Tk and no application imports."""
import importlib.util
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chapter_storage import ChapterStore, ChapterConflictError


def load_editor():
    spec = importlib.util.spec_from_file_location("chapter_editor_under_test", ROOT / "ui" / "chapters_tab.py")
    module = importlib.util.module_from_spec(spec)
    context_menu = ModuleType("ui.context_menu")
    context_menu.TextWidgetContextMenu = mock.Mock()
    with mock.patch.dict(sys.modules, {"customtkinter": ModuleType("customtkinter"), "ui.context_menu": context_menu}):
        spec.loader.exec_module(module)
    return module


class ChapterStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = ChapterStore(self.temp.name)

    def test_numeric_order_and_invalid_files(self):
        for number in ["10", "2", "1"]:
            self.store.create(number)
        (self.store.directory / "chapter_bad.txt").touch()
        (self.store.directory / "chapter_3.txt").mkdir()
        self.assertEqual(self.store.list_chapters(), ["1", "2", "10"])
        self.assertEqual(self.store.next_number(), "11")

    def test_lossless_round_trip_and_empty_save(self):
        text = "  第一章\r\n\n结尾  \n"
        self.store.create("1", text)
        content, snapshot = self.store.load("1")
        self.assertEqual(content, text)
        self.store.save("1", content, snapshot)
        self.assertEqual(self.store.path("1").read_bytes(), text.encode("utf-8"))
        self.store.save("1", "", snapshot)
        self.assertEqual(self.store.load("1")[0], "")

    def test_atomic_failure_retains_original_and_cleans_temp(self):
        self.store.create("1", "original")
        _, snapshot = self.store.load("1")
        with mock.patch("chapter_storage.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.save("1", "new", snapshot)
        self.assertEqual(self.store.load("1")[0], "original")
        self.assertEqual(len(list(self.store.directory.iterdir())), 1)

    def test_conflict_and_deleted_file(self):
        self.store.create("1", "old")
        _, snapshot = self.store.load("1")
        self.store.path("1").write_bytes(b"external")
        with self.assertRaises(ChapterConflictError):
            self.store.save("1", "mine", snapshot)
        with self.assertRaises(ChapterConflictError):
            self.store.delete("1", snapshot)
        self.store.path("1").unlink()
        with self.assertRaises(ChapterConflictError):
            self.store.save("1", "mine", snapshot)
        self.assertFalse(self.store.path("1").exists())

    def test_validation_duplicates_and_delete(self):
        for number in ["../1", "", "1.5", "-1", "１"]:
            with self.assertRaises(ValueError):
                self.store.create(number)
        self.store.create("01", "keep")
        with self.assertRaises(FileExistsError):
            self.store.create("1")
        self.store.delete("01", self.store.load("01")[1])
        self.assertEqual(self.store.list_chapters(), [])
        with self.assertRaises(ValueError):
            ChapterStore("")

    def test_decode_error_is_not_empty_content(self):
        self.store.create("1")
        self.store.path("1").write_bytes(b"\xff\xfe")
        with self.assertRaises(UnicodeError):
            self.store.load("1")


class ChapterEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.editor = load_editor()
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = ChapterStore(self.temp.name)
        for number in ["1", "2", "10"]:
            self.store.create(number, f"chapter {number}\n")
        widget = tk.Text(self.root, undo=True, autoseparators=True)
        self.addCleanup(widget.destroy)
        textbox = SimpleNamespace(_textbox=widget, get=widget.get, focus_set=widget.focus_set)
        self.owner = SimpleNamespace(
            master=self.root, chapter_view_text=textbox, _chapter_store=None,
            _chapter_number="", _chapter_snapshot=None, _chapter_saved_text="",
            _chapter_loading=False, chapters_list=[], safe_log=mock.Mock(),
            chapters_word_count_label=mock.Mock(), chapter_select_menu=mock.Mock(),
            chapter_select_var=tk.StringVar(self.root), filepath_var=tk.StringVar(self.root, value=self.temp.name),
            chapter_search_entry=mock.Mock(), chapter_replace_entry=mock.Mock())
        self.dialog = mock.patch.object(self.editor, "messagebox").start()
        self.addCleanup(mock.patch.stopall)
        self.editor.refresh_chapters_list(self.owner)

    def edit(self, content):
        widget = self.owner.chapter_view_text._textbox
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.edit_separator()

    def test_switch_cancel_restores_selector_and_content(self):
        self.edit("unsaved")
        self.owner.chapter_select_var.set("2")
        self.dialog.askyesnocancel.return_value = None
        self.assertFalse(self.editor.on_chapter_selected(self.owner, "2"))
        self.assertEqual(self.owner.chapter_select_var.get(), "1")
        self.assertEqual(self.editor._text(self.owner), "unsaved")

    def test_save_guard_and_numeric_navigation(self):
        self.edit("  saved\n\n")
        self.dialog.askyesnocancel.return_value = True
        self.assertTrue(self.editor.next_chapter(self.owner))
        self.assertEqual(self.store.load("1")[0], "  saved\n\n")
        self.editor.next_chapter(self.owner)
        self.assertEqual(self.owner._chapter_number, "10")
        self.editor.prev_chapter(self.owner)
        self.assertEqual(self.owner._chapter_number, "2")

    def test_save_failure_blocks_guard(self):
        self.edit("unsaved")
        self.store.path("1").write_bytes(b"external")
        self.dialog.askyesnocancel.return_value = True
        self.assertFalse(self.editor.guard_chapter_changes(self.owner))
        self.assertEqual(self.editor._text(self.owner), "unsaved")
        self.assertEqual(self.store.load("1")[0], "external")

    def test_save_uses_loaded_project_after_path_change(self):
        self.edit("correct project")
        self.owner.filepath_var.set(str(Path(self.temp.name) / "other"))
        self.assertTrue(self.editor.save_current_chapter(self.owner))
        self.assertEqual(self.store.load("1")[0], "correct project")
        self.assertFalse((Path(self.temp.name) / "other").exists())

    def test_refresh_cancel_discard_and_missing_directory(self):
        self.edit("mine")
        self.dialog.askyesnocancel.return_value = None
        self.assertFalse(self.editor.refresh_chapters_list(self.owner))
        self.dialog.askyesnocancel.return_value = False
        self.assertTrue(self.editor.refresh_chapters_list(self.owner))
        self.assertEqual(self.editor._text(self.owner), "chapter 1\n")
        self.owner.filepath_var.set(str(Path(self.temp.name) / "missing"))
        self.editor.refresh_chapters_list(self.owner)
        self.assertEqual(self.owner.chapters_list, [])
        self.assertEqual(self.editor._text(self.owner), "")
        self.assertEqual(self.owner.chapter_select_var.get(), "")

    def test_load_failure_retains_editor_and_selection(self):
        self.store.path("2").write_bytes(b"\xff")
        self.assertFalse(self.editor.load_chapter_content(self.owner, "2"))
        self.assertEqual(self.editor._text(self.owner), "chapter 1\n")
        self.assertEqual(self.owner.chapter_select_var.get(), "1")

    def test_replace_all_is_single_undo_and_redo(self):
        self.edit("猫 猫\n")
        widget = self.owner.chapter_view_text._textbox
        widget.edit_reset()
        self.owner.chapter_search_entry.get.return_value = "猫"
        self.owner.chapter_replace_entry.get.return_value = "小猫"
        self.assertEqual(self.editor.replace_all(self.owner), 2)
        self.assertEqual(self.editor._text(self.owner), "小猫 小猫\n")
        self.editor._undo_redo(self.owner, False)
        self.assertEqual(self.editor._text(self.owner), "猫 猫\n")
        self.editor._undo_redo(self.owner, True)
        self.assertEqual(self.editor._text(self.owner), "小猫 小猫\n")

    def test_literal_search_wrap_and_replace_selection(self):
        self.edit("a.b a.b")
        self.owner.chapter_search_entry.get.return_value = "a.b"
        self.owner.chapter_replace_entry.get.return_value = ""
        self.assertTrue(self.editor.find_next(self.owner))
        self.assertTrue(self.editor.replace_current(self.owner))
        self.assertEqual(self.editor._text(self.owner), " a.b")
        self.editor._undo_redo(self.owner, False)
        self.assertEqual(self.editor._text(self.owner), "a.b a.b")
        self.owner.chapter_search_entry.get.return_value = ""
        self.assertEqual(self.editor.replace_all(self.owner), 0)

    def test_new_delete_and_no_cross_chapter_undo(self):
        with mock.patch.object(self.editor.simpledialog, "askstring", return_value="11"):
            self.assertTrue(self.editor.new_chapter(self.owner))
        self.assertEqual(self.owner.chapters_list, ["1", "2", "10", "11"])
        self.editor._undo_redo(self.owner, False)
        self.assertEqual(self.editor._text(self.owner), "")
        self.dialog.askyesno.return_value = True
        self.assertTrue(self.editor.delete_current_chapter(self.owner))
        self.assertEqual(self.owner._chapter_number, "10")
        self.assertFalse(self.store.path("11").exists())

    def test_modified_status_tracks_undo(self):
        self.edit("modified")
        self.editor._modified(self.owner)
        self.assertIn("未保存", self.owner.chapters_word_count_label.configure.call_args.kwargs["text"])
        self.editor.save_current_chapter(self.owner)
        self.assertNotIn("未保存", self.owner.chapters_word_count_label.configure.call_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
