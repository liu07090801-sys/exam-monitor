"""共享 UI 主题 — 设计令牌 + 自绘控件（监考端 / 考生端通用）。

纯 tkinter 实现，无新增依赖；所有颜色取自 config 的 colors 调色板
（config.yaml 的 colors 段可整体换肤）。提供：

  - Pal / font / mix           设计令牌与颜色工具
  - style(root)                ttk 扁平滚动条等基础样式
  - Pill                       胶囊徽章（可带状态圆点，可点击）
  - RoundButton                圆角按钮（primary / danger / ghost / success）
  - Card                       圆角卡片（可选左侧状态色条、整卡点击）
  - Banner                     圆角警示横幅（可脉动）
  - Dot                        状态圆点（带光晕）
  - make_scroll_canvas         滚动画布（自动跟随宽度 + 滚轮）
  - dialog_chrome              无边框自定义标题栏弹窗
  - draw_shield                盾牌图标（品牌图形）
"""

import tkinter as tk
import tkinter.font as tkfont

import config

# ─── 字体 ────────────────────────────────────────────────────────
FAMILY = "Microsoft YaHei"
MONO = "Consolas"

_font_cache: dict[tuple, tkfont.Font] = {}


def font(size: int = 10, weight: str = "normal", mono: bool = False):
    """返回字体元组。"""
    fam = MONO if mono else FAMILY
    return (fam, size, weight)


def _tkfont(size: int, weight: str, mono: bool = False) -> tkfont.Font:
    key = (size, weight, mono)
    f = _font_cache.get(key)
    if f is not None:
        try:
            f.actual()          # 探测：Tk 根窗口销毁后旧字体对象失效
        except tk.TclError:
            f = None
    if f is None:
        f = tkfont.Font(family=MONO if mono else FAMILY,
                        size=size, weight=weight)
        _font_cache[key] = f
    return f


# ─── 颜色工具 ────────────────────────────────────────────────────
def _to_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _to_hex(t) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in t))


def mix(a: str, b: str, t: float) -> str:
    """a、b 按比例 t 混合（t=0 -> a, t=1 -> b）。"""
    ra, rb = _to_rgb(a), _to_rgb(b)
    return _to_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def darker(c: str, t: float = 0.22) -> str:
    return mix(c, "#000000", t)


def lighter(c: str, t: float = 0.22) -> str:
    return mix(c, "#ffffff", t)


def readable_on(bg: str) -> str:
    """在给定背景上可读的前景色（近白 / 近黑）。"""
    r, g, b = _to_rgb(bg)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#10151f" if lum > 0.55 else "#ffffff"


class _Pal:
    """调色板：每次属性访问实时读取 config，支持热加载换肤。

    基础六色（normal/alert/offline/bg/card_bg/text）沿用 config；
    扩展色缺失时回退到按基础色推导，保证旧 config.yaml 兼容。
    """

    @property
    def bg(self):        # 窗口底色
        return getattr(config, "COLOR_BG", "#0b1120")

    @property
    def card(self):      # 卡片/面板
        return getattr(config, "COLOR_CARD_BG", "#151f38")

    @property
    def text(self):
        return getattr(config, "COLOR_TEXT", "#e6eaf2")

    @property
    def normal(self):
        return getattr(config, "COLOR_NORMAL", "#34d399")

    @property
    def alert(self):
        return getattr(config, "COLOR_ALERT", "#f87171")

    @property
    def offline(self):
        return getattr(config, "COLOR_OFFLINE", "#64748b")

    @property
    def muted(self):     # 次要文字
        return getattr(config, "COLOR_TEXT_MUTED", mix(self.text, self.card, 0.45))

    @property
    def accent(self):    # 主强调色（蓝）
        return getattr(config, "COLOR_ACCENT", "#4f8cff")

    @property
    def accent2(self):   # 次强调色（紫）
        return getattr(config, "COLOR_ACCENT2", "#8b5cf6")

    @property
    def warn(self):      # 警告黄
        return getattr(config, "COLOR_WARN", "#fbbf24")

    @property
    def success(self):   # 成功绿
        return getattr(config, "COLOR_SUCCESS", self.normal)

    @property
    def border(self):    # 描边
        return getattr(config, "COLOR_BORDER", mix(self.card, "#ffffff", 0.09))

    @property
    def hover(self):     # 悬停/徽章底
        return getattr(config, "COLOR_HOVER", mix(self.card, "#ffffff", 0.06))

    @property
    def deep(self):      # 比窗口底色更深（阴影/输入底）
        return mix(self.bg, "#000000", 0.28)


