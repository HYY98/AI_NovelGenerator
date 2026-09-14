# -*- coding: utf-8 -*-
"""Chapter editor; the public functions retain main_window's method bindings."""
import tkinter as tk
from tkinter import messagebox, simpledialog
import customtkinter as ctk
from chapter_storage import ChapterStore
from ui.context_menu import TextWidgetContextMenu
from utils import get_word_count


def build_chapters_tab(self):
    self.chapters_view_tab = self.tabview.add("章节编辑")
    self.chapters_view_tab.rowconfigure(2, weight=1)
    self.chapters_view_tab.columnconfigure(0, weight=1)
    self._chapter_store = None
    self._chapter_number = ""
    self._chapter_saved_text = ""
    self._chapter_snapshot = None
    self._chapter_loading = False
    self.chapters_list = []
    # The owner may call this before closing or changing projects; False cancels.
    self.guard_chapter_changes = lambda action="关闭窗口": guard_chapter_changes(self, action)

    top = ctk.CTkFrame(self.chapters_view_tab)
    top.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
    top.columnconfigure(3, weight=1)
    self.chapter_select_var = ctk.StringVar(value="")
    self.chapter_select_menu = ctk.CTkOptionMenu(top, values=[""], variable=self.chapter_select_var,
                                              command=self.on_chapter_selected, width=95)
    self.chapter_select_menu.grid(row=0, column=1, padx=4, pady=4)
    for column, text, command in [(0, "上一章", self.prev_chapter), (2, "下一章", self.next_chapter),
                                  (4, "保存", self.save_current_chapter),
                                  (5, "刷新", self.refresh_chapters_list)]:
        ctk.CTkButton(top, text=text, width=70, command=command).grid(row=0, column=column, padx=4, pady=4)
    self.chapters_word_count_label = ctk.CTkLabel(top, text="字数：0")
    self.chapters_word_count_label.grid(row=0, column=3, padx=4)
    for column, text, command in [(0, "新建", lambda: new_chapter(self)),
                                  (1, "删除", lambda: delete_current_chapter(self)),
                                  (2, "撤销", lambda: _undo_redo(self, False)),
                                  (4, "重做", lambda: _undo_redo(self, True))]:
        ctk.CTkButton(top, text=text, width=70, command=command).grid(row=1, column=column, padx=4, pady=4)

    self.chapter_project_label = ctk.CTkLabel(top, text="", anchor="w", wraplength=600)
    self.chapter_project_label.grid(row=2, column=0, columnspan=6, sticky="ew", padx=4, pady=4)
    top.bind("<Configure>", lambda event: self.chapter_project_label.configure(wraplength=max(100, event.width - 20)), add="+")

    search = ctk.CTkFrame(self.chapters_view_tab)
    search.grid(row=1, column=0, sticky="ew", padx=5)
    search.columnconfigure((0, 1), weight=1)
    self.chapter_search_entry = ctk.CTkEntry(search, placeholder_text="查找")
    self.chapter_search_entry.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
    self.chapter_replace_entry = ctk.CTkEntry(search, placeholder_text="替换为")
    self.chapter_replace_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
    for column, text, command in [(2, "查找下一个", lambda: find_next(self)),
                                  (3, "替换", lambda: replace_current(self)),
                                  (4, "全部替换", lambda: replace_all(self))]:
        ctk.CTkButton(search, text=text, width=80, command=command).grid(row=0, column=column, padx=4)
    self.chapter_search_entry.bind("<Return>", lambda event: find_next(self))
    self.chapter_view_text = ctk.CTkTextbox(self.chapters_view_tab, wrap="word", undo=True,
                                          maxundo=200, autoseparators=True, font=("Microsoft YaHei", 12))
    self.chapter_view_text.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
    TextWidgetContextMenu(self.chapter_view_text)
    widget = self.chapter_view_text._textbox
    widget.bind("<<Modified>>", lambda event: _modified(self), add="+")
    for key, command in [("<Control-s>", lambda: save_current_chapter(self)),
                         ("<Control-z>", lambda: _undo_redo(self, False)),
                         ("<Control-y>", lambda: _undo_redo(self, True)),
                         ("<Control-Shift-Z>", lambda: _undo_redo(self, True)),
                         ("<Control-f>", self.chapter_search_entry.focus_set)]:
        widget.bind(key, lambda event, callback=command: _shortcut(callback))
    refresh_chapters_list(self)


