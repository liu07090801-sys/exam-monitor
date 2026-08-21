"""
监考端服务 - 屏幕监控中心
运行在监考老师机器上，接收所有考试端数据并显示仪表盘。

改进:
  - Token 认证：客户端必须携带正确令牌才能注册
  - 二进制帧接收：截图不再 base64-in-JSON，直接收 JPEG bytes
  - 线程安全：所有共享状态加 threading.Lock
  - SQLite 持久化：事件日志和截图索引入库
  - 截图自动清理：按 retention_days 定期清理
  - logging 模块：替代 print，分级输出
  - 导出路径固定到 exports/ 目录
  - 截图历史支持翻页浏览

用法: python server.py
"""

import asyncio
import base64
import csv
import json
import logging
import os
import sqlite3
import ssl
import threading
import time
import tkinter as tk
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import websockets
from PIL import Image, ImageTk

import config

# ─── 日志 ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")

# ─── 导出目录 ───
EXPORT_DIR = Path(__file__).parent / "exports"
EXPORT_DIR.mkdir(exist_ok=True)


# ════════════════════════════════════════════════════════════════
#  SQLite 持久层
# ════════════════════════════════════════════════════════════════

def build_server_ssl_context():
    """按配置构建服务端 SSLContext；未启用 TLS 时返回 None。"""
    if not config.USE_TLS:
        return None
    cert_file = config.BASE_DIR / config.TLS_CERT_FILE
    key_file = config.BASE_DIR / config.TLS_KEY_FILE
    if not cert_file.exists() or not key_file.exists():
        raise FileNotFoundError(
            f"TLS 已启用但证书文件不存在: {cert_file} / {key_file}。"
            "请生成证书或在 config.yaml 中关闭 tls.enabled。"
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))
    return context


def screenshot_filename(now: datetime | None = None) -> str:
    """生成服务端截图文件名（时-分-秒_微秒），同一秒内多考生/多帧不冲突。"""
    now = now or datetime.now()
    return now.strftime("%H%M%S_%f") + ".jpg"


