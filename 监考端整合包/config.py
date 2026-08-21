"""
屏幕监控系统 - 配置加载器
从 config.yaml 读取配置，缺失项使用内置默认值。
"""

import sys
from pathlib import Path

import yaml

if getattr(sys, "frozen", False):
    # PyInstaller 打包运行：配置文件与 exe 同目录，方便考生端现场修改
    BASE_DIR: Path = Path(sys.executable).resolve().parent
else:
    BASE_DIR: Path = Path(__file__).resolve().parent

_CONFIG_PATH = BASE_DIR / "config.yaml"

_DEFAULTS = {
    "network": {"host": "0.0.0.0", "port": 8765, "token": "exam2024"},
    "tls": {
        "enabled": False,
        "cert_file": "cert.pem",
        "key_file": "key.pem",
        "ca_file": "ca.pem",
    },
    "screenshot": {
        "interval": 5, "quality": 60, "dir": "screenshots",
        "retention_days": 7, "multi_monitor": True,
    },
    "hash": {
        "size": 16, "send_threshold": 24,
        "local_threshold": 4, "force_send_interval": 30,
    },
    "window": {
        "check_interval": 1, "violation_confirm_count": 3,
        "whitelist": ["洛谷"], "whitelist_processes": [],
    },
    "exam": {
        "enabled": True,
        "url_whitelist": ["luogu.com.cn"],
        "browsers": ["chrome.exe", "msedge.exe", "firefox.exe",
                     "360se.exe", "360chrome.exe"],
        "fullscreen_required": True,
        "grace_seconds": 120,
        "strict_url_check": False,
    },
    "client": {
        "heartbeat_interval": 3, "connect_timeout": 5,
        "reconnect_base_delay": 2, "reconnect_max_delay": 30,
        "lock_mode": "popup", "alert_hold_seconds": 0,
    },
    "server": {
        "client_timeout": 15, "freeze_timeout": 90,
        "alert_hold_seconds": 300, "alert_flash_duration": 3000,
        "max_screenshot_history": 200, "preview_refresh_ms": 2000,
        "db_file": "monitor.db", "cleanup_interval": 3600,
        "teacher_password": "", "max_screenshots_disk": 0,
    },
    "colors": {
        "normal": "#4CAF50", "alert": "#F44336", "offline": "#9E9E9E",
        "bg": "#1a1a2e", "card_bg": "#16213e", "text": "#e0e0e0",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先。"""
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _load_yaml() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        return _deep_merge(_DEFAULTS, user)
    return _DEFAULTS


_cfg = _load_yaml()

# ─── 导出扁平化常量（兼容旧代码） ───
# 网络
SERVER_HOST: str = _cfg["network"]["host"]
SERVER_PORT: int = _cfg["network"]["port"]
AUTH_TOKEN: str = _cfg["network"]["token"]

# TLS
USE_TLS: bool = _cfg["tls"]["enabled"]
TLS_CERT_FILE: str = _cfg["tls"]["cert_file"]
TLS_KEY_FILE: str = _cfg["tls"]["key_file"]
TLS_CA_FILE: str = _cfg["tls"]["ca_file"]

# 截屏
SCREENSHOT_INTERVAL: int = _cfg["screenshot"]["interval"]
SCREENSHOT_QUALITY: int = _cfg["screenshot"]["quality"]
SCREENSHOT_DIR: str = _cfg["screenshot"]["dir"]
SCREENSHOT_RETENTION_DAYS: int = _cfg["screenshot"]["retention_days"]
MULTI_MONITOR: bool = _cfg["screenshot"]["multi_monitor"]

# 感知哈希
HASH_SIZE: int = _cfg["hash"]["size"]
HASH_SEND_THRESHOLD: int = _cfg["hash"]["send_threshold"]
HASH_LOCAL_THRESHOLD: int = _cfg["hash"]["local_threshold"]
FORCE_SEND_INTERVAL: int = _cfg["hash"]["force_send_interval"]

# 窗口检测
FOCUS_CHECK_INTERVAL: int = _cfg["window"]["check_interval"]
VIOLATION_CONFIRM_COUNT: int = _cfg["window"]["violation_confirm_count"]
WHITE_LIST: list = _cfg["window"]["whitelist"]
WHITELIST_PROCESSES: list = _cfg["window"]["whitelist_processes"]

# 考试网址与全屏强制
EXAM_ENABLED: bool = _cfg["exam"]["enabled"]
EXAM_URL_WHITELIST: list = _cfg["exam"]["url_whitelist"]
EXAM_BROWSERS: list = _cfg["exam"]["browsers"]
EXAM_BROWSERS_LOWER: tuple = tuple(b.lower() for b in EXAM_BROWSERS)
EXAM_FULLSCREEN_REQUIRED: bool = _cfg["exam"]["fullscreen_required"]
EXAM_GRACE_SECONDS: int = _cfg["exam"]["grace_seconds"]
EXAM_STRICT_URL_CHECK: bool = _cfg["exam"]["strict_url_check"]

# 客户端
HEARTBEAT_INTERVAL: int = _cfg["client"]["heartbeat_interval"]
CONNECT_TIMEOUT: int = _cfg["client"]["connect_timeout"]
RECONNECT_BASE_DELAY: int = _cfg["client"]["reconnect_base_delay"]
RECONNECT_MAX_DELAY: int = _cfg["client"]["reconnect_max_delay"]
CLIENT_LOCK_MODE: str = _cfg["client"]["lock_mode"]
CLIENT_ALERT_HOLD_SECONDS: int = _cfg["client"]["alert_hold_seconds"]

# 监考端
CLIENT_TIMEOUT: int = _cfg["server"]["client_timeout"]
FREEZE_TIMEOUT: int = _cfg["server"]["freeze_timeout"]
ALERT_HOLD_SECONDS: int = _cfg["server"]["alert_hold_seconds"]
ALERT_FLASH_DURATION: int = _cfg["server"]["alert_flash_duration"]
MAX_SCREENSHOT_HISTORY: int = _cfg["server"]["max_screenshot_history"]
PREVIEW_REFRESH_MS: int = _cfg["server"]["preview_refresh_ms"]
DB_FILE: str = _cfg["server"]["db_file"]
CLEANUP_INTERVAL: int = _cfg["server"]["cleanup_interval"]
TEACHER_PASSWORD: str = _cfg["server"]["teacher_password"]
MAX_SCREENSHOTS_DISK: int = _cfg["server"]["max_screenshots_disk"]

# 颜色
COLOR_NORMAL: str = _cfg["colors"]["normal"]
COLOR_ALERT: str = _cfg["colors"]["alert"]
COLOR_OFFLINE: str = _cfg["colors"]["offline"]
COLOR_BG: str = _cfg["colors"]["bg"]
COLOR_CARD_BG: str = _cfg["colors"]["card_bg"]
COLOR_TEXT: str = _cfg["colors"]["text"]


def reload():
    """运行时重新加载配置（热加载）。"""
    global _cfg
    global SERVER_HOST, SERVER_PORT, AUTH_TOKEN
    global USE_TLS, TLS_CERT_FILE, TLS_KEY_FILE, TLS_CA_FILE
    global SCREENSHOT_INTERVAL, SCREENSHOT_QUALITY, SCREENSHOT_DIR
    global SCREENSHOT_RETENTION_DAYS, MULTI_MONITOR
    global HASH_SIZE, HASH_SEND_THRESHOLD, HASH_LOCAL_THRESHOLD, FORCE_SEND_INTERVAL
    global FOCUS_CHECK_INTERVAL, VIOLATION_CONFIRM_COUNT, WHITE_LIST, WHITELIST_PROCESSES
    global EXAM_ENABLED, EXAM_URL_WHITELIST, EXAM_BROWSERS, EXAM_BROWSERS_LOWER
    global EXAM_FULLSCREEN_REQUIRED, EXAM_GRACE_SECONDS, EXAM_STRICT_URL_CHECK
    global HEARTBEAT_INTERVAL, CONNECT_TIMEOUT, RECONNECT_BASE_DELAY, RECONNECT_MAX_DELAY
    global CLIENT_LOCK_MODE, CLIENT_ALERT_HOLD_SECONDS
    global CLIENT_TIMEOUT, FREEZE_TIMEOUT, ALERT_HOLD_SECONDS, ALERT_FLASH_DURATION, MAX_SCREENSHOT_HISTORY, PREVIEW_REFRESH_MS
    global DB_FILE, CLEANUP_INTERVAL, TEACHER_PASSWORD, MAX_SCREENSHOTS_DISK
    global COLOR_NORMAL, COLOR_ALERT, COLOR_OFFLINE, COLOR_BG, COLOR_CARD_BG, COLOR_TEXT

    _cfg = _load_yaml()

    SERVER_HOST = _cfg["network"]["host"]
    SERVER_PORT = _cfg["network"]["port"]
    AUTH_TOKEN = _cfg["network"]["token"]

    USE_TLS = _cfg["tls"]["enabled"]
    TLS_CERT_FILE = _cfg["tls"]["cert_file"]
    TLS_KEY_FILE = _cfg["tls"]["key_file"]
    TLS_CA_FILE = _cfg["tls"]["ca_file"]

    SCREENSHOT_INTERVAL = _cfg["screenshot"]["interval"]
    SCREENSHOT_QUALITY = _cfg["screenshot"]["quality"]
    SCREENSHOT_DIR = _cfg["screenshot"]["dir"]
    SCREENSHOT_RETENTION_DAYS = _cfg["screenshot"]["retention_days"]
    MULTI_MONITOR = _cfg["screenshot"]["multi_monitor"]

    HASH_SIZE = _cfg["hash"]["size"]
    HASH_SEND_THRESHOLD = _cfg["hash"]["send_threshold"]
    HASH_LOCAL_THRESHOLD = _cfg["hash"]["local_threshold"]
    FORCE_SEND_INTERVAL = _cfg["hash"]["force_send_interval"]

    FOCUS_CHECK_INTERVAL = _cfg["window"]["check_interval"]
    VIOLATION_CONFIRM_COUNT = _cfg["window"]["violation_confirm_count"]
    WHITE_LIST = _cfg["window"]["whitelist"]
    WHITELIST_PROCESSES = _cfg["window"]["whitelist_processes"]

    EXAM_ENABLED = _cfg["exam"]["enabled"]
    EXAM_URL_WHITELIST = _cfg["exam"]["url_whitelist"]
    EXAM_BROWSERS = _cfg["exam"]["browsers"]
    EXAM_BROWSERS_LOWER = tuple(b.lower() for b in EXAM_BROWSERS)
    EXAM_FULLSCREEN_REQUIRED = _cfg["exam"]["fullscreen_required"]
    EXAM_GRACE_SECONDS = _cfg["exam"]["grace_seconds"]
    EXAM_STRICT_URL_CHECK = _cfg["exam"]["strict_url_check"]

    HEARTBEAT_INTERVAL = _cfg["client"]["heartbeat_interval"]
    CONNECT_TIMEOUT = _cfg["client"]["connect_timeout"]
    RECONNECT_BASE_DELAY = _cfg["client"]["reconnect_base_delay"]
    RECONNECT_MAX_DELAY = _cfg["client"]["reconnect_max_delay"]
    CLIENT_LOCK_MODE = _cfg["client"]["lock_mode"]
    CLIENT_ALERT_HOLD_SECONDS = _cfg["client"]["alert_hold_seconds"]

    CLIENT_TIMEOUT = _cfg["server"]["client_timeout"]
    FREEZE_TIMEOUT = _cfg["server"]["freeze_timeout"]
    ALERT_HOLD_SECONDS = _cfg["server"]["alert_hold_seconds"]
    ALERT_FLASH_DURATION = _cfg["server"]["alert_flash_duration"]
    MAX_SCREENSHOT_HISTORY = _cfg["server"]["max_screenshot_history"]
    PREVIEW_REFRESH_MS = _cfg["server"]["preview_refresh_ms"]
    DB_FILE = _cfg["server"]["db_file"]
    CLEANUP_INTERVAL = _cfg["server"]["cleanup_interval"]
    TEACHER_PASSWORD = _cfg["server"]["teacher_password"]
    MAX_SCREENSHOTS_DISK = _cfg["server"]["max_screenshots_disk"]

    COLOR_NORMAL = _cfg["colors"]["normal"]
    COLOR_ALERT = _cfg["colors"]["alert"]
    COLOR_OFFLINE = _cfg["colors"]["offline"]
    COLOR_BG = _cfg["colors"]["bg"]
    COLOR_CARD_BG = _cfg["colors"]["card_bg"]
    COLOR_TEXT = _cfg["colors"]["text"]