def _shortcut(callback):
    callback()
    return "break"


def _text(self):
    return self.chapter_view_text.get("1.0", "end-1c")


def _dirty(self):
    return _text(self) != self._chapter_saved_text


def _update_status(self):
    suffix = " · 未保存" if _dirty(self) else ""
    self.chapters_word_count_label.configure(text=f"字数：{get_word_count(_text(self))}{suffix}")


def _modified(self):
    widget = self.chapter_view_text._textbox
    if widget.edit_modified():
        widget.edit_modified(False)
        if not self._chapter_loading:
            _update_status(self)


def _set_text(self, content):
    self._chapter_loading = True
    try:
        widget = self.chapter_view_text._textbox
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.edit_reset()
        widget.edit_modified(False)
        if not self._chapter_number:
            widget.configure(state="disabled")
        self._chapter_saved_text = content
        _update_status(self)
    finally:
        self._chapter_loading = False


def _error(action, exc):
    messagebox.showerror(action, str(exc))


def guard_chapter_changes(self, action="关闭窗口"):
    """Return False to cancel. Call before owner-level close/project changes."""
    if not _dirty(self):
        return True
    answer = messagebox.askyesnocancel("未保存修改", f"{action}前保存当前章节修改吗？\n选择“否”放弃修改，选择“取消”留在当前章节。")
    if answer is None:
        return False
    return save_current_chapter(self) if answer else True


def _restore_selection(self):
    self.chapter_select_var.set(self._chapter_number)


def _commit_load(self, store, number, content, snapshot, numbers=None):
    self._chapter_store = store
    self._chapter_number = number
    self._chapter_snapshot = snapshot
    if hasattr(self, "chapter_project_label"):
        self.chapter_project_label.configure(text=f"当前目录：{store.directory}" if store else "当前目录：未选择")
    if numbers is not None:
        self.chapters_list = numbers
        self.chapter_select_menu.configure(values=numbers or [""], state="normal" if numbers else "disabled")
    _restore_selection(self)
    _set_text(self, content)


def refresh_chapters_list(self):
    if not guard_chapter_changes(self, "刷新章节列表"):
        _restore_selection(self)
        return False
    filepath = self.filepath_var.get().strip()
    try:
        store = ChapterStore(filepath) if filepath else None
        numbers = store.list_chapters() if store else []
        same_project = store and self._chapter_store and store.directory == self._chapter_store.directory
        number = self._chapter_number if same_project and self._chapter_number in numbers else (numbers[0] if numbers else "")
        content, snapshot = store.load(number) if number else ("", None)
    except (OSError, ValueError) as exc:
        _error("刷新失败", exc)
        _restore_selection(self)
        return False
    _commit_load(self, store, number, content, snapshot, numbers)
    return True


def on_chapter_selected(self, value):
    return load_chapter_content(self, value)


def load_chapter_content(self, chapter_number_str):
    if not chapter_number_str:
        _restore_selection(self)
        return False
    if not guard_chapter_changes(self, "切换章节"):
        _restore_selection(self)
        return False
    try:
        # Navigation refers to the displayed project's list, not a changed path entry.
        store = self._chapter_store or ChapterStore(self.filepath_var.get().strip())
        content, snapshot = store.load(chapter_number_str)
    except (OSError, ValueError) as exc:
        _error("加载失败", exc)
        _restore_selection(self)
        return False
    _commit_load(self, store, chapter_number_str, content, snapshot)
    return True


def save_current_chapter(self):
    if not self._chapter_number or self._chapter_store is None:
        messagebox.showwarning("无法保存", "尚未选择章节。")
        return False
    content = _text(self)
    try:
        snapshot = self._chapter_store.save(self._chapter_number, content, self._chapter_snapshot)
    except (OSError, ValueError) as exc:
        _error("保存失败（编辑内容已保留）", exc)
        return False
    self._chapter_snapshot = snapshot
    self._chapter_saved_text = content
    _update_status(self)
    self.safe_log(f"已保存第 {self._chapter_number} 章：{self._chapter_store.path(self._chapter_number)}")
    return True


