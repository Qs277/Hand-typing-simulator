# -*- coding: utf-8 -*-
"""
手打模拟器 —— 屏幕框选 + 键盘级逐字输入
适用于禁止粘贴的网页 / 软件：框选输入框，逐字敲入（Unicode 键击，非剪贴板）。
"""

import ctypes
import ctypes.wintypes as wt
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageColor, ImageDraw, ImageGrab, ImageTk

# ----------------------------------------------------------------------------
# 基础配置
# ----------------------------------------------------------------------------

F = "Microsoft YaHei UI"

C = {
    "bg":       "#0B0E13",
    "panel":    "#121720",
    "panel_hi": "#161D28",
    "line":     "#1E2734",
    "line_hi":  "#2C3A4C",
    "text":     "#E9EFF8",
    "muted":    "#8B96A8",
    "dim":      "#5C6678",
    "accent":   "#FFB35C",
    "accent2":  "#FF7A45",
    "teal":     "#5FE3C0",
    "danger":   "#FF8A8A",
    "track":    "#1B2330",
}

SCALE = 1.0          # 运行时按系统 DPI 计算（150% 缩放时 = 1.5）
BASE_W = 456
BASE_H = 676
BASE_BAR_H = 62
KEY_COLOR = "#010203"


def S(v):
    """把设计稿像素换算为当前 DPI 下的实际像素"""
    return int(round(v * SCALE))


# ----------------------------------------------------------------------------
# Win32 键鼠注入
# ----------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

VK_RETURN = 0x0D
VK_TAB = 0x09


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", INPUTUNION)]


user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = ctypes.c_uint
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = ctypes.c_int


def _send_key(w_vk, w_scan, flags):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(w_vk, w_scan, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def tap_unicode(ch):
    """以 Unicode 键击方式输入单个字符（不经过剪贴板 / 输入法）"""
    data = ch.encode("utf-16-le")
    for i in range(0, len(data), 2):
        scan = int.from_bytes(data[i:i + 2], "little")
        _send_key(0, scan, KEYEVENTF_UNICODE)
        _send_key(0, scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)


def tap_vk(vk, scan):
    _send_key(vk, scan, 0)
    _send_key(vk, scan, KEYEVENTF_KEYUP)


def click_at(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.06)
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    time.sleep(0.03)
    inp.union.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def virtual_screen():
    gsm = user32.GetSystemMetrics
    return gsm(76), gsm(77), gsm(78), gsm(79)  # X, Y, CX, CY


# ----------------------------------------------------------------------------
# 图形工具（PIL 超采样绘制，保证圆角 / 渐变边缘细腻）
# ----------------------------------------------------------------------------

_photo_cache = {}


def _rgb(color):
    return ImageColor.getrgb(color)


def _lighten(color, amount):
    r, g, b = _rgb(color)
    return "#%02X%02X%02X" % (
        min(255, int(r + (255 - r) * amount)),
        min(255, int(g + (255 - g) * amount)),
        min(255, int(b + (255 - b) * amount)),
    )


def _darken(color, amount):
    r, g, b = _rgb(color)
    return "#%02X%02X%02X" % (int(r * (1 - amount)), int(g * (1 - amount)), int(b * (1 - amount)))


def rounded_photo(w, h, radius, fill, outline=None, ow=1):
    key = ("rr", w, h, radius, fill, outline, ow)
    if key in _photo_cache:
        return _photo_cache[key]
    s = 4
    img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [0, 0, w * s - 1, h * s - 1], radius=max(1, radius) * s,
        fill=fill, outline=outline, width=max(1, ow) * s,
    )
    img = img.resize((w, h), Image.LANCZOS)
    ph = ImageTk.PhotoImage(img)
    _photo_cache[key] = ph
    return ph


def gradient_photo(w, h, radius, c1, c2):
    key = ("gr", w, h, radius, c1, c2)
    if key in _photo_cache:
        return _photo_cache[key]
    s = 4
    ws, hs = w * s, h * s
    base = Image.new("RGB", (ws, hs))
    d = ImageDraw.Draw(base)
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    for x in range(ws):
        t = x / (ws - 1)
        d.line([(x, 0), (x, hs)], fill=(
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        ))
    mask = Image.new("L", (ws, hs), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, ws - 1, hs - 1],
                                          radius=max(1, radius) * s, fill=255)
    out = Image.new("RGBA", (ws, hs), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)
    ph = ImageTk.PhotoImage(out.resize((w, h), Image.LANCZOS))
    _photo_cache[key] = ph
    return ph


