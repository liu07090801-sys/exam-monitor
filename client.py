"""
考试端客户端 - 屏幕监控
运行在每台考生机器上，负责截屏和窗口检测。

改进:
  - Token 认证：注册时携带令牌
  - 二进制帧传输：截图直接发 JPEG bytes，不再 base64-in-JSON
  - 多显示器支持：可抓取所有屏幕拼接为一张
  - logging 模块：替代 print
  - 修正默认服务器地址为 ws://127.0.0.1:8765
  - 中文帮助信息

用法: python client.py --server ws://192.168.1.100:8765 --student-id 2024001
      python client.py --student-id 2024001                     # 默认连接本机
"""

import argparse
import asyncio
import base64
import ctypes
import dataclasses
import json
import logging
import platform
import ssl
import threading
import time
import tkinter as tk
from datetime import datetime
from io import BytesIO
from pathlib import Path

import mss
import websockets
from PIL import Image

import config

# ─── 安全锁定：以下值硬编码进 exe，config.yaml 无法覆盖 ───
# 考生即使修改 config.yaml 也无法关闭考试强制、放宽白名单或禁用全屏要求
config.EXAM_ENABLED = True
config.EXAM_FULLSCREEN_REQUIRED = True
config.EXAM_URL_WHITELIST = ["luogu.com.cn"]
config.EXAM_STRICT_URL_CHECK = False
config.EXAM_GRACE_SECONDS = 30

try:
    import psutil
except ImportError:
    psutil = None

try:
    import uiautomation as _uia
except ImportError:
    _uia = None

# ─── 日志 ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("client")

if psutil is None:
    log.info("未安装 psutil，进程名检测已降级为 Win32 API（功能不受影响）")
if _uia is None:
    log.info("未安装 uiautomation，考试网址检测将降级为标题匹配（考试端执行 pip install uiautomation 可启用 URL 级检测）")


