"""Run manually with project dependencies available; no API calls are made."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import tkinter as tk
from unittest.mock import patch
import customtkinter as ctk
from ui.main_window import NovelGeneratorGUI
from ui import chapters_tab


def main():
    errors = []
    ctk.set_widget_scaling(1.0)
    ctk.set_window_scaling(1.0)
    root = ctk.CTk()
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        with tempfile.TemporaryDirectory() as directory:
            with patch('ui.main_window.load_config', return_value={}):
                app = NovelGeneratorGUI(root)
            app.filepath_var.set(directory)
            assert app.card_library.load_project()
            app.card_library.name_var.set('Smoke location')
            assert app.card_library.save_card()
            with patch('ui.chapters_tab.simpledialog.askstring', return_value='1'):
                assert chapters_tab.new_chapter(app)
            app.chapter_view_text.insert('1.0', 'Smoke chapter\n')
            assert app.save_current_chapter()
            for width, height in [(1350, 840), (1000, 760)]:
                root.geometry(f'{width}x{height}')
                for tab in ['设定卡库', '章节编辑']:
                    app.tabview.set(tab)
                    root.update()
                    assert root.winfo_width() >= width, (root.winfo_width(), width)
                    if tab == '章节编辑':
                        assert app.chapter_view_text.winfo_width() > 400
                        assert app.chapter_view_text.winfo_height() > 200
                    else:
                        assert app.card_library.editor.winfo_width() > 250
            assert not errors, errors
            assert app.guard_chapter_changes()
            assert app.card_library.can_close()
            print('Desktop smoke passed: main window, cards, chapter save, two window sizes, clean close.')
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