def circle_photo(size, fill, glow_color=None):
    key = ("ci", size, fill, glow_color)
    if key in _photo_cache:
        return _photo_cache[key]
    s = 4
    img = Image.new("RGBA", (size * s, size * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if glow_color:
        d.ellipse([1 * s, 1 * s, (size - 1) * s, (size - 1) * s], fill=glow_color)
    d.ellipse([3 * s, 3 * s, (size - 3) * s, (size - 3) * s], fill=fill)
    img = img.resize((size, size), Image.LANCZOS)
    ph = ImageTk.PhotoImage(img)
    _photo_cache[key] = ph
    return ph


# ----------------------------------------------------------------------------
# 小控件
# ----------------------------------------------------------------------------

class IconBtn(tk.Label):
    """标题栏上的极简图标按钮"""

    def __init__(self, master, x, y, glyph, cmd, size, tip=""):
        super().__init__(master, text=glyph, bg=C["bg"], fg=C["dim"],
                         font=(F, 11), cursor="hand2", bd=0, highlightthickness=0)
        self.place(x=x, y=y, width=size, height=size)
        self._cmd = cmd
        self._tip = tip
        self.bind("<Button-1>", lambda e: self._cmd())
        self.bind("<Enter>", lambda e: self.configure(fg=C["text"]))
        self.bind("<Leave>", lambda e: self.configure(fg=C["dim"]))


class Btn(tk.Label):
    """图片按钮：primary / ghost / danger / dark"""

    def __init__(self, master, x, y, w, h, text, cmd, kind="ghost", size=9):
        self.kind = kind
        self.bw, self.bh = w, h
        fg = {"primary": "#26160A", "ghost": C["text"], "danger": "#FFB7B7", "dark": C["text"]}[kind]
        super().__init__(master, text=text, bg=C["bg"], fg=fg, bd=0,
                         highlightthickness=0, cursor="hand2",
                         font=(F, size, "bold"), compound="center")
        self._cmd = cmd
        self._enabled = True
        self._imgs = self._make()
        self.configure(image=self._imgs["n"])
        self.place(x=x, y=y, width=w, height=h)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _make(self):
        r = S(12)
        r_small = S(10)
        if self.kind == "primary":
            return {
                "n": gradient_photo(self.bw, self.bh, r, C["accent"], C["accent2"]),
                "h": gradient_photo(self.bw, self.bh, r, _lighten(C["accent"], .10), _lighten(C["accent2"], .10)),
                "p": gradient_photo(self.bw, self.bh, r, _darken(C["accent"], .08), _darken(C["accent2"], .10)),
                "d": gradient_photo(self.bw, self.bh, r, "#3A3F48", "#333840"),
            }
        if self.kind == "danger":
            return {
                "n": rounded_photo(self.bw, self.bh, r, "#241417", "#432228", S(1)),
                "h": rounded_photo(self.bw, self.bh, r, "#2E191D", "#59302F", S(1)),
                "p": rounded_photo(self.bw, self.bh, r, "#1D1114", "#3A1E22", S(1)),
                "d": rounded_photo(self.bw, self.bh, r, "#191C22", "#252A33", S(1)),
            }
        if self.kind == "dark":
            return {
                "n": rounded_photo(self.bw, self.bh, r_small, "#1A212B", C["line"], S(1)),
                "h": rounded_photo(self.bw, self.bh, r_small, "#222B38", C["line_hi"], S(1)),
                "p": rounded_photo(self.bw, self.bh, r_small, "#151B24", C["line"], S(1)),
                "d": rounded_photo(self.bw, self.bh, r_small, "#151A22", "#1D232C", S(1)),
            }
        return {
            "n": rounded_photo(self.bw, self.bh, r_small, "#151B24", "#232C3A", S(1)),
            "h": rounded_photo(self.bw, self.bh, r_small, "#1C2532", "#344155", S(1)),
            "p": rounded_photo(self.bw, self.bh, r_small, "#11161E", "#202836", S(1)),
            "d": rounded_photo(self.bw, self.bh, r_small, "#131820", "#1C222B", S(1)),
        }

    def _on_enter(self, _e=None):
        if self._enabled:
            self.configure(image=self._imgs["h"])

    def _on_leave(self, _e=None):
        if self._enabled:
            self.configure(image=self._imgs["n"])

    def _on_press(self, _e=None):
        if self._enabled:
            self.configure(image=self._imgs["p"])

    def _on_release(self, _e=None):
        if self._enabled:
            self.configure(image=self._imgs["h"])
            self._cmd()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(image=self._imgs["n"] if on else self._imgs["d"],
                       fg={"primary": "#26160A", "ghost": C["text"],
                           "danger": "#FFB7B7", "dark": C["text"]}[self.kind] if on else C["dim"],
                       cursor="hand2" if on else "arrow")


class Slider(tk.Canvas):
    """自绘滑杆：轨道 + 发光旋钮"""

    def __init__(self, master, x, y, w, label, minv, maxv, value, step, fmt, accent, cb):
        super().__init__(master, width=w, height=S(34), bg=C["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.place(x=x, y=y, width=w, height=S(34))
        self.w = w
        self.minv, self.maxv, self.step = minv, maxv, step
        self.value = value
        self.fmt = fmt
        self.cb = cb
        self.accent = accent
        self.x0, self.x1 = S(86), S(330)
        self.cy = S(18)
        self.create_text(S(2), self.cy, text=label, anchor="w",
                         fill=C["muted"], font=(F, 8))
        self.create_line(self.x0, self.cy, self.x1, self.cy, width=S(4),
                         fill=C["track"], capstyle="round")
        self.fill_item = self.create_line(self.x0, self.cy, self.x0, self.cy, width=S(4),
                                          fill=accent, capstyle="round")
        self.val_item = self.create_text(w - S(2), self.cy, text=fmt(value), anchor="e",
                                         fill=C["text"], font=(F, 8, "bold"))
        self.knob = circle_photo(S(14), accent, _lighten(accent, .35))
        self.knob_item = self.create_image(self.x0, self.cy, image=self.knob)
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)

    def _drag(self, e):
        ratio = max(0.0, min(1.0, (e.x - self.x0) / (self.x1 - self.x0)))
        raw = self.minv + ratio * (self.maxv - self.minv)
        stepped = round(raw / self.step) * self.step
        stepped = max(self.minv, min(self.maxv, stepped))
        if abs(stepped - self.value) > 1e-9:
            self.value = stepped
            self.redraw()
            if self.cb:
                self.cb(self.value)

    def redraw(self):
        ratio = (self.value - self.minv) / (self.maxv - self.minv)
        kx = self.x0 + ratio * (self.x1 - self.x0)
        self.coords(self.fill_item, self.x0, self.cy, max(self.x0 + 0.1, kx), self.cy)
        self.coords(self.knob_item, kx, self.cy)
        self.itemconfigure(self.val_item, text=self.fmt(self.value))


class Toggle(tk.Canvas):
    def __init__(self, master, x, y, w, label, value, cb):
        super().__init__(master, width=w, height=S(26), bg=C["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.place(x=x, y=y, width=w, height=S(26))
        self.value = bool(value)
        self.cb = cb
        self.on_img = rounded_photo(S(34), S(18), S(9), C["accent"], None, S(1))
        self.off_img = rounded_photo(S(34), S(18), S(9), "#232C3A", None, S(1))
        self.knob_on = circle_photo(S(12), "#FFFFFF", None)
        self.knob_off = circle_photo(S(12), "#7F8A9C", None)
        self.track_item = self.create_image(S(17), S(9), image=self.on_img if value else self.off_img)
        self.knob_item = self.create_image(S(25) if value else S(9), S(9),
                                          image=self.knob_on if value else self.knob_off)
        self.text_item = self.create_text(S(42), S(9), text=label, anchor="w",
                                         fill=C["muted"], font=(F, 8))
        self.bind("<Button-1>", lambda e: self.toggle())

    def toggle(self):
        self.value = not self.value
        self.itemconfigure(self.track_item, image=self.on_img if self.value else self.off_img)
        self.coords(self.knob_item, S(25) if self.value else S(9), S(9))
        self.itemconfigure(self.knob_item, image=self.knob_on if self.value else self.knob_off)
        if self.cb:
            self.cb(self.value)


class Bar(tk.Canvas):
    """细进度条"""

    def __init__(self, master, x, y, w, h, color=None):
        super().__init__(master, width=w, height=h, bg=C["bg"], highlightthickness=0, bd=0)
        self.place(x=x, y=y, width=w, height=h)
        self.w, self.h = w, h
        self.color = color or C["teal"]
        self.create_line(0, h / 2, w, h / 2, width=h, fill=C["track"], capstyle="round")
        self.fill_item = self.create_line(0, h / 2, 0, h / 2, width=h,
                                          fill=self.color, capstyle="round")

    def set(self, frac):
        frac = max(0.0, min(1.0, frac))
        self.coords(self.fill_item, 0, self.h / 2, max(0.5, self.w * frac), self.h / 2)
        self.itemconfigure(self.fill_item, state="normal" if frac > 0.01 else "hidden")


# ----------------------------------------------------------------------------
# 目标区域卡片
# ----------------------------------------------------------------------------

class TargetCard(tk.Canvas):
    def __init__(self, master, x, y, w, h, on_pick):
        super().__init__(master, width=w, height=h, bg=C["bg"], highlightthickness=0,
                         bd=0, cursor="hand2")
        self.place(x=x, y=y, width=w, height=h)
        self.w, self.h = w, h
        self.on_pick = on_pick
        self.selected = False
        self._thumb = None
        self._thumb_item = None
        self._normal = rounded_photo(w, h, S(12), C["panel_hi"], C["line"], S(1))
        self._hover = rounded_photo(w, h, S(12), "#1A2331", C["line_hi"], S(1))
        self._error = rounded_photo(w, h, S(12), "#241518", "#5A2A2E", S(1))
        self.bg_item = self.create_image(0, 0, anchor="nw", image=self._normal)
        self.create_text(S(24), h // 2, text="◎", fill=C["accent"], font=(F, 12))
        self.title_item = self.create_text(S(44), S(14), text="目标区域", anchor="w",
                                          fill=C["muted"], font=(F, 7))
        self.status_item = self.create_text(S(44), S(35), text="点击此处框选需要手打的屏幕区域",
                                           anchor="w", fill=C["dim"], font=(F, 8))
        self.hint_item = self.create_text(w - S(16), h // 2, text="框选  →", anchor="e",
                                          fill=C["accent"], font=(F, 8, "bold"))
        self.bind("<Button-1>", lambda e: self.on_pick())
        self.bind("<Enter>", lambda e: self._set_bg(self._hover))
        self.bind("<Leave>", lambda e: self._set_bg(self._normal))

    def _set_bg(self, img):
        self.itemconfigure(self.bg_item, image=img)

    def set_rect(self, rect, thumb_img=None):
        """rect: (x, y, w, h) 屏幕坐标"""
        self.selected = True
        x, y, w, h = rect
        self.itemconfigure(self.status_item,
                           text="已选 %d × %d   ·   起点 (%d, %d)" % (w, h, x, y),
                           fill=C["teal"])
        self.itemconfigure(self.hint_item, state="hidden")
        if thumb_img is not None:
            self.set_thumb(thumb_img)

    def set_thumb(self, thumb_img):
        self._thumb = thumb_img
        if self._thumb_item is None:
            self._thumb_item = self.create_image(self.w - S(108), S(8), anchor="nw",
                                                 image=self._thumb)
            self.create_rectangle(self.w - S(108), S(8), self.w - S(16), S(48),
                                  outline=C["line_hi"], width=S(1))
        else:
            self.itemconfigure(self._thumb_item, image=self._thumb)

    def flash_error(self, msg="请先框选目标区域"):
        self._set_bg(self._error)
        self.itemconfigure(self.status_item, text=msg, fill=C["danger"])
        self.after(1400, self._restore)

    def _restore(self):
        self._set_bg(self._normal)
        if self.selected:
            self.itemconfigure(self.status_item, fill=C["teal"])
        else:
            self.itemconfigure(self.status_item,
                               text="点击此处框选需要手打的屏幕区域", fill=C["dim"])


# ----------------------------------------------------------------------------
# 文本卡片
# ----------------------------------------------------------------------------

class TextCard(tk.Canvas):
    def __init__(self, master, x, y, w, on_change):
        ch = S(248)
        super().__init__(master, width=w, height=ch, bg=C["bg"],
                         highlightthickness=0, bd=0)
        self.place(x=x, y=y, width=w, height=ch)
        self.w = w
        self.ch = ch
        self.on_change = on_change
        self.create_image(0, 0, anchor="nw",
                          image=rounded_photo(w, ch, S(14), C["panel"], C["line"], S(1)))
        self.create_text(S(16), S(18), text="待输入文本", anchor="w",
                         fill=C["text"], font=(F, 9, "bold"))
        self.create_text(w - S(16), S(18), text="自动识别换行 · 支持粘贴 / 导入",
                         anchor="e", fill=C["dim"], font=(F, 7))
        self.create_line(S(14), S(34), w - S(14), S(34), fill=C["line"])

        self.text = tk.Text(self, bg=C["panel"], fg=C["text"], bd=0,
                            highlightthickness=0, insertbackground=C["accent"],
                            insertwidth=S(2), font=(F, 10), wrap="word",
                            padx=S(2), pady=0, selectbackground="#28405A",
                            selectforeground="#FFFFFF", spacing1=S(3), spacing3=S(4),
                            tabs=(str(S(40)),))
        self.create_window(S(16), S(42), anchor="nw", window=self.text,
                           width=w - S(44), height=S(190))
        self.text.configure(yscrollcommand=self._on_scroll)

        # 自绘细滚动指示条
        sx = w - S(19)
        self.scroll_track = self.create_line(sx, S(44), sx, S(230), width=S(6),
                                             fill="#151B24", capstyle="round",
                                             state="hidden")
        self.scroll_thumb = self.create_line(sx, S(44), sx, S(44), width=S(6),
                                             fill="#33404F", capstyle="round",
                                             state="hidden")

        # 空文本占位提示（置于 Text 之上）
        self.ph = tk.Label(self, text="把需要「手打」的文本粘贴到这里，换行会逐行敲入…",
                           bg=C["panel"], fg=C["dim"], font=(F, 9),
                           anchor="nw", justify="left", wraplength=w - S(76))
        self.ph.place(x=S(22), y=S(46), width=w - S(66), height=S(90))
        self.ph.bind("<Button-1>", lambda e: self.text.focus_set())

        self.text.bind("<<Modified>>", self._modified)
        self.text.bind("<MouseWheel>", self._wheel)
        self.text.edit_modified(False)

    def _on_scroll(self, first, last):
        first, last = float(first), float(last)
        if last - first >= 0.999:
            self.itemconfigure(self.scroll_track, state="hidden")
            self.itemconfigure(self.scroll_thumb, state="hidden")
            return
        top = S(44) + first * S(186)
        bottom = S(44) + last * S(186)
        sx = self.w - S(19)
        self.itemconfigure(self.scroll_track, state="normal")
        self.itemconfigure(self.scroll_thumb, state="normal")
        self.coords(self.scroll_thumb, sx, top, sx, max(top + S(6), bottom))

    def _sync_placeholder(self):
        if self.get_text():
            self.ph.place_forget()
        else:
            self.ph.place(x=S(22), y=S(46), width=self.w - S(66), height=S(90))

    def _wheel(self, e):
        self.text.yview_scroll(int(-e.delta / 120), "lines")

    def _modified(self, _e=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._sync_placeholder()
            if self.on_change:
                self.on_change()

    def get_text(self):
        return self.text.get("1.0", "end-1c")

    def set_text(self, value):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.edit_modified(False)
        self._sync_placeholder()
        if self.on_change:
            self.on_change()

    def append_text(self, value):
        cur = self.get_text()
        if cur and not cur.endswith("\n"):
            value = "\n" + value
        self.text.insert("end", value)
        self.text.see("end")
        self._sync_placeholder()
        if self.on_change:
            self.on_change()


# ----------------------------------------------------------------------------
# 区域框选遮罩
# ----------------------------------------------------------------------------

class RegionPicker:
    def __init__(self, root, callback):
        self.cb = callback
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.28)
        self.top.configure(bg="#000000")
        self.top.geometry("%dx%d+%d+%d" % (self.vw, self.vh, self.vx, self.vy))
        self.cv = tk.Canvas(self.top, bg="#000000", highlightthickness=0,
                            bd=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)
        self.cv.create_text(self.vw // 2, S(40),
                            text="拖动鼠标框选要手打的目标区域　·　Esc 取消",
                            fill="#FFFFFF", font=(F, 12))
        self.x0 = self.y0 = None
        self.rect = None
        self.size_lbl = None
        self.cross_v = self.cv.create_line(0, 0, 0, self.vh, fill="#5FE3C0",
                                           dash=(S(4), S(6)), width=S(1))
        self.cross_h = self.cv.create_line(0, 0, self.vw, 0, fill="#5FE3C0",
                                           dash=(S(4), S(6)), width=S(1))
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Button-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.top.bind("<Escape>", lambda e: self.close())
        self.top.focus_force()

    def _motion(self, e):
        self.cv.coords(self.cross_v, e.x, 0, e.x, self.vh)
        self.cv.coords(self.cross_h, 0, e.y, self.vw, e.y)

    def _press(self, e):
        self.x0, self.y0 = e.x, e.y
        if self.rect:
            self.cv.delete(self.rect)
        if self.size_lbl:
            self.cv.delete(self.size_lbl)
        self.rect = self.cv.create_rectangle(e.x, e.y, e.x, e.y,
                                            outline="#5FE3C0", width=S(2))
        self.size_lbl = self.cv.create_text(e.x, max(S(18), e.y - S(20)), text="", anchor="w",
                                            fill="#FFFFFF", font=(F, 9))

    def _drag(self, e):
        if self.rect is None or self.x0 is None:
            return
        w, h = abs(e.x - self.x0), abs(e.y - self.y0)
        self.cv.coords(self.rect, self.x0, self.y0, e.x, e.y)
        tx = min(self.x0, e.x)
        ty = min(self.y0, e.y)
        self.cv.coords(self.size_lbl, tx, max(S(18), ty - S(16)))
        self.cv.itemconfigure(self.size_lbl, text="  %d × %d  " % (w, h))

    def _release(self, e):
        x, y = self.x0, self.y0
        w, h = abs(e.x - x), abs(e.y - y)
        x, y = min(x, e.x), min(y, e.y)
        if w >= S(24) and h >= S(24):
            rect = (x + self.vx, y + self.vy, w, h)
            self.close()
            self.cb(rect)
        else:
            self.close()

    def close(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


# ----------------------------------------------------------------------------
# 倒计时徽标（透明底浮标）
# ----------------------------------------------------------------------------

class CountdownBadge:
    def __init__(self, root, rect):
        size = S(96)
        self.size = size
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=KEY_COLOR)
        try:
            self.top.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass
        x = rect[0] + rect[2] // 2 - size // 2
        y = rect[1] - size - S(16)
        if y < S(8):
            y = rect[1] + rect[3] + S(12)
        self.top.geometry("%dx%d+%d+%d" % (size, size, x, y))
        self.cv = tk.Canvas(self.top, width=size, height=size,
                            bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.cv.pack()
        p0, p1 = S(7), size - S(7)
        self.cv.create_oval(p0, p0, p1, p1, outline="#26313F", width=S(3))
        self.arc = self.cv.create_arc(p0, p0, p1, p1, start=90, extent=0,
                                      outline=C["teal"], width=S(3), style="arc")
        self.num = self.cv.create_text(size // 2, S(42), text="3", fill="#FFFFFF",
                                       font=(F, 21, "bold"))
        self.cv.create_text(size // 2, S(69), text="即将开始", fill=C["muted"], font=(F, 7))

    def update(self, n, frac):
        self.cv.itemconfigure(self.num, text=str(n))
        self.cv.itemconfigure(self.arc, extent=-359.9 * max(0.0, min(1.0, frac)))

    def close(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


# ----------------------------------------------------------------------------
# 打字引擎
# ----------------------------------------------------------------------------

class Engine:
    def __init__(self, q):
        self.q = q
        self.pause_evt = threading.Event()
        self.pause_evt.set()
        self.stop_flag = threading.Event()
        self.thread = None

    def start(self, text, rect, cps, jitter, click_focus, countdown):
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag.clear()
        self.pause_evt.set()
        self.thread = threading.Thread(
            target=self._run,
            args=(text, rect, cps, jitter, click_focus, countdown),
            daemon=True,
        )
        self.thread.start()

    def pause(self):
        self.pause_evt.clear()

    def resume(self):
        self.pause_evt.set()

    def stop(self):
        self.stop_flag.set()
        self.pause_evt.set()

    def _wait(self, seconds):
        end = time.time() + seconds
        while True:
            if self.stop_flag.is_set():
                return False
            if not self.pause_evt.is_set():
                remain = max(0.0, end - time.time())
                while not self.pause_evt.is_set():
                    if self.stop_flag.is_set():
                        return False
                    time.sleep(0.02)
                end = time.time() + remain
            left = end - time.time()
            if left <= 0:
                return True
            time.sleep(min(0.02, left))

    def _run(self, text, rect, cps, jitter, click_focus, countdown):
        try:
            for n in range(countdown, 0, -1):
                self.q.put(("countdown", n, n / float(max(1, countdown))))
                if not self._wait(1.0):
                    self.q.put(("state", "stopped"))
                    return
            self.q.put(("badge_close",))
            self.q.put(("state", "typing"))

            if click_focus and rect:
                click_at(rect[0] + rect[2] // 2, rect[1] + min(S(24), rect[3] // 2))
                if not self._wait(0.35):
                    self.q.put(("state", "stopped"))
                    return

            total = len(text)
            base = 1000.0 / max(0.5, cps)
            for i, ch in enumerate(text):
                if self.stop_flag.is_set():
                    self.q.put(("state", "stopped"))
                    return
                if not self.pause_evt.is_set():
                    self.q.put(("state", "paused"))
                    while not self.pause_evt.is_set():
                        if self.stop_flag.is_set():
                            self.q.put(("state", "stopped"))
                            return
                        time.sleep(0.02)
                    self.q.put(("state", "typing"))

                if ch == "\n":
                    tap_vk(VK_RETURN, 0x1C)
                elif ch == "\t":
                    tap_vk(VK_TAB, 0x0F)
                elif ch == " ":
                    tap_unicode(" ")
                elif ch.isprintable():
                    tap_unicode(ch)

                self.q.put(("progress", i + 1, total))

                interval = base * (1.0 + random.uniform(-jitter, jitter))
                if not self._wait(interval / 1000.0):
                    self.q.put(("state", "stopped"))
                    return

            self.q.put(("state", "finished"))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("error", str(exc)))


# ----------------------------------------------------------------------------
# 全局热键（F9 / F10）
# ----------------------------------------------------------------------------

class Hotkeys:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_NOREPEAT = 0x4000

    def __init__(self, q):
        self.q = q
        self.tid = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        u = user32
        ok9 = u.RegisterHotKey(None, 1, self.MOD_NOREPEAT, 0x78)
        ok10 = u.RegisterHotKey(None, 2, self.MOD_NOREPEAT, 0x79)
        if not (ok9 and ok10):
            self.q.put(("hotkeys", False))
        else:
            self.q.put(("hotkeys", True))
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                self.q.put(("hotkey", "f9" if msg.wParam == 1 else "f10"))

    def close(self):
        u = user32
        u.UnregisterHotKey(None, 1)
        u.UnregisterHotKey(None, 2)
        if self.tid:
            u.PostThreadMessageW(self.tid, self.WM_QUIT, 0, 0)


# ----------------------------------------------------------------------------
# 主应用
# ----------------------------------------------------------------------------

class App:
    def __init__(self):
        self._enable_dpi()
        self.root = tk.Tk()
        self.root.title("手打模拟器")
        self.root.overrideredirect(True)
        self.root.configure(bg=C["bg"])
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        # 按系统 DPI 放大整体界面（150% 缩放时整个浮窗等比放大 1.5 倍）
        global SCALE
        try:
            SCALE = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
            self.root.tk.call("tk", "scaling", SCALE * 96 / 72.0)
        except tk.TclError:
            SCALE = 1.0

        self.W = S(BASE_W)
        self.H = S(BASE_H)
        self.BARH = S(BASE_BAR_H)

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H,
                                            sw - self.W - S(18), sh - self.H - S(30)))

        self.q = queue.Queue()
        self.engine = Engine(self.q)
        self.state = "idle"
        self.rect = None
        self.cps = 5.0
        self.jitter = 0.25
        self.collapsed = False
        self._badge = None
        self._drag_dx = 0
        self._drag_dy = 0
        self._auto_collapsed = False

        self._build()
        self._bind_drag()
        self.hotkeys = Hotkeys(self.q)
        self.root.after(40, self._poll)
        self._refresh_stats()

    # ---------------------------------------------------------------- 基础设施
    def _enable_dpi(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    def _build(self):
        W, H = self.W, self.H
        self.main = tk.Frame(self.root, bg=C["bg"], width=W, height=H)
        self.main.place(x=0, y=0, width=W, height=H)

        # 顶部渐变饰条
        self.top_line = tk.Canvas(self.main, width=W, height=S(2), bg=C["bg"],
                                  highlightthickness=0)
        self.top_line.place(x=0, y=0)
        self.top_line.create_line(0, S(1), W, S(1), fill=C["accent"], width=S(2))

        # 头部
        self.header = tk.Canvas(self.main, width=W, height=S(58), bg=C["bg"],
                                highlightthickness=0)
        self.header.place(x=0, y=0)
        self.header.create_line(S(16), S(57), W - S(16), S(57), fill="#161E29")

        self.logo = tk.Canvas(self.main, width=S(30), height=S(30), bg=C["bg"],
                              highlightthickness=0)
        self.logo.place(x=S(16), y=S(14))
        self._logo_img = gradient_photo(S(30), S(30), S(9), C["accent"], C["accent2"])
        self.logo.create_image(S(15), S(15), image=self._logo_img)
        self.logo.create_text(S(15), S(14), text="打", fill="#271607",
                              font=(F, 11, "bold"))

        self.title_lbl = tk.Label(self.main, text="手打模拟器", bg=C["bg"], fg=C["text"],
                                  font=(F, 13, "bold"))
        self.title_lbl.place(x=S(56), y=S(11))
        self.sub_lbl = tk.Label(self.main, text="MANUAL TYPING SIMULATOR  ·  逐字键击，绕过粘贴限制",
                                bg=C["bg"], fg=C["dim"], font=(F, 7))
        self.sub_lbl.place(x=S(57), y=S(35))

        IconBtn(self.main, W - S(74), S(17), "—", self.collapse, S(26))
        IconBtn(self.main, W - S(40), S(17), "×", self.on_close, S(26))

        # 目标区域
        self.target_card = TargetCard(self.main, S(16), S(66), W - S(32), S(56), self.pick_region)

        # 文本
        self.text_area = TextCard(self.main, S(16), S(134), W - S(32), self._refresh_stats)

        # 工具条
        self.btn_import = Btn(self.main, S(16), S(390), S(96), S(28), "导入文本",
                              self.import_file, "ghost", 9)
        self.btn_paste = Btn(self.main, S(120), S(390), S(76), S(28), "粘贴",
                             self.paste_clipboard, "ghost", 9)
        self.btn_clear = Btn(self.main, S(204), S(390), S(60), S(28), "清空",
                             self.clear_text, "ghost", 9)
        self.count_lbl = tk.Label(self.main, text="0 字 · 0 行", bg=C["bg"], fg=C["dim"],
                                  font=(F, 8))
        self.count_lbl.place(x=W - S(16), y=S(396), anchor="e")

        # 节奏滑杆
        self.sl_cps = Slider(self.main, S(16), S(424), W - S(32), "打字速度", 1.0, 20.0,
                             self.cps, 0.5, lambda v: "%.1f 字/秒" % v, C["accent"], self._on_cps)
        self.sl_jit = Slider(self.main, S(16), S(458), W - S(32), "节奏抖动", 0.0, 0.8,
                             self.jitter, 0.05, lambda v: "%d %%" % int(v * 100), C["teal"],
                             self._on_jitter)

        # 选项
        self.tg_focus = Toggle(self.main, S(16), S(500), S(210), "开始时点击目标以聚焦", True, None)
        self.tg_collapse = Toggle(self.main, S(250), S(500), S(200), "开始后自动收起面板", True, None)

        # 主操作
        self.btn_main = Btn(self.main, S(16), S(536), S(312), S(50), "开始手打",
                            self.on_primary, "primary", 11)
        self.btn_stop = Btn(self.main, S(344), S(536), S(96), S(50), "停止",
                            self.stop_typing, "danger", 10)
        self.btn_stop.place_forget()

        # 进度
        self.bar = Bar(self.main, S(16), S(598), W - S(32), S(6), C["teal"])
        self.bar.set(0)
        self.prog_lbl = tk.Label(self.main, text="就绪", bg=C["bg"], fg=C["dim"], font=(F, 8))
        self.prog_lbl.place(x=S(16), y=S(612))
        self.pct_lbl = tk.Label(self.main, text="0%", bg=C["bg"], fg=C["dim"],
                                font=(F, 8, "bold"))
        self.pct_lbl.place(x=W - S(16), y=S(612), anchor="e")

        # 底部提示
        self.footer = tk.Label(self.main, text="F9 开始 / 暂停      F10 停止      Esc 取消框选",
                               bg=C["bg"], fg=C["dim"], font=(F, 7))
        self.footer.place(x=W // 2, y=S(650), anchor="center")

        self._build_bar()
        tk.Misc.lift(self.top_line)

    def _build_bar(self):
        """收起后的迷你条"""
        W, H = self.W, self.BARH
        self.barframe = tk.Frame(self.root, bg=C["bg"], width=W, height=H)
        self.bar_logo = tk.Canvas(self.barframe, width=S(26), height=S(26), bg=C["bg"],
                                  highlightthickness=0)
        self.bar_logo.place(x=S(14), y=S(18))
        self.bar_logo.create_image(S(13), S(13), image=self._logo_img)
        self.bar_logo.create_text(S(13), S(12), text="打", fill="#271607", font=(F, 9, "bold"))
        self.bar_title = tk.Label(self.barframe, text="手打进行中", bg=C["bg"], fg=C["text"],
                                  font=(F, 9, "bold"))
        self.bar_title.place(x=S(50), y=S(11))
        self.bar_sub = tk.Label(self.barframe, text="0 / 0 · 剩余 0:00", bg=C["bg"],
                                fg=C["muted"], font=(F, 7))
        self.bar_sub.place(x=S(50), y=S(31))
        self.bar_bar = Bar(self.barframe, S(50), S(50), S(220), S(4), C["teal"])
        self.bar_bar.set(0)

        IconBtn(self.barframe, S(288), S(19), "Ⅱ", self.on_primary, S(24))
        IconBtn(self.barframe, S(316), S(19), "■", self.stop_typing, S(24))
        IconBtn(self.barframe, S(344), S(19), "▢", self.expand, S(24))
        IconBtn(self.barframe, S(372), S(19), "×", self.on_close, S(24))

    def _bind_drag(self):
        for w in (self.header, self.logo, self.title_lbl, self.sub_lbl):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, e):
        self._drag_dx = e.x_root - self.root.winfo_x()
        self._drag_dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry("+%d+%d" % (e.x_root - self._drag_dx, e.y_root - self._drag_dy))

    # ---------------------------------------------------------------- 交互
    def _on_cps(self, v):
        self.cps = v
        self._refresh_stats()

    def _on_jitter(self, v):
        self.jitter = v

    def _refresh_stats(self):
        raw = self.text_area.get_text().replace("\r\n", "\n").replace("\r", "\n")
        chars = len(raw)
        lines = raw.count("\n") + 1 if raw else 0
        self.count_lbl.configure(text="%d 字 · %d 行" % (chars, lines))
        if self.state in ("idle", "finished", "stopped"):
            if chars:
                eta = chars / max(0.5, self.cps)
                self.prog_lbl.configure(text="就绪 · 预计 %s" % self._fmt_time(eta), fg=C["dim"])
            else:
                self.prog_lbl.configure(text="就绪", fg=C["dim"])

    @staticmethod
    def _fmt_time(seconds):
        seconds = int(seconds)
        return "%d:%02d" % (seconds // 60, seconds % 60)

    def import_file(self):
        path = filedialog.askopenfilename(
            title="选择要手打的文本",
            filetypes=[("文本文件", "*.txt *.md *.log *.csv *.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        data = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
            try:
                with open(path, "r", encoding=enc) as fh:
                    data = fh.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if data is None:
            self._toast("文件编码无法识别", C["danger"])
            return
        self.text_area.set_text(data)
        self._toast("已导入 %d 字" % len(data), C["teal"])

    def paste_clipboard(self):
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            self._toast("剪贴板为空", C["danger"])
            return
        self.text_area.append_text(data)
        self._toast("已从剪贴板追加 %d 字" % len(data), C["teal"])

    def clear_text(self):
        self.text_area.set_text("")
        self._toast("已清空", C["muted"])

    def _toast(self, msg, color):
        self.footer.configure(text=msg, fg=color)
        try:
            self.root.after_cancel(self._toast_job)
        except Exception:
            pass
        self._toast_job = self.root.after(
            1800, lambda: self.footer.configure(
                text="F9 开始 / 暂停      F10 停止      Esc 取消框选", fg=C["dim"]))

    # ---------------------------------------------------------------- 框选
    def pick_region(self):
        RegionPicker(self.root, self._region_selected)

    def _region_selected(self, rect):
        self.rect = rect
        self.target_card.set_rect(rect)
        self._toast("已框选 %d × %d" % (rect[2], rect[3]), C["teal"])
        self.root.after(180, lambda: self._grab_thumb(rect))

    def _grab_thumb(self, rect):
        if self.rect != rect:
            return
        try:
            shot = ImageGrab.grab(bbox=(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]),
                                  all_screens=True).convert("RGB")
            tw, th = S(92), S(40)
            shot = shot.resize((tw, th), Image.LANCZOS)
            mask = Image.new("L", (tw * 4, th * 4), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, tw * 4 - 1, th * 4 - 1],
                                                  radius=S(8) * 4, fill=255)
            mask = mask.resize((tw, th), Image.LANCZOS)
            out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            out.paste(shot, (0, 0), mask)
            self.target_card.set_thumb(ImageTk.PhotoImage(out))
        except Exception:
            pass

    # ---------------------------------------------------------------- 打字流程
    def on_primary(self):
        if self.state == "typing":
            self.pause_typing()
        elif self.state == "paused":
            self.resume_typing()
        elif self.state in ("idle", "finished", "stopped"):
            self.start_typing()

    def start_typing(self):
        text = self.text_area.get_text().replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            self._toast("请先粘贴或导入需要手打的文本", C["danger"])
            self.text_area.text.focus_set()
            return
        if not self.rect:
            self.target_card.flash_error()
            self._toast("请先框选目标区域", C["danger"])
            return

        self.state = "countdown"
        self.bar.set(0)
        self.bar_bar.set(0)
        self.prog_lbl.configure(text="3 秒后开始…", fg=C["accent"])
        self.pct_lbl.configure(text="0%", fg=C["dim"])
        self.btn_main.configure(text="暂停")
        self.btn_stop.place(x=S(344), y=S(536), width=S(96), height=S(50))
        self.btn_main.set_enabled(True)

        if self.tg_collapse.value:
            self.collapse(auto=True)

        self.engine.start(text, self.rect, self.cps, self.jitter,
                          self.tg_focus.value, 3)

    def pause_typing(self):
        if self.state in ("typing", "countdown"):
            self.engine.pause()
            self.state = "paused"
            self.btn_main.configure(text="继续")
            self.bar_title.configure(text="已暂停")

    def resume_typing(self):
        if self.state == "paused":
            self.engine.resume()
            self.state = "typing"
            self.btn_main.configure(text="暂停")
            self.bar_title.configure(text="手打进行中")

    def stop_typing(self):
        if self.state in ("typing", "paused", "countdown"):
            self.engine.stop()
            self.state = "stopped"
            self._badge_close()
            self.expand()
            self._reset_idle("已停止")

    def _reset_idle(self, reason):
        self.state = "finished" if reason == "手打完成" else "stopped"
        self.btn_main.configure(text="再次手打" if reason == "手打完成" else "开始手打")
        self.btn_main.set_enabled(True)
        self.btn_stop.place_forget()
        self.prog_lbl.configure(text=reason, fg=C["teal"] if reason == "手打完成" else C["muted"])
        self._toast(reason, C["teal"] if reason == "手打完成" else C["muted"])

    def _badge_close(self):
        if self._badge:
            self._badge.close()
            self._badge = None

    # ---------------------------------------------------------------- 收起 / 展开
    def collapse(self, auto=False):
        if self.collapsed:
            return
        self._auto_collapsed = auto
        self.collapsed = True
        self.main.place_forget()
        self.root.geometry("%dx%d" % (self.W, self.BARH))
        self.barframe.place(x=0, y=0, width=self.W, height=self.BARH)

    def expand(self):
        if not self.collapsed:
            return
        self.collapsed = False
        self.barframe.place_forget()
        self.root.geometry("%dx%d" % (self.W, self.H))
        self.main.place(x=0, y=0, width=self.W, height=self.H)

    # ---------------------------------------------------------------- 事件循环
    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                self._handle(ev)
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "hotkey":
            if ev[1] == "f9":
                self.on_primary()
            else:
                self.stop_typing()
        elif kind == "hotkeys":
            if not ev[1]:
                self._toast("热键注册失败（F9/F10 可能被占用）", C["danger"])
        elif kind == "countdown":
            n, frac = ev[1], ev[2]
            self.prog_lbl.configure(text="%d 秒后开始…" % n, fg=C["accent"])
            self.bar_title.configure(text="准备手打")
            if self._badge is None and self.rect:
                self._badge = CountdownBadge(self.root, self.rect)
            if self._badge:
                self._badge.update(n, frac)
        elif kind == "badge_close":
            self._badge_close()
        elif kind == "state":
            self._on_state(ev[1])
        elif kind == "progress":
            i, total = ev[1], ev[2]
            frac = i / max(1, total)
            self.bar.set(frac)
            self.bar_bar.set(frac)
            self.pct_lbl.configure(text="%d%%" % int(frac * 100), fg=C["text"])
            remain = (total - i) / max(0.5, self.cps)
            self.prog_lbl.configure(
                text="手打中 %d / %d · 剩余 %s" % (i, total, self._fmt_time(remain)),
                fg=C["text"],
            )
            self.bar_sub.configure(text="%d / %d · 剩余 %s" % (i, total, self._fmt_time(remain)))
        elif kind == "error":
            self._badge_close()
            self.expand()
            self._reset_idle("输入失败")
            self._toast(ev[1], C["danger"])

    def _on_state(self, value):
        if value == "typing":
            self.state = "typing"
            self.btn_main.configure(text="暂停")
            self.bar_title.configure(text="手打进行中")
            self.prog_lbl.configure(fg=C["text"])
        elif value == "paused":
            self.state = "paused"
            self.btn_main.configure(text="继续")
            self.bar_title.configure(text="已暂停")
        elif value == "finished":
            self._badge_close()
            self.expand()
            self.state = "finished"
            self.bar.set(1.0)
            self.bar_bar.set(1.0)
            self.pct_lbl.configure(text="100%", fg=C["teal"])
            self._reset_idle("手打完成")
        elif value == "stopped":
            self._badge_close()
            self.expand()
            self.state = "stopped"
            self.bar.set(0)
            self.bar_bar.set(0)
            self._reset_idle("已停止")

    def on_close(self):
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            self.hotkeys.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def main():
    app = App()
    if "--selftest" in sys.argv:
        app.root.after(1200, app.on_close)
    app.run()


if __name__ == "__main__":
    main()