def _navigate(self, step):
    if self._chapter_number not in self.chapters_list:
        return False
    index = self.chapters_list.index(self._chapter_number) + step
    if 0 <= index < len(self.chapters_list):
        return load_chapter_content(self, self.chapters_list[index])
    messagebox.showinfo("提示", "已经是第一章了。" if step < 0 else "已经是最后一章了。")
    return False


def prev_chapter(self):
    return _navigate(self, -1)


def next_chapter(self):
    return _navigate(self, 1)


def new_chapter(self):
    if not guard_chapter_changes(self, "新建章节"):
        return False
    try:
        store = ChapterStore(self.filepath_var.get().strip())
        number = simpledialog.askstring("新建章节", "章节编号：", initialvalue=store.next_number(), parent=self.master)
        if number is None:
            return False
        number = number.strip()
        store.create(number)
        content, snapshot = store.load(number)
        numbers = store.list_chapters()
    except (OSError, ValueError) as exc:
        _error("新建失败", exc)
        return False
    _commit_load(self, store, number, content, snapshot, numbers)
    self.chapter_view_text.focus_set()
    return True


def delete_current_chapter(self):
    if not self._chapter_number:
        return False
    if not guard_chapter_changes(self, "删除章节"):
        return False
    number, store = self._chapter_number, self._chapter_store
    if not messagebox.askyesno("删除章节", f"永久删除第 {number} 章？\n{store.path(number)}"):
        return False
    try:
        numbers = store.list_chapters()
        index = numbers.index(number)
        remaining = [item for item in numbers if item != number]
        target = remaining[min(index, len(remaining) - 1)] if remaining else ""
        content, snapshot = store.load(target) if target else ("", None)
        store.delete(number, self._chapter_snapshot)
    except (OSError, ValueError) as exc:
        _error("删除失败", exc)
        return False
    _commit_load(self, store, target, content, snapshot, remaining)
    return True


def _undo_redo(self, redo):
    try:
        widget = self.chapter_view_text._textbox
        widget.edit_redo() if redo else widget.edit_undo()
    except tk.TclError:
        pass
    _update_status(self)


def find_next(self):
    query = self.chapter_search_entry.get()
    if not query:
        return False
    widget = self.chapter_view_text._textbox
    start = widget.index("insert")
    position = widget.search(query, start, stopindex="end", exact=True)
    if not position:
        position = widget.search(query, "1.0", stopindex=start, exact=True)
    widget.tag_remove("sel", "1.0", "end")
    if not position:
        messagebox.showinfo("查找", "未找到匹配文本。")
        return False
    end = widget.index(f"{position}+{len(query)}c")
    widget.tag_add("sel", position, end)
    widget.mark_set("insert", end)
    widget.see(position)
    widget.focus_set()
    return True


def replace_current(self):
    if not self._chapter_number:
        return False
    query = self.chapter_search_entry.get()
    if not query:
        return False
    widget = self.chapter_view_text._textbox
    selection = widget.tag_ranges("sel")
    if not selection or widget.get(*selection) != query:
        return find_next(self)
    start, end = selection
    widget.edit_separator()
    widget.configure(autoseparators=False)
    try:
        widget.delete(start, end)
        widget.insert(start, self.chapter_replace_entry.get())
    finally:
        widget.configure(autoseparators=True)
        widget.edit_separator()
    _update_status(self)
    return True


def replace_all(self):
    if not self._chapter_number:
        return 0
    query = self.chapter_search_entry.get()
    if not query:
        return 0
    content = _text(self)
    count = content.count(query)
    replacement = self.chapter_replace_entry.get()
    if count and replacement != query:
        widget = self.chapter_view_text._textbox
        widget.edit_separator()
        # Disable automatic boundaries so the entire replacement is one undo.
        widget.configure(autoseparators=False)
        try:
            widget.delete("1.0", "end")
            widget.insert("1.0", content.replace(query, replacement))
        finally:
            widget.configure(autoseparators=True)
            widget.edit_separator()
        _update_status(self)
    messagebox.showinfo("全部替换", f"匹配 {count} 处。")
    return count