Pal = _Pal()


# ─── 基础样式（ttk 滚动条等） ────────────────────────────────────
def style(root=None) -> None:
    from tkinter import ttk
    st = ttk.Style()
    try:
        st.theme_use("clam")
    except tk.TclError:
        return
    bg, hover, muted = Pal.bg, Pal.hover, Pal.muted
    st.configure(
        "TScrollbar",
        background=hover, troughcolor=bg, bordercolor=bg,
        lightcolor=bg, darkcolor=bg, arrowcolor=muted,
        relief="flat", arrowsize=0, gripcount=0,
    )
    st.map("TScrollbar", background=[("active", lighter(Pal.hover, 0.15))])
    st.configure("Vertical.TScrollbar", width=9)
    st.configure("Horizontal.TScrollbar", width=9)
    # 面板内滚动条：trough 用卡片底色（放在 Card 里时更协调）
    st.configure("Panel.TScrollbar", troughcolor=Pal.card, background=hover,
                 bordercolor=Pal.card, lightcolor=Pal.card, darkcolor=Pal.card,
                 arrowcolor=muted, relief="flat", width=9)
    # 去掉 clam 默认布局中的上下箭头（扁平深色主题里很丑）
    for name, orient, sticky in (("Vertical.TScrollbar", "Vertical", "ns"),
                                 ("Horizontal.TScrollbar", "Horizontal", "ew"),
                                 ("Panel.TScrollbar", "Vertical", "ns")):
        try:
            st.layout(name, [(orient + ".Scrollbar.trough", {
                "children": [(orient + ".Scrollbar.thumb",
                             {"expand": "true", "sticky": sticky})],
                "sticky": sticky,
            })])
        except tk.TclError:
            pass


# ─── 圆角矩形绘制 ────────────────────────────────────────────────
def round_rect(cv: tk.Canvas, x0, y0, x1, y1, r, **kw):
    """smooth 多边形画圆角矩形；r<=0 退化为普通矩形。"""
    kw.pop("outline", None)
    r = max(0, min(r, (y1 - y0) / 2, (x1 - x0) / 2))
    if r < 1:
        return cv.create_rectangle(x0, y0, x1, y1, outline="", **kw)
    pts = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]
    return cv.create_polygon(pts, smooth=True, outline="", **kw)


# ─── 状态圆点 ────────────────────────────────────────────────────
class Dot(tk.Canvas):
    """带外圈光晕的状态圆点。"""

    def __init__(self, parent, size: int = 14, color: str | None = None, bg: str | None = None):
        super().__init__(parent, width=size, height=size,
                         bg=bg or parent["bg"], highlightthickness=0, bd=0)
        self._size = size
        self.set(color or Pal.normal)

    def set(self, color: str):
        self.delete("all")
        s = self._size
        self.create_oval(s * 0.16, s * 0.20, s * 0.84, s * 0.88,
                         fill=darker(color, 0.55), outline="")
        self.create_oval(s * 0.24, s * 0.14, s * 0.92, s * 0.82,
                         fill=color, outline="")


