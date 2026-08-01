"""Novel Generator 启动器。"""

import atexit
import codecs
import errno
import locale
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Literal, TextIO

import customtkinter as ctk

from backend.runtime import (
    BACKEND_PORT_ENV_VAR,
    DEFAULT_BACKEND_PORT,
    DEFAULT_FRONTEND_PORT,
    FRONTEND_PORT_ENV_VAR,
    get_backend_port,
    get_frontend_port,
)
from launcher_settings import (
    load_launcher_settings,
    save_launcher_settings,
    validate_launcher_settings,
)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
LOGS_DIR = BASE_DIR / "logs"

LOG_READ_CHUNK_SIZE = 4096
LOG_FLUSH_INTERVAL_MS = 80
LOG_FLUSH_BATCH_CHARS = 16000
MAX_LOG_CHARS = 220_000
RETAIN_LOG_CHARS = 160_000
MAX_PENDING_LOG_CHARS = 160_000
RETAIN_PENDING_LOG_CHARS = 90_000
MAX_LOG_FILES = 100

ServiceState = Literal["stopped", "starting", "running", "stopping"]

_LOG_DIR_LOCK = threading.Lock()


def is_port_in_use(port: int) -> bool:
    """检查本机回环地址上是否已有服务接受连接。

    Args:
        port: 要检查的 TCP 端口。

    Returns:
        有服务监听时返回 True，否则返回 False。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def get_port_bind_error(port: int) -> str | None:
    """通过实际绑定探测端口是否可供新服务使用。

    Args:
        port: 要探测的 TCP 端口。

    Returns:
        端口可绑定时返回 None；被占用、系统保留或无权限时返回中文错误说明。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror == 10013 or exc.errno in {errno.EACCES, errno.EPERM}:
                return f"端口 {port} 被 Windows 保留或当前账户无绑定权限，请在启动器中更换端口"
            if winerror == 10048 or exc.errno == errno.EADDRINUSE:
                return f"端口 {port} 已被其他服务占用，请先确认该服务或在启动器中更换端口"
            return f"端口 {port} 无法绑定：{exc}"
    return None


def ensure_logs_dir() -> Path:
    with _LOG_DIR_LOCK:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def build_log_file_path(service_key: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return ensure_logs_dir() / f"{timestamp}_{service_key}.log"


def cleanup_old_log_files() -> None:
    """最多保留最近的日志文件，避免目录无限增长。"""
    with _LOG_DIR_LOCK:
        if not LOGS_DIR.exists():
            return

        log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime)
        overflow = len(log_files) - MAX_LOG_FILES
        if overflow <= 0:
            return

        for path in log_files[:overflow]:
            try:
                path.unlink()
            except OSError:
                # 打开的日志文件或系统锁定文件直接跳过，避免影响当前启动流程。
                continue


class AdaptiveStreamDecoder:
    """增量解码子进程输出，兼容 UTF-8 与常见 Windows 编码。"""

    def __init__(self):
        seen: set[str] = set()
        self._encodings: list[str] = []
        for encoding in ("utf-8", "gbk", locale.getpreferredencoding(False)):
            normalized = (encoding or "").lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                self._encodings.append(encoding)

        self._buffer = bytearray()
        self._encoding: str | None = None
        self._decoder = None

    def _bind_decoder(self, encoding: str) -> None:
        self._encoding = encoding
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")

    def decode(self, chunk: bytes) -> str:
        if not chunk:
            return ""

        if self._decoder is not None:
            return self._decoder.decode(chunk, final=False)

        self._buffer.extend(chunk)
        for encoding in self._encodings:
            try:
                self._buffer.decode(encoding)
            except UnicodeDecodeError:
                continue

            self._bind_decoder(encoding)
            decoded = self._decoder.decode(bytes(self._buffer), final=False)
            self._buffer.clear()
            return decoded

        if len(self._buffer) < LOG_READ_CHUNK_SIZE * 2:
            return ""

        self._bind_decoder(self._encodings[0])
        decoded = self._decoder.decode(bytes(self._buffer), final=False)
        self._buffer.clear()
        return decoded

    def flush(self) -> str:
        if self._decoder is not None:
            tail = self._decoder.decode(b"", final=True)
            if self._buffer:
                tail += bytes(self._buffer).decode(self._encoding or self._encodings[0], errors="replace")
                self._buffer.clear()
            return tail

        if not self._buffer:
            return ""

        for encoding in self._encodings:
            try:
                decoded = self._buffer.decode(encoding)
                self._buffer.clear()
                return decoded
            except UnicodeDecodeError:
                continue

        decoded = self._buffer.decode(self._encodings[0], errors="replace")
        self._buffer.clear()
        return decoded


