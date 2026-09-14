"""Real Tk smoke tests; skip only when GUI dependencies/display are unavailable."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
    import customtkinter as ctk
except ImportError:
    ctk = None

if ctk is not None:
    from ui.card_library import build_card_tab


@unittest.skipIf(ctk is None, "customtkinter is not installed")
class CardLibraryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = ctk.CTk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(self.close_root)
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
        self.root.geometry("1000x760")
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.app = SimpleNamespace(tabview=ctk.CTkTabview(self.root),
                                   filepath_var=tk.StringVar(self.root, value=str(self.path / "a")))
        self.app.tabview.pack(fill="both", expand=True)
        self.editor = build_card_tab(self.app)
        self.errors = []
        self.editor.error = self.errors.append
        self.root.update()

    def close_root(self):
        for job in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', job)
        self.root.destroy()

    def test_layout_and_crud_each_type(self):
        editor = self.editor
        self.assertGreater(editor.winfo_width(), 800)
        self.assertGreater(editor.editor.winfo_height(), 200)
        for label in ("地点卡", "物品卡", "规则设定卡"):
            editor.change_type(label)
            editor.name_var.set(label + "测试")
            first = next(iter(editor.fields.values()))
            first.insert("1.0", "测试内容")
            self.assertTrue(editor.is_dirty())
            self.assertTrue(editor.save_card())
            self.assertFalse(editor.is_dirty())
            editor.name_var.set(label + "已编辑")
            self.assertTrue(editor.save_card())
            self.root.update()
            self.assertGreater(next(iter(editor.fields.values())).winfo_width(), 200)
        self.assertEqual(len(editor.store.list_cards()), 3)
        with patch("ui.card_library.messagebox.askyesno", return_value=True):
            editor.delete_card()
        self.assertEqual(len(editor.store.list_cards()), 2)
        self.assertEqual(self.errors, [])

    def test_long_name_list_and_dirty_status(self):
        editor = self.editor
        name = "超长名称" * 100
        editor.name_var.set(name)
        editor.after_cancel(editor._status_job)
        editor._update_dirty_status()
        self.assertEqual(editor.status.cget("text"), "未保存的修改")
        self.assertTrue(editor.save_card())
        self.root.update()
        self.assertEqual(editor.name_var.get(), name)
        self.assertEqual(editor.store.list_cards()[0]["name"], name)
        button = editor.list_frame.winfo_children()[0]
        self.assertLess(len(button.cget("text")), 20)
        self.assertLessEqual(button.winfo_width(), editor.list_frame.winfo_width())

    def test_cancel_and_save_failure_protect_draft(self):
        editor = self.editor
        editor.name_var.set("草稿")
        with patch("ui.card_library.messagebox.askyesnocancel", return_value=None):
            self.assertFalse(editor.can_close())
            editor.change_type("物品卡")
            self.assertEqual(editor.kind, "location")
            editor.new_card()
            self.app.filepath_var.set(str(self.path / "b"))
            self.assertFalse(editor.load_project())
        self.assertEqual(editor.name_var.get(), "草稿")
        self.assertEqual(editor.store.directory, self.path / "a")
        editor.name_var.set("")
        editor.enabled_var.set(False)
        with patch("ui.card_library.messagebox.askyesnocancel", return_value=True):
            self.assertFalse(editor.can_close())
        self.assertTrue(editor.is_dirty())
        self.assertEqual(len(self.errors), 1)

    def test_path_binding_and_import_export(self):
        editor = self.editor
        editor.name_var.set("测试卡")
        self.app.filepath_var.set(str(self.path / "b"))
        self.assertTrue(editor.save_card())
        self.assertTrue((self.path / "a" / "setting_cards.json").exists())
        self.assertFalse((self.path / "b" / "setting_cards.json").exists())
        filename = str(self.path / "cards.json")
        with patch("ui.card_library.filedialog.asksaveasfilename", return_value=filename):
            editor.export_cards()
        self.assertTrue(editor.load_project())
        self.assertEqual(editor.store.list_cards(), [])
        with patch("ui.card_library.filedialog.askopenfilename", return_value=filename), \
                patch("ui.card_library.messagebox.askyesnocancel", return_value=False):
            editor.import_cards()
        self.assertEqual(len(editor.store.list_cards()), 1)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