def build_client_ssl_context():
    """按配置构建客户端 SSLContext；未启用 TLS 时返回 None。

    - 配置了 ca_file：加载并严格校验服务端证书（推荐）
    - 未配置 ca_file：使用自签名场景，降级为不校验证书并记录警告
    """
    if not config.USE_TLS:
        return None
    ca_file = config.BASE_DIR / config.TLS_CA_FILE
    if ca_file.exists():
        context = ssl.create_default_context(cafile=str(ca_file))
        context.check_hostname = False
    else:
        log.warning(
            "TLS 已启用但未找到 CA 证书 (%s)，将不校验服务端证书（不安全，仅限自签名内网环境）",
            ca_file,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


# ════════════════════════════════════════════════════════════════
#  感知哈希
# ════════════════════════════════════════════════════════════════

def image_hash(img, size: int = config.HASH_SIZE):
    """缩放为灰度图，按均值生成 size*size bit 并打包为 bytes。"""
    gray = img.convert("L").resize((size, size))
    pixels = gray.tobytes()  # 灰度模式每像素 1 字节，与 getdata() 等价但无弃用警告
    avg = sum(pixels) / len(pixels)
    out = bytearray()
    byte = 0
    for i, p in enumerate(pixels):
        byte |= (1 if p >= avg else 0) << (i % 8)
        if i % 8 == 7:
            out.append(byte)
            byte = 0
    return bytes(out)


def hash_diff(h1, h2) -> int:
    """两个感知哈希按位的汉明距离。"""
    if h1 is None or h2 is None or len(h1) != len(h2):
        return 10**9
    return sum(bin(a ^ b).count("1") for a, b in zip(h1, h2))


# ════════════════════════════════════════════════════════════════
#  窗口检测
# ════════════════════════════════════════════════════════════════

def _proc_name_from_hwnd(hwnd) -> str:
    """用 Win32 API 直接查询窗口所属进程名（psutil 缺失时的降级方案）。"""
    try:
        pid = ctypes.c_ulong()
        if not ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return ""
        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION (0x1000)
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def get_foreground_window_info():
    """获取当前前台窗口信息，返回 (hwnd, 标题, 进程名)。"""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    proc_name = ""
    pid = ctypes.c_ulong()
    if ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        if psutil is not None:
            try:
                proc_name = psutil.Process(pid.value).name()
            except Exception:
                pass
        else:
            proc_name = _proc_name_from_hwnd(hwnd)
    return hwnd, title, proc_name


def is_window_allowed(title: str, process_name: str = "") -> bool:
    title_allowed = bool(title) and any(
        kw.lower() in title.lower() for kw in config.WHITE_LIST
    )
    if process_name and config.WHITELIST_PROCESSES:
        proc_allowed = any(
            process_name.lower() == p.lower() for p in config.WHITELIST_PROCESSES
        )
    else:
        proc_allowed = False
    return title_allowed or proc_allowed


# ════════════════════════════════════════════════════════════════
#  考试网址 + 全屏强制
# ════════════════════════════════════════════════════════════════

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _rect_near(a, b, tol: int = 2) -> bool:
    """两个 (left, top, right, bottom) 矩形是否在 tol 像素内一致。"""
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def is_window_fullscreen(hwnd) -> bool:
    """窗口是否覆盖其所在显示器的完整区域（F11 全屏覆盖 rcMonitor，最大化只到 rcWork）。"""
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return False
        r = info.rcMonitor
        return _rect_near(
            (rect.left, rect.top, rect.right, rect.bottom),
            (r.left, r.top, r.right, r.bottom),
        )
    except Exception:
        return False


def _looks_like_url(value: str) -> bool:
    v = value.lower()
    return v.startswith("http://") or v.startswith("https://") or v.startswith("www.")


def _pick_url_from_edits(edits) -> str:
    """从 [(控件名, 值), ...] 中挑选最像地址栏的值（纯函数，便于单元测试）。

    优先级：
    1. 控件名含地址栏关键词 且 值像 URL（地址栏最可信，直接命中）
    2. 任一值像 URL（地址栏未识别时的兜底）
    3. 控件名含地址栏关键词 且 值非空（如 about:blank）
    4. 空串（读取失败）
    """
    url_vals: list[str] = []
    addr_vals: list[str] = []
    for name, value in edits:
        value = (value or "").strip()
        if not value:
            continue
        name_l = (name or "").lower()
        is_addr = "address" in name_l or "地址" in name_l or "网址" in name_l
        if _looks_like_url(value):
            if is_addr:
                return value
            url_vals.append(value)
        elif is_addr:
            addr_vals.append(value)
    if url_vals:
        return url_vals[0]
    if addr_vals:
        return addr_vals[0]
    return ""


def get_browser_url(hwnd) -> str:
    """用 UI Automation 读取浏览器地址栏 URL；未安装依赖或读取失败返回空串。"""
    if _uia is None or not hwnd:
        return ""
    deadline = time.time() + 0.8  # 限制 UIA 遍历耗时，避免阻塞焦点检测线程
    try:
        win = _uia.ControlFromHandle(hwnd)
        # 地址栏是浏览器窗口内的 Edit 控件，深度优先收集后按可信度筛选
        found = []
        stack = [win]
        visited = 0
        while stack and visited < 600 and len(found) < 12 and time.time() < deadline:
            c = stack.pop()
            visited += 1
            if c.ControlTypeName == "EditControl":
                found.append(c)
            try:
                children = c.GetChildren()
            except Exception:
                children = []
            stack.extend(children)

        edits = []
        for e in found:
            try:
                val = (e.GetValuePattern().Value or "").strip()
            except Exception:
                continue
            edits.append((e.Name or "", val))
        return _pick_url_from_edits(edits)
    except Exception:
        return ""


def _split_url(u: str):
    """把 URL/白名单项拆成 (host, path)：去 scheme/端口、去尾部斜杠、统一小写。"""
    u = (u or "").strip().lower().rstrip("/")
    if "://" in u:
        u = u.split("://", 1)[1]
    host = u.split("/", 1)[0].split(":")[0]
    path = "/" + u.split("/", 1)[1] if "/" in u else ""
    return host, path


def is_url_allowed(url: str) -> bool:
    """URL 是否命中考试网址白名单：域名须相等或为其子域，带路径的条目按路径前缀匹配。"""
    uh, up = _split_url(url)
    if not uh:
        return False
    for entry in config.EXAM_URL_WHITELIST:
        eh, ep = _split_url(entry)
        if not eh:
            continue
        if not (uh == eh or uh.endswith("." + eh)):
            continue
        if ep and not (up == ep or up.startswith(ep + "/")):
            continue
        return True
    return False


@dataclasses.dataclass
class WindowVerdict:
    """前台窗口合规判定结果。"""
    status: str          # self / desktop / allowed / violation
    reason: str = ""     # 违规原因（中文，可直接展示/上报）
    url: str = ""        # 读到的浏览器 URL（可能为空）
    fullscreen: bool = False
    is_browser: bool = False


def classify_window(own_hwnd, hwnd, title: str, proc_name: str, now: float,
                    grace_until: float, url: str = "",
                    fullscreen: bool = False) -> WindowVerdict:
    """对前台窗口做完整合规判定（纯函数，便于单元测试）。

    规则优先级：
    1. 监控客户端自身窗口 -> self（豁免，防自我报警）
    2. 无标题（桌面/任务栏等）-> desktop（豁免，点击任务栏属瞬态）
    3. 宽限期内 -> allowed（考生准备时间，任何窗口暂不判违规）
    4. 考试网址强制开启且前台是浏览器进程：
       a. 要求全屏但未全屏  -> violation「浏览器未全屏」
       b. 读到 URL 且不命中 -> violation「考试网址不符」
       c. 读不到 URL        -> 严格模式判违规，否则退回标题白名单
    5. 其他窗口沿用标题/进程白名单。
    """
    if hwnd and own_hwnd and hwnd == own_hwnd:
        return WindowVerdict("self")
    if not title:
        return WindowVerdict("desktop")

    proc_l = (proc_name or "").lower()
    is_browser = proc_l in config.EXAM_BROWSERS_LOWER

    if now < grace_until:
        return WindowVerdict("allowed", "准备阶段", url, fullscreen, is_browser)

    if is_browser and config.EXAM_ENABLED:
        if config.EXAM_FULLSCREEN_REQUIRED and not fullscreen:
            return WindowVerdict("violation", "浏览器未全屏", url, fullscreen, True)
        if url:
            if is_url_allowed(url):
                return WindowVerdict("allowed", "", url, fullscreen, True)
            return WindowVerdict("violation", "考试网址不符", url, fullscreen, True)
        # URL 读不到：严格模式直接判违规；否则退回标题白名单
        if config.EXAM_STRICT_URL_CHECK:
            return WindowVerdict("violation", "无法读取浏览器网址", "", fullscreen, True)
        if is_window_allowed(title, proc_name):
            return WindowVerdict("allowed", "标题匹配(网址未读取)", "", fullscreen, True)
        return WindowVerdict("violation", "无法读取网址且标题不在白名单", "", fullscreen, True)

    if is_window_allowed(title, proc_name):
        return WindowVerdict("allowed", "", url, fullscreen, is_browser)
    return WindowVerdict("violation", "", url, fullscreen, is_browser)


# ════════════════════════════════════════════════════════════════
#  截屏
# ════════════════════════════════════════════════════════════════

def compose_monitor_images(shots) -> Image.Image:
    """按虚拟屏幕坐标 [(left, top, PIL图), ...] 布局拼接。

    纯函数，便于单元测试；正确处理副屏负坐标、纵向排列等场景。
    """
    if not shots:
        return Image.new("RGB", (1, 1), (0, 0, 0))
    if len(shots) == 1 and shots[0][0] == 0 and shots[0][1] == 0:
        return shots[0][2]
    min_x = min(s[0] for s in shots)
    min_y = min(s[1] for s in shots)
    max_x = max(s[0] + s[2].width for s in shots)
    max_y = max(s[1] + s[2].height for s in shots)
    canvas = Image.new("RGB", (max_x - min_x, max_y - min_y), (0, 0, 0))
    for left, top, img in shots:
        canvas.paste(img, (left - min_x, top - min_y))
    return canvas


def grab_screen_multi() -> Image.Image:
    """抓取所有显示器并按虚拟屏幕坐标拼接为一张图。"""
    with mss.mss() as sct:
        monitors = sct.monitors[1:]  # 跳过 monitors[0]（合成虚拟屏）
        if not monitors:
            monitors = [sct.monitors[0]]
        shots = []
        for mon in monitors:
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            shots.append((mon["left"], mon["top"], img))
    return compose_monitor_images(shots)


def grab_screen() -> Image.Image:
    """根据配置选择单屏或多屏截取。"""
    if config.MULTI_MONITOR:
        return grab_screen_multi()
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        shot = sct.grab(monitor)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def encode_jpeg(pil_img: Image.Image):
    """压缩为 JPEG，返回 (jpeg_bytes, base64_str)。"""
    buf = BytesIO()
    pil_img.save(buf, format="JPEG", quality=config.SCREENSHOT_QUALITY)
    jpeg_bytes = buf.getvalue()
    b64_str = base64.b64encode(jpeg_bytes).decode("ascii")
    return jpeg_bytes, b64_str


def save_screenshot_local(jpeg_bytes: bytes, student_id: str) -> str:
    if not config.SAVE_LOCAL:
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    student_dir = Path(config.SCREENSHOT_DIR) / student_id / today
    student_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S_%f")
    filepath = student_dir / f"{ts}.jpg"
    filepath.write_bytes(jpeg_bytes)
    return str(filepath)


# ════════════════════════════════════════════════════════════════
#  客户端主类
# ════════════════════════════════════════════════════════════════

class ExamClient:
    def __init__(self, server_url: str, student_id: str):
        self.server_url = server_url
        self.student_id = student_id
        self.ws = None
        self.running = False
        self.alert_count = 0
        self.screenshot_count = 0
        self.connected = False
        self.last_alert_time = 0
        self.last_hash = None
        self.last_force_send = 0
        self.has_saved_once = False
        self.consecutive_violations = 0
        self.exam_grace_until = time.time() + config.EXAM_GRACE_SECONDS
        self.loop = None
        self._own_hwnd = 0
        self._lock_mode = config.CLIENT_LOCK_MODE \
            if config.CLIENT_LOCK_MODE in ("popup", "overlay") else "popup"
        self._popup_state = "hidden"
        self._alert_active = False
        self._alert_hide_after = None
        # GUI
        self.root = None
        self.status_label = None
        self.alert_label = None
        self.counter_label = None
        self.current_window_label = None
        self.alert_frame = None
        self.center_frame = None
        self._last_win_update = 0

    # ──────────── GUI ────────────

    def setup_gui(self):
        self.root = tk.Tk()
        self.root.title("考试监控客户端")
        self.root.configure(bg=config.COLOR_BG)
        self.root.attributes("-topmost", True)
        self.root.attributes("-fullscreen", True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_attempt)

        self.root.bind("<Alt-F4>", lambda e: "break")
        self.root.bind("<Escape>", lambda e: "break")
        self.root.bind("<Control-w>", lambda e: "break")

        self.top_bar = tk.Frame(self.root, bg=config.COLOR_CARD_BG, height=50)
        self.top_bar.pack(fill=tk.X, side=tk.TOP)
        self.top_bar.pack_propagate(False)

        self.status_label = tk.Label(
            self.top_bar, text="● 连接中...", font=("Microsoft YaHei", 12),
            bg=config.COLOR_CARD_BG, fg="#FFC107",
        )
        self.status_label.pack(side=tk.LEFT, padx=15)

        self.current_window_label = tk.Label(
            self.top_bar, text="当前窗口: 正在检测...", font=("Microsoft YaHei", 10),
            bg=config.COLOR_CARD_BG, fg="#888888",
        )
        self.current_window_label.pack(side=tk.LEFT, padx=15)

        self.counter_label = tk.Label(
            self.top_bar, text=f"考生: {self.student_id} | 截屏: 0 | 报警: 0",
            font=("Microsoft YaHei", 11), bg=config.COLOR_CARD_BG, fg=config.COLOR_TEXT,
        )
        self.counter_label.pack(side=tk.RIGHT, padx=15)

        self.alert_frame = tk.Frame(self.root, bg=config.COLOR_ALERT, height=40)
        self.alert_label = tk.Label(
            self.alert_frame, text="", font=("Microsoft YaHei", 12, "bold"),
            bg=config.COLOR_ALERT, fg="white",
        )
        self.alert_label.pack(expand=True)

        self.center_frame = tk.Frame(self.root, bg=config.COLOR_BG)
        center_frame = self.center_frame
        center_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center_frame, text="考试监控已启动",
            font=("Microsoft YaHei", 28, "bold"),
            bg=config.COLOR_BG, fg=config.COLOR_TEXT,
        ).pack()

        tk.Label(
            center_frame, text="请勿切换窗口，系统正在监控屏幕",
            font=("Microsoft YaHei", 14),
            bg=config.COLOR_BG, fg="#FF5722",
        ).pack(pady=(10, 0))

        tk.Label(
            center_frame,
            text=f"白名单窗口: {', '.join(config.WHITE_LIST[:5])}",
            font=("Microsoft YaHei", 10),
            bg=config.COLOR_BG, fg="#888888",
        ).pack(pady=(20, 0))

        monitor_info = "多屏拼接" if config.MULTI_MONITOR else "单屏"
        tk.Label(
            center_frame, text=f"截屏模式: {monitor_info}",
            font=("Microsoft YaHei", 10),
            bg=config.COLOR_BG, fg="#666666",
        ).pack(pady=(5, 0))

        tk.Label(
            center_frame, text="如需退出请联系监考老师",
            font=("Microsoft YaHei", 10),
            bg=config.COLOR_BG, fg="#666666",
        ).pack(pady=(5, 0))

    def update_status(self, text, color):
        if self.status_label:
            self.status_label.config(text=text, fg=color)
        if self._lock_mode == "popup" and self._popup_state != "alert":
            # 断开/异常时弹出角落状态窗；正常连接时隐藏（不遮挡考试页面）
            if any(k in text for k in ("重连", "断开", "异常", "认证失败", "拒绝", "失败")):
                self._set_popup("small")
            else:
                self._set_popup("hidden")

    def update_counter(self):
        if self.counter_label:
            self.counter_label.config(
                text=f"考生: {self.student_id} | 截屏: {self.screenshot_count} | 报警: {self.alert_count}"
            )

    def show_alert(self, message):
        if self.alert_frame and self.alert_label:
            self.alert_label.config(text=f"⚠ {message}")
            self.alert_frame.pack(fill=tk.X, side=tk.TOP, before=self.top_bar)
        self._alert_active = True
        if self._lock_mode == "popup":
            # popup 模式：平时窗口隐藏，违规时才弹出全屏警示
            self._set_popup("alert")
            hold = config.CLIENT_ALERT_HOLD_SECONDS
            if hold > 0:
                if self._alert_hide_after is not None:
                    try:
                        self.root.after_cancel(self._alert_hide_after)
                    except Exception:
                        pass
                self._alert_hide_after = self.root.after(hold * 1000, self.hide_alert)
        else:
            self.root.after(config.ALERT_FLASH_DURATION, self.hide_alert)

    def hide_alert(self):
        if self.alert_frame:
            self.alert_frame.pack_forget()
        self._alert_active = False
        # 取消 pending 的自动隐藏定时器，避免旧定时器提前撤掉新一轮警示
        if self._alert_hide_after is not None:
            try:
                self.root.after_cancel(self._alert_hide_after)
            except Exception:
                pass
            self._alert_hide_after = None
        if self._lock_mode == "popup":
            # 恢复合规后撤掉警示；仍未连接时显示角落状态窗
            self._set_popup("hidden" if self.connected else "small")

    def _set_popup(self, state: str):
        """popup 锁定模式窗口三态：hidden（隐藏）/ alert（全屏警示）/ small（角落状态窗）。"""
        if self._lock_mode != "popup" or state == self._popup_state:
            return
        self._popup_state = state
        if state == "hidden":
            self.root.withdraw()
        elif state == "alert":
            self.root.attributes("-fullscreen", True)
            if self.center_frame is not None:
                self.center_frame.place(relx=0.5, rely=0.5, anchor="center")
            self.root.deiconify()
            self.root.lift()
        elif state == "small":
            self.root.attributes("-fullscreen", False)
            if self.center_frame is not None:
                self.center_frame.place_forget()  # 小窗只显示状态栏，隐藏大字号提示
            sw = self.root.winfo_screenwidth()
            self.root.geometry(f"480x140+{max(sw - 500, 0)}+40")
            self.root.deiconify()
            self.root.lift()

    def on_close_attempt(self):
        pass

    # ──────────── 窗口焦点检测 ────────────

    def focus_check_loop(self):
        # COM 初始化：uiautomation 需要，此函数运行在子线程，主线程的初始化无效
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass
        try:
            while self.running:
                try:
                    hwnd, title, proc_name = get_foreground_window_info()
                    now = time.time()

                    # 仅对浏览器读取网址/全屏状态，其他窗口零开销
                    proc_l = (proc_name or "").lower()
                    if config.EXAM_ENABLED and proc_l in config.EXAM_BROWSERS_LOWER:
                        url = get_browser_url(hwnd)
                        fullscreen = is_window_fullscreen(hwnd)
                    else:
                        url, fullscreen = "", False

                    verdict = classify_window(
                        self._own_hwnd, hwnd, title, proc_name,
                        now, self.exam_grace_until, url, fullscreen,
                    )

                    if verdict.status == "violation":
                        self.consecutive_violations += 1
                    else:
                        self.consecutive_violations = 0
                        if verdict.status == "allowed" and self._alert_active \
                                and self._popup_state == "alert":
                            # 已恢复合规：撤掉 popup 全屏警示
                            self.root.after(0, self.hide_alert)

                    if now - self._last_win_update >= 2:
                        self._last_win_update = now
                        self.root.after(0, self.update_current_window, title, verdict)

                    if verdict.status == "violation" and \
                            self.consecutive_violations >= config.VIOLATION_CONFIRM_COUNT:
                        if now - self.last_alert_time > 3:
                            self.last_alert_time = now
                            self.alert_count += 1
                            self.root.after(0, self.update_counter)
                            reason_msg = verdict.reason or f"检测到切换窗口: {title}"
                            if not self._alert_active:
                                # 报警即截图：先取证（违规瞬间画面），再弹警示，确保证据不含警示遮罩
                                try:
                                    pil_img = grab_screen()
                                    h = image_hash(pil_img)
                                    self.last_hash = h
                                    jpeg_bytes, _b64 = encode_jpeg(pil_img)
                                    save_screenshot_local(jpeg_bytes, self.student_id)
                                    self.screenshot_count += 1
                                    self.root.after(0, self.update_counter)
                                    if self.ws:
                                        asyncio.run_coroutine_threadsafe(
                                            self.send_evidence_and_alert(
                                                jpeg_bytes, h.hex(), title, verdict),
                                            self.loop,
                                        )
                                except Exception as e:
                                    log.error("报警取证失败: %s", e)
                            elif self.ws:
                                # 警示未解除的持续违规：仅刷新报警信息（首帧证据已取，避免拍到警示遮罩）
                                asyncio.run_coroutine_threadsafe(
                                    self.send_alert(title, verdict.reason, verdict.url),
                                    self.loop,
                                )
                            self.root.after(0, self.show_alert, reason_msg)
                except Exception as e:
                    log.error("窗口检测错误: %s", e)
                time.sleep(config.FOCUS_CHECK_INTERVAL)
        finally:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    def update_current_window(self, title, verdict):
        if not self.current_window_label:
            return
        status = verdict.status
        if status == "self":
            shown, color = "(监控界面本身)", config.COLOR_NORMAL
        elif status == "desktop":
            shown, color = "(桌面/无窗口)", "#FFC107"
        else:
            shown = title if len(title) <= 36 else title[:36] + "..."
            if status == "violation":
                shown += f" [违规: {verdict.reason or '窗口不符'}]"
                color = config.COLOR_ALERT
            else:
                shown += " [合规]" if not verdict.reason else f" [{verdict.reason}]"
                color = config.COLOR_NORMAL
        self.current_window_label.config(text=f"当前窗口: {shown}", fg=color)

    # ──────────── WebSocket 发送 ────────────

    async def send_alert(self, target_window: str, reason: str = "", url: str = ""):
        if not self.ws:
            return
        msg = {
            "type": "alert",
            "client_id": self.student_id,
            "target_window": target_window,
            "reason": reason,
            "url": url,
            "timestamp": time.time(),
            "alert_count": self.alert_count,
        }
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            pass

    async def send_evidence_and_alert(self, jpeg_bytes: bytes, hash_hex: str,
                                      target_window: str, verdict: WindowVerdict):
        """违规取证：先推证据截图（二进制帧+哈希存证），再上报报警详情。"""
        await self.send_screenshot_binary(jpeg_bytes, hash_hex)
        await self.send_alert(target_window, verdict.reason, verdict.url)

    async def send_screenshot_binary(self, jpeg_bytes: bytes, hash_hex: str = ""):
        """先发二进制帧（JPEG），再发 JSON 元数据。"""
        if not self.ws:
            return
        try:
            await self.ws.send(jpeg_bytes)
            meta = {
                "type": "screenshot_meta",
                "client_id": self.student_id,
                "hash": hash_hex,
                "timestamp": time.time(),
            }
            await self.ws.send(json.dumps(meta))
        except Exception:
            pass

    async def send_heartbeat(self):
        if not self.ws:
            return
        msg = {
            "type": "heartbeat",
            "client_id": self.student_id,
            "timestamp": time.time(),
            "hostname": platform.node(),
        }
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            pass

    # ──────────── 截屏循环 ────────────

    async def screenshot_loop(self):
        while self.running:
            try:
                pil_img = grab_screen()
                cur_hash = image_hash(pil_img)
                hamming = hash_diff(cur_hash, self.last_hash)
                self.last_hash = cur_hash
                now = time.time()

                save_local = not self.has_saved_once or hamming >= config.HASH_LOCAL_THRESHOLD
                send_now = hamming >= config.HASH_SEND_THRESHOLD or \
                           (now - self.last_force_send) >= config.FORCE_SEND_INTERVAL

                if save_local or send_now:
                    jpeg_bytes, _b64_str = encode_jpeg(pil_img)

                    if save_local:
                        save_screenshot_local(jpeg_bytes, self.student_id)
                        self.has_saved_once = True

                    if send_now:
                        self.last_force_send = now
                        self.screenshot_count += 1
                        self.root.after(0, self.update_counter)
                        await self.send_screenshot_binary(jpeg_bytes, cur_hash.hex())
            except Exception as e:
                log.error("截屏错误: %s", e)
            await asyncio.sleep(config.SCREENSHOT_INTERVAL)

    # ──────────── 心跳 ────────────

    async def heartbeat_loop(self):
        while self.running:
            await self.send_heartbeat()
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)

    # ──────────── 监听服务器指令 ────────────

    async def monitor_server_messages(self):
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    continue
                data = json.loads(message)
                if data.get("type") == "stop":
                    log.info("收到监考端结束指令，正在退出...")
                    self.root.after(0, self.on_teacher_stop)
                elif data.get("type") == "register_rejected":
                    reason = data.get("reason", "认证失败")
                    log.error("注册被拒绝: %s", reason)
                    self.root.after(0, self.update_status, f"● {reason}", config.COLOR_ALERT)
                    self.running = False
        except Exception:
            pass

    def on_teacher_stop(self):
        self.root.destroy()

    # ──────────── 连接循环 ────────────

    async def connect(self):
        retry_delay = config.RECONNECT_BASE_DELAY
        ssl_context = None
        if config.USE_TLS:
            try:
                ssl_context = build_client_ssl_context()
            except Exception as e:
                log.error("TLS context build failed: %s", e)
                self.running = False
                return
        while self.running:
            try:
                async with websockets.connect(
                    self.server_url,
                    open_timeout=config.CONNECT_TIMEOUT,
                    ssl=ssl_context,
                    ping_interval=None,
                ) as ws:
                    self.ws = ws
                    self.connected = True
                    self.root.after(0, self.update_status, "● 已连接", config.COLOR_NORMAL)
                    retry_delay = config.RECONNECT_BASE_DELAY  # 连接成功，重置退避
                    log.info("已连接到服务器: %s", self.server_url)

                    # 注册（携带 Token）
                    register_msg = {
                        "type": "register",
                        "client_id": self.student_id,
                        "hostname": platform.node(),
                        "timestamp": time.time(),
                        "token": config.AUTH_TOKEN,
                    }
                    await ws.send(json.dumps(register_msg))

                    # 等待注册确认
                    try:
                        resp_raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        if isinstance(resp_raw, bytes):
                            resp = json.loads(resp_raw)
                        else:
                            resp = json.loads(resp_raw)
                        if resp.get("type") == "register_rejected":
                            # 令牌错误是永久性错误，重连也无法解决：停止循环而非指数退避重试
                            reason = resp.get("reason", "认证失败")
                            log.error("注册被拒绝: %s，停止重连", reason)
                            self.root.after(0, self.update_status, f"● {reason}", config.COLOR_ALERT)
                            self.running = False
                            return
                    except asyncio.TimeoutError:
                        log.warning("注册超时，继续运行（服务端可能未发送确认）")

                    await asyncio.gather(
                        self.screenshot_loop(),
                        self.heartbeat_loop(),
                        self.monitor_server_messages(),
                    )
            except (ConnectionRefusedError, OSError, TimeoutError) as e:
                self.connected = False
                self.ws = None
                self.root.after(0, self.update_status, "● 连接断开，重连中...", config.COLOR_ALERT)
                log.warning("连接失败: %s，%d秒后重试", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, config.RECONNECT_MAX_DELAY)
            except Exception as e:
                self.connected = False
                self.ws = None
                self.root.after(0, self.update_status, "● 连接异常", config.COLOR_ALERT)
                log.error("连接异常: %s", e)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, config.RECONNECT_MAX_DELAY)

    # ──────────── 启动 ────────────

    def start(self):
        self.running = True
        self.setup_gui()
        if self._lock_mode == "popup":
            # popup 模式：正常状态窗口隐藏，不遮挡考试页面、不进入截图
            self.root.withdraw()
        # 焦点检测线程只读此值，避免跨线程调用 Tk
        self._own_hwnd = self.root.winfo_id()

        # 先建事件循环，再启动焦点检测线程，避免 self.loop 未就绪的竞态
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        focus_thread = threading.Thread(target=self.focus_check_loop, daemon=True)
        focus_thread.start()

        def run_loop():
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self.connect())
            except RuntimeError:
                # 主线程退出时 loop.stop() 会中断 run_until_complete，属正常路径
                pass

        ws_thread = threading.Thread(target=run_loop, daemon=True)
        ws_thread.start()

        self.root.mainloop()
        self.running = False
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="考试端监控客户端 — 自动截屏、窗口检测、实时上报",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python client.py --student-id 2024001
  python client.py --server ws://192.168.1.100:8765 --student-id 2024001
  python client.py --student-id 2024001 --server ws://10.0.0.1:8765
        """,
    )
    parser.add_argument(
        "--server", default=f"ws://127.0.0.1:{config.SERVER_PORT}",
        help=f"监考端服务器地址 (默认: ws://127.0.0.1:{config.SERVER_PORT})",
    )
    parser.add_argument(
        "--student-id", required=True,
        help="考生学号（唯一标识）",
    )
    args = parser.parse_args()

    client = ExamClient(args.server, args.student_id)
    client.start()


if __name__ == "__main__":
    main()