# ─── 胶囊徽章 ────────────────────────────────────────────────────
class Pill(tk.Canvas):
    """圆角信息徽章：可选左侧状态点。set(...) 动态更新。"""

    def __init__(self, parent, text: str = "", size: int = 10, weight: str = "normal",
                 fg: str | None = None, bg: str | None = None, surround: str | None = None,
                 dot: str | None = None, padx: int = 11, pady: int = 5,
                 command=None):
        self._surround = surround or parent["bg"]
        super().__init__(parent, bg=self._surround, highlightthickness=0, bd=0)
        self._size, self._weight = size, weight
        self._padx, self._pady = padx, pady
        self._fg = fg or Pal.text
        self._bg0 = self._bg = bg or Pal.hover
        self._border = None
        self._dot = dot
        self._text = text
        self._command = command
        if command:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda e: self._command and self._command())
            self.bind("<Enter>", lambda e: self._hovered(True))
            self.bind("<Leave>", lambda e: self._hovered(False))
        self.redraw()

    def _hovered(self, on: bool):
        self._bg = lighter(self._bg0, 0.14) if on else self._bg0
        self.redraw()

    @property
    def text(self):
        return self._text

    def set(self, text: str | None = None, fg: str | None = None,
            bg: str | None = None, dot: str | None = None,
            surround: str | None = None):
        if text is not None:
            self._text = text
        if fg is not None:
            self._fg = fg
        if bg is not None:
            self._bg0 = self._bg = bg
        if dot is not None:
            self._dot = dot
        if surround is not None and surround != self._surround:
            self._surround = surround
            self.configure(bg=surround)
        self.redraw()

    def set_command(self, command):
        self._command = command
        self.configure(cursor="hand2" if command else "")

    def redraw(self):
        f = _tkfont(self._size, self._weight)
        tw = f.measure(self._text)
        th = f.metrics("ascent") + f.metrics("descent")
        dot_w = (self._size + 7) if self._dot else 0
        w = int(tw + dot_w + self._padx * 2)
        h = int(th + self._pady * 2)
        self.configure(width=w, height=h)
        self.delete("all")
        round_rect(self, 0, 0, w - 1, h - 1, h / 2 - 1, fill=self._bg)
        x = self._padx
        if self._dot:
            r = self._size * 0.34
            cy = h / 2
            self.create_oval(x, cy - r, x + 2 * r, cy + r, fill=self._dot, outline="")
            x += dot_w
        self.create_text(x + 2, h / 2, text=self._text,
                         font=(FAMILY, self._size, self._weight),
                         fill=self._fg, anchor="w")


# ─── 圆角按钮 ────────────────────────────────────────────────────
class RoundButton(tk.Canvas):
    """扁平圆角按钮。kind: primary / danger / ghost / success。"""

    def __init__(self, parent, text: str, command=None, kind: str = "primary",
                 size: int = 10, padx: int = 16, pady: int = 8, surround=None,
                 icon: str = ""):
        self._surround = surround or parent["bg"]
        super().__init__(parent, bg=self._surround, highlightthickness=0, bd=0)
        self._text, self._icon = text, icon
        self._command, self._kind = command, kind
        self._size, self._padx, self._pady = size, padx, pady
        self._hover, self._down, self._enabled = False, False, True
        self.configure(cursor="hand2")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.redraw()

    def _colors(self):
        k = self._kind
        if k == "primary":
            base, fg = Pal.accent, "#ffffff"
        elif k == "danger":
            base, fg = darker(Pal.alert, 0.10), "#ffffff"
        elif k == "success":
            base, fg = Pal.success, "#10151f"
        else:  # ghost
            base, fg = Pal.hover, Pal.text
        if self._down:
            base = darker(base, 0.25)
        elif self._hover:
            base = lighter(base, 0.13)
        if not self._enabled:
            base, fg = Pal.deep, Pal.muted
        border = Pal.border if k == "ghost" else base
        return base, fg, border

    def _on_enter(self, _):
        self._hover = True
        self.redraw()

    def _on_leave(self, _):
        self._hover = self._down = False
        self.redraw()

    def _on_press(self, _):
        if self._enabled:
            self._down = True
            self.redraw()
        return "break"

    def _on_release(self, ev):
        self._down = False
        self.redraw()
        if self._enabled and self._command:
            inside = (0 <= ev.x <= self.winfo_width() and
                      0 <= ev.y <= self.winfo_height())
            if inside:
                self._command()
        return "break"

    def set_text(self, text: str):
        self._text = text
        self.redraw()

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "")
        self.redraw()

    def invoke(self):
        if self._enabled and self._command:
            self._command()

    def set_surround(self, color: str):
        if color != self._surround:
            self._surround = color
            self.configure(bg=color)

    def redraw(self):
        f = _tkfont(self._size, "bold")
        label = (self._icon + "  " if self._icon else "") + self._text
        tw = f.measure(label)
        th = f.metrics("ascent") + f.metrics("descent")
        w = int(tw + self._padx * 2)
        h = int(th + self._pady * 2)
        self.configure(width=w, height=h)
        self.delete("all")
        bg, fg, border = self._colors()
        r = h / 2 - 1
        if self._kind == "ghost":
            round_rect(self, 0, 0, w - 1, h - 1, r, fill=border)
            round_rect(self, 2, 2, w - 3, h - 3, max(0, r - 2), fill=bg)
        else:
            round_rect(self, 0, 0, w - 1, h - 1, r, fill=bg)
        self.create_text(w / 2, h / 2, text=label, font=(FAMILY, self._size, "bold"),
                         fill=fg)


