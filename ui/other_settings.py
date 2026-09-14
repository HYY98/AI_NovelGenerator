# ui/other_settings.py
import customtkinter as ctk
from ui.config_tab import create_label_with_help
from tkinter import messagebox
from config_manager import load_config, save_config
import requests
from requests.auth import HTTPBasicAuth
import os
import json
import tempfile
import threading
from xml.etree import ElementTree as ET
import shutil
import time

REQUEST_TIMEOUT = (5, 30)
def build_other_settings_tab(self):
    self.other_settings_tab = self.tabview.add("Other Settings")
    self.other_settings_tab.rowconfigure(0, weight=1)
    self.other_settings_tab.columnconfigure(0, weight=1)
    if "webdav_config" not in self.loaded_config:
        self.loaded_config["webdav_config"] = {
            "webdav_url": "",
            "webdav_username": "",
            "webdav_password": ""
        }

    self.webdav_url_var.set(self.loaded_config["webdav_config"].get("webdav_url", ""))
    self.webdav_username_var.set(self.loaded_config["webdav_config"].get("webdav_username", ""))
    self.webdav_password_var.set(self.loaded_config["webdav_config"].get("webdav_password", ""))


    def save_webdav_settings():
        self.loaded_config["webdav_config"]["webdav_url"] = self.webdav_url_var.get().strip()
        self.loaded_config["webdav_config"]["webdav_username"] = self.webdav_username_var.get().strip()
        self.loaded_config["webdav_config"]["webdav_password"] = self.webdav_password_var.get().strip()
        save_config(self.loaded_config, self.config_file)


    def set_webdav_buttons_state(state):
        for btn in (test_btn, save_btn, reset_btn):
            btn.configure(state=state)

    def run_webdav_task(worker, success_message, success_callback=None):
        set_webdav_buttons_state("disabled")

        def finish(success, result):
            set_webdav_buttons_state("normal")
            if success:
                if success_callback:
                    success_callback(result)
                messagebox.showinfo("成功", success_message)
            else:
                messagebox.showerror("错误", f"发生未知错误: {result}")

        def task():
            try:
                result = worker()
            except Exception as e:
                print(e)
                self.master.after(0, lambda err=e: finish(False, err))
                return
            self.master.after(0, lambda res=result: finish(True, res))

        threading.Thread(target=task, daemon=True).start()

    def test_webdav_connection():
        webdav_url = self.webdav_url_var.get().strip()
        username = self.webdav_username_var.get().strip()
        password = self.webdav_password_var.get().strip()

        def worker():
            client = WebDAVClient(webdav_url, username, password)
            client.list_directory()
            return True

        run_webdav_task(worker, "WebDAV 连接成功！", lambda _result: save_webdav_settings())

    def backup_to_webdav():
        save_webdav_settings()
        webdav_url = self.webdav_url_var.get().strip()
        username = self.webdav_username_var.get().strip()
        password = self.webdav_password_var.get().strip()
        config_file = self.config_file

        def worker():
            target_dir = "AI_Novel_Generator"
            client = WebDAVClient(webdav_url, username, password)
            if not client.ensure_directory_exists(target_dir):
                if not client.create_directory(target_dir):
                    raise RuntimeError("创建远程备份目录失败")
            if not client.upload_file(config_file, f"{target_dir}/config.json"):
                raise RuntimeError("上传配置文件失败")
            return True

        run_webdav_task(worker, "配置备份成功！")

    def restore_from_webdav():
        webdav_url = self.webdav_url_var.get().strip()
        username = self.webdav_username_var.get().strip()
        password = self.webdav_password_var.get().strip()
        config_file = self.config_file

        def worker():
            target_dir = "AI_Novel_Generator"
            client = WebDAVClient(webdav_url, username, password)
            if not client.download_file(f"{target_dir}/config.json", config_file):
                raise RuntimeError("配置恢复失败，未修改本地配置文件。")
            return load_config(config_file)

        def on_success(loaded_config):
            self.loaded_config = loaded_config

        run_webdav_task(worker, "配置恢复成功！", on_success)




    dav_frame = ctk.CTkFrame(self.other_settings_tab)
    dav_frame.pack(padx=20, pady=20, fill="x")

    dav_title = ctk.CTkLabel(dav_frame, text="webdav设置", font=("Microsoft YaHei", 16, "bold"))
    dav_title.pack(anchor="w", padx=5, pady=(0, 5))
    dav_warp_frame = ctk.CTkFrame(dav_frame, corner_radius=10, border_width=2, border_color="gray")
    dav_warp_frame.pack(fill="x", padx=5)
    dav_warp_frame.columnconfigure(1, weight=1)

    

    create_label_with_help(self, parent=dav_warp_frame, label_text="Webdav URL", tooltip_key="webdav_url",row=0, column=0, font=("Microsoft YaHei", 12), sticky="w")
    dav_url_entry = ctk.CTkEntry(dav_warp_frame, textvariable=self.webdav_url_var, font=("Microsoft YaHei", 12))
    dav_url_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")

    create_label_with_help(self, parent=dav_warp_frame, label_text="Webdav用户名", tooltip_key="webdav_username",row=1, column=0, font=("Microsoft YaHei", 12), sticky="w")
    dav_username_entry = ctk.CTkEntry(dav_warp_frame, textvariable=self.webdav_username_var, font=("Microsoft YaHei", 12))
    dav_username_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

    create_label_with_help(self, parent=dav_warp_frame, label_text="Webdav密码", tooltip_key="webdav_password",row=2, column=0, font=("Microsoft YaHei", 12), sticky="w")
    dav_password_entry = ctk.CTkEntry(dav_warp_frame, textvariable=self.webdav_password_var, font=("Microsoft YaHei", 12), show="*")
    dav_password_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")

    button_frame = ctk.CTkFrame(dav_warp_frame)
    button_frame.grid(row=3, column=0, columnspan=2, padx=5, pady=10, sticky="w")
    
    # 测试连接按钮
    test_btn = ctk.CTkButton(button_frame, text="测试连接", font=("Microsoft YaHei", 12),
                            command=test_webdav_connection)
    test_btn.pack(side="left", padx=5)
    
    # 保存设置按钮
    save_btn = ctk.CTkButton(button_frame, text="备份", font=("Microsoft YaHei", 12),
                            command=backup_to_webdav)
    save_btn.pack(side="left", padx=5)
    
    # 重置按钮
    reset_btn = ctk.CTkButton(button_frame, text="恢复", font=("Microsoft YaHei", 12),
                             command=restore_from_webdav)
    reset_btn.pack(side="left", padx=5)







from webdav_client import WebDAVClient
