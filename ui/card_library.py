"""Chinese structured-card editor. No main-window or generation dependencies."""
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from card_store import CARD_TYPES, CardStore, CardStoreError

FONT = ("Microsoft YaHei", 12)


class CardLibrary(ctk.CTkFrame):
    """Embed in a tab; host must call can_close() before destroying the app.

    load_project() switches to filepath_var only after resolving unsaved edits.
    Drafts always belong to the loaded store, never to a newly typed path.
    """
    def __init__(self, master, filepath_var):
        super().__init__(master)
        self.filepath_var = filepath_var
        self.store = None
        self.kind = "location"
        self.current_id = None
        self.snapshot = None
        self.fields = {}
        self.name_var = tk.StringVar(master=self)
        self.enabled_var = tk.BooleanVar(master=self, value=True)
        self.search_var = tk.StringVar(master=self)
        self.rowconfigure(2, weight=1)
        self.columnconfigure(1, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        for label, command in [("加载小说", self.load_project), ("导入 JSON", self.import_cards), ("导出 JSON", self.export_cards)]:
            ctk.CTkButton(bar, text=label, width=100, font=FONT, command=command).pack(side="left", padx=3)
        self.path_label = ctk.CTkLabel(self, text="尚未加载小说", font=FONT, anchor="w", wraplength=600)
        self.path_label.grid(row=1, column=0, columnspan=2, sticky="ew", padx=9)

        left = ctk.CTkFrame(self, width=230)
        left.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)
        self.type_menu = ctk.CTkOptionMenu(left, values=[value[0] for value in CARD_TYPES.values()],
                                           command=self.change_type, font=FONT)
        self.type_menu.pack(fill="x", padx=6, pady=6)
        ctk.CTkEntry(left, textvariable=self.search_var, placeholder_text="搜索名称或内容", font=FONT).pack(fill="x", padx=6, pady=6)
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        self.list_frame = ctk.CTkScrollableFrame(left, width=215)
        self.list_frame.pack(fill="both", expand=True, padx=6, pady=6)
        ctk.CTkButton(left, text="新增卡片", command=self.new_card, font=FONT).pack(fill="x", padx=6, pady=6)

        right = ctk.CTkFrame(self)
        right.grid(row=2, column=1, sticky="nsew", padx=6, pady=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        ctk.CTkLabel(right, text="名称（必填）", anchor="w", font=FONT).grid(row=0, column=0, sticky="w", padx=8)
        ctk.CTkEntry(right, textvariable=self.name_var, font=FONT).grid(row=1, column=0, sticky="ew", padx=8, pady=5)
        self.editor = ctk.CTkScrollableFrame(right)
        self.editor.grid(row=2, column=0, sticky="nsew", padx=8, pady=5)
        self.editor.columnconfigure(0, weight=1)
        footer = ctk.CTkFrame(right, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkCheckBox(footer, text="生成时启用", variable=self.enabled_var, font=FONT).pack(side="left", padx=4)
        ctk.CTkButton(footer, text="保存", width=75, command=self.save_card, font=FONT).pack(side="right", padx=4)
        ctk.CTkButton(footer, text="删除", width=75, command=self.delete_card, font=FONT).pack(side="right", padx=4)
        self.status = ctk.CTkLabel(self, text="", font=FONT, anchor="w")
        self.status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=9)
        self.show_card(None)
        if filepath_var.get().strip():
            self.load_project()
        self._dirty_status = False
        self._status_job = self.after(250, self._update_dirty_status)

    def _update_dirty_status(self):
        dirty = self.is_dirty()
        if dirty:
            self.status.configure(text="未保存的修改")
        elif self._dirty_status:
            self.status.configure(text="无未保存修改")
        self._dirty_status = dirty
        self._status_job = self.after(250, self._update_dirty_status)

    def destroy(self):
        if getattr(self, "_status_job", None):
            self.after_cancel(self._status_job)
            self._status_job = None
        super().destroy()

    def error(self, exc):
        messagebox.showerror("卡片操作失败", str(exc), parent=self.winfo_toplevel())

    def draft(self):
        card = {"type": self.kind, "name": self.name_var.get(), "enabled": self.enabled_var.get(),
                "fields": {key: widget.get("1.0", "end-1c") for key, widget in self.fields.items()}}
        if self.current_id:
            card["id"] = self.current_id
        return card

    def is_dirty(self):
        return self.snapshot is not None and self.draft() != self.snapshot

    def can_close(self):
        if not self.is_dirty():
            return True
        answer = messagebox.askyesnocancel("未保存的修改", "是否保存当前卡片？\n选择“否”将放弃修改。", parent=self.winfo_toplevel())
        if answer is None:
            return False
        return self.save_card() if answer else True

    def load_project(self):
        if not self.can_close():
            return False
        try:
            store = CardStore(self.filepath_var.get().strip())
        except (CardStoreError, OSError) as exc:
            self.error(exc)
            return False
        self.store = store
        self.path_label.configure(text=f"当前小说：{store.directory}")
        self.search_var.set("")
        self.show_card(None)
        self.refresh_list()
        self.status.configure(text="已加载")
        return True

    def ensure_store(self):
        if self.store is None:
            self.error("请先加载小说保存路径")
            return False
        return True

    def show_card(self, card):
        self.current_id = card["id"] if card else None
        self.name_var.set(card["name"] if card else "")
        self.enabled_var.set(card["enabled"] if card else True)
        for widget in self.editor.winfo_children():
            widget.destroy()
        self.fields = {}
        for index, (key, label) in enumerate(CARD_TYPES[self.kind][1].items()):
            ctk.CTkLabel(self.editor, text=label, font=FONT, anchor="w").grid(row=index * 2, column=0, sticky="w")
            widget = ctk.CTkTextbox(self.editor, height=85, wrap="word", font=FONT)
            widget.grid(row=index * 2 + 1, column=0, sticky="ew", pady=(0, 8))
            widget.insert("1.0", card["fields"].get(key, "") if card else "")
            self.fields[key] = widget
        self.snapshot = self.draft()

    def refresh_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        cards = self.store.list_cards(self.kind, self.search_var.get()) if self.store else []
        for card in cards:
            name = card["name"].replace("\n", " ").replace("\r", " ")
            label = (name[:11] + "…" if len(name) > 12 else name)
            label += "（停用）" if not card["enabled"] else ""
            button = ctk.CTkButton(self.list_frame, text=label, width=190, font=FONT, anchor="w",
                                 command=lambda item=card: self.select_card(item))
            button.pack(fill="x", pady=3)
        if not cards:
            ctk.CTkLabel(self.list_frame, text="暂无匹配卡片", font=FONT).pack(pady=12)

    def select_card(self, card):
        if card["id"] == self.current_id:
            return
        if self.can_close():
            self.show_card(card)

    def change_type(self, label):
        kind = next(key for key, value in CARD_TYPES.items() if value[0] == label)
        if kind == self.kind:
            return
        if not self.can_close():
            self.type_menu.set(CARD_TYPES[self.kind][0])
            return
        self.kind = kind
        self.show_card(None)
        self.refresh_list()

    def new_card(self):
        if self.ensure_store() and self.can_close():
            self.show_card(None)
            self.status.configure(text="新建卡片")

    def save_card(self):
        if not self.ensure_store():
            return False
        try:
            card = self.store.save_card(self.draft())
        except (CardStoreError, OSError) as exc:
            self.error(exc)
            return False
        self.show_card(card)
        self.refresh_list()
        self.status.configure(text="已保存")
        return True

    def delete_card(self):
        if not self.ensure_store() or not self.current_id:
            return
        if not messagebox.askyesno("删除卡片", "确定删除当前卡片？当前未保存修改也将丢弃。", parent=self.winfo_toplevel()):
            return
        try:
            self.store.delete_card(self.current_id)
        except (CardStoreError, OSError) as exc:
            self.error(exc)
            return
        self.show_card(None)
        self.refresh_list()
        self.status.configure(text="已删除")

    def import_cards(self):
        if not self.ensure_store() or not self.can_close():
            return
        filename = filedialog.askopenfilename(title="导入卡片 JSON", filetypes=[("JSON 文件", "*.json")], parent=self.winfo_toplevel())
        if not filename:
            return
        replace = messagebox.askyesnocancel("导入方式", "是否替换当前小说的全部卡片？\n是：全部替换；否：合并（重复时不导入）；取消：退出。", parent=self.winfo_toplevel())
        if replace is None:
            return
        if replace and not messagebox.askyesno("确认全部替换", "当前小说的全部卡片将被导入文件替换，确定继续？", parent=self.winfo_toplevel()):
            return
        try:
            count = self.store.import_json(filename, replace=replace)
        except (CardStoreError, OSError) as exc:
            self.error(exc)
            return
        self.show_card(None)
        self.refresh_list()
        self.status.configure(text=f"已导入 {count} 张卡片")

    def export_cards(self):
        if not self.ensure_store() or not self.can_close():
            return
        filename = filedialog.asksaveasfilename(title="导出全部卡片", defaultextension=".json", initialfile="设定卡.json",
                                               filetypes=[("JSON 文件", "*.json")], parent=self.winfo_toplevel())
        if not filename:
            return
        try:
            self.store.export_json(filename)
        except (CardStoreError, OSError) as exc:
            self.error(exc)
            return
        self.status.configure(text="已导出全部已保存卡片")


def build_card_tab(app):
    """Attach to app.tabview; app.filepath_var holds the novel directory.

    The host must gate window close with app.card_library.can_close().
    Call load_project() after a desired path change; it returns False on cancel.
    """
    app.card_tab = app.tabview.add("设定卡库")
    app.card_library = CardLibrary(app.card_tab, app.filepath_var)
    app.card_library.pack(fill="both", expand=True)
    return app.card_library