# ─── 圆角卡片 ────────────────────────────────────────────────────
class Card(tk.Frame):
    """圆角面板：self.inner 上放内容；可选 command 整卡点击、accent 左侧状态条。"""

    def __init__(self, parent, *, radius: int = 14, pad: int = 12,
                 fill: str | None = None, command=None, min_h: int = 0,
                 min_w: int = 0):
        self._fill = fill or Pal.card
        super().__init__(parent, bg=parent["bg"])
        self._radius, self._pad = radius, pad
        self._command = command
        self._accent: str | None = None
        self._min_h = min_h
        self._redrawing = False
        self.canvas = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0)
        # 父容器不强制定尺寸时（pack 场景），用请求值兜底
        if min_h:
            self.canvas.configure(height=min_h)
        if min_w:
            self.canvas.configure(width=min_w)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=self._fill)
        self._win = self.canvas.create_window(pad, pad, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", self._redraw)
        if command:
            self.bind_click(command)

    # -- 工具 --
    @property
    def bg_fill(self):
        return self._fill

    def add(self, **kw):
        """在卡片内容区创建一个同底色 Frame。"""
        kw.setdefault("bg", self._fill)
        return tk.Frame(self.inner, **kw)

    def label(self, text="", size=10, weight="normal", fg=None, **kw):
        """创建与卡片同底色的 Label。"""
        kw.setdefault("bg", self._fill)
        kw.setdefault("font", font(size, weight))
        return tk.Label(self.inner, text=text, fg=fg or Pal.text, **kw)

    def _bind_recursive(self, w, cmd):
        w.bind("<Button-1>", lambda e: cmd())
        if isinstance(w, (tk.Canvas,)):
            pass
        for c in w.winfo_children():
            self._bind_recursive(c, cmd)

    def bind_click(self, cmd):
        self._command = cmd
        for w in (self, self.canvas, self.inner):
            try:
                w.configure(cursor="hand2")
            except tk.TclError:
                pass
        self._bind_recursive(self.inner, cmd)

    def set_accent(self, color: str | None):
        self._accent = color
        self._redraw()

    def _redraw(self, *_):
        if self._redrawing:
            return
        self._redrawing = True
        try:
            w = self.canvas.winfo_width()
            h = max(self.canvas.winfo_height(), self._min_h)
            if w <= 4:
                return
            self.canvas.delete("shape")
            r, p = self._radius, self._pad
            # 底部阴影 + 面板
            round_rect(self.canvas, 0, 2, w - 1, h + 1, r, fill=Pal.deep)
            round_rect(self.canvas, 0, 0, w - 1, h - 1, r, fill=self._fill)
            if self._accent:
                bh = max(24, (h - 2 * p) * 0.55)
                y0 = (h - bh) / 2
                round_rect(self.canvas, p - 8, y0, p - 4, y0 + bh, 2, fill=self._accent)
            self.canvas.itemconfigure(
                self._win, width=max(10, w - 2 * p), height=max(10, h - 2 * p))
        finally:
            self._redrawing = False


# ─── 警示横幅 ────────────────────────────────────────────────────
class Banner(tk.Canvas):
    """圆角警示横幅（红底白字，可脉动）。"""

    def __init__(self, parent, *, height: int = 46, radius: int = 12,
                 surround: str | None = None, icon: str = "⚠"):
        self._surround = surround or parent["bg"]
        super().__init__(parent, height=height, bg=self._surround,
                         highlightthickness=0, bd=0)
        self._h, self._r, self._icon = height, radius, icon
        self._text = ""
        self._pulse = False
        self._phase = 0
        self._job = None
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, text: str):
        self._text = text
        self.redraw()

    def start_pulse(self):
        if not self._pulse:
            self._pulse = True
            self._tick()

    def stop_pulse(self):
        self._pulse = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._phase = 0
        self.redraw()

    def _tick(self):
        if not self._pulse:
            return
        self._phase ^= 1
        self.redraw()
        self._job = self.after(450, self._tick)

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 60)
        h = self._h
        base = lighter(Pal.alert, 0.10) if self._phase else Pal.alert
        round_rect(self, 0, 0, w - 1, h - 1, self._r, fill=darker(base, 0.45))
        round_rect(self, 2, 2, w - 3, h - 3, max(0, self._r - 2), fill=base)
        if self._text:
            self.create_text(20, h / 2, text=self._icon, font=(FAMILY, 14, "bold"),
                             fill="#ffffff", anchor="w")
            self.create_text(50, h / 2, text=self._text,
                             font=(FAMILY, 11, "bold"), fill="#ffffff", anchor="w")