class ServicePanel(ctk.CTkFrame):
    """单个服务的控制面板与日志窗口。"""

    def __init__(
        self,
        master,
        service_key: str,
        title: str,
        command: list[str],
        cwd: Path,
        port: int,
        url: str,
        accent_color: str,
        env: dict[str, str] | None = None,
    ):
        super().__init__(
            master,
            corner_radius=18,
            border_width=1,
            border_color=("gray82", "gray22"),
            fg_color=("gray97", "gray10"),
        )
        self.service_key = service_key
        self.command = command
        self.cwd = str(cwd)
        self.env = env or {}
        self.port = port
        self.url = url
        self.accent_color = accent_color

        self._proc: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None
        self._state: ServiceState = "stopped"
        self._shutdown = False
        self._state_lock = threading.Lock()

        self._log_chunks: deque[str] = deque()
        self._pending_char_count = 0
        self._displayed_char_count = 0
        self._log_lock = threading.Lock()

        self._event_queue: deque[tuple[str, object]] = deque()
        self._event_lock = threading.Lock()

        self._log_file: TextIO | None = None
        self._log_file_path: Path | None = None
        self._log_file_lock = threading.Lock()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.grid_columnconfigure(1, weight=1)

        title_group = ctk.CTkFrame(header, fg_color="transparent")
        title_group.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            title_group,
            text=title,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        self.status_badge = ctk.CTkLabel(
            title_group,
            text="已停止",
            corner_radius=999,
            padx=10,
            pady=4,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.status_badge.pack(side="left", padx=(10, 0))

        self.header_actions = ctk.CTkFrame(header, fg_color="transparent")
        self.header_actions.grid(row=0, column=1, sticky="e")

        self.log = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=("gray99", "gray7"),
            text_color=("gray15", "gray90"),
            corner_radius=14,
            border_width=1,
            border_color=("gray84", "gray18"),
            state="disabled",
            wrap="word",
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)

        primary_actions = ctk.CTkFrame(footer, fg_color="transparent")
        primary_actions.grid(row=0, column=0, sticky="w")

        secondary_actions = ctk.CTkFrame(footer, fg_color="transparent")
        secondary_actions.grid(row=0, column=1, sticky="e")

        self.start_btn = ctk.CTkButton(
            primary_actions,
            text="启动",
            width=92,
            height=34,
            fg_color=accent_color,
            hover_color=self._blend_color(accent_color, 0.86),
            command=self.start,
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            primary_actions,
            text="停止",
            width=92,
            height=34,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self.stop,
            state="disabled",
        )
        self.stop_btn.pack(side="left")

        self.clear_btn = ctk.CTkButton(
            secondary_actions,
            text="清空",
            width=92,
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color=("gray74", "gray28"),
            text_color=("gray30", "gray72"),
            hover_color=("gray86", "gray18"),
            command=self.clear_log,
        )
        self.clear_btn.pack(side="left", padx=(0, 8))

        self.open_btn = ctk.CTkButton(
            secondary_actions,
            text="打开",
            width=92,
            height=34,
            fg_color="transparent",
            border_width=1,
            border_color=("gray74", "gray28"),
            text_color=("gray30", "gray72"),
            hover_color=("gray86", "gray18"),
            command=lambda: webbrowser.open(self.url),
        )
        self.open_btn.pack(side="left")

        self.set_command(command)
        self._apply_status("stopped")
        self.after(LOG_FLUSH_INTERVAL_MS, self._flush_ui_updates)

    @staticmethod
    def _blend_color(color: str, factor: float) -> str:
        color = color.lstrip("#")
        if len(color) != 6:
            return "#1f2937"

        channels = []
        for index in (0, 2, 4):
            value = int(color[index:index + 2], 16)
            channels.append(max(0, min(255, int(value * factor))))
        return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"

    def _format_command(self, command: list[str]) -> str:
        if not command:
            return "-"
        if sys.platform == "win32":
            return subprocess.list2cmdline(command)
        return shlex.join(command)

    def _queue_event(self, event_type: str, payload=None) -> None:
        with self._event_lock:
            self._event_queue.append((event_type, payload))

    def _queue_log(self, text: str) -> None:
        if not text:
            return

        with self._log_lock:
            self._log_chunks.append(text)
            self._pending_char_count += len(text)

            # 仅裁剪 UI 待渲染缓存，磁盘日志始终保留完整输出。
            if self._pending_char_count <= MAX_PENDING_LOG_CHARS:
                return

            while self._log_chunks and self._pending_char_count > RETAIN_PENDING_LOG_CHARS:
                removed = self._log_chunks.popleft()
                self._pending_char_count -= len(removed)

            notice = "\n[Launcher] 界面仅保留最近日志，完整输出请查看 /logs。\n"
            self._log_chunks.appendleft(notice)
            self._pending_char_count += len(notice)

    def _pop_log_batch(self, max_chars: int) -> str:
        batch: list[str] = []
        batch_chars = 0

        with self._log_lock:
            while self._log_chunks and batch_chars < max_chars:
                chunk = self._log_chunks.popleft()
                batch.append(chunk)
                batch_chars += len(chunk)
                self._pending_char_count -= len(chunk)

        return "".join(batch)

    def _apply_status(self, state: ServiceState) -> None:
        styles = {
            "stopped": {
                "label": "已停止",
                "badge_fg": ("gray86", "gray20"),
                "badge_text": ("gray40", "gray70"),
                "start": "normal",
                "stop": "disabled",
                "border": ("gray82", "gray22"),
            },
            "starting": {
                "label": "启动中",
                "badge_fg": ("#fef3c7", "#4a3410"),
                "badge_text": ("#92400e", "#fbbf24"),
                "start": "disabled",
                "stop": "disabled",
                "border": "#f59e0b",
            },
            "running": {
                "label": "运行中",
                "badge_fg": ("#dcfce7", "#0f3320"),
                "badge_text": (self.accent_color, self.accent_color),
                "start": "disabled",
                "stop": "normal",
                "border": self.accent_color,
            },
            "stopping": {
                "label": "停止中",
                "badge_fg": ("#ffedd5", "#46200f"),
                "badge_text": ("#c2410c", "#fb923c"),
                "start": "disabled",
                "stop": "disabled",
                "border": "#f97316",
            },
        }
        style = styles[state]
        self.status_badge.configure(
            text=style["label"],
            fg_color=style["badge_fg"],
            text_color=style["badge_text"],
        )
        self.start_btn.configure(state=style["start"])
        self.stop_btn.configure(state=style["stop"])
        self.configure(border_color=style["border"])

    def _should_autoscroll(self) -> bool:
        try:
            _, bottom = self.log.yview()
            return bottom >= 0.995
        except Exception:
            return True

    def _append_to_log(self, text: str) -> None:
        should_autoscroll = self._should_autoscroll()
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self._displayed_char_count += len(text)

        if self._displayed_char_count > MAX_LOG_CHARS:
            trim_chars = self._displayed_char_count - RETAIN_LOG_CHARS
            self.log.delete("1.0", f"1.0 + {trim_chars} chars")

            trim_notice = "[Launcher] 界面历史日志已裁剪，完整输出请查看 /logs。\n"
            self.log.insert("1.0", trim_notice)
            self._displayed_char_count = RETAIN_LOG_CHARS + len(trim_notice)

        if should_autoscroll:
            self.log.see("end")
        self.log.configure(state="disabled")

    def _flush_ui_updates(self) -> None:
        if not self.winfo_exists():
            return

        with self._event_lock:
            events = list(self._event_queue)
            self._event_queue.clear()

        for event_type, payload in events:
            if event_type == "status":
                self._apply_status(payload)

        batch = self._pop_log_batch(LOG_FLUSH_BATCH_CHARS)
        if batch:
            self._append_to_log(batch)

        self.after(LOG_FLUSH_INTERVAL_MS, self._flush_ui_updates)

    def _open_log_file(self) -> None:
        self._close_log_file()

        try:
            log_path = build_log_file_path(self.service_key)
            log_file = log_path.open("a", encoding="utf-8", buffering=1)
        except Exception as exc:
            self._queue_log(f"[Launcher] 创建日志文件失败: {exc}\n")
            return

        with self._log_file_lock:
            self._log_file = log_file
            self._log_file_path = log_path

        cleanup_old_log_files()
        self._write_file_only(f"[Launcher] started_at={datetime.now().isoformat(timespec='seconds')}\n")
        self._write_file_only(f"[Launcher] service={self.service_key}\n")
        self._write_file_only(f"[Launcher] cwd={self.cwd}\n")
        self._write_file_only(f"[Launcher] command={self._format_command(self.command)}\n\n")

    def _write_file_only(self, text: str) -> None:
        if not text:
            return

        with self._log_file_lock:
            if self._log_file is None:
                return

            try:
                self._log_file.write(text)
                self._log_file.flush()
            except OSError:
                return

    def _close_log_file(self) -> None:
        with self._log_file_lock:
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except OSError:
                    pass
            self._log_file = None
            self._log_file_path = None

    def write_log(self, text: str) -> None:
        self._queue_log(text)
        self._write_file_only(text)

    def _read_stream(self, stream, stream_name: str) -> None:
        decoder = AdaptiveStreamDecoder()

        try:
            while True:
                # 按块读取 stdout/stderr，避免长文本无换行时把生产进程卡在管道缓冲区。
                if hasattr(stream, "read1"):
                    chunk = stream.read1(LOG_READ_CHUNK_SIZE)
                else:
                    chunk = stream.read(LOG_READ_CHUNK_SIZE)

                if not chunk:
                    break

                decoded = decoder.decode(chunk)
                if decoded:
                    self.write_log(decoded)
        except Exception as exc:
            self.write_log(f"\n[Launcher Error] 读取 {stream_name} 流异常: {exc}\n")
        finally:
            tail = decoder.flush()
            if tail:
                self.write_log(tail)

    def _monitor_process(self, proc: subprocess.Popen) -> None:
        stdout_thread = threading.Thread(
            target=self._read_stream,
            args=(proc.stdout, "stdout"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._read_stream,
            args=(proc.stderr, "stderr"),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        code = proc.wait()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        self.write_log(f"\n--- 进程退出 (code {code}) ---\n")
        self._close_log_file()

        with self._state_lock:
            if self._proc is proc:
                self._proc = None
            self._state = "stopped"

        self._queue_event("status", "stopped")

    def _build_env(self) -> dict[str, str]:
        full_env = os.environ.copy()
        full_env.update(self.env)
        full_env["PYTHONIOENCODING"] = "utf-8"
        full_env["PYTHONUTF8"] = "1"
        return full_env

    def _fail_start(self, message: str) -> None:
        self.write_log(f"[ERROR] {message}\n")
        self._close_log_file()
        with self._state_lock:
            self._state = "stopped"
        self._queue_event("status", "stopped")

    def _start_worker(self) -> None:
        if self._shutdown:
            with self._state_lock:
                self._state = "stopped"
            self._queue_event("status", "stopped")
            return

        self._open_log_file()

        if is_port_in_use(self.port):
            self._fail_start(
                f"端口 {self.port} 已有服务监听。启动器不会自动终止未知进程，请复用现有服务或更换端口。"
            )
            return

        # connect_ex 无法识别 Windows 排除端口，必须实际 bind 才能提前捕获 WinError 10013。
        bind_error = get_port_bind_error(self.port)
        if bind_error:
            self._fail_start(bind_error)
            return

        kwargs = {
            "cwd": self.cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "bufsize": 0,
            "env": self._build_env(),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.Popen(self.command, **kwargs)
        except Exception as exc:
            self.write_log(f"[ERROR] 启动进程失败: {exc}\n")
            self._close_log_file()
            with self._state_lock:
                self._state = "stopped"
            self._queue_event("status", "stopped")
            return

        should_kill = False
        with self._state_lock:
            # 关闭信号与进程登记必须处于同一临界区，避免窗口关闭后漏掉刚创建的子进程。
            if self._shutdown:
                should_kill = True
            else:
                self._proc = proc
                self._state = "running"

        if should_kill:
            self._kill_proc(proc)
            self._close_log_file()
            with self._state_lock:
                self._state = "stopped"
            self._queue_event("status", "stopped")
            return

        self._queue_event("status", "running")
        self._monitor_thread = threading.Thread(target=self._monitor_process, args=(proc,), daemon=True)
        self._monitor_thread.start()

    def start(self) -> None:
        with self._state_lock:
            if self._state != "stopped":
                return
            self._state = "starting"

        self._apply_status("starting")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _kill_proc(self, proc: subprocess.Popen | None = None) -> None:
        target = proc
        if target is None:
            with self._state_lock:
                target = self._proc

        if target is None or target.poll() is not None:
            return

        pid = target.pid
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            target.wait(timeout=5)
        except Exception:
            try:
                target.kill()
            except Exception:
                pass

    def stop(self) -> None:
        with self._state_lock:
            proc = self._proc
            if self._state != "running" or proc is None:
                return
            self._state = "stopping"

        self._apply_status("stopping")
        threading.Thread(target=self._kill_proc, args=(proc,), daemon=True).start()

    def clear_log(self) -> None:
        with self._log_lock:
            self._log_chunks.clear()
            self._pending_char_count = 0

        self._displayed_char_count = 0
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def is_running(self) -> bool:
        with self._state_lock:
            return self._state == "running" and self._proc is not None and self._proc.poll() is None

    def is_stopped(self) -> bool:
        """返回服务面板是否处于可重新配置的停止状态。

        Args:
            无。

        Returns:
            仅当服务状态为 stopped 时返回 True。
        """
        with self._state_lock:
            return self._state == "stopped"

    def get_state(self) -> ServiceState:
        """返回服务面板当前的线程安全状态快照。

        Args:
            无。

        Returns:
            stopped、starting、running 或 stopping 之一。
        """
        with self._state_lock:
            return self._state

    def configure_runtime(self, *, port: int, url: str, env: dict[str, str]) -> bool:
        """在服务停止时同步更新监听端口、打开地址和子进程环境。

        Args:
            port: 服务下次启动时使用的监听端口。
            url: 面板“打开”按钮访问的地址。
            env: 启动子进程时注入的环境变量。

        Returns:
            更新成功返回 True；服务未停止时返回 False。
        """
        with self._state_lock:
            if self._state != "stopped":
                return False
            self.port = port
            self.url = url
            self.env = dict(env)
        return True

    def force_cleanup(self) -> None:
        with self._state_lock:
            self._shutdown = True
            proc = self._proc

        if proc is None or proc.poll() is not None:
            self._close_log_file()
            return

        self._kill_proc(proc)

    def set_command(self, command: list[str]) -> None:
        self.command = command


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Novel Generator Launcher")
        self.geometry("1380x840")
        self.minsize(1080, 680)
        self.configure(fg_color=("gray95", "gray6"))

        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        warnings: list[str] = []
        try:
            default_backend_port = get_backend_port()
        except ValueError as exc:
            default_backend_port = DEFAULT_BACKEND_PORT
            warnings.append(str(exc))
        try:
            default_frontend_port = get_frontend_port()
        except ValueError as exc:
            default_frontend_port = DEFAULT_FRONTEND_PORT
            warnings.append(str(exc))

        try:
            settings, settings_warning = load_launcher_settings(default_backend_port, default_frontend_port)
        except ValueError as exc:
            # 环境变量彼此冲突时仍要保证启动器能够打开，并继续尝试读取有效的持久化设置。
            warnings.append(f"环境变量端口配置无效：{exc}")
            settings, settings_warning = load_launcher_settings(DEFAULT_BACKEND_PORT, DEFAULT_FRONTEND_PORT)
        if settings_warning:
            warnings.append(settings_warning)
        self.backend_port = settings.backend_port
        self.frontend_port = settings.frontend_port
        self._applied_settings = settings

        toolbar = ctk.CTkFrame(
            self,
            corner_radius=18,
            border_width=1,
            border_color=("gray82", "gray22"),
            fg_color=("gray98", "gray11"),
        )
        toolbar.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
        toolbar.grid_columnconfigure(1, weight=1)

        action_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        action_group.grid(row=0, column=0, sticky="w", padx=16, pady=14)

        ctk.CTkButton(
            action_group,
            text="全部启动",
            width=120,
            height=36,
            fg_color="#15803d",
            hover_color="#166534",
            command=self.start_all,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            action_group,
            text="全部停止",
            width=120,
            height=36,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self.stop_all,
        ).pack(side="left")

        port_group = ctk.CTkFrame(
            toolbar,
            corner_radius=12,
            fg_color=("gray94", "gray16"),
        )
        port_group.grid(row=0, column=1, sticky="ew", padx=12, pady=8)
        port_group.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(port_group, text="后端端口", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=0, padx=(12, 6), pady=(8, 4)
        )
        self.backend_port_var = ctk.StringVar(value=str(self.backend_port))
        self.backend_port_entry = ctk.CTkEntry(
            port_group,
            width=78,
            height=30,
            justify="center",
            textvariable=self.backend_port_var,
        )
        self.backend_port_entry.grid(row=0, column=1, padx=(0, 12), pady=(8, 4))

        ctk.CTkLabel(port_group, text="前端端口", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=2, padx=(0, 6), pady=(8, 4)
        )
        self.frontend_port_var = ctk.StringVar(value=str(self.frontend_port))
        self.frontend_port_entry = ctk.CTkEntry(
            port_group,
            width=78,
            height=30,
            justify="center",
            textvariable=self.frontend_port_var,
        )
        self.frontend_port_entry.grid(row=0, column=3, padx=(0, 10), pady=(8, 4))

        self.apply_ports_button = ctk.CTkButton(
            port_group,
            text="应用端口",
            width=94,
            height=30,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._apply_port_settings,
        )
        self.apply_ports_button.grid(row=0, column=4, padx=(0, 12), pady=(8, 4))

        initial_status = "；".join(warnings) if warnings else "端口保存在当前用户配置中，服务停止时可修改"
        self.port_status_label = ctk.CTkLabel(
            port_group,
            text=initial_status,
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("#b45309", "#fbbf24") if warnings else ("gray42", "gray65"),
        )
        self.port_status_label.grid(row=1, column=0, columnspan=6, sticky="ew", padx=12, pady=(0, 7))

        self.theme_selector = ctk.CTkSegmentedButton(
            toolbar,
            values=["系统", "浅色", "深色"],
            height=36,
            command=self._on_theme_mode_change,
        )
        self.theme_selector.grid(row=0, column=2, sticky="e", padx=16, pady=14)
        self.theme_selector.set("系统")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1, uniform="service")
        content.grid_columnconfigure(1, weight=1, uniform="service")

        self.backend = ServicePanel(
            content,
            service_key="backend",
            title="Backend",
            command=[str(VENV_PYTHON), "main.py"],
            cwd=BASE_DIR,
            port=self.backend_port,
            url=f"http://127.0.0.1:{self.backend_port}/docs",
            accent_color="#15803d",
            env={
                BACKEND_PORT_ENV_VAR: str(self.backend_port),
                FRONTEND_PORT_ENV_VAR: str(self.frontend_port),
            },
        )
        self.backend.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.backend_debug = ctk.CTkCheckBox(
            self.backend.header_actions,
            text="调试日志",
            command=self._on_backend_debug_change,
        )
        self.backend_debug.pack(side="right")
        self._on_backend_debug_change()

        self.frontend = ServicePanel(
            content,
            service_key="frontend",
            title="Frontend",
            command=[],
            cwd=FRONTEND_DIR,
            port=self.frontend_port,
            url=f"http://127.0.0.1:{self.frontend_port}",
            accent_color="#2563eb",
            env={"NEXT_PUBLIC_API_BASE": f"http://127.0.0.1:{self.backend_port}"},
        )
        self.frontend.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.frontend_mode = ctk.CTkSegmentedButton(
            self.frontend.header_actions,
            values=["生产模式", "开发模式"],
            height=30,
            command=self._on_frontend_mode_change,
        )
        self.frontend_mode.pack(side="right")
        self.frontend_mode.set("生产模式")
        self._on_frontend_mode_change("生产模式", announce=False)

        # 所有启动入口都先应用输入框内容，防止界面显示值与实际监听端口不一致。
        self.backend.start_btn.configure(command=self._start_backend)
        self.frontend.start_btn.configure(command=self._start_frontend)
        self._port_controls_enabled: bool | None = None
        self._sync_port_controls()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        atexit.register(self._atexit_cleanup)

    def _set_port_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": ("gray42", "gray65"),
            "success": ("#15803d", "#86efac"),
            "warning": ("#b45309", "#fbbf24"),
            "error": ("#b91c1c", "#fca5a5"),
        }
        self.port_status_label.configure(text=message, text_color=colors[level])

    def _sync_port_controls(self) -> None:
        enabled = self.backend.is_stopped() and self.frontend.is_stopped()
        if enabled != self._port_controls_enabled:
            state = "normal" if enabled else "disabled"
            self.backend_port_entry.configure(state=state)
            self.frontend_port_entry.configure(state=state)
            self.apply_ports_button.configure(state=state)
            self._port_controls_enabled = enabled
        if self.winfo_exists():
            self.after(250, self._sync_port_controls)

    def _apply_port_settings(self) -> bool:
        if not self.backend.is_stopped() or not self.frontend.is_stopped():
            self._set_port_status("请先停止前后端服务，再修改端口", "error")
            return False

        try:
            settings = validate_launcher_settings(self.backend_port_var.get(), self.frontend_port_var.get())
        except ValueError as exc:
            self._set_port_status(str(exc), "error")
            return False

        for label, port in (("后端", settings.backend_port), ("前端", settings.frontend_port)):
            if is_port_in_use(port):
                self._set_port_status(
                    f"{label}端口 {port} 已有外部服务监听；请先停止它，启动器不会自动终止未知进程",
                    "error",
                )
                return False
            bind_error = get_port_bind_error(port)
            if bind_error:
                self._set_port_status(bind_error, "error")
                return False

        try:
            save_launcher_settings(settings)
        except OSError as exc:
            self._set_port_status(f"端口设置保存失败：{exc}", "error")
            return False

        backend_configured = self.backend.configure_runtime(
            port=settings.backend_port,
            url=f"http://127.0.0.1:{settings.backend_port}/docs",
            env={
                BACKEND_PORT_ENV_VAR: str(settings.backend_port),
                FRONTEND_PORT_ENV_VAR: str(settings.frontend_port),
            },
        )
        frontend_configured = self.frontend.configure_runtime(
            port=settings.frontend_port,
            url=f"http://127.0.0.1:{settings.frontend_port}",
            env={"NEXT_PUBLIC_API_BASE": f"http://127.0.0.1:{settings.backend_port}"},
        )
        if not backend_configured or not frontend_configured:
            self._set_port_status("服务状态已变化，请停止服务后重试", "error")
            return False

        self.backend_port = settings.backend_port
        self.frontend_port = settings.frontend_port
        self._applied_settings = settings
        self._on_frontend_mode_change(self.frontend_mode.get(), announce=False)
        self._set_port_status(
            f"设置已保存：后端 {self.backend_port} / 前端 {self.frontend_port}",
            "success",
        )
        return True

    def _ensure_port_settings_applied(self) -> bool:
        try:
            requested = validate_launcher_settings(self.backend_port_var.get(), self.frontend_port_var.get())
        except ValueError as exc:
            self._set_port_status(str(exc), "error")
            return False
        if requested == self._applied_settings:
            return True
        return self._apply_port_settings()

    def _service_can_start(self, service: ServicePanel, label: str) -> bool:
        state = service.get_state()
        if state == "stopping":
            self._set_port_status(f"{label}服务正在停止，请等待状态变为已停止后再启动", "error")
            return False
        if state in {"starting", "running"}:
            return True
        if is_port_in_use(service.port):
            self._set_port_status(
                f"{label}端口 {service.port} 已有外部服务监听；请先停止它或更换端口",
                "error",
            )
            return False
        bind_error = get_port_bind_error(service.port)
        if bind_error:
            self._set_port_status(bind_error, "error")
            return False
        return True

    def _on_theme_mode_change(self, mode: str) -> None:
        mapping = {
            "系统": "system",
            "浅色": "light",
            "深色": "dark",
        }
        ctk.set_appearance_mode(mapping[mode])

    def _on_frontend_mode_change(self, mode: str, *, announce: bool = True) -> None:
        run_args = f"--hostname 127.0.0.1 --port {self.frontend_port}"
        if mode == "生产模式":
            command = f"{self.npm_cmd} run build && {self.npm_cmd} run start -- {run_args}"
            if sys.platform == "win32":
                cmd = ["cmd.exe", "/c", command]
            else:
                cmd = ["sh", "-c", command]
        else:
            cmd = [
                self.npm_cmd,
                "run",
                "dev",
                "--",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(self.frontend_port),
            ]

        self.frontend.set_command(cmd)
        if announce and self.frontend.is_running():
            self.frontend.write_log("[INFO] 前端运行模式已切换，重启前端后生效。\n")

    def _build_backend_command(self) -> list[str]:
        cmd = [str(VENV_PYTHON), "main.py"]
        if self.backend_debug.get():
            cmd.append("--debug")
        return cmd

    def _on_backend_debug_change(self) -> None:
        self.backend.set_command(self._build_backend_command())
        if self.backend.is_running():
            self.backend.write_log("[INFO] 后端调试模式已切换，重启后端后生效。\n")

    def _start_backend(self) -> None:
        if not self._ensure_port_settings_applied():
            return
        if self._service_can_start(self.backend, "后端"):
            self.backend.start()

    def _start_frontend(self) -> None:
        if not self._ensure_port_settings_applied():
            return
        if not self._service_can_start(self.backend, "后端"):
            return
        if self._service_can_start(self.frontend, "前端"):
            self.frontend.start()

    def start_all(self) -> None:
        if not self._ensure_port_settings_applied():
            return
        if not self._service_can_start(self.backend, "后端"):
            return
        if not self._service_can_start(self.frontend, "前端"):
            return
        self.backend.start()
        self.frontend.start()

    def stop_all(self) -> None:
        self.backend.stop()
        self.frontend.stop()

    def _on_close(self) -> None:
        self.backend.force_cleanup()
        self.frontend.force_cleanup()
        self.destroy()

    def _atexit_cleanup(self) -> None:
        self.backend.force_cleanup()
        self.frontend.force_cleanup()


if __name__ == "__main__":
    App().mainloop()