class Database:
    """线程安全的 SQLite 封装（单连接 + 锁）。"""

    def __init__(self, db_path: str = "monitor.db"):
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    client_id TEXT    NOT NULL,
                    kind      TEXT    NOT NULL,
                    detail    TEXT    DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS screenshots (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    client_id TEXT    NOT NULL,
                    path      TEXT    NOT NULL,
                    hash_hex  TEXT    DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
                CREATE INDEX IF NOT EXISTS idx_ss_ts ON screenshots(ts);
            """)

    def add_event(self, client_id: str, kind: str, detail: str = ""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, client_id, kind, detail) VALUES (?,?,?,?)",
                (time.time(), client_id, kind, detail),
            )
            self._conn.commit()

    def add_screenshot(self, client_id: str, path: str, hash_hex: str = ""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO screenshots (ts, client_id, path, hash_hex) VALUES (?,?,?,?)",
                (time.time(), client_id, path, hash_hex),
            )
            self._conn.commit()

    def export_events_csv(self, filepath: str):
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, client_id, kind, detail FROM events ORDER BY ts"
            ).fetchall()
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["时间", "考生", "类型", "详情"])
            for ts, cid, kind, detail in rows:
                dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                w.writerow([dt, cid, kind, detail])

    def update_last_screenshot_hash(self, client_id: str, hash_hex: str):
        """把客户端随后发来的感知哈希回填到最近一条截图索引（存证用）。"""
        if not hash_hex:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE screenshots SET hash_hex=? WHERE id = ("
                "  SELECT id FROM screenshots WHERE client_id=? ORDER BY id DESC LIMIT 1)",
                (hash_hex, client_id),
            )
            self._conn.commit()

    def enforce_screenshot_cap(self, limit: int) -> int:
        """落盘截图总量上限：超过 limit 时删除最旧记录和文件，返回删除的文件数。"""
        if limit <= 0:
            return 0
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM screenshots").fetchone()[0]
            excess = count - limit
            if excess <= 0:
                return 0
            rows = self._conn.execute(
                "SELECT path FROM screenshots ORDER BY id ASC LIMIT ?", (excess,)
            ).fetchall()
            self._conn.execute(
                "DELETE FROM screenshots WHERE id IN ("
                "SELECT id FROM screenshots ORDER BY id ASC LIMIT ?)", (excess,)
            )
            self._conn.commit()
        removed = 0
        for (path,) in rows:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        log.info("截图总量超过上限 %d，已清理最旧 %d 条", limit, removed)
        return removed

    def cleanup_old(self, retention_days: int):
        """删除超过 retention_days 天的截图记录和文件。"""
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM screenshots WHERE ts < ?", (cutoff,)
            ).fetchall()
            self._conn.execute("DELETE FROM screenshots WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()
        for (path,) in rows:
            try:
                os.remove(path)
            except OSError:
                pass
        if rows:
            log.info("已清理 %d 条过期记录和截图", len(rows))

    def close(self):
        self._conn.close()


# ════════════════════════════════════════════════════════════════
#  客户端信息
# ════════════════════════════════════════════════════════════════

class ClientInfo:
    """单个考试端连接信息。"""

    def __init__(self, client_id: str, hostname: str = ""):
        self.client_id = client_id
        self.hostname = hostname
        self.ws = None
        self.last_heartbeat = time.time()
        self.alert_count = 0
        self.screenshot_count = 0
        self.last_screenshot_b64 = ""   # 最新全尺寸 base64（供预览）
        self.last_hash = ""
        self.last_screenshot_ts = 0.0    # 最近一次收到截图帧的时间
        self.freeze_logged = False
        self.alert_history: list[tuple[float, str, str, str]] = []  # (ts, target, reason, url)
        self.screenshot_history: list[tuple[float, str]] = []  # (ts, thumbnail_b64)
        self.is_online = True
        self.last_alert_target = ""
        self.last_alert_time = 0.0   # 最近一次报警时间，用于状态灯红色保持窗口

    def update_heartbeat(self):
        self.last_heartbeat = time.time()
        self.is_online = True

    def check_online(self, timeout: int) -> bool:
        if time.time() - self.last_heartbeat > timeout:
            self.is_online = False
        return self.is_online

    def freeze_check(self, freeze_timeout: float, now: float | None = None) -> bool:
        """在线但超过 freeze_timeout 秒未收到截图帧时，首次返回 True（防重复报警）。

        注意：冻结指“客户端不再发帧”，不是“相邻帧哈希相同”——静止画面
        由客户端的 force_send_interval 定期补帧，属正常现象。
        """
        now = time.time() if now is None else now
        if not self.is_online or self.last_screenshot_ts <= 0:
            return False
        if now - self.last_screenshot_ts > freeze_timeout:
            if not self.freeze_logged:
                self.freeze_logged = True
                return True
            return False
        self.freeze_logged = False
        return False


# ════════════════════════════════════════════════════════════════
#  监考服务主类
# ════════════════════════════════════════════════════════════════

class MonitorServer:
    def __init__(self):
        self.clients: dict[str, ClientInfo] = {}
        self._lock = threading.Lock()  # 线程安全锁
        self.running = False
        self.loop = None
        self.db = Database(config.DB_FILE)
        self._alert_hide_after = None
        # GUI
        self.root = None
        self.client_frames = {}
        self.alert_banner = None
        self.log_text = None
        self.event_log = []  # 内存中的近期日志（GUI 用）

    # ──────────── GUI ────────────

    def setup_gui(self):
        self.root = tk.Tk()
        self.root.title("监考中心 - 屏幕监控系统")
        self.root.configure(bg=config.COLOR_BG)
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)

        # 顶部标题栏
        header = tk.Frame(self.root, bg=config.COLOR_CARD_BG, height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(
            header, text="监考中心", font=("Microsoft YaHei", 18, "bold"),
            bg=config.COLOR_CARD_BG, fg=config.COLOR_TEXT,
        ).pack(side=tk.LEFT, padx=15)

        tk.Label(
            header,
            text=f"端口: {config.SERVER_PORT}  |  认证: {'启用' if config.AUTH_TOKEN else '关闭'}",
            font=("Microsoft YaHei", 11),
            bg=config.COLOR_CARD_BG, fg="#888888",
        ).pack(side=tk.LEFT, padx=15)

        self.status_count_label = tk.Label(
            header, text="在线: 0 | 报警: 0",
            font=("Microsoft YaHei", 11),
            bg=config.COLOR_CARD_BG, fg=config.COLOR_TEXT,
        )
        self.status_count_label.pack(side=tk.RIGHT, padx=15)

        export_btn = tk.Button(
            header, text="导出日志", font=("Microsoft YaHei", 10),
            command=self.export_log, bg="#2196F3", fg="white",
            relief=tk.FLAT, padx=10, pady=3,
        )
        export_btn.pack(side=tk.RIGHT, padx=5)

        stop_btn = tk.Button(
            header, text="结束监考", font=("Microsoft YaHei", 10),
            command=self.stop_all_clients, bg="#F44336", fg="white",
            relief=tk.FLAT, padx=10, pady=3,
        )
        stop_btn.pack(side=tk.RIGHT, padx=5)

        # 报警横幅
        self.alert_banner = tk.Frame(self.root, bg=config.COLOR_ALERT, height=35)
        tk.Label(
            self.alert_banner, text="", font=("Microsoft YaHei", 12, "bold"),
            bg=config.COLOR_ALERT, fg="white",
        ).pack(expand=True)

        # 主内容区
        self.main_frame = tk.Frame(self.root, bg=config.COLOR_BG)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左侧：考生网格
        left_frame = tk.Frame(self.main_frame, bg=config.COLOR_BG)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            left_frame, text="考生状态", font=("Microsoft YaHei", 13, "bold"),
            bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        ).pack(anchor=tk.W, pady=(0, 5))

        self.clients_canvas = tk.Canvas(left_frame, bg=config.COLOR_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.clients_canvas.yview)
        self.clients_inner = tk.Frame(self.clients_canvas, bg=config.COLOR_BG)

        self.clients_inner.bind(
            "<Configure>",
            lambda e: self.clients_canvas.configure(scrollregion=self.clients_canvas.bbox("all")),
        )
        self.clients_canvas.create_window((0, 0), window=self.clients_inner, anchor=tk.NW)
        self.clients_canvas.configure(yscrollcommand=scrollbar.set)
        self.clients_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 右侧：日志面板
        right_frame = tk.Frame(self.main_frame, bg=config.COLOR_BG, width=350)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_frame.pack_propagate(False)

        tk.Label(
            right_frame, text="事件日志", font=("Microsoft YaHei", 13, "bold"),
            bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        ).pack(anchor=tk.W, pady=(0, 5))

        self.log_text = tk.Text(
            right_frame, bg=config.COLOR_CARD_BG, fg=config.COLOR_TEXT,
            font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED,
            insertbackground="white",
        )
        self.log_text.tag_configure("info", foreground=config.COLOR_TEXT)
        self.log_text.tag_configure("alert", foreground=config.COLOR_ALERT)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        log_scroll = ttk.Scrollbar(right_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 截图预览面板
        preview_frame = tk.Frame(right_frame, bg=config.COLOR_BG, height=200)
        preview_frame.pack(fill=tk.X, pady=(10, 0))
        preview_frame.pack_propagate(False)

        self.preview_title = tk.Label(
            preview_frame, text="截图预览", font=("Microsoft YaHei", 10, "bold"),
            bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        )
        self.preview_title.pack(anchor=tk.W, pady=(0, 3))

        self.preview_label = tk.Label(
            preview_frame, text="点击考生卡片查看最新截图",
            font=("Microsoft YaHei", 9), bg=config.COLOR_CARD_BG,
            fg="#888888", anchor=tk.CENTER,
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True, ipady=30)
        self._preview_photo = None
        self._preview_client = None
        self.root.after(config.PREVIEW_REFRESH_MS, self.refresh_preview)

    # ──────────── 日志 ────────────

    def add_log(self, message: str, tag: str = "info", client_id: str = "", kind: str = "info"):
        ts_str = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts_str}] {message}\n"
        self.event_log.append((ts_str, message))
        # 持久化到 SQLite
        self.db.add_event(client_id or "-", kind, message)
        log.info(message)

        def _insert():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line, tag)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        self.root.after(0, _insert)

    def show_alert_banner(self, text: str):
        def _show():
            for child in self.alert_banner.winfo_children():
                child.config(text=f"⚠ {text}")
            self.alert_banner.pack(fill=tk.X, before=self.main_frame)
            if self._alert_hide_after is not None:
                try:
                    self.root.after_cancel(self._alert_hide_after)
                except Exception:
                    pass
            self._alert_hide_after = self.root.after(
                config.ALERT_FLASH_DURATION, self.hide_alert_banner
            )
        self.root.after(0, _show)

    def hide_alert_banner(self):
        self.alert_banner.pack_forget()

    # ──────────── 考生卡片 ────────────

    def create_client_card(self, client_id: str, hostname: str = ""):
        card = tk.Frame(self.clients_inner, bg=config.COLOR_CARD_BG, padx=10, pady=8)
        card.pack(fill=tk.X, padx=5, pady=3)

        indicator = tk.Canvas(card, width=12, height=12, bg=config.COLOR_CARD_BG, highlightthickness=0)
        indicator.pack(side=tk.LEFT, padx=(0, 8))
        dot = indicator.create_oval(2, 2, 10, 10, fill=config.COLOR_NORMAL, outline="")

        info_frame = tk.Frame(card, bg=config.COLOR_CARD_BG)
        info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        name_label = tk.Label(
            info_frame, text=f"考生: {client_id}",
            font=("Microsoft YaHei", 11, "bold"),
            bg=config.COLOR_CARD_BG, fg=config.COLOR_TEXT,
        )
        name_label.pack(anchor=tk.W)

        detail_label = tk.Label(
            info_frame, text=f"主机: {hostname} | 截屏: 0 | 报警: 0",
            font=("Microsoft YaHei", 9),
            bg=config.COLOR_CARD_BG, fg="#888888",
        )
        detail_label.pack(anchor=tk.W)

        view_btn = tk.Button(
            card, text="查看截图", font=("Microsoft YaHei", 9),
            command=lambda: self.view_screenshots(client_id),
            bg="#2196F3", fg="white", relief=tk.FLAT, padx=8, pady=2,
        )
        view_btn.pack(side=tk.RIGHT, padx=5)

        card.bind("<Button-1>", lambda e: self.preview_latest_screenshot(client_id))
        name_label.bind("<Button-1>", lambda e: self.preview_latest_screenshot(client_id))
        detail_label.bind("<Button-1>", lambda e: self.preview_latest_screenshot(client_id))

        self.client_frames[client_id] = {
            "card": card, "indicator": indicator, "dot": dot,
            "name_label": name_label, "detail_label": detail_label,
        }

    def update_client_card(self, client_id: str):
        if client_id not in self.client_frames:
            return
        with self._lock:
            if client_id not in self.clients:
                return
            client = self.clients[client_id]
            # 拷贝需要的字段，避免长时间持锁
            is_online = client.is_online
            alert_count = client.alert_count
            hostname = client.hostname
            screenshot_count = client.screenshot_count
            last_alert_time = client.last_alert_time
            last_alert_target = client.last_alert_target
        frame = self.client_frames[client_id]

        color = self.alert_color(is_online, last_alert_time)

        frame["indicator"].itemconfig(frame["dot"], fill=color)
        alert_txt = f" | 最近: {last_alert_target}" if last_alert_target else ""
        frame["detail_label"].config(
            text=f"主机: {hostname} | 截屏: {screenshot_count} | 报警: {alert_count}{alert_txt}"
        )

    @staticmethod
    def alert_color(is_online: bool, last_alert_time: float,
                    now: float | None = None, hold_seconds: int | None = None) -> str:
        """状态灯颜色：离线灰、在线绿、最近报警保持窗口内红色。

        红色按"最近一次报警时间"保持 alert_hold_seconds 秒后自动恢复绿色，
        避免一次误报后卡片永久变红；持续违规会不断刷新时间，保持红色。
        """
        now = time.time() if now is None else now
        hold_seconds = config.ALERT_HOLD_SECONDS if hold_seconds is None else hold_seconds
        if not is_online:
            return config.COLOR_OFFLINE
        if last_alert_time > 0 and now - last_alert_time < hold_seconds:
            return config.COLOR_ALERT
        return config.COLOR_NORMAL

    # ──────────── 截图工具 ────────────

    @staticmethod
    def _make_thumbnail(b64_str: str, max_width: int = 480, quality: int = 50) -> str:
        try:
            img = Image.open(BytesIO(base64.b64decode(b64_str)))
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, round(img.height * ratio)), Image.LANCZOS)
            buf = BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=quality)
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return b64_str

    @staticmethod
    def _b64_to_photo(b64_str: str, max_size=(360, 240)):
        try:
            img = Image.open(BytesIO(base64.b64decode(b64_str)))
            img.thumbnail(max_size)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def preview_latest_screenshot(self, client_id: str):
        if client_id not in self.clients:
            return
        self._preview_client = client_id
        self.refresh_preview()

    def refresh_preview(self):
        cid = self._preview_client
        if not cid or cid not in self.clients:
            self.root.after(config.PREVIEW_REFRESH_MS, self.refresh_preview)
            return
        client = self.clients[cid]

        if client.last_screenshot_b64:
            photo = self._b64_to_photo(client.last_screenshot_b64, (320, 180))
            if photo:
                self._preview_photo = photo
                self.preview_label.config(image=photo, text="", bg=config.COLOR_CARD_BG)
                self.preview_title.config(
                    text=f"最新截图 - {cid} ({datetime.now().strftime('%H:%M:%S')})"
                )
        else:
            self.preview_label.config(image="", text="暂无截图", bg=config.COLOR_CARD_BG)
            self.preview_title.config(text=f"截图预览 - {cid}")

        self.root.after(config.PREVIEW_REFRESH_MS, self.refresh_preview)

    # ──────────── 截图历史（翻页） ────────────

    def view_screenshots(self, client_id: str):
        if client_id not in self.clients:
            return
        client = self.clients[client_id]
        if not client.screenshot_history:
            messagebox.showinfo("截图记录", f"考生 {client_id} 暂无截图记录")
            return

        PAGE_SIZE = 20
        current_page = [0]  # 用列表包裹以便闭包修改

        win = tk.Toplevel(self.root)
        win.title(f"截图记录 - {client_id}")
        win.configure(bg=config.COLOR_BG)
        win.geometry("860x650")

        title_label = tk.Label(
            win, text="", font=("Microsoft YaHei", 13, "bold"),
            bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        )
        title_label.pack(pady=10)

        photos_ref = []  # 防止 GC

        canvas = tk.Canvas(win, bg=config.COLOR_BG, highlightthickness=0)
        scroll = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=config.COLOR_BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def total_pages():
            return max(1, (len(client.screenshot_history) + PAGE_SIZE - 1) // PAGE_SIZE)

        def render_page():
            for w in inner.winfo_children():
                w.destroy()
            photos_ref.clear()

            # 每次取最新列表：窗口打开期间新到的截图也能翻看到
            history = client.screenshot_history
            page = min(current_page[0], total_pages() - 1)
            current_page[0] = page
            start = max(len(history) - (page + 1) * PAGE_SIZE, 0)
            end = len(history) - page * PAGE_SIZE

            title_label.config(
                text=f"考生 {client_id} 的截图记录 — 第 {page + 1}/{total_pages()} 页 (共 {len(history)} 张)"
            )

            for ts, b64_img in reversed(history[start:end]):
                row = tk.Frame(inner, bg=config.COLOR_CARD_BG, padx=8, pady=5)
                row.pack(fill=tk.X, padx=10, pady=2)

                photo = self._b64_to_photo(b64_img, (240, 160))
                if photo:
                    photos_ref.append(photo)
                    tk.Label(row, image=photo, bg=config.COLOR_CARD_BG).pack(side=tk.LEFT, padx=5)
                else:
                    tk.Label(
                        row, text="[图片加载失败]", font=("Microsoft YaHei", 9),
                        bg=config.COLOR_CARD_BG, fg=config.COLOR_ALERT,
                    ).pack(side=tk.LEFT, padx=5)

                dt = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                tk.Label(
                    row, text=dt, font=("Consolas", 10),
                    bg=config.COLOR_CARD_BG, fg="#888888", width=8,
                ).pack(side=tk.LEFT, padx=5)

            update_page_label()

        nav_frame = tk.Frame(win, bg=config.COLOR_BG)
        nav_frame.pack(fill=tk.X, pady=5)

        def prev_page():
            if current_page[0] < total_pages() - 1:
                current_page[0] += 1
                render_page()

        def next_page():
            if current_page[0] > 0:
                current_page[0] -= 1
                render_page()

        tk.Button(
            nav_frame, text="◀ 上一页", font=("Microsoft YaHei", 10),
            command=prev_page, bg="#2196F3", fg="white", relief=tk.FLAT, padx=10,
        ).pack(side=tk.LEFT, padx=20)

        tk.Button(
            nav_frame, text="下一页 ▶", font=("Microsoft YaHei", 10),
            command=next_page, bg="#2196F3", fg="white", relief=tk.FLAT, padx=10,
        ).pack(side=tk.RIGHT, padx=20)

        page_label = tk.Label(
            nav_frame, text="", font=("Microsoft YaHei", 10),
            bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        )
        page_label.pack()

        def update_page_label():
            page_label.config(text=f"第 {current_page[0] + 1} / {total_pages()} 页")

        render_page()

    # ──────────── 导出 ────────────

    def _teacher_authorized(self) -> bool:
        """监考老师口令校验：未配置口令（空串）时直接放行。"""
        if not config.TEACHER_PASSWORD:
            return True
        pwd = simpledialog.askstring(
            "口令验证", "请输入监考老师口令:", show="*", parent=self.root
        )
        return pwd == config.TEACHER_PASSWORD

    def export_log(self):
        if not self._teacher_authorized():
            messagebox.showwarning("导出日志", "口令错误，已取消导出")
            return
        if not self.event_log and not self.db:
            messagebox.showinfo("导出", "暂无日志记录")
            return

        filename = f"monitor_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = EXPORT_DIR / filename
        self.db.export_events_csv(str(filepath))

        messagebox.showinfo("导出成功", f"日志已导出到:\n{filepath}")
        self.add_log(f"日志已导出: {filepath}")

    # ──────────── 统计 ────────────

    def update_counts(self):
        with self._lock:
            online = sum(1 for c in self.clients.values() if c.is_online)
            total_alerts = sum(c.alert_count for c in self.clients.values())
        self.status_count_label.config(text=f"在线: {online} | 报警: {total_alerts}")

    # ──────────── 结束监考 ────────────

    def stop_all_clients(self):
        with self._lock:
            if not self.clients:
                messagebox.showwarning("结束监考", "当前没有在线考生")
                return
            count = len(self.clients)
        if not self._teacher_authorized():
            messagebox.showwarning("结束监考", "口令错误，已取消操作")
            return
        if not messagebox.askyesno("结束监考", f"确认结束监考，将关闭全部 {count} 个考生端？"):
            return

        stopped = 0
        with self._lock:
            clients_snapshot = list(self.clients.items())
        # 先并行提交全部发送任务，再统一限时等待，避免 N 个客户端串行阻塞 GUI
        futs = []
        for cid, client in clients_snapshot:
            if client.ws:
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        client.ws.send(json.dumps({"type": "stop", "message": "监考已结束"})),
                        self.loop,
                    )
                    futs.append(fut)
                except Exception:
                    pass
        deadline = time.time() + 3
        for fut in futs:
            try:
                fut.result(timeout=max(0.1, deadline - time.time()))
                stopped += 1
            except Exception:
                pass
        self.add_log(f"已向 {stopped} 个考生端发送结束指令")
        messagebox.showinfo("结束监考", f"已向 {stopped} 个考生端发送结束指令")

    # ════════════════════════════════════════════════════════════
    #  WebSocket 消息处理
    # ════════════════════════════════════════════════════════════

    async def handle_client(self, websocket, path=None):
        client_id = None
        try:
            async for raw_message in websocket:
                # ─── 二进制帧 = 截图 JPEG bytes ───
                if isinstance(raw_message, bytes):
                    with self._lock:
                        if client_id and client_id in self.clients:
                            client = self.clients[client_id]
                        else:
                            continue
                    b64_str = base64.b64encode(raw_message).decode("ascii")
                    client.screenshot_count += 1
                    client.last_screenshot_ts = time.time()
                    client.last_screenshot_b64 = b64_str
                    client.screenshot_history.append(
                        (time.time(), self._make_thumbnail(b64_str))
                    )
                    if len(client.screenshot_history) > config.MAX_SCREENSHOT_HISTORY:
                        client.screenshot_history = client.screenshot_history[-config.MAX_SCREENSHOT_HISTORY:]
                    # 持久化截图索引（保存到本地文件）
                    save_dir = Path(config.SCREENSHOT_DIR) / client_id / datetime.now().strftime("%Y-%m-%d")
                    save_dir.mkdir(parents=True, exist_ok=True)
                    fname = save_dir / screenshot_filename()
                    fname.write_bytes(raw_message)
                    self.db.add_screenshot(client_id, str(fname), "")
                    client.update_heartbeat()
                    self.root.after(0, self.update_client_card, client_id)
                    self.root.after(0, self.update_counts)
                    continue

                # ─── JSON 控制消息 ───
                try:
                    data = json.loads(raw_message)
                except json.JSONDecodeError:
                    log.warning("忽略非法 JSON 消息: %.80s", raw_message)
                    continue
                if not isinstance(data, dict):
                    continue
                msg_type = data.get("type")

                if msg_type == "register":
                    # Token 认证
                    token = data.get("token", "")
                    if config.AUTH_TOKEN and token != config.AUTH_TOKEN:
                        await websocket.send(json.dumps({
                            "type": "register_rejected",
                            "reason": "认证失败：令牌不正确",
                        }))
                        log.warning("注册被拒绝（令牌错误）: %s", data.get("client_id"))
                        await websocket.close(4001, "认证失败")
                        return

                    client_id = data.get("client_id")
                    if not client_id:
                        await websocket.close(4000, "缺少 client_id")
                        return
                    hostname = data.get("hostname", "")
                    is_new = False
                    with self._lock:
                        if client_id not in self.clients:
                            self.clients[client_id] = ClientInfo(client_id, hostname)
                            self.root.after(0, self.create_client_card, client_id, hostname)
                            is_new = True
                        else:
                            self.clients[client_id].hostname = hostname
                            self.clients[client_id].update_heartbeat()
                        self.clients[client_id].ws = websocket
                    # 日志落库涉及磁盘 IO，移到锁外执行
                    if is_new:
                        self.add_log(f"考生上线: {client_id} ({hostname})", "info", client_id, "register")
                    self.root.after(0, self.update_counts)
                    await websocket.send(json.dumps({"type": "register_ok"}))

                elif msg_type == "heartbeat":
                    cid = data.get("client_id")
                    if not cid:
                        continue
                    with self._lock:
                        if cid in self.clients:
                            self.clients[cid].update_heartbeat()
                            self.clients[cid].hostname = data.get("hostname", self.clients[cid].hostname)
                    self.root.after(0, self.update_client_card, cid)
                    self.root.after(0, self.update_counts)

                elif msg_type == "screenshot_meta":
                    # 二进制帧的元数据（hash 等），通过 JSON 附带
                    cid = data.get("client_id")
                    if not cid:
                        continue
                    new_hash = data.get("hash", "")
                    with self._lock:
                        if cid not in self.clients:
                            self.clients[cid] = ClientInfo(cid)
                            self.root.after(0, self.create_client_card, cid)
                        client = self.clients[cid]
                        client.last_hash = new_hash
                        client.last_screenshot_ts = time.time()  # 记录最近一次收到截图的时间
                    # 哈希存证：回填到最近一条截图索引（二进制帧先到、meta 后到）
                    self.db.update_last_screenshot_hash(cid, new_hash)
                    self.root.after(0, self.update_client_card, cid)
                    self.root.after(0, self.update_counts)

                elif msg_type == "alert":
                    cid = data.get("client_id")
                    if not cid:
                        continue
                    target = data.get("target_window", "未知")
                    reason = data.get("reason", "")
                    url = data.get("url", "")
                    with self._lock:
                        if cid not in self.clients:
                            self.clients[cid] = ClientInfo(cid)
                            self.root.after(0, self.create_client_card, cid)
                        cl = self.clients[cid]
                        cl.alert_count = data.get("alert_count", cl.alert_count + 1)
                        cl.last_alert_target = target if not reason else f"{target} [{reason}]"
                        cl.last_alert_time = time.time()
                        cl.alert_history.append(
                            (data.get("timestamp", time.time()), target, reason, url)
                        )
                        cl.update_heartbeat()
                    self.root.after(0, self.update_client_card, cid)
                    self.root.after(0, self.update_counts)
                    alert_msg = f"考生 {cid} 违规 -> {target}"
                    if reason:
                        alert_msg += f"（{reason}）"
                    if url:
                        alert_msg += f" | 网址: {url}"
                    self.add_log(alert_msg, "alert", cid, "violation")
                    self.show_alert_banner(alert_msg)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if client_id:
                # 仅当该连接仍是当前连接时才标记离线：
                # 客户端断线后立即重连时，旧连接的 finally 不应把新连接误标为离线
                is_current = False
                with self._lock:
                    if client_id in self.clients and self.clients[client_id].ws is websocket:
                        self.clients[client_id].ws = None
                        self.clients[client_id].is_online = False
                        is_current = True
                if is_current:
                    self.root.after(0, self.update_client_card, client_id)
                    self.root.after(0, self.update_counts)
                    self.add_log(f"考生离线: {client_id}", "info", client_id, "offline")

    # ──────────── 在线检测循环 ────────────

    async def check_online_loop(self):
        while self.running:
            # 冻结判定阈值：默认取 force_send_interval 的数倍，避免误报
            freeze_timeout = config.FREEZE_TIMEOUT if config.FREEZE_TIMEOUT > 0 \
                else max(config.FORCE_SEND_INTERVAL * 3, 90)
            events = []          # (tag, cid, kind, message) 锁外统一落库，避免持锁磁盘 IO
            ws_to_close = []     # (cid, ws) 锁外关闭
            with self._lock:
                for cid, client in self.clients.items():
                    was_online = client.is_online
                    client.check_online(config.CLIENT_TIMEOUT)

                    # ── 心跳超时：判离线并主动关闭连接，清理 zombie ──
                    if was_online and not client.is_online:
                        events.append(("alert", cid, "timeout", f"考生掉线: {cid}"))
                        self.root.after(0, self.update_client_card, cid)

                    # ── 冻结检测：在线但长时间无任何截图帧 ──
                    if client.is_online and client.freeze_check(freeze_timeout):
                        idle = int(time.time() - client.last_screenshot_ts)
                        events.append((
                            "alert", cid, "freeze",
                            f"考生 {cid} 画面冻结：已 {idle} 秒未收到截图",
                        ))
                        self.root.after(0, self.update_client_card, cid)

                # 收集需在锁外关闭的连接，避免持锁期间 await
                for cid in list(self.clients):
                    c = self.clients[cid]
                    if not c.is_online and c.ws is not None:
                        ws_to_close.append((cid, c.ws))

            for cid, ws in ws_to_close:
                try:
                    await ws.close(code=4000, reason="心跳超时")
                except Exception:
                    pass
                # 关闭后清引用，避免每轮重复 close；重连新连接不会被误清
                with self._lock:
                    if cid in self.clients and self.clients[cid].ws is ws:
                        self.clients[cid].ws = None

            for tag, cid, kind, message in events:
                self.add_log(message, tag, cid, kind)

            self.root.after(0, self.update_counts)
            await asyncio.sleep(3)

    # ──────────── 截图清理循环 ────────────

    async def cleanup_loop(self):
        """定期清理过期截图，并执行落盘截图总量上限。"""
        while self.running:
            await asyncio.sleep(config.CLEANUP_INTERVAL)
            if config.SCREENSHOT_RETENTION_DAYS > 0:
                self.db.cleanup_old(config.SCREENSHOT_RETENTION_DAYS)
            self.db.enforce_screenshot_cap(config.MAX_SCREENSHOTS_DISK)

    # ──────────── 启动 ────────────

    async def start_server(self):
        self.running = True
        try:
            ssl_context = build_server_ssl_context()
        except FileNotFoundError as e:
            log.error("TLS config error: %s", e)
            self.add_log(f"start failed: {e}", "alert", "-", "error")
            self.running = False
            return
        scheme = "wss" if ssl_context else "ws"
        server = await websockets.serve(
            self.handle_client,
            config.SERVER_HOST,
            config.SERVER_PORT,
            ssl=ssl_context,
        )
        self.add_log(f"监考服务已启动，监听端口 {config.SERVER_PORT} [{scheme}]")
        log.info("监考服务已启动: ws://%s:%d", config.SERVER_HOST, config.SERVER_PORT)

        await asyncio.gather(
            self.check_online_loop(),
            self.cleanup_loop(),
        )

    def run(self):
        self.setup_gui()
        style = ttk.Style()
        style.theme_use("clam")

        self.loop = asyncio.new_event_loop()

        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.start_server())

        ws_thread = threading.Thread(target=run_loop, daemon=True)
        ws_thread.start()

        self.root.mainloop()
        self.running = False
        self.db.close()


def main():
    server = MonitorServer()
    server.run()


if __name__ == "__main__":
    main()