# ─── 盾牌品牌图标 ────────────────────────────────────────────────
def draw_shield(cv: tk.Canvas, cx: float, cy: float, s: float,
                color: str, check: bool = True, tags=""):
    """画一枚盾牌（可选白色对勾）。s 为整体尺寸。"""
    pts = [cx, cy - s * 0.52, cx + s * 0.44, cy - s * 0.34,
           cx + s * 0.44, cy + s * 0.06, cx, cy + s * 0.56,
           cx - s * 0.44, cy + s * 0.06, cx - s * 0.44, cy - s * 0.34]
    cv.create_polygon(pts, smooth=True, fill=color, outline="", tags=tags)
    # 右半边稍暗制造立体感
    cv.create_polygon([cx, cy - s * 0.52, cx + s * 0.44, cy - s * 0.34,
                       cx + s * 0.44, cy + s * 0.06, cx, cy + s * 0.56],
                      smooth=True, fill=darker(color, 0.18), outline="", tags=tags)
    if check:
        lw = max(2, int(s * 0.10))
        cv.create_line(cx - s * 0.20, cy - s * 0.02, cx - s * 0.04, cy + s * 0.15,
                       cx + s * 0.24, cy - s * 0.16, fill="#ffffff", width=lw,
                       capstyle="round", joinstyle="round", tags=tags)


