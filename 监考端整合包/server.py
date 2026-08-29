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
import theme

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
        self._log_records: list[tuple[str, str, str, str]] = []  # 过滤重绘用
        self._log_filter = "全部"
        self._log_rows = 0
        self._grid_cols = -1

    # ──────────── GUI ────────────

    def setup_gui(self):
        Pal, font = theme.Pal, theme.font
        self.root = tk.Tk()
        theme.style(self.root)
        self.root.title("监考中心 · 屏幕监控系统")
        self.root.configure(bg=Pal.bg)
        self.root.geometry("1280x840")
        self.root.minsize(980, 640)

        # ── 顶部品牌栏 ──
        header = tk.Frame(self.root, bg=Pal.card)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header.configure(height=70)
        tk.Frame(self.root, bg=Pal.border, height=1).pack(fill=tk.X)

        brand = tk.Frame(header, bg=Pal.card)
        brand.pack(side=tk.LEFT, padx=(18, 0))
        logo = tk.Canvas(brand, width=42, height=42, bg=Pal.card, highlightthickness=0)
        logo.pack(side=tk.LEFT)
        theme.draw_shield(logo, 21, 21, 36, Pal.accent)
        brand_text = tk.Frame(brand, bg=Pal.card)
        brand_text.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(brand_text, text="监考中心", font=font(16, "bold"), bg=Pal.card,
                 fg=Pal.text).pack(anchor="w")
        auth_txt = "令牌认证" if config.AUTH_TOKEN else "无认证"
        tls_txt = "TLS 加密" if config.USE_TLS else "明文传输"
        tk.Label(brand_text,
                 text=f"屏幕监控 · 端口 {config.SERVER_PORT} · {auth_txt} · {tls_txt}",
                 font=font(9), bg=Pal.card, fg=Pal.muted).pack(anchor="w")

        btns = tk.Frame(header, bg=Pal.card)
        btns.pack(side=tk.RIGHT, padx=(6, 18))
        stop_btn = theme.RoundButton(btns, "结束监考", kind="danger", size=10,
                                     command=self.stop_all_clients)
        export_btn = theme.RoundButton(btns, "导出日志", kind="primary", size=10,
                                       command=self.export_log)
        stop_btn.pack(side=tk.RIGHT)
        export_btn.pack(side=tk.RIGHT, padx=(0, 10))

        stats = tk.Frame(header, bg=Pal.card)
        stats.pack(side=tk.RIGHT, padx=4)
        self.stat_online = theme.Pill(stats, "在线 0", dot=Pal.normal,
                                      surround=Pal.card, fg=Pal.text, bg=Pal.hover)
        self.stat_offline = theme.Pill(stats, "离线 0", dot=Pal.offline,
                                       surround=Pal.card, fg=Pal.muted, bg=Pal.hover)
        self.stat_alert = theme.Pill(stats, "报警 0", dot=Pal.alert,
                                     surround=Pal.card, fg=Pal.muted, bg=Pal.hover)
        for p in (self.stat_online, self.stat_offline, self.stat_alert):
            p.pack(side=tk.LEFT, padx=3)
        self.clock_pill = theme.Pill(header, "", surround=Pal.card, fg=Pal.muted,
                                     bg=Pal.hover)
        self.clock_pill.pack(side=tk.RIGHT, padx=(4, 10))
        self._tick_clock()

        # ── 报警横幅（默认隐藏）──
        banner_holder = tk.Frame(self.root, bg=Pal.bg)
        self._banner_holder = banner_holder
        self.alert_banner = theme.Banner(banner_holder, surround=Pal.bg)
        self.alert_banner.pack(fill=tk.X, padx=16, pady=(12, 0))

        # ── 主区域：左考生网格 / 右日志+预览 ──
        self.main_frame = tk.Frame(self.root, bg=Pal.bg)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 6))
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=0, minsize=396)
        self.main_frame.rowconfigure(0, weight=1)

        # 左侧：考生卡片网格
        left = tk.Frame(self.main_frame, bg=Pal.bg)
        left.grid(row=0, column=0, sticky="nsew")
        head_row = theme.section_title(left, "考生状态")
        head_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(head_row, text="点击卡片查看截图", font=font(9),
                 bg=Pal.bg, fg=Pal.muted).pack(side=tk.RIGHT)
        holder, self.clients_canvas, self.clients_inner = theme.make_scroll_canvas(left)
        holder.pack(fill=tk.BOTH, expand=True)
        self.clients_canvas.bind("<Configure>", self._relayout_grid)

        # 空状态提示卡
        self._empty_hint = theme.Card(self.clients_inner, min_h=158)
        eh_icon = tk.Canvas(self._empty_hint.inner, width=44, height=44,
                            bg=self._empty_hint.bg_fill, highlightthickness=0)
        theme.draw_shield(eh_icon, 22, 22, 34, Pal.border, check=False)
        eh_icon.pack(expand=True, pady=(18, 4))
        tk.Label(self._empty_hint.inner, text="等待考生接入 …", font=font(12, "bold"),
                 bg=self._empty_hint.bg_fill, fg=Pal.muted).pack()
        tk.Label(self._empty_hint.inner, text="请确认考生端已启动并指向本服务地址",
                 font=font(9), bg=self._empty_hint.bg_fill, fg=theme.darker(Pal.muted, 0.2)).pack(
            pady=(2, 18))
        self._empty_hint.grid(row=0, column=0, sticky="ew", padx=4, pady=5)

        # 右侧：事件日志 + 截图预览
        right = tk.Frame(self.main_frame, bg=Pal.bg)
        right.grid(row=0, column=1, sticky="nsew")

        theme.section_title(right, "事件日志", Pal.accent2).pack(fill=tk.X)
        filters = tk.Frame(right, bg=Pal.bg)
        filters.pack(fill=tk.X, pady=(4, 8))
        self._log_pills = {}
        for label in ("全部", "上线", "违规", "离线", "系统"):
            p = theme.Pill(filters, label, size=9, padx=10, pady=4,
                           command=lambda l=label: self._set_log_filter(l))
            p.pack(side=tk.LEFT, padx=(0, 6))
            self._log_pills[label] = p
        self._set_log_filter("全部", render=False)

        log_card = theme.Card(right, pad=8)
        log_card.pack(fill=tk.BOTH, expand=True)
        log_sb = ttk.Scrollbar(log_card.inner, command=None, style="Panel.TScrollbar")
        self.log_text = tk.Text(
            log_card.inner, bg=Pal.card, fg=Pal.text, wrap=tk.WORD, bd=0,
            highlightthickness=0, state=tk.DISABLED, padx=10, pady=8,
            width=1, font=font(9), insertbackground=Pal.text,
            selectbackground=Pal.accent,
        )
        log_sb.configure(command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        zebra = theme.mix(Pal.card, "#000000", 0.16)
        self.log_text.tag_configure("even", background=Pal.card)
        self.log_text.tag_configure("odd", background=zebra)
        self.log_text.tag_configure("time", foreground=Pal.muted, font=(theme.MONO, 9))
        self.log_text.tag_configure("info", foreground=Pal.text)
        self.log_text.tag_configure("alert", foreground=theme.lighter(Pal.alert, 0.12))
        self.log_text.tag_configure("sys", foreground=Pal.accent)
        self.log_text.tag_configure("warn", foreground=Pal.warn)
        self.log_text.tag_configure("ok", foreground=Pal.success)

        prev_card = theme.Card(right, pad=12, min_h=292)
        prev_card.pack(fill=tk.X, pady=(12, 0))
        ptop = prev_card.add()
        ptop.pack(fill=tk.X)
        self.preview_title = tk.Label(ptop, text="截图预览", font=font(11, "bold"),
                                      bg=prev_card.bg_fill, fg=Pal.text)
        self.preview_title.pack(side=tk.LEFT)
        self.preview_live = theme.Pill(ptop, "待机", dot=Pal.offline, size=9,
                                       padx=9, pady=4, bg=Pal.hover, fg=Pal.muted,
                                       surround=prev_card.bg_fill)
        self.preview_live.pack(side=tk.RIGHT)
        self.preview_label = tk.Label(
            prev_card.inner, text="点击左侧考生卡片\n查看该考生最新截图",
            font=font(10), bg=Pal.deep, fg=Pal.muted, justify=tk.CENTER,
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.preview_label.bind("<Double-Button-1>", lambda e: self._open_preview_window())

        self._preview_photo = None
        self._preview_client = None
        self.root.after(config.PREVIEW_REFRESH_MS, self.refresh_preview)

        # ── 底部状态条 ──
        sbar = tk.Frame(self.root, bg=theme.mix(Pal.bg, Pal.card, 0.5))
        sbar.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Frame(sbar, bg=Pal.border, height=1).pack(fill=tk.X)
        row = tk.Frame(sbar, bg=theme.mix(Pal.bg, Pal.card, 0.5))
        row.pack(fill=tk.X, padx=16, pady=4)
        tk.Label(row, text=f"● 服务监听 {config.SERVER_HOST}:{config.SERVER_PORT}",
                 font=(theme.MONO, 9), bg=row["bg"], fg=Pal.success).pack(side=tk.LEFT)
        tk.Label(row, text=f"数据: {config.DB_FILE} · 截图: {config.SCREENSHOT_DIR}/ · 保留 {config.SCREENSHOT_RETENTION_DAYS} 天",
                 font=font(9), bg=row["bg"], fg=Pal.muted).pack(side=tk.RIGHT)

    # ──────────── 布局 / 小工具 ────────────

    def _tick_clock(self):
        self.clock_pill.set(datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    def _relayout_grid(self, _event=None):
        """考生卡片自适应列数（宽度变化 / 新卡片接入时重排）。"""
        w = self.clients_canvas.winfo_width()
        cols = max(1, min(3, (w - 16) // 330))
        cards = [f["card"] for f in self.client_frames.values()]
        if cols == self._grid_cols and cards:
            return
        self._grid_cols = cols
        for c in cards:
            c.grid_forget()
        if not cards:
            self._empty_hint.grid(row=0, column=0, sticky="ew", padx=4, pady=5)
        else:
            self._empty_hint.grid_forget()
        for i, c in enumerate(cards):
            r, col = divmod(i, cols)
            c.grid(row=r, column=col, sticky="nsew", padx=4, pady=5)
            self.clients_inner.rowconfigure(r, weight=1, uniform="row")
        for c in range(3):
            self.clients_inner.columnconfigure(
                c, weight=1 if c < cols else 0,
                uniform="col" if c < cols else None)

    # ──────────── 日志 ────────────

    def add_log(self, message: str, tag: str = "info", client_id: str = "", kind: str = "info"):
        ts_str = datetime.now().strftime("%H:%M:%S")
        self.event_log.append((ts_str, message))
        # 持久化到 SQLite
        self.db.add_event(client_id or "-", kind, message)
        log.info(message)

        def _append():
            self._log_records.append((ts_str, message, tag, kind))
            if self._log_match(kind):
                self._append_log_line(ts_str, message, tag, self._log_rows % 2 == 1)
                self._log_rows += 1
                self.log_text.see(tk.END)

        self.root.after(0, _append)

    def _log_match(self, kind: str) -> bool:
        f = self._log_filter
        if f == "全部":
            return True
        spec = {
            "上线": ("register",),
            "违规": ("violation", "freeze"),
            "离线": ("offline", "timeout"),
        }.get(f)
        known = ("register", "violation", "freeze", "offline", "timeout")
        if spec is not None:
            return kind in spec
        return kind not in known  # "系统"

    def _set_log_filter(self, name: str, render: bool = True):
        self._log_filter = name
        Pal = theme.Pal
        for label, p in self._log_pills.items():
            active = label == name
            p.set(bg=Pal.accent if active else Pal.hover,
                  fg="#ffffff" if active else Pal.muted)
        if render:
            self._render_log()

    def _append_log_line(self, ts: str, message: str, tag: str, odd: bool):
        t = self.log_text
        zebra = "odd" if odd else "even"
        t.config(state=tk.NORMAL)
        t.insert(tk.END, f" {ts}  ", ("time", zebra))
        t.insert(tk.END, message + "\n", (tag, zebra))
        t.config(state=tk.DISABLED)

    def _render_log(self):
        """按当前过滤器重绘日志（保留最近 600 条匹配记录）。"""
        t = self.log_text
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.config(state=tk.DISABLED)
        rows = [rec for rec in self._log_records if self._log_match(rec[3])][-600:]
        self._log_rows = len(rows)
        for i, (ts, msg, tag, _kind) in enumerate(rows):
            self._append_log_line(ts, msg, tag, i % 2 == 1)
        if rows:
            t.see(tk.END)

    def show_alert_banner(self, text: str):
        def _show():
            self.alert_banner.set(text)
            self._banner_holder.pack(fill=tk.X, before=self.main_frame)
            self.alert_banner.start_pulse()
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
        self._alert_hide_after = None
        self.alert_banner.stop_pulse()
        self._banner_holder.pack_forget()

    # ──────────── 考生卡片 ────────────

    def create_client_card(self, client_id: str, hostname: str = ""):
        Pal, font = theme.Pal, theme.font
        wrapper = tk.Frame(self.clients_inner, bg=Pal.bg)
        card = theme.Card(wrapper, pad=14, min_h=184)
        card.pack(fill=tk.BOTH, expand=True)
        inner, cbg = card.inner, card.bg_fill

        top = card.add()
        top.pack(fill=tk.X)
        dot = theme.Dot(top, size=16, color=Pal.offline, bg=cbg)
        dot.pack(side=tk.LEFT, pady=(3, 4))
        id_col = tk.Frame(top, bg=cbg)
        id_col.pack(side=tk.LEFT, padx=(9, 0))
        name_label = tk.Label(id_col, text=client_id, font=font(13, "bold"),
                              bg=cbg, fg=Pal.text)
        name_label.pack(anchor="w")
        host_label = tk.Label(id_col, text=f"主机 {hostname or '未知'}",
                              font=font(9), bg=cbg, fg=Pal.muted)
        host_label.pack(anchor="w")
        time_label = tk.Label(top, text="--:--:--", font=(theme.MONO, 9),
                              bg=cbg, fg=Pal.muted)
        time_label.pack(side=tk.RIGHT, pady=3)

        acts = card.add()
        acts.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        theme.RoundButton(acts, "截图历史", kind="ghost", size=9, padx=12, pady=5,
                          surround=cbg,
                          command=lambda: self.view_screenshots(client_id)).pack(side=tk.RIGHT)
        theme.RoundButton(acts, "预览", kind="primary", size=9, padx=14, pady=5,
                          surround=cbg,
                          command=lambda: self.preview_latest_screenshot(client_id)).pack(
            side=tk.RIGHT, padx=(0, 6))

        pills = card.add()
        pills.pack(fill=tk.X, pady=(8, 0))
        shot_pill = theme.Pill(pills, "截屏 0", size=9, padx=9, pady=3,
                               bg=Pal.hover, fg=Pal.muted, surround=cbg)
        shot_pill.pack(side=tk.LEFT, padx=(2, 5))
        alert_pill = theme.Pill(pills, "报警 0", size=9, padx=9, pady=3,
                                bg=Pal.hover, fg=Pal.muted, surround=cbg)
        alert_pill.pack(side=tk.LEFT, padx=5)

        reason_label = tk.Label(inner, text="无违规记录", font=font(9), bg=cbg,
                                fg=Pal.muted, anchor="w")
        reason_label.pack(fill=tk.X, pady=(4, 0))

        card.bind_click(lambda: self.preview_latest_screenshot(client_id))

        self.client_frames[client_id] = {
            "card": wrapper, "card_obj": card, "dot": dot,
            "name_label": name_label, "host_label": host_label,
            "time_label": time_label, "shot_pill": shot_pill,
            "alert_pill": alert_pill, "reason_label": reason_label,
        }
        self._relayout_grid()

    def update_client_card(self, client_id: str):
        if client_id not in self.client_frames:
            return
        with self._lock:
            if client_id not in self.clients:
                return
            client = self.clients[client_id]
            is_online = client.is_online
            alert_count = client.alert_count
            hostname = client.hostname
            screenshot_count = client.screenshot_count
            last_alert_time = client.last_alert_time
            last_alert_target = client.last_alert_target
            last_beat = client.last_heartbeat
        frame = self.client_frames[client_id]
        Pal = theme.Pal

        color = self.alert_color(is_online, last_alert_time)
        frame["dot"].set(color)
        frame["card_obj"].set_accent(color)
        suffix = "" if is_online else " · 已离线"
        frame["host_label"].config(text=f"主机 {hostname or '未知'}{suffix}")
        frame["shot_pill"].set(f"截屏 {screenshot_count}")
        if alert_count:
            frame["alert_pill"].set(
                f"报警 {alert_count}", fg="#ffffff",
                bg=theme.darker(config.COLOR_ALERT, 0.18))
        else:
            frame["alert_pill"].set("报警 0", fg=Pal.muted, bg=Pal.hover)
        if last_alert_target:
            txt = last_alert_target
            if len(txt) > 14:
                txt = txt[:14] + "…"
            red = color == config.COLOR_ALERT
            frame["reason_label"].config(
                text=f"最近违规: {txt}",
                fg=config.COLOR_ALERT if red else Pal.muted)
        else:
            frame["reason_label"].config(text="无违规记录", fg=Pal.muted)
        frame["time_label"].config(
            text=datetime.fromtimestamp(last_beat).strftime("%H:%M:%S"))

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
        Pal = theme.Pal
        if not cid or cid not in self.clients:
            self.root.after(config.PREVIEW_REFRESH_MS, self.refresh_preview)
            return
        client = self.clients[cid]

        if client.last_screenshot_b64:
            photo = self._b64_to_photo(client.last_screenshot_b64, (368, 200))
            if photo:
                self._preview_photo = photo
                self.preview_label.config(image=photo, text="", bg=Pal.deep)
                self.preview_title.config(text=f"最新截图 · {cid}")
                self.preview_live.set(
                    datetime.now().strftime("实时 %H:%M:%S"),
                    dot=Pal.success, fg=Pal.success, bg=Pal.hover)
        else:
            self.preview_label.config(image="", text="该考生暂无截图", bg=Pal.deep)
            self.preview_title.config(text=f"截图预览 · {cid}")
            self.preview_live.set("暂无截图", dot=Pal.offline, fg=Pal.muted, bg=Pal.hover)

        self.root.after(config.PREVIEW_REFRESH_MS, self.refresh_preview)

    def _open_preview_window(self):
        """双击预览：弹出接近原始分辨率的大图窗口。"""
        cid = self._preview_client
        if not cid or cid not in self.clients or not self.clients[cid].last_screenshot_b64:
            return
        b64 = self.clients[cid].last_screenshot_b64
        try:
            img = Image.open(BytesIO(base64.b64decode(b64)))
        except Exception:
            messagebox.showwarning("截图预览", "截图数据损坏，无法显示", parent=self.root)
            return
        max_w = int(self.root.winfo_screenwidth() * 0.82)
        max_h = int(self.root.winfo_screenheight() * 0.74)
        img.thumbnail((max_w, max_h))
        photo = ImageTk.PhotoImage(img)

        win = tk.Toplevel(self.root)
        win.configure(bg=theme.Pal.bg)
        show = theme.dialog_chrome(win, "截图预览", subtitle=f"考生 {cid}")
        body = tk.Frame(win, bg=theme.Pal.deep)
        body.pack(fill=tk.BOTH, expand=True)
        lbl = tk.Label(body, image=photo, bg=theme.Pal.deep)
        lbl.pack(padx=14, pady=14)
        win._photo = photo  # 防止 GC
        show(img.width + 28, img.height + 48 + 28)

    # ──────────── 截图历史（翻页） ────────────

    def view_screenshots(self, client_id: str):
        Pal, font = theme.Pal, theme.font
        if client_id not in self.clients:
            return
        client = self.clients[client_id]
        if not client.screenshot_history:
            messagebox.showinfo("截图记录", f"考生 {client_id} 暂无截图记录",
                                parent=self.root)
            return

        PAGE_SIZE = 12
        current_page = [0]  # 用列表包裹以便闭包修改

        win = tk.Toplevel(self.root)
        win.configure(bg=Pal.bg)
        show = theme.dialog_chrome(win, "截图历史", subtitle=f"考生 {client_id}")

        holder, canvas, inner = theme.make_scroll_canvas(win)
        holder.pack(fill=tk.BOTH, expand=True, padx=(14, 8), pady=(10, 4))

        photos_ref = []  # 防止 GC

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

            cols = 2 if canvas.winfo_width() >= 640 else 1
            for i, (ts, b64_img) in enumerate(reversed(history[start:end])):
                cell = tk.Frame(inner, bg=Pal.bg)
                cell.grid(row=i // cols, column=i % cols, sticky="nsew",
                          padx=4, pady=5)
                cell.columnconfigure(0, weight=1)
                card = theme.Card(cell, pad=10, min_h=196)
                card.pack(fill=tk.BOTH, expand=True)
                photo = self._b64_to_photo(b64_img, (264, 150))
                if photo:
                    photos_ref.append(photo)
                    tk.Label(card.inner, image=photo, bg=card.bg_fill).pack(pady=(2, 4))
                else:
                    tk.Label(card.inner, text="[图片加载失败]", font=font(9),
                             bg=card.bg_fill, fg=Pal.alert).pack(expand=True)
                dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                tk.Label(card.inner, text=dt, font=(theme.MONO, 9),
                         bg=card.bg_fill, fg=Pal.muted).pack(pady=(0, 4))
            update_page_label()

        nav_frame = tk.Frame(win, bg=Pal.bg)
        nav_frame.pack(fill=tk.X, padx=14, pady=(2, 12))
        prev_btn = theme.RoundButton(nav_frame, "◀ 更早", kind="ghost", size=10)
        next_btn = theme.RoundButton(nav_frame, "较新 ▶", kind="primary", size=10)
        prev_btn.pack(side=tk.LEFT)
        next_btn.pack(side=tk.RIGHT)
        page_pill = theme.Pill(nav_frame, "", size=10, bg=Pal.hover, fg=Pal.text)
        page_pill.pack(side=tk.LEFT, padx=14)

        def prev_page():
            if current_page[0] < total_pages() - 1:
                current_page[0] += 1
                render_page()

        def next_page():
            if current_page[0] > 0:
                current_page[0] -= 1
                render_page()

        prev_btn._command = prev_page
        next_btn._command = next_page

        def update_page_label():
            page_pill.set(f"第 {current_page[0] + 1} / {total_pages()} 页 · 共 {len(client.screenshot_history)} 张")

        render_page()
        show(920, 660)
        win.after(120, render_page)   # 定尺寸后按实际宽度重排两列

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
            messagebox.showwarning("导出日志", "口令错误，已取消导出", parent=self.root)
            return
        if not self.event_log and not self.db:
            messagebox.showinfo("导出", "暂无日志记录", parent=self.root)
            return

        filename = f"monitor_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = EXPORT_DIR / filename
        self.db.export_events_csv(str(filepath))

        messagebox.showinfo("导出成功", f"日志已导出到:\n{filepath}", parent=self.root)
        self.add_log(f"日志已导出: {filepath}", "sys", "-", "info")

    # ──────────── 统计 ────────────

    def update_counts(self):
        with self._lock:
            online = sum(1 for c in self.clients.values() if c.is_online)
            offline = sum(1 for c in self.clients.values() if not c.is_online)
            total_alerts = sum(c.alert_count for c in self.clients.values())
        self.stat_online.set(f"在线 {online}")
        self.stat_offline.set(f"离线 {offline}")
        if total_alerts:
            self.stat_alert.set(f"报警 {total_alerts}", fg="#ffffff",
                                bg=theme.darker(config.COLOR_ALERT, 0.18))
        else:
            self.stat_alert.set("报警 0", fg=theme.Pal.muted, bg=theme.Pal.hover)

    # ──────────── 结束监考 ────────────

    def stop_all_clients(self):
        with self._lock:
            if not self.clients:
                messagebox.showwarning("结束监考", "当前没有在线考生", parent=self.root)
                return
            count = len(self.clients)
        if not self._teacher_authorized():
            messagebox.showwarning("结束监考", "口令错误，已取消操作", parent=self.root)
            return
        if not messagebox.askyesno("结束监考",
                                   f"确认结束监考，将关闭全部 {count} 个考生端？",
                                   parent=self.root):
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
        self.add_log(f"已向 {stopped} 个考生端发送结束指令", "warn", "-", "system")
        messagebox.showinfo("结束监考", f"已向 {stopped} 个考生端发送结束指令",
                            parent=self.root)

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