# ─── 滚动画布 ────────────────────────────────────────────────────
def make_scroll_canvas(parent, *, bg: str | None = None, scrollbar: bool = True):
    """返回 (holder, canvas, inner)。inner 宽度自动跟随 canvas，支持滚轮。"""
    from tkinter import ttk
    bg = bg or parent["bg"]
    holder = tk.Frame(parent, bg=bg)
    cv = tk.Canvas(holder, bg=bg, highlightthickness=0, bd=0)
    inner = tk.Frame(cv, bg=bg)
    if scrollbar:
        sb = ttk.Scrollbar(holder, orient="vertical", command=cv.yview,
                           style="TScrollbar")
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
    cv.pack(side="left", fill="both", expand=True)
    win = cv.create_window(0, 0, window=inner, anchor="nw")

    def on_canvas_cfg(e):
        cv.itemconfigure(win, width=max(e.width, 1))

    def on_inner_cfg(e):
        cv.configure(scrollregion=cv.bbox("all") or (0, 0, 0, 0))

    def inside():
        try:
            x, y = cv.winfo_pointerxy()
            rx, ry = cv.winfo_rootx(), cv.winfo_rooty()
            return (rx <= x <= rx + cv.winfo_width() and
                    ry <= y <= ry + cv.winfo_height())
        except Exception:
            return False

    def on_wheel(ev):
        if inside():
            cv.yview_scroll(int(-ev.delta / 120), "units")
        return "break"

    cv.bind("<Configure>", on_canvas_cfg)
    inner.bind("<Configure>", on_inner_cfg)
    cv.bind_all("<MouseWheel>", on_wheel, add="+")
    return holder, cv, inner


# ─── 无边框弹窗 + 自定义标题栏 ───────────────────────────────────
def dialog_chrome(win: tk.Toplevel, title: str, subtitle: str = "",
                  accent: str | None = None):
    """给 Toplevel 装一个深色圆角标题栏：拖动、关闭按钮、Esc 关闭。

    返回 show(w, h)：设置尺寸并居中、置顶、聚焦。
    """
    win.overrideredirect(True)
    win.configure(bg=Pal.bg)
    bar = tk.Frame(win, bg=Pal.card, height=48)
    bar.pack(fill="x")
    bar.pack_propagate(False)
    tk.Frame(win, bg=Pal.border, height=1).pack(fill="x")

    left = tk.Frame(bar, bg=Pal.card)
    left.pack(side="left", fill="y", padx=14)
    ic = tk.Canvas(left, width=24, height=24, bg=Pal.card, highlightthickness=0)
    ic.pack(side="left", pady=12)
    draw_shield(ic, 12, 12, 17, accent or Pal.accent)
    tk.Label(left, text=title, font=(FAMILY, 11, "bold"), bg=Pal.card,
             fg=Pal.text).pack(side="left", padx=(8, 0))
    if subtitle:
        tk.Label(left, text=subtitle, font=(FAMILY, 9), bg=Pal.card,
                 fg=Pal.muted).pack(side="left", padx=(8, 0))

    close = RoundButton(bar, "✕", kind="ghost", size=10, padx=10, pady=6,
                        surround=Pal.card, command=lambda: win.destroy())
    close.pack(side="right", padx=12, pady=10)

    def _press(e):
        win._drag_off = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())

    def _move(e):
        dx, dy = getattr(win, "_drag_off", (0, 0))
        win.geometry("+{}+{}".format(e.x_root - dx, e.y_root - dy))

    bar.bind("<ButtonPress-1>", _press)
    bar.bind("<B1-Motion>", _move)
    win.bind("<Escape>", lambda e: win.destroy())

    def show(w, h):
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry("{}x{}+{}+{}".format(w, h, (sw - w) // 2, (sh - h) // 2))
        win.update_idletasks()
        win.attributes("-topmost", True)   # 先置顶确保浮在 QQ/浏览器等窗口之上
        win.lift()
        try:
            win.focus_force()
        except tk.TclError:
            pass

        def _unpin():
            try:
                win.attributes("-topmost", False)
            except tk.TclError:
                pass

        win.after(600, _unpin)
        return None

    return show


# ─── 通用小部件 ──────────────────────────────────────────────────
def section_title(parent, text: str, icon_color: str | None = None):
    """带左侧竖条的小节标题行。"""
    row = tk.Frame(parent, bg=parent["bg"])
    tk.Frame(row, width=4, height=16,
             bg=icon_color or Pal.accent).pack(side="left", padx=(0, 8))
    tk.Label(row, text=text, font=(FAMILY, 12, "bold"),
             bg=parent["bg"], fg=Pal.text).pack(side="left")
    return row
