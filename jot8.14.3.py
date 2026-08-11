"""
Jot_ — Instant side-panel notes with AI assistance
Requirements: pip install PyQt6 anthropic
To compile:   pip install pyinstaller
              pyinstaller --onefile --windowed --name Jot jot.py
"""

import sys
import os
import json
import random
import math
import winreg
import keyboard

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QPushButton, QLabel,
    QSystemTrayIcon, QMenu, QFrame, QDialog,
    QScrollArea, QLineEdit, QStackedWidget, QFileDialog, QToolTip,
    QComboBox, QSizePolicy, QProxyStyle, QStyle
)


from PyQt6.QtCore import (
    Qt, QTimer, QSize, QRectF,
    QThread, pyqtSignal, QPoint, QPointF, QObject,
    QByteArray, QBuffer, QIODevice, QMimeData, QUrl, QEvent
)
from PyQt6.QtGui import (
    QIcon, QColor, QFont, QKeySequence,
    QShortcut, QPixmap, QPainter, QPen, QBrush, QPainterPath,
    QFontDatabase, QImage, QDesktopServices, QLinearGradient
)

# ── Custom button font — loaded once at startup, falls back to Open Sans ──────
BUTTON_FONT_FAMILY = "Open Sans"  # overwritten by _load_button_font() at launch

def _button_font_path() -> str:
    """Looks for the bundled TTF next to the script/exe, in a 'fonts' folder."""
    base_dir = os.path.dirname(_app_path()) if "_app_path" in globals() else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "fonts", "ModularBlackBlockyBoldModern.ttf")

def _load_button_font():
    """Registers the custom TTF with Qt's font database and returns the family
    name Qt assigns it. Falls back to 'Open Sans' if the file is missing or
    fails to load — buttons simply keep the previous look in that case."""
    global BUTTON_FONT_FAMILY
    path = _button_font_path()
    if not os.path.exists(path):
        return
    font_id = QFontDatabase.addApplicationFont(path)
    if font_id == -1:
        return
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        BUTTON_FONT_FAMILY = families[0]

def _apply_btn_font(qss: str) -> str:
    """Swaps the __BTNFONT__ placeholder used in stylesheets for the actual
    loaded button font family name (kept out of f-strings so none of the
    QSS's own curly braces need escaping)."""
    return qss.replace("__BTNFONT__", BUTTON_FONT_FAMILY)

def _asset_path(filename: str) -> str:
    """Resolves a file inside the bundled 'assets' folder (next to the
    script/exe, same convention as the fonts/ folder). Returns a forward-
    slash path since Qt stylesheets require '/' in url() regardless of OS."""
    base_dir = os.path.dirname(_app_path()) if "_app_path" in globals() else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "assets", filename).replace("\\", "/")

# ── AI model — user-selectable via the dropdown in the AI section; the
# chosen id is persisted to settings.json under "ai_model" and passed
# through to every AIWorker / ApiKeyTestWorker call at request time. ──────────
# (id, display name) pairs, ordered as they appear in the dropdown.
AVAILABLE_MODELS = [
    ("claude-sonnet-5",         "Claude Sonnet 5"),
    ("claude-opus-4-8",         "Claude Opus 4.8"),
    ("claude-sonnet-4-5",       "Claude Sonnet 4.5"),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
]
AI_MODEL_DISPLAY_NAMES = dict(AVAILABLE_MODELS)
DEFAULT_AI_MODEL = "claude-sonnet-5"

# The prompt bar doubles as the AI status line (status label is hidden), so
# its idle text lives in one place that both the UI setup and the status
# helpers below can refer back to.
DEFAULT_AI_PROMPT_PLACEHOLDER = "Ask AI to do something with this jot..."


def _make_sun_icon() -> QIcon:
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#555555"), 1.2))
    p.setBrush(QBrush(QColor("#555555")))
    # Centre circle
    p.drawEllipse(6, 6, 6, 6)
    # 8 rays
    import math
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(8):
        angle = math.radians(i * 45)
        x1 = 9 + math.cos(angle) * 5.5
        y1 = 9 + math.sin(angle) * 5.5
        x2 = 9 + math.cos(angle) * 8
        y2 = 9 + math.sin(angle) * 8
        p.drawLine(int(x1), int(y1), int(x2), int(y2))
    p.end()
    return QIcon(pix)

def _make_moon_icon() -> QIcon:
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#555555")))
    # Full circle, then punch out offset circle to make crescent
    from PyQt6.QtGui import QRegion
    full = QRegion(2, 1, 14, 14, QRegion.RegionType.Ellipse)
    cutout = QRegion(6, 1, 12, 12, QRegion.RegionType.Ellipse)
    p.setClipRegion(full.subtracted(cutout))
    p.drawEllipse(2, 1, 14, 14)
    p.end()
    return QIcon(pix)


# ── Animated sun -> moon theme toggle ──────────────────────────────────────
# Ported from a Tkinter widget the user supplied (same animation math: a
# growing disc, a rotating crescent cutout, and rays that shrink/fade away)
# to a QWidget that paints itself with QPainter and drives the animation via
# QTimer instead of Canvas.after(). The crescent is a true geometric cutout
# via QPainterPath.subtracted() (same technique _make_moon_icon() above uses)
# rather than the Tkinter version's "paint a background-colored circle on
# top" trick, so it doesn't need to know what's behind the button.
def _ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


class ThemeToggle(QWidget):
    DURATION_MS = 550
    FPS = 60
    ICON_COLOR = "#555555"  # matches every other header icon in this app

    def __init__(self, parent=None, size=28, dark=False, on_toggle=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scale = size / 24.0  # icon designed in a 24x24 space
        self.dark = dark
        self.on_toggle = on_toggle
        self._hovered = False
        self._t = 1.0 if dark else 0.0  # animation progress: 0 sun, 1 moon
        self._animate_from = self._t
        self._frame = 0
        self._total = max(1, round(self.DURATION_MS / (1000 / self.FPS)))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # -- public API ----------------------------------------------------------
    def toggle(self):
        self.set_dark(not self.dark, animate=True)

    def set_dark(self, dark: bool, animate: bool = False):
        if dark == self.dark:
            return
        self.dark = dark
        self._timer.stop()
        if animate:
            self._animate_from = self._t
            self._frame = 0
            self._timer.start(round(1000 / self.FPS))
        else:
            self._t = 1.0 if dark else 0.0
            self.update()
        if self.on_toggle:
            self.on_toggle(self.dark)

    # -- animation -------------------------------------------------------------
    def _tick(self):
        self._frame += 1
        p = _ease_in_out_cubic(min(1.0, self._frame / self._total))
        target = 1.0 if self.dark else 0.0
        self._t = self._animate_from + (target - self._animate_from) * p
        self.update()
        if self._frame >= self._total:
            self._timer.stop()

    # -- drawing -------------------------------------------------------------
    def _pt(self, x, y, rot):
        """Rotate a 24-space point around (12,12) by `rot` radians, scale to px."""
        dx, dy = x - 12, y - 12
        rx = 12 + dx * math.cos(rot) - dy * math.sin(rot)
        ry = 12 + dx * math.sin(rot) + dy * math.cos(rot)
        return rx * self._scale, ry * self._scale

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle()
        super().mousePressEvent(e)

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        t = self._t
        s = self._scale
        color = QColor(self.ICON_COLOR)
        rot = math.radians(180 * t)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Hover highlight — painted manually (matching the other header
        # icon buttons' hover color) rather than routed through the style
        # system, since QStyle.drawPrimitive() here reliably crashed the
        # app on exit in testing.
        if self._hovered:
            hover_color = QColor("#2e2e2e" if self.dark else "#e8e8e5")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hover_color)
            p.drawRoundedRect(self.rect(), 14, 14)

        # Sun disc growing into the full moon disc (r 5 -> 8.5)
        r = (5 + 3.5 * t) * s
        cx = cy = 12 * s
        disc = QPainterPath()
        disc.addEllipse(QPointF(cx, cy), r, r)

        # Crescent cutout: rotates in as t increases so the finished crescent
        # opens to the top right, matching the original animation.
        if t > 0.001:
            mx = -3 + (8.8 - -3) * t
            my = 27 + (15.2 - 27) * t
            px, py = self._pt(mx, my, rot)
            mr = 8 * s
            cutout = QPainterPath()
            cutout.addEllipse(QPointF(px, py), mr, mr)
            disc = disc.subtracted(cutout)
        p.fillPath(disc, color)

        # Rays: shrink toward the disc and fade out (alpha instead of the
        # original's color-mix-toward-background, since this button doesn't
        # need to know what's behind it).
        if t < 0.999:
            shrink = 1 - t
            ray_color = QColor(color)
            ray_color.setAlphaF(max(0.0, min(1.0, shrink)))
            pen = QPen(ray_color, max(1, round(2.5 * s * shrink)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            inner = 6.5 + (12 - 6.5) * t  # retract toward center
            outer = inner + 4.75 * shrink
            for i in range(8):
                ang = math.radians(i * 45) + rot
                x1, y1 = self._pt(12 + inner * math.cos(ang), 12 + inner * math.sin(ang), 0)
                x2, y2 = self._pt(12 + outer * math.cos(ang), 12 + outer * math.sin(ang), 0)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.end()


def _make_fullscreen_icon() -> QIcon:
    """Four corner brackets — 'expand to fullscreen'."""
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#555555"), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    L = 4  # bracket arm length
    corners = [
        # (x, y, dx1, dy1, dx2, dy2) — arm directions from each corner
        (3, 3,   L, 0,   0, L),
        (15, 3, -L, 0,   0, L),
        (3, 15,  L, 0,   0, -L),
        (15, 15, -L, 0,  0, -L),
    ]
    for x, y, dx1, dy1, dx2, dy2 in corners:
        p.drawLine(x, y, x + dx1, y + dy1)
        p.drawLine(x, y, x + dx2, y + dy2)
    p.end()
    return QIcon(pix)

def _make_restore_icon() -> QIcon:
    """Four inward-pointing corner brackets — 'restore to side panel'."""
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#555555"), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    L = 4
    corners = [
        (7, 7,   -L, 0,   0, -L),
        (11, 7,   L, 0,   0, -L),
        (7, 11,  -L, 0,   0, L),
        (11, 11,  L, 0,   0, L),
    ]
    for x, y, dx1, dy1, dx2, dy2 in corners:
        p.drawLine(x, y, x + dx1, y + dy1)
        p.drawLine(x, y, x + dx2, y + dy2)
    p.end()
    return QIcon(pix)

def _make_link_icon() -> QIcon:
    """Small 'external link' icon — an open-cornered box with an arrow
    breaking out the top-right, used for the header link to the Jot_ site."""
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#555555"), 1.3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    box = QPainterPath()
    box.moveTo(11, 4)
    box.lineTo(4, 4)
    box.lineTo(4, 14)
    box.lineTo(14, 14)
    box.lineTo(14, 7)
    p.drawPath(box)
    p.drawLine(9, 9, 15, 3)
    p.drawLine(15, 3, 11, 3)
    p.drawLine(15, 3, 15, 7)
    p.end()
    return QIcon(pix)

def _make_key_icon() -> QIcon:
    """Small key icon — used for the 'set API key' button."""
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#555555"), 1.3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(2, 6, 7, 7)       # bow (ring)
    p.drawLine(8, 9, 15, 9)          # shaft
    p.drawLine(12, 9, 12, 12)        # tooth
    p.drawLine(15, 9, 15, 12)        # tooth
    p.end()
    return QIcon(pix)

# ── Startup registry helpers ──────────────────────────────────────────────────
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "Jot"

def _app_path() -> str:
    """Returns the exe path when compiled, or the script path when running raw."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)

def is_startup_enabled() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, REG_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False

def enable_startup():
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, _app_path())
    winreg.CloseKey(key)

def disable_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, REG_NAME)
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass


# ── Saved Jots storage (simple JSON file next to the exe) ─────────────────────
def _jots_file_path() -> str:
    """Stores jots.json alongside the exe (or script when running raw)."""
    base_dir = os.path.dirname(_app_path())
    return os.path.join(base_dir, "jots.json")

def load_jots() -> list:
    """Returns a list of {"name": str, "text": str} dicts, newest first."""
    path = _jots_file_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []

def save_jots(jots: list):
    path = _jots_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(jots, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

def add_jot(name: str, text: str, attachments: list = None):
    jots = load_jots()
    jots.insert(0, {"name": name, "text": text, "attachments": attachments or []})
    save_jots(jots)

def delete_jot(index: int):
    jots = load_jots()
    if 0 <= index < len(jots):
        jots.pop(index)
        save_jots(jots)


# ── App settings storage (theme, etc. — JSON file next to the exe) ────────────
def _settings_file_path() -> str:
    base_dir = os.path.dirname(_app_path())
    return os.path.join(base_dir, "settings.json")

def load_settings() -> dict:
    path = _settings_file_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}

def save_settings(settings: dict):
    path = _settings_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ── AI Worker (lazy-loads anthropic only when first used) ─────────────────────
class AIWorker(QThread):
    result_ready   = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, text, mode, api_key, model, instruction=None):
        super().__init__()
        self.text = text
        self.mode = mode
        self.api_key = api_key
        self.model = model
        self.instruction = instruction

    def run(self):
        try:
            import anthropic  # ← lazy import: doesn't slow down app startup
            client = anthropic.Anthropic(api_key=self.api_key)
            prompts = {
                "polish": f"Clean up and polish this note. Fix grammar, make it clearer. Return only the improved text:\n\n{self.text}",
                "email":  f"Turn this into a professional email. Return only the email text:\n\n{self.text}",
                "expand": f"Expand this brief idea into a more detailed outline. Return only the expanded text:\n\n{self.text}",
                "custom": f"{self.instruction}\n\nHere is the text to work with:\n\n{self.text}\n\nReturn only the result, with no preamble.",
            }
            message = client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompts[self.mode]}]
            )
            self.result_ready.emit(message.content[0].text)
        except Exception as e:
            self.error_occurred.emit(str(e))


# ── API key test worker — minimal-cost request to confirm a key is valid ──────
class ApiKeyTestWorker(QThread):
    success        = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, api_key, model):
        super().__init__()
        self.api_key = api_key
        self.model = model

    def run(self):
        try:
            import anthropic  # ← lazy import: doesn't slow down app startup
            client = anthropic.Anthropic(api_key=self.api_key)
            client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "Hi"}]
            )
            self.success.emit()
        except Exception as e:
            self.error_occurred.emit(str(e))


# ── Tray icon: "J." in black on white ────────────────────────────────────────
def make_tray_icon() -> QIcon:
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.white)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(QFont("Georgia", 16, QFont.Weight.Bold))
    p.setPen(QPen(QColor("#111111")))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "J.")
    p.end()
    return QIcon(pix)


# ── Hotkey signal (safely bridges keyboard thread → Qt main thread) ───────────
class HotkeySignal(QObject):
    triggered = pyqtSignal()


# ── Clipboard mime data — orders formats so text/html wins ────────────────────
class ClipboardMimeData(QMimeData):
    """Many paste targets (Outlook, Explorer, etc.) only honor the *first*
    format they recognise, and favor a file/URI list over rich text when
    both are on the clipboard — so copying a jot with attachments would
    paste only the files, dropping the note text. Reordering formats() to
    put text/html and text/plain ahead of text/uri-list fixes that for
    targets that respect format order."""
    def formats(self):
        fmts = super().formats()
        preferred = ["text/html", "text/plain"]
        ordered = [f for f in preferred if f in fmts]
        ordered += [f for f in fmts if f not in preferred]
        return ordered


# ── Logo label — draws the wordmark plus a short, thick underscore mark ───────
# right after it. The mark is painted directly in paintEvent() (computed from
# self.fontMetrics()/self.text() live, at the moment of drawing) rather than
# positioned as a separately-tracked child widget, since the latter needs its
# position recomputed from font metrics queried *before* the label's font is
# fully resolved/polished, which was producing a slightly-wrong, overlapping
# position. Painting it inline sidesteps that entirely.
class LogoLabel(QLabel):
    UNDERSCORE_WIDTH  = 6   # shorter than the font's own "_" glyph
    UNDERSCORE_HEIGHT = 3   # thicker than the font's own "_" glyph
    UNDERSCORE_GAP    = 2   # gap between the last letter and the mark
    LEFT_PAD          = 4   # matches this label's own QSS padding (2px 4px)
    BOTTOM_INSET      = 6   # distance from the bottom edge to the mark

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._underscore_color = QColor("#111111")

    def set_underscore_color(self, color: str):
        self._underscore_color = QColor(color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        x = self.LEFT_PAD + text_w + self.UNDERSCORE_GAP
        y = self.height() - self.UNDERSCORE_HEIGHT - self.BOTTOM_INSET
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._underscore_color)
        p.drawRoundedRect(QRectF(x, y, self.UNDERSCORE_WIDTH, self.UNDERSCORE_HEIGHT), 1, 1)
        p.end()


# ── Resize handle — grafted onto the editor's own bottom-left corner ──────────
class ResizeHandle(QLabel):
    """The resize grip is drawn as the window's own bottom-left border,
    thickened into a rounded ribbon that bulges out around the corner and
    tapers back down to the panel's normal 1px border at both ends —
    rather than a separate patch sitting on top of the corner."""
    MIN_W  = 300
    MIN_H  = 420
    SIZE   = 48   # overlay footprint — big enough to hold the full taper
    RADIUS = 12   # matches the panel window's own border-radius exactly
    ARM    = 18   # length of straight taper arm beyond the corner arc
    BASE_HALF = 0.5   # half-width at the taper ends — matches the 1px panel border
    IDLE_PEAK_HALF  = 2.0   # half-width at the corner apex, at rest
    HOVER_PEAK_HALF = 3.5   # half-width at the corner apex, hovered/dragging
    ARC_STEPS = 24

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self._win      = parent_window
        self._dragging  = False
        self._hovered   = False
        self._dark      = False
        self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip("Drag to resize")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._set_colors()

    def _set_colors(self):
        # Idle color matches the panel's own border exactly, so at rest the
        # thickened strip still reads as "the window's border", not a
        # separate control — only the hover/drag color calls attention to it.
        if self._dark:
            self._border        = QColor("#333333")
            self._border_active = QColor("#8a8a8a")
        else:
            self._border        = QColor("#d9d9d6")
            self._border_active = QColor("#8a8a8a")

    def set_theme(self, dark: bool):
        self._dark = dark
        self._set_colors()
        self.update()

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        super().leaveEvent(e)

    def _centerline(self):
        """Returns an ordered list of (x, y, nx, ny) samples tracing the
        window's bottom-left border — straight up, around the rounded
        corner, straight along the bottom — with (nx, ny) the outward
        normal at each point. Local widget coordinates; the widget is
        anchored at the window's own (0, height - SIZE)."""
        s, r = self.SIZE, self.RADIUS
        pts = []

        # Vertical arm: straight edge above the arc, running downward.
        y_top = (s - r) - self.ARM
        for i in range(9):
            t = i / 8
            y = y_top + t * ((s - r) - y_top)
            pts.append((0.0, y, -1.0, 0.0))

        # Arc: quarter circle from (0, s-r) to (r, s), center (r, s-r).
        import math
        cx, cy = r, s - r
        for i in range(1, self.ARC_STEPS):
            t = i / self.ARC_STEPS
            theta = math.radians(180 + 90 * t)
            x = cx + r * math.cos(theta)
            y = cy - r * math.sin(theta)
            nx = math.cos(theta)
            ny = -math.sin(theta)
            pts.append((x, y, nx, ny))

        # Horizontal arm: straight edge right of the arc, running rightward.
        for i in range(9):
            t = i / 8
            x = r + t * self.ARM
            pts.append((x, float(s), 0.0, 1.0))

        return pts

    def _ribbon_path(self, peak_half: float) -> QPainterPath:
        import math
        pts = self._centerline()
        n = len(pts)
        outer, inner = [], []
        for i, (x, y, nx, ny) in enumerate(pts):
            frac = i / (n - 1)
            bump = math.sin(math.pi * frac)  # 0 at both ends, 1 at the middle
            half = self.BASE_HALF + (peak_half - self.BASE_HALF) * bump
            outer.append(QPointF(x + nx * half, y + ny * half))
            inner.append(QPointF(x - nx * half, y - ny * half))

        path = QPainterPath()
        path.moveTo(outer[0])
        for pt in outer[1:]:
            path.lineTo(pt)
        for pt in reversed(inner):
            path.lineTo(pt)
        path.closeSubpath()
        return path

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self._hovered or self._dragging
        peak = self.HOVER_PEAK_HALF if active else self.IDLE_PEAK_HALF
        color = self._border_active if active else self._border
        p.fillPath(self._ribbon_path(peak), color)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_global = e.globalPosition().toPoint()
            self._start_geom   = self._win.geometry()
            self.update()

    def mouseMoveEvent(self, e):
        if not self._dragging:
            return
        delta = e.globalPosition().toPoint() - self._start_global
        g = self._start_geom
        new_w = max(self.MIN_W, g.width() - delta.x())   # drag left = wider
        new_h = max(self.MIN_H, g.height() + delta.y())  # drag down = taller
        # Always derive x from the live screen edge so the right side never drifts
        screen_right = self._win._screen_rect().right()
        new_x = screen_right - new_w
        self._win.setGeometry(new_x, g.y(), new_w, new_h)

    def mouseReleaseEvent(self, e):
        self._dragging = False
        self.update()


# ── Panel widget: paints its own background + border, handles click-to-defocus ─
class PanelWidget(QWidget):
    RADIUS = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg     = QColor("#f7f7f5")
        self._border = QColor("#d9d9d6")
        self._editor = None  # set after editor is created
        self._win    = None  # set to the top-level Jot window after creation
        self._radius = self.RADIUS
        self._square = False
        self._drag_start_global = None
        self._drag_start_win_pos = None

    def set_theme(self, dark: bool):
        if dark:
            self._bg     = QColor("#1e1e1e")
            self._border = QColor("#333333")
        else:
            self._bg     = QColor("#f7f7f5")
            self._border = QColor("#d9d9d6")
        self.update()

    def set_square_corners(self, square: bool):
        """Switches between the normal left-rounded shape and a plain
        rectangle — used when the window fills the whole screen."""
        self._square = square
        self._radius = 0 if square else self.RADIUS
        self.update()

    def _build_paths(self):
        """Returns (fill_path, border_path).
        fill_path  — closed shape used to paint the background.
        border_path — open path along top → left → bottom only (no right edge).
        Both use the left-rounded, right-square shape that matches the QRegion mask.
        """
        w, h, r = self.width(), self.height(), self._radius

        if self._square:
            fill = QPainterPath()
            fill.addRect(0, 0, w, h)
            o = 0.5
            bdr = QPainterPath()
            bdr.moveTo(w, o)
            bdr.lineTo(o, o)
            bdr.lineTo(o, h - o)
            bdr.lineTo(w, h - o)
            return fill, bdr

        # ── Fill (closed) ──────────────────────────────────────────────────
        fill = QPainterPath()
        fill.moveTo(w, 0)
        fill.lineTo(r, 0)
        fill.arcTo(0, 0, 2*r, 2*r, 90, 90)          # top-left arc
        fill.lineTo(0, h - r)
        fill.arcTo(0, h - 2*r, 2*r, 2*r, 180, 90)   # bottom-left arc
        fill.lineTo(w, h)
        fill.closeSubpath()

        # ── Border (open — no right edge) ──────────────────────────────────
        # 0.5 offset keeps 1-px stroke on pixel centres so it renders crisp
        o = 0.5
        bdr = QPainterPath()
        bdr.moveTo(w, o)
        bdr.lineTo(r, o)
        bdr.arcTo(o, o, 2*r - 1, 2*r - 1, 90, 90)
        bdr.lineTo(o, h - r)
        bdr.arcTo(o, h - 2*r - o, 2*r - 1, 2*r - 1, 180, 90)
        bdr.lineTo(w, h - o)

        return fill, bdr

    def paintEvent(self, e):
        fill, bdr = self._build_paths()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillPath(fill, self._bg)
        p.setPen(QPen(self._border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(bdr)
        p.end()

    def mousePressEvent(self, e):
        # Clicking any blank area of the panel removes focus from the editor
        if self._editor:
            self._editor.clearFocus()
        if e.button() == Qt.MouseButton.LeftButton and self._win is not None \
                and not self._win.is_fullscreen:
            self._drag_start_global = e.globalPosition().toPoint()
            self._drag_start_win_pos = self._win.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start_global is not None and self._win is not None:
            delta = e.globalPosition().toPoint() - self._drag_start_global
            sr = self._win._screen_rect()
            new_y = self._drag_start_win_pos.y() + delta.y()
            new_y = max(sr.top(), min(new_y, sr.bottom() - self._win.height()))
            new_x = sr.right() - self._win.width()  # stays pinned to the right edge
            self._win.move(new_x, new_y)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start_global = None
        self._drag_start_win_pos = None
        super().mouseReleaseEvent(e)


# ── Save Jot popup — small borderless dialog matching the panel's style ───────
class SaveJotDialog(QDialog):
    RADIUS = 12

    def __init__(self, parent, dark: bool, title: str = "Save Jot_",
                 placeholder: str = "Name this jot...", initial_text: str = "",
                 confirm_label: str = "Save"):
        super().__init__(parent)
        self.dark = dark
        self.result_name = None
        self._title_text = title
        self._placeholder = placeholder
        self._initial_text = initial_text
        self._confirm_label = confirm_label
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(280, 150)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("saveCard")
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(self._title_text)
        title.setObjectName("saveTitle")
        layout.addWidget(title)

        self.name_input = QLineEdit()
        self.name_input.setObjectName("saveInput")
        self.name_input.setPlaceholderText(self._placeholder)
        if self._initial_text:
            self.name_input.setText(self._initial_text)
            self.name_input.selectAll()
        self.name_input.returnPressed.connect(self._on_save)
        layout.addWidget(self.name_input)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("saveCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton(self._confirm_label)
        save_btn.setObjectName("saveConfirmBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._apply_theme()
        self.name_input.setFocus()

    def _apply_theme(self):
        if self.dark:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #1e1e1e;
                    border: 1px solid #333333;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #f0f0f0;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #saveInput {{
                    background: #2a2a2a;
                    color: #e8e8e8;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 8px 10px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #2a2a2a; color: #cccccc; }}
                #saveConfirmBtn {{
                    background: #e8e8e8;
                    color: #111111;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #ffffff; }}
            """)
        else:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #f7f7f5;
                    border: 1px solid #d9d9d6;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #111111;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #saveInput {{
                    background: #f0f0ee;
                    color: #111111;
                    border: 1px solid #dcdcd9;
                    border-radius: 7px;
                    padding: 8px 10px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #aaaaaa;
                    border: 1px solid #d9d9d6;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #f5f5f5; color: #555555; }}
                #saveConfirmBtn {{
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #333333; }}
            """)

    def _on_save(self):
        name = self.name_input.text().strip()
        if not name:
            name = "Untitled Jot"
        self.result_name = name
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)


# ── API key popup — lets the user paste their own Anthropic key, stored ───────
# locally in settings.json on this machine only (never bundled into the exe).
class ApiKeyDialog(QDialog):
    RADIUS = 12

    def __init__(self, parent, dark: bool, current_key: str = ""):
        super().__init__(parent)
        self.dark = dark
        self.result_key = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(300, 190)
        self._build_ui(current_key)

    def _build_ui(self, current_key: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("saveCard")
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("Anthropic API Key")
        title.setObjectName("saveTitle")
        layout.addWidget(title)

        subtitle = QLabel("Stored locally on this PC only — never shared.")
        subtitle.setObjectName("confirmMessage")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.key_input = QLineEdit()
        self.key_input.setObjectName("saveInput")
        self.key_input.setPlaceholderText("sk-ant-...")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        if current_key:
            self.key_input.setText(current_key)
        self.key_input.returnPressed.connect(self._on_save)
        input_row.addWidget(self.key_input, stretch=1)

        self.reveal_btn = QPushButton("👁")
        self.reveal_btn.setObjectName("keyRevealBtn")
        self.reveal_btn.setFixedSize(28, 28)
        self.reveal_btn.setCheckable(True)
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.toggled.connect(self._toggle_reveal)
        input_row.addWidget(self.reveal_btn)

        layout.addLayout(input_row)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("saveCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveConfirmBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._apply_theme()
        self.key_input.setFocus()

    def _toggle_reveal(self, checked: bool):
        self.key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _on_save(self):
        key = self.key_input.text().strip()
        if not key:
            return
        self.result_key = key
        self.accept()

    def _apply_theme(self):
        if self.dark:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #1e1e1e;
                    border: 1px solid #333333;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #f0f0f0;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #aaaaaa;
                    font-size: 11px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #saveInput {{
                    background: #2a2a2a;
                    color: #e8e8e8;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 8px 10px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }}
                #keyRevealBtn {{
                    background: #2a2a2a;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    font-size: 12px;
                }}
                #keyRevealBtn:hover    {{ background: #333333; color: #cccccc; }}
                #keyRevealBtn:checked  {{ background: #3a3a3a; color: #e8e8e8; }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #2a2a2a; color: #cccccc; }}
                #saveConfirmBtn {{
                    background: #e8e8e8;
                    color: #111111;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #ffffff; }}
            """)
        else:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #f7f7f5;
                    border: 1px solid #d9d9d6;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #111111;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #777777;
                    font-size: 11px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #saveInput {{
                    background: #f0f0ee;
                    color: #111111;
                    border: 1px solid #dcdcd9;
                    border-radius: 7px;
                    padding: 8px 10px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }}
                #keyRevealBtn {{
                    background: #f0f0ee;
                    color: #aaaaaa;
                    border: 1px solid #dcdcd9;
                    border-radius: 7px;
                    font-size: 12px;
                }}
                #keyRevealBtn:hover    {{ background: #e8e8e5; color: #555555; }}
                #keyRevealBtn:checked  {{ background: #e0e0dd; color: #111111; }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #aaaaaa;
                    border: 1px solid #d9d9d6;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #f5f5f5; color: #555555; }}
                #saveConfirmBtn {{
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #333333; }}
            """)


# ── Confirm popup — small borderless dialog matching the panel's style ────────
class ConfirmDialog(QDialog):
    RADIUS = 12

    def __init__(self, parent, dark: bool, title: str, message: str, confirm_label: str = "Overwrite"):
        super().__init__(parent)
        self.dark = dark
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(280, 150)
        self._build_ui(title, message, confirm_label)

    def _build_ui(self, title: str, message: str, confirm_label: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("saveCard")
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("saveTitle")
        layout.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setObjectName("confirmMessage")
        msg_lbl.setWordWrap(True)
        layout.addWidget(msg_lbl)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("saveCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton(confirm_label)
        confirm_btn.setObjectName("saveConfirmBtn")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.clicked.connect(self.accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

        self._apply_theme()

    def _apply_theme(self):
        if self.dark:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #1e1e1e;
                    border: 1px solid #333333;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #f0f0f0;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #aaaaaa;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #2a2a2a; color: #cccccc; }}
                #saveConfirmBtn {{
                    background: #e8e8e8;
                    color: #111111;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #ffffff; }}
            """)
        else:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #f7f7f5;
                    border: 1px solid #d9d9d6;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #111111;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #777777;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #aaaaaa;
                    border: 1px solid #d9d9d6;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #f5f5f5; color: #555555; }}
                #saveConfirmBtn {{
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #333333; }}
            """)


# ── AI result preview — Accept / Discard popup for prompt-bar AI results ──────
class AIPreviewDialog(QDialog):
    RADIUS = 12

    def __init__(self, parent, dark: bool, prompt: str, result_text: str):
        super().__init__(parent)
        self.dark = dark
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(300, 320)
        self._build_ui(prompt, result_text)

    def _build_ui(self, prompt: str, result_text: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("saveCard")
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title_lbl = QLabel("AI Result")
        title_lbl.setObjectName("saveTitle")
        layout.addWidget(title_lbl)

        if prompt:
            prompt_lbl = QLabel(f"\u201c{prompt}\u201d")
            prompt_lbl.setObjectName("confirmMessage")
            prompt_lbl.setWordWrap(True)
            layout.addWidget(prompt_lbl)

        self.preview = QTextEdit()
        self.preview.setObjectName("aiPreviewText")
        self.preview.setPlainText(result_text)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        discard_btn = QPushButton("Discard")
        discard_btn.setObjectName("saveCancelBtn")
        discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        discard_btn.clicked.connect(self.reject)

        accept_btn = QPushButton("Accept")
        accept_btn.setObjectName("saveConfirmBtn")
        accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        accept_btn.clicked.connect(self.accept)

        btn_row.addWidget(discard_btn)
        btn_row.addWidget(accept_btn)
        layout.addLayout(btn_row)

        self._apply_theme()

    def _apply_theme(self):
        if self.dark:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #1e1e1e;
                    border: 1px solid #333333;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #f0f0f0;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #aaaaaa;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #aiPreviewText {{
                    background: #2a2a2a;
                    color: #e8e8e8;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 8px 10px;
                    font-family: 'Georgia';
                    font-size: 12px;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #2a2a2a; color: #cccccc; }}
                #saveConfirmBtn {{
                    background: #e8e8e8;
                    color: #111111;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #ffffff; }}
            """)
        else:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #f7f7f5;
                    border: 1px solid #d9d9d6;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #111111;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #777777;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #aiPreviewText {{
                    background: #f0f0ee;
                    color: #111111;
                    border: 1px solid #dcdcd9;
                    border-radius: 7px;
                    padding: 8px 10px;
                    font-family: 'Georgia';
                    font-size: 12px;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #aaaaaa;
                    border: 1px solid #d9d9d6;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #f5f5f5; color: #555555; }}
                #saveConfirmBtn {{
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #333333; }}
            """)


# ── Unsaved-changes popup — Don't Save / Cancel / Save, shown when closing ────
# a jot tab that has edits which haven't been written back to jots.json yet.
class SaveChangesDialog(QDialog):
    RADIUS = 12

    def __init__(self, parent, dark: bool, jot_name: str, message: str = None):
        super().__init__(parent)
        self.dark = dark
        self.choice = None  # "save" | "discard" | "cancel"
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(300, 160)
        self._build_ui(jot_name, message)

    def _build_ui(self, jot_name: str, message: str = None):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("saveCard")
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_lbl = QLabel("Unsaved Changes")
        title_lbl.setObjectName("saveTitle")
        layout.addWidget(title_lbl)

        msg_text = message if message else f"Save changes to \"{jot_name}\" before closing this tab?"
        msg_lbl = QLabel(msg_text)
        msg_lbl.setObjectName("confirmMessage")
        msg_lbl.setWordWrap(True)
        layout.addWidget(msg_lbl)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        discard_btn = QPushButton("Don't Save")
        discard_btn.setObjectName("saveCancelBtn")
        discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        discard_btn.clicked.connect(self._on_discard)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("saveCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self._on_cancel)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveConfirmBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(discard_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._apply_theme()

    def _on_discard(self):
        self.choice = "discard"
        self.accept()

    def _on_cancel(self):
        self.choice = "cancel"
        self.reject()

    def _on_save(self):
        self.choice = "save"
        self.accept()

    def _apply_theme(self):
        if self.dark:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #1e1e1e;
                    border: 1px solid #333333;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #f0f0f0;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #aaaaaa;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 7px 10px;
                    font-size: 11px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #2a2a2a; color: #cccccc; }}
                #saveConfirmBtn {{
                    background: #e8e8e8;
                    color: #111111;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 10px;
                    font-size: 11px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #ffffff; }}
            """)
        else:
            self.card.setStyleSheet(f"""
                #saveCard {{
                    background: #f7f7f5;
                    border: 1px solid #d9d9d6;
                    border-radius: {self.RADIUS}px;
                }}
                #saveTitle {{
                    color: #111111;
                    font-size: 15px;
                    font-weight: 700;
                    font-family: 'Georgia';
                }}
                #confirmMessage {{
                    color: #777777;
                    font-size: 12px;
                    font-family: 'Segoe UI', sans-serif;
                }}
                #saveCancelBtn {{
                    background: transparent;
                    color: #aaaaaa;
                    border: 1px solid #d9d9d6;
                    border-radius: 7px;
                    padding: 7px 10px;
                    font-size: 11px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                }}
                #saveCancelBtn:hover {{ background: #f5f5f5; color: #555555; }}
                #saveConfirmBtn {{
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 7px 10px;
                    font-size: 11px;
                    font-family: '{BUTTON_FONT_FAMILY}';
                    font-weight: 600;
                }}
                #saveConfirmBtn:hover {{ background: #333333; }}
            """)


# ── Jots list view — shown in place of the editor when "Jots_" is toggled ─────
class JotsListView(QWidget):
    jot_selected = pyqtSignal(int)   # index into load_jots()
    jot_deleted  = pyqtSignal(int)
    jot_renamed  = pyqtSignal(int, str)
    jot_created  = pyqtSignal(str)   # name for a brand-new empty jot

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("jotsListRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.dark = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Pinned above the scroll area (not inside it) so it always stays
        # at the top, no matter how many jots get added below it.
        self.new_jot_btn = QPushButton("+  New Jot_")
        self.new_jot_btn.setObjectName("newJotBtn")
        self.new_jot_btn.setFixedHeight(34)
        self.new_jot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_jot_btn.clicked.connect(self._request_new_jot)
        layout.addWidget(self.new_jot_btn)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("jotsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.inner = QWidget()
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.inner_layout.setSpacing(6)
        self.inner_layout.addStretch()
        self.scroll.setWidget(self.inner)

        layout.addWidget(self.scroll)

        self.empty_label = QLabel("No saved jots yet.")
        self.empty_label.setObjectName("jotsEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

    def _request_new_jot(self):
        dialog = SaveJotDialog(
            self.window(), self.dark,
            title="New Jot_",
            placeholder="Name this jot...",
            confirm_label="Create"
        )
        self._center_dialog(dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_name:
            name = dialog.result_name
            jots = load_jots()
            duplicate = any(j.get("name", "") == name for j in jots)
            if duplicate:
                confirm = ConfirmDialog(
                    self.window(), self.dark,
                    title="Duplicate Name",
                    message=f"Another jot is already named \"{name}\". Create anyway?",
                    confirm_label="Create"
                )
                self._center_dialog(confirm)
                if confirm.exec() != QDialog.DialogCode.Accepted:
                    return
            self.jot_created.emit(name)

    def set_theme(self, dark: bool):
        self.dark = dark
        self.refresh()

    def refresh(self):
        # Clear existing rows
        while self.inner_layout.count() > 1:  # keep trailing stretch
            item = self.inner_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        jots = load_jots()
        self.empty_label.setVisible(len(jots) == 0)
        self.scroll.setVisible(len(jots) > 0)

        for idx, jot in enumerate(jots):
            row = self._make_row(idx, jot.get("name", "Untitled Jot"))
            self.inner_layout.insertWidget(self.inner_layout.count() - 1, row)

        self._apply_row_theme()

    def _center_dialog(self, dialog):
        win = self.window()
        dialog.move(
            win.x() + (win.width() - dialog.width()) // 2,
            win.y() + (win.height() - dialog.height()) // 2
        )

    def _make_row(self, index: int, name: str) -> QFrame:
        row = QFrame()
        row.setObjectName("jotRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 8, 8)
        row_layout.setSpacing(8)

        label = QLabel(name)
        label.setObjectName("jotRowLabel")
        row_layout.addWidget(label, stretch=1)

        edit_btn = QPushButton("✎")
        edit_btn.setObjectName("jotEditBtn")
        edit_btn.setFixedSize(22, 22)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.clicked.connect(lambda: self._request_rename(index))
        row_layout.addWidget(edit_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("jotDeleteBtn")
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda: self._request_delete(index))
        row_layout.addWidget(del_btn)

        def _row_click(e, i=index):
            self.jot_selected.emit(i)
        row.mousePressEvent = _row_click

        return row

    def _request_rename(self, index: int):
        jots = load_jots()
        if not (0 <= index < len(jots)):
            return
        current_name = jots[index].get("name", "Untitled Jot")
        dialog = SaveJotDialog(
            self.window(), self.dark,
            title="Rename Jot_",
            placeholder="New name...",
            initial_text=current_name,
            confirm_label="Rename"
        )
        self._center_dialog(dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_name:
            new_name = dialog.result_name
            if new_name == current_name:
                return
            duplicate = any(
                i != index and j.get("name", "") == new_name
                for i, j in enumerate(jots)
            )
            if duplicate:
                confirm = ConfirmDialog(
                    self.window(), self.dark,
                    title="Duplicate Name",
                    message=f"Another jot is already named \"{new_name}\". Rename anyway?",
                    confirm_label="Rename"
                )
                self._center_dialog(confirm)
                if confirm.exec() != QDialog.DialogCode.Accepted:
                    return
            self.jot_renamed.emit(index, new_name)

    def _request_delete(self, index: int):
        jots = load_jots()
        if not (0 <= index < len(jots)):
            return
        confirm = ConfirmDialog(
            self.window(), self.dark,
            title="Delete Jot?",
            message="Permanently delete jot?",
            confirm_label="Delete"
        )
        self._center_dialog(confirm)
        if confirm.exec() == QDialog.DialogCode.Accepted:
            self.jot_deleted.emit(index)

    def _apply_row_theme(self):
        if self.dark:
            style = """
                #jotsListRoot {
                    background: #1e1e1e;
                }
                QScrollArea#jotsScroll {
                    background: transparent;
                    border: none;
                }
                QScrollArea#jotsScroll > QWidget > QWidget {
                    background: transparent;
                }
                #jotRow {
                    background: #2a2a2a;
                    border: 1px solid #3a3a3a;
                    border-radius: 8px;
                }
                #jotRow:hover { background: #333333; }
                #jotRowLabel {
                    color: #e8e8e8;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }
                #jotEditBtn {
                    background: transparent;
                    color: #666666;
                    border: none;
                    border-radius: 11px;
                    font-size: 11px;
                }
                #jotEditBtn:hover { background: #3a3a3a; color: #cccccc; }
                #jotDeleteBtn {
                    background: transparent;
                    color: #666666;
                    border: none;
                    border-radius: 11px;
                    font-size: 14px;
                }
                #jotDeleteBtn:hover { background: #3a3a3a; color: #cccccc; }
                #jotsEmpty {
                    color: #666666;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                    padding-top: 20px;
                }
                #newJotBtn {
                    background: transparent;
                    color: #999999;
                    border: 1px dashed #3a3a3a;
                    border-radius: 8px;
                    font-size: 12px;
                    font-family: '__BTNFONT__';
                    font-weight: 600;
                }
                #newJotBtn:hover { background: #2a2a2a; color: #e8e8e8; border: 1px dashed #555555; }
            """
        else:
            style = """
                #jotsListRoot {
                    background: #f7f7f5;
                }
                QScrollArea#jotsScroll {
                    background: transparent;
                    border: none;
                }
                QScrollArea#jotsScroll > QWidget > QWidget {
                    background: transparent;
                }
                #jotRow {
                    background: #f0f0ee;
                    border: 1px solid #dcdcd9;
                    border-radius: 8px;
                }
                #jotRow:hover { background: #e8e8e5; }
                #jotRowLabel {
                    color: #111111;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }
                #jotEditBtn {
                    background: transparent;
                    color: #aaaaaa;
                    border: none;
                    border-radius: 11px;
                    font-size: 11px;
                }
                #jotEditBtn:hover { background: #dcdcd9; color: #555555; }
                #jotDeleteBtn {
                    background: transparent;
                    color: #aaaaaa;
                    border: none;
                    border-radius: 11px;
                    font-size: 14px;
                }
                #jotDeleteBtn:hover { background: #dcdcd9; color: #555555; }
                #jotsEmpty {
                    color: #aaaaaa;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                    padding-top: 20px;
                }
                #newJotBtn {
                    background: transparent;
                    color: #888888;
                    border: 1px dashed #d5d5d5;
                    border-radius: 8px;
                    font-size: 12px;
                    font-family: '__BTNFONT__';
                    font-weight: 600;
                }
                #newJotBtn:hover { background: #f5f5f5; color: #111111; border: 1px dashed #aaaaaa; }
            """
        self.setStyleSheet(_apply_btn_font(style))


# ── Rich-text editor: embeds pasted/dropped images inline, surfaces ───────────
# dropped non-image files as attachment candidates via a signal.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
EDITOR_MAX_IMAGE_WIDTH = 480  # downscale so jots.json / clipboard don't balloon

class JotEditor(QTextEdit):
    file_dropped = pyqtSignal(str)  # local path of a dropped non-image file

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAcceptRichText(True)
        self._placeholder_text = ""

        # ── Blinking underscore text cursor ────────────────────────────────
        # The native vertical-bar caret is switched off (cursorWidth=0) and
        # replaced with a small solid bar drawn on the viewport (rather than
        # relying on the font's own "_" glyph, which is too thin to control
        # precisely), blinked via a timer and repositioned whenever the
        # cursor moves, the text changes, or the view scrolls.
        self.setCursorWidth(0)
        self.CURSOR_THICKNESS = 3  # bar height in px — thicker than a normal underscore glyph
        self._blink_cursor = QLabel("", self.viewport())
        self._blink_cursor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._blink_cursor.setStyleSheet("background: #111111; border: none; border-radius: 1px;")
        self._blink_cursor.hide()
        self._blink_visible = True
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self.cursorPositionChanged.connect(self._reposition_blink_cursor)
        self.textChanged.connect(self._reposition_blink_cursor)
        self.verticalScrollBar().valueChanged.connect(self._reposition_blink_cursor)

    def setPlaceholderText(self, text):
        """Remembers the placeholder so it can be hidden while focused (Qt
        otherwise keeps showing it — overlapping the blinking cursor — for
        as long as the document is empty, focus or no) and restored on
        blur if the field is still empty."""
        self._placeholder_text = text
        QTextEdit.setPlaceholderText(self, "" if self.hasFocus() else text)

    def set_cursor_color(self, color: str):
        """Called by Jot._apply_theme() so the underscore matches the
        editor's current text color in light/dark mode."""
        self._blink_cursor.setStyleSheet(f"background: {color}; border: none; border-radius: 1px;")

    def _toggle_blink(self):
        self._blink_visible = not self._blink_visible
        self._blink_cursor.setVisible(self._blink_visible and self.hasFocus())

    def _reposition_blink_cursor(self):
        rect = self.cursorRect()
        fm = self.fontMetrics()
        w = fm.horizontalAdvance("_") + 2  # same length as before
        thickness = self.CURSOR_THICKNESS
        self._blink_cursor.setFixedSize(w, thickness)
        self._blink_cursor.move(rect.x(), rect.bottom() - thickness - 2)
        if self.hasFocus():
            self._blink_visible = True
            self._blink_cursor.setVisible(True)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        QTextEdit.setPlaceholderText(self, "")
        self._reposition_blink_cursor()
        self._blink_visible = True
        self._blink_cursor.show()
        self._blink_timer.start()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        QTextEdit.setPlaceholderText(self, self._placeholder_text)
        self._blink_timer.stop()
        self._blink_cursor.hide()

    def insert_image(self, qimage: QImage):
        """Embeds an image inline as a base64 data URI so it round-trips
        through toHtml()/setHtml() (jots.json storage) and through the
        system clipboard (so it survives a paste into an email body)."""
        if qimage.isNull():
            return
        if qimage.width() > EDITOR_MAX_IMAGE_WIDTH:
            qimage = qimage.scaledToWidth(
                EDITOR_MAX_IMAGE_WIDTH, Qt.TransformationMode.SmoothTransformation
            )
        buf = QByteArray()
        buffer = QBuffer(buf)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        qimage.save(buffer, "PNG")
        b64 = bytes(buf.toBase64()).decode("ascii")
        self.textCursor().insertHtml(f'<img src="data:image/png;base64,{b64}" />')

    def canInsertFromMimeData(self, source) -> bool:
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            self.insert_image(QImage(source.imageData()))
            return
        if source.hasUrls():
            any_url = False
            for url in source.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                any_url = True
                ext = os.path.splitext(path)[1].lower()
                qimage = QImage(path) if ext in IMAGE_EXTENSIONS else QImage()
                if not qimage.isNull():
                    self.insert_image(qimage)
                else:
                    self.file_dropped.emit(path)
            if any_url:
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasImage() or md.hasUrls():
            self.insertFromMimeData(md)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


# ── Metal AI buttons — brushed-aluminum push buttons with a polished-chrome
# rim, engraved text, and a real 3D extruded left/bottom edge that flattens
# on press. Ported from a standalone PySide6 prototype the user supplied —
# same visual design, adapted to PyQt6's scoped enums (Qt.CursorShape.*,
# QFont.Weight.*, QPainter.RenderHint.*, Qt.AlignmentFlag.*) and to this
# app's existing imports. ─────────────────────────────────────────────────────
class MetalButton(QPushButton):
    DEPTH = 6          # px of visible left/bottom extrusion
    RIM = 6            # chrome rim width
    RADIUS = 13         # outer corner radius
    EXTRUDE_COLORS = ["#99a0a8", "#e2e6ea", "#8a919a", "#7e858e", "#d3d8dd", "#737a83"]

    def __init__(self, text="", parent=None, height=30):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        f = QFont("Helvetica", max(9, int(height * 0.26)), QFont.Weight.DemiBold)
        self.setFont(f)
        self.setFixedHeight(height + self.DEPTH)
        w = self.fontMetrics().horizontalAdvance(text) + height * 1.2
        self.setFixedWidth(int(w) + self.DEPTH)
        # deterministic brushed-grain rows
        rnd = random.Random(hash(text) & 0xFFFF)
        self._grain = [(rnd.random(), rnd.random(), rnd.random()) for _ in range(300)]

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)  # dims the whole button while an AI request is in flight
        pressed = self.isDown()
        depth = 1 if pressed else self.DEPTH
        # button body rect: extrusion pokes out to the left/bottom
        shift = (self.DEPTH - depth)
        body = QRectF(self.DEPTH - shift, shift, self.width() - self.DEPTH, self.height() - self.DEPTH)

        # --- extrusion stack (left + down offsets in alternating chrome tones) ---
        for i in range(depth, 0, -1):
            c = QColor(self.EXTRUDE_COLORS[(i - 1) % len(self.EXTRUDE_COLORS)])
            path = QPainterPath()
            path.addRoundedRect(body.translated(-i, i), self.RADIUS, self.RADIUS)
            p.fillPath(path, c)

        # --- chrome rim: high-contrast mirror banding ---
        rim = QLinearGradient(body.topLeft(), body.bottomRight())
        for pos, col in [(0.0, "#ffffff"), (0.07, "#e6ebf0"), (0.20, "#5f6771"),
                         (0.32, "#fbfdff"), (0.37, "#ffffff"), (0.50, "#9aa2ac"),
                         (0.60, "#454c55"), (0.72, "#eef2f6"), (0.78, "#ffffff"),
                         (0.90, "#6e7680"), (1.0, "#dce1e6")]:
            rim.setColorAt(pos, QColor(col))
        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)
        p.fillPath(path, rim)

        # --- brushed aluminum face ---
        face = body.adjusted(self.RIM, self.RIM, -self.RIM, -self.RIM)
        fpath = QPainterPath()
        fpath.addRoundedRect(face, self.RADIUS - 4, self.RADIUS - 4)
        p.fillPath(fpath, QColor("#c9cdd3"))
        p.save()
        p.setClipPath(fpath)
        # grain lines
        for gy, gx, gv in self._grain:
            y = face.top() + gy * face.height()
            x0 = face.left() + gx * face.width() * 0.6
            ln = 20 + gv * face.width() * 0.4
            col = QColor(255, 255, 255, 26) if gv < 0.5 else QColor(70, 76, 84, 22)
            p.setPen(QPen(col, 1))
            p.drawLine(QPointF(x0, y), QPointF(x0 + ln, y))
        # vertical sheen: bright top, shaded bottom
        sheen = QLinearGradient(face.topLeft(), face.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 120))
        sheen.setColorAt(0.45, QColor(255, 255, 255, 20))
        sheen.setColorAt(0.60, QColor(20, 24, 30, 12))
        sheen.setColorAt(1.0, QColor(20, 24, 30, 60))
        p.fillPath(fpath, sheen)
        # diagonal specular sweep
        sweep = QLinearGradient(face.topLeft(), QPointF(face.right(), face.center().y()))
        sweep.setColorAt(0.30, QColor(255, 255, 255, 0))
        sweep.setColorAt(0.48, QColor(255, 255, 255, 150 if not pressed else 110))
        sweep.setColorAt(0.66, QColor(255, 255, 255, 0))
        p.fillPath(fpath, sweep)
        if pressed:
            p.fillPath(fpath, QColor(20, 24, 30, 18))
        p.restore()

        # --- engraved text: catch-light below, shadow above, dark fill ---
        p.setFont(self.font())
        r = face.toRect()
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(r.translated(0, 1), Qt.AlignmentFlag.AlignCenter, self.text())
        p.setPen(QColor(0, 0, 0, 130))
        p.drawText(r.translated(0, -1), Qt.AlignmentFlag.AlignCenter, self.text())
        p.setPen(QColor("#2c3037"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


# ── AI button row that collapses into a "…" overflow menu ─────────────────────
# MetalButtons are fixed-width, so on a narrow panel they'd otherwise just
# get cropped/overlap. Instead, once they no longer all fit, the row hides
# the ones that don't and replaces them with a single "…" MetalButton that
# opens a menu with the remaining actions.
class AIButtonBar(QWidget):
    def __init__(self, actions, on_trigger, parent=None):
        """actions: list of (label, mode) tuples. on_trigger(mode) is called
        whenever a button (visible or overflowed) is activated."""
        super().__init__(parent)
        self._actions = actions
        self._on_trigger = on_trigger

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch()

        self._buttons = []
        for label, mode in actions:
            btn = MetalButton(label)
            btn.setObjectName("aiBtn")
            btn.clicked.connect(lambda _, m=mode: self._on_trigger(m))
            self._buttons.append(btn)
            self._layout.addWidget(btn)

        self._more_btn = MetalButton("…")
        self._more_btn.setObjectName("aiBtn")
        self._more_btn.setToolTip("More AI actions")
        self._more_btn.clicked.connect(self._show_overflow_menu)
        self._more_btn.hide()
        self._layout.addWidget(self._more_btn)

        self._layout.addStretch()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_overflow()

    def _update_overflow(self):
        avail = self.width()
        if avail <= 0:
            return
        spacing = self._layout.spacing()
        widths = [b.sizeHint().width() for b in self._buttons]

        full_total = sum(widths) + spacing * max(0, len(widths) - 1)
        if full_total <= avail:
            for b in self._buttons:
                b.show()
            self._more_btn.hide()
            return

        more_w = self._more_btn.sizeHint().width()
        running = 0
        shown = 0
        for i, w in enumerate(widths):
            candidate = running + w + (spacing if i > 0 else 0)
            # Always keep room for the "…" button once anything overflows.
            if candidate + spacing + more_w <= avail:
                running = candidate
                shown += 1
            else:
                break

        for i, b in enumerate(self._buttons):
            b.setVisible(i < shown)
        self._more_btn.setVisible(shown < len(self._buttons))

    def _show_overflow_menu(self):
        menu = QMenu(self)
        for i, (label, mode) in enumerate(self._actions):
            if not self._buttons[i].isVisible():
                action = menu.addAction(label)
                action.triggered.connect(lambda checked=False, m=mode: self._on_trigger(m))
        menu.exec(self._more_btn.mapToGlobal(QPoint(0, self._more_btn.height())))


# ── Hides a QLineEdit's native blinking bar cursor so a custom blinking
# underscore overlay (see Jot._reposition_prompt_blink) can stand in for it
# without the two rendering on top of each other. ─────────────────────────────
class ZeroWidthCursorStyle(QProxyStyle):
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_TextCursorWidth:
            return 0
        return super().pixelMetric(metric, option, widget)


# ── Main Window ───────────────────────────────────────────────────────────────
class Jot(QMainWindow):
    PANEL_WIDTH  = 380  # wide enough that Polish / Draft Email / Expand Idea all show in full on open
    PANEL_HEIGHT = 580

    def __init__(self):
        super().__init__()
        self.is_visible = False
        self.ai_worker  = None
        self.current_jot_index = None  # None = not tied to a saved jot yet
        self.is_fullscreen = False
        self._pre_fullscreen_geom = None
        self._settings = load_settings()
        self.dark_mode  = bool(self._settings.get("dark_mode", False))
        self.ai_model   = self._settings.get("ai_model", DEFAULT_AI_MODEL)
        if self.ai_model not in AI_MODEL_DISPLAY_NAMES:
            self.ai_model = DEFAULT_AI_MODEL  # guards against a stale/removed id in settings.json
        self._setup_window()
        self._setup_ui()
        self._enforce_ai_button_min_width()
        self._setup_tray()
        self._setup_hotkey()
        self._position_offscreen()
        self.show()  # always visible, just offscreen until opened

        # Auto-enable startup on first run
        if not is_startup_enabled():
            enable_startup()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(300, 420)
        self.resize(self.PANEL_WIDTH, self.PANEL_HEIGHT)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        from PyQt6.QtGui import QRegion
        w, h = self.width(), self.height()
        if self.is_fullscreen:
            # Plain rectangle — no rounded corners while fullscreen.
            self.setMask(QRegion(0, 0, w, h))
        else:
            # Build a mask: rounded corners on the left, square on the right,
            # matching Windows 11's ~12 px corner radius aesthetic.
            r = 12
            reg = QRegion(r, 0, w - r, h)                                          # body (right of left-curve strip)
            reg = reg.united(QRegion(0, r, r, h - 2 * r))                         # left strip (straight middle)
            reg = reg.united(QRegion(0, 0,         2*r, 2*r, QRegion.RegionType.Ellipse))  # top-left circle
            reg = reg.united(QRegion(0, h - 2 * r, 2*r, 2*r, QRegion.RegionType.Ellipse)) # bottom-left circle
            self.setMask(reg)
        self._position_resize_handle()

    def _toggle_fullscreen(self):
        central = self.centralWidget()
        if not self.is_fullscreen:
            # Entering fullscreen: remember current geometry so we can return to it.
            self._pre_fullscreen_geom = self.geometry()
            self.is_fullscreen = True
            if hasattr(self, "resize_handle"):
                self.resize_handle.hide()
            if hasattr(central, "set_square_corners"):
                central.set_square_corners(True)
            screen_rect = self._screen_rect()
            self.setGeometry(screen_rect)
            self.fullscreen_btn.setIcon(_make_restore_icon())
            self.fullscreen_btn.setToolTip("Exit fullscreen")
        else:
            # Restoring: go back to the side-panel geometry.
            self.is_fullscreen = False
            if hasattr(central, "set_square_corners"):
                central.set_square_corners(False)
            if self._pre_fullscreen_geom is not None:
                self.setGeometry(self._pre_fullscreen_geom)
            if hasattr(self, "resize_handle"):
                self.resize_handle.show()
            self.fullscreen_btn.setIcon(_make_fullscreen_icon())
            self.fullscreen_btn.setToolTip("Toggle fullscreen")

    def _position_resize_handle(self):
        # Fixed at the window's own bottom-left corner, matching the panel's
        # rounded corner — stays put regardless of which view (editor or
        # Jots list) is currently showing.
        if not hasattr(self, "resize_handle"):
            return
        size = self.resize_handle.SIZE
        x = 0
        y = self.height() - size
        self.resize_handle.move(x, y)
        self.resize_handle.raise_()

    def _setup_ui(self):
        central = PanelWidget()
        central.setObjectName("panel")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        # Bottom margin is padded out to clear the resize handle's footprint
        # (ResizeHandle.SIZE = 26px) so it never overlaps the AI buttons.
        layout.setContentsMargins(24, 20, 24, 32)
        layout.setSpacing(14)

        # Header
        # Dark/light mode toggle — lives in the header next to the fullscreen
        # button; created here so it exists before btn_group is assembled.
        # Starts in whatever state self.dark_mode already is (no animation on
        # startup — it should just match the current theme immediately) and
        # animates the sun<->moon spin on every subsequent click.
        self.dark_btn = ThemeToggle(central, size=28, dark=self.dark_mode,
                                    on_toggle=self._on_theme_toggle_changed)
        self.dark_btn.setObjectName("darkBtn")
        self.dark_btn.setToolTip("Toggle dark mode")

        header = QHBoxLayout()
        self.title_jot = LogoLabel("Jot")
        self.title_jot.setObjectName("titleJot")
        self.title_jot.setCursor(Qt.CursorShape.PointingHandCursor)
        # Fixed width (sized to fit the longer "Jots" variant, plus the
        # drawn underscore mark) so the link button beside it never shifts
        # position when the label text swaps between "Jot" and "Jots" on
        # hover / view toggle. Kept tight (small buffer) so the
        # hover-highlight box doesn't trail off with extra empty space on
        # the right past the text.
        _title_metrics_font = QFont("Georgia", 22, QFont.Weight.Bold)
        self.title_jot.setFont(_title_metrics_font)
        from PyQt6.QtGui import QFontMetrics
        _title_w = (QFontMetrics(_title_metrics_font).horizontalAdvance("Jots") + 4
                    + LogoLabel.UNDERSCORE_GAP + LogoLabel.UNDERSCORE_WIDTH)
        self.title_jot.setFixedWidth(_title_w)
        self.title_jot.enterEvent = lambda e: self._title_hover_enter()
        self.title_jot.leaveEvent = lambda e: self._title_hover_leave()
        self.title_jot.mousePressEvent = lambda e: self._title_pressed()
        self.title_jot.mouseReleaseEvent = lambda e: self._title_released_and_toggle()

        title_dot = QLabel(".")
        title_dot.setObjectName("titleDot")

        self.link_btn = QPushButton()
        self.link_btn.setObjectName("linkBtn")
        self.link_btn.setFixedSize(24, 24)
        self.link_btn.setIcon(_make_link_icon())
        self.link_btn.setIconSize(QSize(15, 15))
        self.link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_btn.setToolTip("Open the Jot_ guide")
        self.link_btn.clicked.connect(self._open_guide_link)

        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._handle_close_request)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        minimise_btn = QPushButton("−")
        minimise_btn.setObjectName("minimiseBtn")
        minimise_btn.setFixedSize(28, 28)
        minimise_btn.clicked.connect(self.hide_panel)
        minimise_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.fullscreen_btn = QPushButton()
        self.fullscreen_btn.setObjectName("fullscreenBtn")
        self.fullscreen_btn.setFixedSize(28, 28)
        self.fullscreen_btn.setIcon(_make_fullscreen_icon())
        self.fullscreen_btn.setIconSize(self.fullscreen_btn.size())
        self.fullscreen_btn.setToolTip("Toggle fullscreen")
        self.fullscreen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)

        btn_group = QHBoxLayout()
        btn_group.setSpacing(4)
        btn_group.addWidget(self.dark_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_group.addWidget(self.fullscreen_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_group.addWidget(minimise_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_group.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        header.addWidget(self.title_jot, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.link_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        header.addLayout(btn_group)
        layout.addLayout(header)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        layout.addWidget(line)

        # ── Stacked content: main editor view <-> Jots list view ───────────
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        main_view = QWidget()
        main_layout = QVBoxLayout(main_view)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(14)

        # Editor
        # ── Formatting toolbar ────────────────────────────────────────────
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)

        self.bold_btn = QPushButton("B")
        self.bold_btn.setObjectName("fmtBtn")
        self.bold_btn.setFixedSize(28, 28)
        self.bold_btn.setCheckable(True)
        self.bold_btn.setFont(QFont("Georgia", 11, QFont.Weight.Bold))
        self.bold_btn.clicked.connect(self._toggle_bold)

        self.italic_btn = QPushButton("I")
        self.italic_btn.setObjectName("fmtBtn")
        self.italic_btn.setFixedSize(28, 28)
        self.italic_btn.setCheckable(True)
        italic_font = QFont("Georgia", 11, QFont.Weight.Normal)
        italic_font.setItalic(True)
        self.italic_btn.setFont(italic_font)
        self.italic_btn.clicked.connect(self._toggle_italic)

        self.caps_btn = QPushButton("Aa")
        self.caps_btn.setObjectName("capsBtn")
        self.caps_btn.setFixedSize(28, 28)
        self.caps_btn.setCheckable(False)
        self.caps_btn.setFont(QFont(BUTTON_FONT_FAMILY, 11, QFont.Weight.Bold))
        self.caps_btn.pressed.connect(self._caps_pressed)
        self.caps_btn.released.connect(self._caps_released)

        fmt_row.addWidget(self.bold_btn)
        fmt_row.addWidget(self.italic_btn)
        fmt_row.addWidget(self.caps_btn)
        fmt_row.addStretch()

        main_layout.addLayout(fmt_row)

        # ── Jot tabs bar — browser-style tabs for loaded jots ──────────────
        # Open tabs live in self.open_tabs (list of dicts: jot_index, name,
        # text (live draft HTML), baseline (last-saved HTML), attachments,
        # baseline_attachments). self.active_tab is the index into open_tabs
        # currently shown in the editor, or None when the editor holds a
        # fresh/unsaved note with no tab.
        self.open_tabs = []
        self.active_tab = None
        self._tab_widgets = []  # parallel list of {frame, label, dot, close_btn}
        self.current_attachments = []  # attachment dicts for whatever's in the editor now

        self.tabs_scroll = QScrollArea()
        self.tabs_scroll.setObjectName("tabsScroll")
        self.tabs_scroll.setWidgetResizable(True)
        self.tabs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tabs_scroll.setFixedHeight(30)
        self.tabs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tabs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs_scroll.setVisible(False)  # hidden until a tab is open

        tabs_inner = QWidget()
        tabs_inner.setObjectName("tabsInner")
        self.tabs_row = QHBoxLayout(tabs_inner)
        self.tabs_row.setContentsMargins(0, 0, 0, 0)
        self.tabs_row.setSpacing(4)
        self.tabs_row.addStretch()
        self.tabs_scroll.setWidget(tabs_inner)

        # ── Editor ────────────────────────────────────────────────────────
        self.editor = JotEditor()
        self.editor.setObjectName("editor")
        self.editor.setPlaceholderText("Start jotting...\n\nIdeas, drafts, reminders — anything.")
        self.editor.currentCharFormatChanged.connect(self._sync_fmt_buttons)
        self.editor.textChanged.connect(self._on_editor_text_changed)
        self.editor.file_dropped.connect(self._on_file_dropped)
        central._editor = self.editor  # allows PanelWidget to clear focus on blank clicks
        central._win = self  # allows PanelWidget to drag-move the window vertically

        # Tabs bar sits directly on the editor with zero gap so the two read
        # as one connected piece, like a browser's tab strip + content pane.
        editor_group = QWidget()
        editor_group_layout = QVBoxLayout(editor_group)
        editor_group_layout.setContentsMargins(0, 0, 0, 0)
        editor_group_layout.setSpacing(4)
        editor_group_layout.addWidget(self.tabs_scroll)
        editor_group_layout.addWidget(self.editor, stretch=1)
        main_layout.addWidget(editor_group, stretch=1)

        # ── Attachments chip row — non-image files attached to this jot ────
        self.attach_scroll = QScrollArea()
        self.attach_scroll.setObjectName("attachScroll")
        self.attach_scroll.setWidgetResizable(True)
        self.attach_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.attach_scroll.setFixedHeight(28)
        self.attach_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attach_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attach_scroll.setVisible(False)  # hidden until a file is attached

        attach_inner = QWidget()
        attach_inner.setObjectName("attachInner")
        self.attach_row = QHBoxLayout(attach_inner)
        self.attach_row.setContentsMargins(0, 0, 0, 0)
        self.attach_row.setSpacing(4)
        self.attach_row.addStretch()
        self.attach_scroll.setWidget(attach_inner)

        main_layout.addWidget(self.attach_scroll)

        # Copy to clipboard + Save / Save As buttons
        clipboard_row = QHBoxLayout()
        clipboard_row.setSpacing(6)
        copy_btn = QPushButton("⧉")
        copy_btn.setObjectName("copyBtn")
        copy_btn.setFixedSize(28, 28)
        copy_btn.setFont(QFont("Segoe UI Symbol", 12))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_to_clipboard)
        clipboard_row.addWidget(copy_btn)

        attach_btn = QPushButton("📎")
        attach_btn.setObjectName("copyBtn")
        attach_btn.setFixedSize(28, 28)
        attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        attach_btn.setToolTip("Attach images or files (images are embedded inline)")
        attach_btn.clicked.connect(self._open_attach_dialog)
        clipboard_row.addWidget(attach_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("saveJotBtn")
        self.save_btn.setFixedHeight(28)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setEnabled(False)  # disabled until tied to a saved jot
        self.save_btn.clicked.connect(self._save_current_jot)
        clipboard_row.addWidget(self.save_btn)

        save_as_btn = QPushButton("Save As.")
        save_as_btn.setObjectName("saveJotBtn")
        save_as_btn.setFixedHeight(28)
        save_as_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_as_btn.clicked.connect(self._open_save_dialog)
        clipboard_row.addWidget(save_as_btn)

        clipboard_row.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("clearBtn")
        clear_btn.setFixedHeight(28)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_editor)
        clipboard_row.addWidget(clear_btn)

        main_layout.addLayout(clipboard_row)

        # Status
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        main_layout.addWidget(self.status)
        self.status.hide()  # status messages ("Loaded X", "Saved X", etc.) are no longer shown
        def _editor_mouse_press(e):
            self.status.setText("")
            QTextEdit.mousePressEvent(self.editor, e)
        self.editor.mousePressEvent = _editor_mouse_press

        # Divider — separates the editor/status area from the AI section,
        # mirroring the divider between the header and the editor.
        ai_divider = QFrame()
        ai_divider.setFrameShape(QFrame.Shape.HLine)
        ai_divider.setObjectName("divider")
        main_layout.addWidget(ai_divider)

        # AI section: a model-picker dropdown (replaces the old static
        # "Claude Sonnet 4.5" label) plus a small key button to set/change
        # the API key.
        ai_section_row = QHBoxLayout()
        ai_section_row.setContentsMargins(0, 0, 0, 0)
        ai_section_row.setSpacing(4)

        self.model_combo = QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self.model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for model_id, display_name in AVAILABLE_MODELS:
            self.model_combo.addItem(display_name, model_id)
        current_idx = self.model_combo.findData(self.ai_model)
        self.model_combo.setCurrentIndex(current_idx if current_idx >= 0 else 0)
        self.model_combo.setToolTip("Choose which Claude model powers the AI features")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        # Without these, QComboBox happily stretches to fill the whole row
        # (its default horizontal policy is Expanding), which left a wide
        # gap between the model name and the drop-down arrow. Hugging the
        # content instead lets the trailing addStretch() below take up the
        # leftover space, so the button reads as "model name + arrow" with
        # no dead air in between.
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        ai_section_row.addWidget(self.model_combo)
        ai_section_row.addStretch()

        self.api_key_btn = QPushButton()
        self.api_key_btn.setObjectName("apiKeyBtn")
        self.api_key_btn.setFixedSize(20, 20)
        self.api_key_btn.setIcon(_make_key_icon())
        self.api_key_btn.setIconSize(QSize(13, 13))
        self.api_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.api_key_btn.setToolTip("Set your Anthropic API key")
        self.api_key_btn.clicked.connect(self._open_api_key_dialog)
        ai_section_row.addWidget(self.api_key_btn)

        main_layout.addLayout(ai_section_row)

        # AI buttons — all on one row, using the brushed-metal MetalButton
        # widgets (fixed-size, self-painted) rather than stretch-filled flat
        # buttons, so they're centered with breathing room either side. When
        # the panel is narrowed past the point where all three fit, the bar
        # collapses the overflow into a single "…" button with a menu,
        # rather than letting the buttons get cropped or squashed.
        self.ai_button_bar = AIButtonBar(
            [("Polish", "polish"), ("Draft Email", "email"), ("Expand Idea", "expand")],
            self._run_ai
        )
        main_layout.addWidget(self.ai_button_bar)

        # Persistent API-key warning — stays visible (unlike the transient
        # prompt-bar tooltip messages) until the key is fixed or a request
        # succeeds, since an outdated/invalid key is easy to miss otherwise.
        self.api_key_warning = QLabel("")
        self.api_key_warning.setObjectName("apiKeyWarning")
        self.api_key_warning.setWordWrap(True)
        self.api_key_warning.hide()
        main_layout.addWidget(self.api_key_warning)

        # AI prompt bar — free-form instructions applied to the current jot
        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(6)
        self.ai_prompt_bar = QLineEdit()
        self.ai_prompt_bar.setObjectName("aiPromptBar")
        self._prompt_logical_placeholder = DEFAULT_AI_PROMPT_PLACEHOLDER
        self.ai_prompt_bar.setPlaceholderText(DEFAULT_AI_PROMPT_PLACEHOLDER)
        self.ai_prompt_bar.returnPressed.connect(self._run_ai_prompt)
        prompt_row.addWidget(self.ai_prompt_bar, stretch=1)

        # Blinking underscore cursor (matches the editor's) — the native
        # bar cursor is switched off via a per-widget style proxy, and a
        # small solid bar stands in for it (drawn rather than relying on
        # the font's own thin "_" glyph), repositioned on every cursor
        # move / keystroke and blinked via a timer while focused.
        self.PROMPT_CURSOR_THICKNESS = 3  # bar height in px — thicker than a normal underscore glyph
        self._prompt_cursor_style = ZeroWidthCursorStyle()
        self.ai_prompt_bar.setStyle(self._prompt_cursor_style)
        self._prompt_blink_cursor = QLabel("", self.ai_prompt_bar)
        self._prompt_blink_cursor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._prompt_blink_cursor.setStyleSheet("background: #111111; border: none; border-radius: 1px;")
        self._prompt_blink_cursor.hide()
        self._prompt_blink_visible = True
        self._prompt_blink_timer = QTimer(self)
        self._prompt_blink_timer.setInterval(530)
        self._prompt_blink_timer.timeout.connect(self._toggle_prompt_blink)
        self.ai_prompt_bar.cursorPositionChanged.connect(lambda old, new: self._reposition_prompt_blink())
        self.ai_prompt_bar.textChanged.connect(lambda _: self._reposition_prompt_blink())
        self.ai_prompt_bar.installEventFilter(self)

        self.ai_prompt_btn = QPushButton("→")
        self.ai_prompt_btn.setObjectName("aiPromptBtn")
        self.ai_prompt_btn.setFixedSize(28, 28)
        self.ai_prompt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_prompt_btn.clicked.connect(self._run_ai_prompt)
        prompt_row.addWidget(self.ai_prompt_btn)

        main_layout.addLayout(prompt_row)

        # AI buttons are styled directly (not just via the parent stylesheet)
        # so their light/dark colors are always guaranteed to be correct.
        self._style_ai_buttons()

        # Jots list view
        self.jots_view = JotsListView()
        self.jots_view.jot_selected.connect(self._load_jot)
        self.jots_view.jot_deleted.connect(self._delete_jot)
        self.jots_view.jot_renamed.connect(self._rename_jot)
        self.jots_view.jot_created.connect(self._create_new_jot)

        self.stack.addWidget(main_view)   # index 0
        self.stack.addWidget(self.jots_view)  # index 1

        central.setStyleSheet(_apply_btn_font("""
            #panel {
                background: transparent;
            }
            #titleJot {
                color: #111111;
                font-size: 22px;
                font-weight: 800;
                font-family: 'Georgia';
                letter-spacing: -0.5px;
                padding: 2px 4px;
                border-radius: 8px;
            }
            #titleJot:hover { background: #e8e8e5; }
            #closeBtn {
                background: transparent;
                color: #aaaaaa;
                border: none;
                border-radius: 14px;
                font-size: 18px;
                padding-bottom: 1px;
            }
            #closeBtn:hover { background: #e8e8e5; color: #333333; }
            #minimiseBtn {
                background: transparent;
                color: #aaaaaa;
                border: none;
                border-radius: 14px;
                font-size: 18px;
            }
            #minimiseBtn:hover { background: #e8e8e5; color: #333333; }
            #fullscreenBtn {
                background: transparent;
                border: none;
                border-radius: 14px;
            }
            #fullscreenBtn:hover { background: #e8e8e5; }
            #divider { color: #eeeeee; max-height: 1px; }
            QScrollArea#tabsScroll { background: transparent; border: none; }
            #tabsInner { background: transparent; }
            QScrollArea#attachScroll { background: transparent; border: none; }
            #attachInner { background: transparent; }
            #editor {
                background: #f0f0ee;
                color: #111111;
                border: 1px solid #dcdcd9;
                border-radius: 8px;
                padding: 10px;
                font-family: 'Georgia';
                font-size: 13px;
                selection-background-color: #d0d0d0;
            }
            #status {
                color: #999999;
                font-size: 11px;
                font-family: 'Segoe UI', sans-serif;
                min-height: 14px;
            }
            #apiKeyWarning {
                color: #b5451b;
                font-size: 11px;
                font-family: 'Segoe UI', sans-serif;
                font-weight: 600;
                padding: 4px 2px;
            }
            #sectionLabel {
                color: #bbbbbb;
                font-size: 9px;
                font-family: 'Segoe UI', sans-serif;
                font-weight: 700;
                letter-spacing: 1px;
            }
            #modelCombo {
                background: #f0f0ee;
                color: #333333;
                border: 1px solid #dcdcd9;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 10px;
                font-family: 'Segoe UI', sans-serif;
                font-weight: 600;
            }
            #modelCombo:hover { background: #e8e8e5; }
            #modelCombo::drop-down { border: none; width: 0px; }
            #modelCombo::down-arrow { image: none; width: 0px; height: 0px; }
            #modelCombo QAbstractItemView {
                background: #f7f7f5;
                color: #111111;
                border: 1px solid #dcdcd9;
                selection-background-color: #e0e0dd;
                selection-color: #111111;
                outline: none;
            }
            #apiKeyBtn {
                background: transparent;
                border: none;
                border-radius: 10px;
            }
            #apiKeyBtn:hover { background: #e8e8e5; }
            #aiBtn {
                background: #111111;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                padding: 8px 4px;
                font-size: 11px;
                font-family: '__BTNFONT__';
                font-weight: 600;
            }
            #aiBtn:hover    { background: #333333; }
            #aiBtn:disabled { background: #dddddd; color: #aaaaaa; }
            #aiPromptBar {
                background: #f0f0ee;
                color: #111111;
                border: 1px solid #dcdcd9;
                border-radius: 7px;
                padding: 6px 10px;
                font-family: 'Open Sans', sans-serif;
                font-size: 12px;
            }
            #aiPromptBar:focus { border: 1px solid #cccccc; }
            #aiPromptBtn {
                background: #111111;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                font-size: 13px;
            }
            #aiPromptBtn:hover { background: #333333; }
            #capsBtn {
                background: #e8e8e5;
                color: #888888;
                border: 1px solid #d9d9d6;
                border-radius: 6px;
                font-family: '__BTNFONT__';
            }
            #capsBtn:hover { background: #e4e4e4; color: #333333; }
            #fmtBtn {
                background: #e8e8e5;
                color: #888888;
                border: 1px solid #d9d9d6;
                border-radius: 6px;
            }
            #fmtBtn:hover   { background: #e4e4e4; color: #333333; }
            #fmtBtn:checked { background: #111111; color: #ffffff; border: 1px solid #111111; }
            #copyBtn {
                background: #e8e8e5;
                color: #888888;
                border: 1px solid #d9d9d6;
                border-radius: 6px;
            }
            #copyBtn:hover { background: #e4e4e4; color: #333333; }
            #saveJotBtn {
                background: #e8e8e5;
                color: #555555;
                border: 1px solid #d9d9d6;
                border-radius: 6px;
                padding: 0px 10px;
                font-size: 11px;
                font-family: '__BTNFONT__';
                font-weight: 600;
            }
            #saveJotBtn:hover { background: #e4e4e4; color: #222222; }
            #clearBtn {
                background: transparent;
                color: #aaaaaa;
                border: 1px solid #d9d9d6;
                border-radius: 7px;
                padding: 0px 14px;
                font-size: 12px;
                font-family: '__BTNFONT__';
            }
            #darkBtn {
                background: transparent;
                border: none;
                border-radius: 14px;
            }
            #darkBtn:hover { background: #e8e8e5; }
            #linkBtn {
                background: transparent;
                border: none;
                border-radius: 12px;
            }
            #linkBtn:hover { background: #e8e8e5; }
            #clearBtn:hover { background: #f5f5f5; color: #555555; }
        """))

        # Resize handle — a top-level child of the window (not the layout) so it
        # can be pinned exactly into the bottom-left rounded corner curve.
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.set_theme(self.dark_mode)
        self._position_resize_handle()

        # Re-apply full theming (central stylesheet, handle, jots list) if the
        # app was closed in dark mode last time, since the styling above is
        # hardcoded to light mode by default.
        if self.dark_mode:
            self._apply_theme()

    def _title_pressed(self):
        bg = "#3a3a3a" if self.dark_mode else "#dcdcd9"
        self.title_jot.setStyleSheet(
            f"#titleJot {{ background: {bg}; border-radius: 8px; padding: 2px 4px; }}"
        )

    def _title_released(self):
        self.title_jot.setStyleSheet("")  # revert to stylesheet default (incl. :hover)

    def _title_released_and_toggle(self):
        self._title_released()
        self._toggle_jots_view()

    def _set_title_text(self, text: str):
        """Sets the wordmark text ("Jot" / "Jots"); LogoLabel recomputes
        and repaints its underscore mark automatically from the new text."""
        self.title_jot.setText(text)

    def _title_hover_enter(self):
        # Editor view: hovering hints at "Jots" (where the click will take you)
        # Jots list view: hovering hints at "Jot" (where the click will take you)
        if self.stack.currentIndex() == 0:
            self._set_title_text("Jots")
        else:
            self._set_title_text("Jot")

    def _title_hover_leave(self):
        # Resting label reflects which view is currently showing
        if self.stack.currentIndex() == 0:
            self._set_title_text("Jot")
        else:
            self._set_title_text("Jots")

    def _open_guide_link(self):
        QDesktopServices.openUrl(QUrl(
            "https://flying-sun-2a4.notion.site/Jot-3762d113328580aeb79beb2bd2df215f"
        ))

    def _toggle_jots_view(self):
        if self.stack.currentIndex() == 0:
            self.jots_view.set_theme(self.dark_mode)
            self.stack.setCurrentIndex(1)
            self._set_title_text("Jots")
        else:
            self.stack.setCurrentIndex(0)
            self._set_title_text("Jot")

    def _open_save_dialog(self):
        if self._editor_is_empty():
            self.status.setText("Write something first.")
            return
        html = self._editor_html()
        attachments = list(self.current_attachments)
        dialog = SaveJotDialog(self, self.dark_mode)
        # Position the dialog centred over the panel
        dialog.move(
            self.x() + (self.width() - dialog.width()) // 2,
            self.y() + (self.height() - dialog.height()) // 2
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_name:
            name = dialog.result_name
            jots = load_jots()
            existing_index = next(
                (i for i, j in enumerate(jots) if j.get("name", "") == name), None
            )
            if existing_index is not None:
                confirm = ConfirmDialog(
                    self, self.dark_mode,
                    title="Overwrite Jot?",
                    message=f"A jot named \"{name}\" already exists. Overwrite its contents?",
                    confirm_label="Overwrite"
                )
                confirm.move(
                    self.x() + (self.width() - confirm.width()) // 2,
                    self.y() + (self.height() - confirm.height()) // 2
                )
                if confirm.exec() != QDialog.DialogCode.Accepted:
                    self.status.setText("Not saved.")
                    return
                jots[existing_index]["text"] = html
                jots[existing_index]["attachments"] = attachments
                save_jots(jots)
                self._open_tab_for_jot(existing_index, name, html, baseline=html, attachments=attachments)
                self.status.setText(f"Saved as \"{name}\".")
            else:
                add_jot(name, html, attachments)
                self._shift_tab_jot_indices(0, +1)  # add_jot() inserts at index 0
                self._open_tab_for_jot(0, name, html, baseline=html, attachments=attachments)
                self.status.setText(f"Saved as \"{name}\".")
            self.jots_view.refresh()

    def _save_current_jot(self):
        if self.current_jot_index is None:
            return  # Save is disabled in this state, but guard anyway
        if self._editor_is_empty():
            self.status.setText("Write something first.")
            return
        html = self._editor_html()
        attachments = list(self.current_attachments)
        jots = load_jots()
        if 0 <= self.current_jot_index < len(jots):
            jots[self.current_jot_index]["text"] = html
            jots[self.current_jot_index]["attachments"] = attachments
            save_jots(jots)
            name = jots[self.current_jot_index].get('name', 'Untitled Jot')
            if self.active_tab is not None:
                tab = self.open_tabs[self.active_tab]
                tab["text"] = html
                tab["baseline"] = html
                tab["attachments"] = attachments
                tab["baseline_attachments"] = list(attachments)
                tab["name"] = name
                self._refresh_tab_widget(self.active_tab)
            self.status.setText(f"Saved \"{name}\".")
        else:
            # The linked jot no longer exists (e.g. deleted elsewhere)
            self.current_jot_index = None
            self.save_btn.setEnabled(False)
            self.status.setText("That jot no longer exists.")

    def _clear_editor(self):
        # Clearing only wipes the text in the box — the tab (if any) stays
        # open and still linked to its jot, so Save can write the blank
        # content back, or the user can just keep typing.
        plain = self._editor_plain()
        if len(plain) > 30 or self._has_image() or self.current_attachments:
            confirm = ConfirmDialog(
                self, self.dark_mode,
                title="Clear Jot?",
                message="This will clear the text, images, and attachments in this jot. Continue?",
                confirm_label="Clear"
            )
            confirm.move(
                self.x() + (self.width() - confirm.width()) // 2,
                self.y() + (self.height() - confirm.height()) // 2
            )
            if confirm.exec() != QDialog.DialogCode.Accepted:
                return
        self.editor.clear()
        self.current_attachments = []
        self._refresh_attachments_ui()
        if self.active_tab is not None:
            self._refresh_tab_dirty(self.active_tab)
        self.status.setText("")

    def _on_editor_text_changed(self):
        # Keep the active tab's in-memory draft (and its dirty dot) in sync
        # as the user types — Save persists it, closing the tab without
        # saving discards it.
        if self.active_tab is not None and 0 <= self.active_tab < len(self.open_tabs):
            self.open_tabs[self.active_tab]["text"] = self._editor_html()
            self._refresh_tab_dirty(self.active_tab)

    # ── Rich content helpers ─────────────────────────────────────────────
    def _editor_html(self) -> str:
        return self.editor.toHtml()

    def _editor_plain(self) -> str:
        return self.editor.toPlainText()

    def _has_image(self) -> bool:
        return "<img" in self.editor.toHtml()

    def _editor_is_empty(self) -> bool:
        return self.editor.document().isEmpty() and not self.current_attachments

    def _set_editor_content(self, content: str):
        """Loads either legacy plain-text jots (no HTML wrapper — setHtml
        would collapse their newlines) or rich HTML jots produced by this
        editor (which always start with a DOCTYPE from toHtml())."""
        if content.strip()[:9].upper() == "<!DOCTYPE":
            self.editor.setHtml(content)
        else:
            self.editor.setPlainText(content)

    # ── Attachments ───────────────────────────────────────────────────────
    def _open_attach_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        if not paths:
            return
        added = 0
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                qimage = QImage(path)
                if not qimage.isNull():
                    self.editor.insert_image(qimage)
                    continue
            self.current_attachments.append({"name": os.path.basename(path), "path": path})
            added += 1
        self._refresh_attachments_ui()
        if self.active_tab is not None:
            self._refresh_tab_dirty(self.active_tab)
        if added:
            self.status.setText(f"Attached {added} file(s).")

    def _on_file_dropped(self, path: str):
        self.current_attachments.append({"name": os.path.basename(path), "path": path})
        self._refresh_attachments_ui()
        if self.active_tab is not None:
            self._refresh_tab_dirty(self.active_tab)
        self.status.setText(f"Attached \"{os.path.basename(path)}\".")

    def _remove_attachment(self, idx: int):
        if 0 <= idx < len(self.current_attachments):
            self.current_attachments.pop(idx)
            self._refresh_attachments_ui()
            if self.active_tab is not None:
                self._refresh_tab_dirty(self.active_tab)

    def _refresh_attachments_ui(self):
        while self.attach_row.count() > 1:  # keep trailing stretch
            item = self.attach_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for idx, att in enumerate(self.current_attachments):
            chip = self._make_attachment_chip(idx, att)
            self.attach_row.insertWidget(self.attach_row.count() - 1, chip)
        self.attach_scroll.setVisible(len(self.current_attachments) > 0)

    def _make_attachment_chip(self, idx: int, att: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("attachChip")
        row = QHBoxLayout(frame)
        row.setContentsMargins(8, 3, 4, 3)
        row.setSpacing(4)

        label = QLabel(att.get("name", "file"))
        label.setObjectName("attachLabel")
        label.setToolTip(att.get("path", ""))
        row.addWidget(label)

        remove_btn = QPushButton("×")
        remove_btn.setObjectName("attachRemoveBtn")
        remove_btn.setFixedSize(16, 16)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda _, i=idx: self._remove_attachment(i))
        row.addWidget(remove_btn)

        self._style_attachment_chip(frame, label, remove_btn)
        return frame

    def _style_attachment_chip(self, frame, label, remove_btn):
        if self.dark_mode:
            bg, border, fg = "#2a2a2a", "#3a3a3a", "#cccccc"
            rm_fg, rm_hover = "#888888", "#3a3a3a"
        else:
            bg, border, fg = "#f0f0ee", "#dcdcd9", "#333333"
            rm_fg, rm_hover = "#aaaaaa", "#dcdcd9"
        frame.setStyleSheet(f"""
            QFrame#attachChip {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
        """)
        label.setStyleSheet(f"""
            QLabel#attachLabel {{
                color: {fg};
                font-size: 10px;
                font-family: 'Segoe UI', sans-serif;
                background: transparent;
                border: none;
            }}
        """)
        remove_btn.setStyleSheet(f"""
            QPushButton#attachRemoveBtn {{
                background: transparent;
                color: {rm_fg};
                border: none;
                border-radius: 8px;
                font-size: 10px;
            }}
            QPushButton#attachRemoveBtn:hover {{ background: {rm_hover}; }}
        """)

    # ── Jot tabs ─────────────────────────────────────────────────────────
    def _find_tab_for_jot(self, jot_index: int):
        for i, tab in enumerate(self.open_tabs):
            if tab["jot_index"] == jot_index:
                return i
        return None

    def _open_tab_for_jot(self, jot_index: int, name: str, text: str, baseline: str = None,
                          attachments: list = None):
        """Opens a new tab for a saved jot, or activates it if already open
        — switching to an already-open tab never discards its draft."""
        attachments = list(attachments) if attachments is not None else []
        existing = self._find_tab_for_jot(jot_index)
        if existing is not None:
            self.open_tabs[existing]["name"] = name
            self._activate_tab(existing)
            return
        self.open_tabs.append({
            "jot_index": jot_index,
            "name": name,
            "text": text,
            "baseline": baseline if baseline is not None else text,
            "attachments": attachments,
            "baseline_attachments": list(attachments),
        })
        self._rebuild_tabs_ui()
        self._activate_tab(len(self.open_tabs) - 1)

    def _activate_tab(self, i: int):
        if not (0 <= i < len(self.open_tabs)):
            return
        if self.active_tab is not None and 0 <= self.active_tab < len(self.open_tabs):
            self.open_tabs[self.active_tab]["text"] = self._editor_html()
            self.open_tabs[self.active_tab]["attachments"] = list(self.current_attachments)
        self.active_tab = i
        tab = self.open_tabs[i]
        self.editor.blockSignals(True)
        self._set_editor_content(tab["text"])
        self.editor.blockSignals(False)
        self.current_jot_index = tab["jot_index"]
        self.current_attachments = list(tab.get("attachments", []))
        self._refresh_attachments_ui()
        self.save_btn.setEnabled(True)
        self._rebuild_tabs_ui()

    def _shift_tab_jot_indices(self, from_index: int, delta: int):
        """Keeps open tabs pointing at the right jots.json row after the
        list shifts (e.g. a new jot inserted at the front, or one deleted)."""
        for tab in self.open_tabs:
            if tab["jot_index"] is not None and tab["jot_index"] >= from_index:
                tab["jot_index"] += delta

    def _remove_tab(self, i: int):
        if not (0 <= i < len(self.open_tabs)):
            return
        was_active = (i == self.active_tab)
        self.open_tabs.pop(i)
        if self.active_tab is not None:
            if was_active:
                self.active_tab = None
            elif i < self.active_tab:
                self.active_tab -= 1
        if was_active:
            if self.open_tabs:
                self._activate_tab(min(i, len(self.open_tabs) - 1))
            else:
                self.editor.blockSignals(True)
                self.editor.clear()
                self.editor.blockSignals(False)
                self.current_jot_index = None
                self.current_attachments = []
                self._refresh_attachments_ui()
                self.save_btn.setEnabled(False)
                self._rebuild_tabs_ui()
        else:
            self._rebuild_tabs_ui()

    def _request_close_tab(self, i: int):
        if not (0 <= i < len(self.open_tabs)):
            return
        tab = self.open_tabs[i]
        if i == self.active_tab:
            tab["text"] = self._editor_html()
            tab["attachments"] = list(self.current_attachments)
        dirty = tab["text"] != tab["baseline"] or tab.get("attachments", []) != tab.get("baseline_attachments", [])
        if dirty:
            dialog = SaveChangesDialog(self, self.dark_mode, tab["name"])
            dialog.move(
                self.x() + (self.width() - dialog.width()) // 2,
                self.y() + (self.height() - dialog.height()) // 2
            )
            dialog.exec()
            if dialog.choice in (None, "cancel"):
                return
            if dialog.choice == "save":
                jots = load_jots()
                if tab["jot_index"] is not None and 0 <= tab["jot_index"] < len(jots):
                    jots[tab["jot_index"]]["text"] = tab["text"]
                    jots[tab["jot_index"]]["attachments"] = tab.get("attachments", [])
                    save_jots(jots)
        self._remove_tab(i)
        self.status.setText("")

    def _handle_close_request(self):
        """Checks for unsaved work — across every open tab plus any
        untethered draft sitting in the editor with no tab/jot behind it —
        before actually quitting, and offers Save / Don't Save / Cancel."""
        # Sync the currently-active tab's in-memory draft first so the
        # dirty check below reflects what's actually in the editor.
        if self.active_tab is not None and 0 <= self.active_tab < len(self.open_tabs):
            self.open_tabs[self.active_tab]["text"] = self._editor_html()
            self.open_tabs[self.active_tab]["attachments"] = list(self.current_attachments)

        dirty_tabs = [
            i for i, t in enumerate(self.open_tabs)
            if t["text"] != t["baseline"] or t.get("attachments", []) != t.get("baseline_attachments", [])
        ]
        untethered_dirty = self.current_jot_index is None and not self._editor_is_empty()

        if not dirty_tabs and not untethered_dirty:
            QApplication.quit()
            return

        if len(dirty_tabs) == 1 and not untethered_dirty:
            message = f"Save changes to \"{self.open_tabs[dirty_tabs[0]]['name']}\" before exiting?"
        else:
            message = "You have unsaved changes. Save them before exiting?"

        dialog = SaveChangesDialog(self, self.dark_mode, "", message=message)
        dialog.move(
            self.x() + (self.width() - dialog.width()) // 2,
            self.y() + (self.height() - dialog.height()) // 2
        )
        dialog.exec()
        if dialog.choice in (None, "cancel"):
            return

        if dialog.choice == "save":
            if dirty_tabs:
                jots = load_jots()
                changed = False
                for i in dirty_tabs:
                    tab = self.open_tabs[i]
                    if tab["jot_index"] is not None and 0 <= tab["jot_index"] < len(jots):
                        jots[tab["jot_index"]]["text"] = tab["text"]
                        jots[tab["jot_index"]]["attachments"] = tab.get("attachments", [])
                        changed = True
                if changed:
                    save_jots(jots)
            if untethered_dirty:
                # No jot to write back to yet — route through the normal
                # Save As flow so the user can name it. If they cancel that
                # dialog we still proceed with exiting (they chose "Save",
                # discarding the name prompt is treated as skipping it).
                self._open_save_dialog()

        QApplication.quit()

    def _rebuild_tabs_ui(self):
        while self.tabs_row.count() > 1:  # keep trailing stretch
            item = self.tabs_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._tab_widgets = []
        for i, tab in enumerate(self.open_tabs):
            frame = self._make_tab_widget(i, tab)
            self.tabs_row.insertWidget(self.tabs_row.count() - 1, frame)
        self.tabs_scroll.setVisible(len(self.open_tabs) > 0)

    def _make_tab_widget(self, i: int, tab: dict) -> QFrame:
        frame = QFrame()
        frame.setObjectName("tabFrame")
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(4)

        dot = QLabel("•")
        dot.setObjectName("tabDirtyDot")
        dot.setVisible(tab["text"] != tab["baseline"])
        row.addWidget(dot)

        label = QLabel(tab["name"])
        label.setObjectName("tabLabel")
        row.addWidget(label)

        close_btn = QPushButton("×")
        close_btn.setObjectName("tabCloseBtn")
        close_btn.setFixedSize(16, 16)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda _, idx=i: self._request_close_tab(idx))
        row.addWidget(close_btn)

        frame.mousePressEvent = lambda e, idx=i: self._tab_mouse_press(e, idx)

        self._style_tab_widget(frame, label, dot, close_btn, i == self.active_tab)
        self._tab_widgets.append({"frame": frame, "label": label, "dot": dot, "close_btn": close_btn})
        return frame

    # ── Tab drag-to-reorder ─────────────────────────────────────────────
    # Press grabs the mouse on the *window* rather than the tab widget
    # itself, so swapping tabs mid-drag (which recreates the tab frames)
    # doesn't break the drag — the window keeps receiving move/release
    # events regardless of which frame currently exists under the cursor.
    def _tab_mouse_press(self, e, i: int):
        if e.button() == Qt.MouseButton.LeftButton:
            self._tab_drag_index = i
            self._tab_drag_start_x = e.globalPosition().x()
            self._tab_drag_moved = False
            self.grabMouse()

    def mouseMoveEvent(self, e):
        idx = getattr(self, "_tab_drag_index", None)
        if idx is None:
            super().mouseMoveEvent(e)
            return
        if abs(e.globalPosition().x() - self._tab_drag_start_x) < 6:
            return
        self._tab_drag_moved = True
        global_x = e.globalPosition().x()
        # Check whether the pointer has crossed past a neighboring tab's
        # centre — if so, swap the dragged tab with that neighbor.
        for j, tw in enumerate(self._tab_widgets):
            if j == idx:
                continue
            center = tw["frame"].mapToGlobal(
                QPoint(tw["frame"].width() // 2, tw["frame"].height() // 2)
            ).x()
            if (j < idx and global_x < center) or (j > idx and global_x > center):
                self.open_tabs[idx], self.open_tabs[j] = self.open_tabs[j], self.open_tabs[idx]
                if self.active_tab == idx:
                    self.active_tab = j
                elif self.active_tab == j:
                    self.active_tab = idx
                self._tab_drag_index = j
                self._rebuild_tabs_ui()
                break

    def mouseReleaseEvent(self, e):
        idx = getattr(self, "_tab_drag_index", None)
        if idx is None:
            super().mouseReleaseEvent(e)
            return
        self.releaseMouse()
        if not getattr(self, "_tab_drag_moved", False):
            self._activate_tab(idx)
        self._tab_drag_index = None
        self._tab_drag_moved = False

    def _style_tab_widget(self, frame, label, dot, close_btn, active: bool):
        if self.dark_mode:
            bg, border   = ("#2a2a2a" if active else "#1e1e1e"), "#3a3a3a"
            fg           = "#e8e8e8" if active else "#888888"
            close_fg, close_hover = "#888888", "#3a3a3a"
        else:
            bg, border   = ("#f0f0ee" if active else "#e8e8e5"), "#dcdcd9"
            fg           = "#111111" if active else "#999999"
            close_fg, close_hover = "#aaaaaa", "#dcdcd9"
        frame.setStyleSheet(f"""
            QFrame#tabFrame {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 7px;
            }}
        """)
        label.setStyleSheet(f"""
            QLabel#tabLabel {{
                color: {fg};
                font-size: 11px;
                font-family: '{BUTTON_FONT_FAMILY}';
                font-weight: {700 if active else 500};
                background: transparent;
                border: none;
            }}
        """)
        dot.setStyleSheet("color: #e0a030; font-size: 13px; background: transparent; border: none;")
        close_btn.setStyleSheet(f"""
            QPushButton#tabCloseBtn {{
                background: transparent;
                color: {close_fg};
                border: none;
                border-radius: 8px;
                font-size: 11px;
            }}
            QPushButton#tabCloseBtn:hover {{ background: {close_hover}; }}
        """)

    def _refresh_tab_dirty(self, i: int):
        if not (0 <= i < len(self._tab_widgets)):
            return
        tab = self.open_tabs[i]
        dirty = tab["text"] != tab["baseline"] or tab.get("attachments", []) != tab.get("baseline_attachments", [])
        self._tab_widgets[i]["dot"].setVisible(dirty)

    def _refresh_tab_widget(self, i: int):
        if not (0 <= i < len(self._tab_widgets)):
            return
        tab = self.open_tabs[i]
        self._tab_widgets[i]["label"].setText(tab["name"])
        self._refresh_tab_dirty(i)

    def _load_jot(self, index: int):
        jots = load_jots()
        if 0 <= index < len(jots):
            name = jots[index].get('name', 'Untitled Jot')
            text = jots[index].get("text", "")
            attachments = jots[index].get("attachments", [])
            self._open_tab_for_jot(index, name, text, baseline=text, attachments=attachments)
            self.status.setText(f"Loaded \"{name}\".")
        self.stack.setCurrentIndex(0)
        self._set_title_text("Jot")

    def _create_new_jot(self, name: str):
        add_jot(name, "", [])
        self._shift_tab_jot_indices(0, +1)  # add_jot() inserts at index 0
        self._open_tab_for_jot(0, name, "", baseline="", attachments=[])
        self.jots_view.refresh()
        self.stack.setCurrentIndex(0)
        self._set_title_text("Jot")
        self.status.setText(f"Created \"{name}\".")
        self.editor.setFocus()

    def _delete_jot(self, index: int):
        delete_jot(index)
        tab_i = self._find_tab_for_jot(index)
        if tab_i is not None:
            self._remove_tab(tab_i)
        self._shift_tab_jot_indices(index + 1, -1)
        self.jots_view.refresh()

    def _rename_jot(self, index: int, new_name: str):
        jots = load_jots()
        if not (0 <= index < len(jots)):
            return
        jots[index]["name"] = new_name
        save_jots(jots)
        tab_i = self._find_tab_for_jot(index)
        if tab_i is not None:
            self.open_tabs[tab_i]["name"] = new_name
            self._refresh_tab_widget(tab_i)
            if tab_i == self.active_tab:
                self.status.setText(f"Renamed to \"{new_name}\".")
        self.jots_view.refresh()

    def _on_model_changed(self, index: int):
        if index < 0 or not hasattr(self, "model_combo"):
            return
        model_id = self.model_combo.itemData(index)
        if not model_id or model_id == self.ai_model:
            return
        self.ai_model = model_id
        self._settings["ai_model"] = model_id
        save_settings(self._settings)
        self._hide_api_key_warning()  # switching models is a fresh start for the warning

    def _on_theme_toggle_changed(self, dark: bool):
        """Called by self.dark_btn (a ThemeToggle) right after a click flips
        its state — the sun/moon spin animation runs independently on its
        own timer, while the actual theme restyle below applies instantly."""
        self.dark_mode = dark
        self._apply_theme()
        self._settings["dark_mode"] = self.dark_mode
        save_settings(self._settings)

    def _apply_theme(self):
        central = self.centralWidget()
        central.set_theme(self.dark_mode)
        if hasattr(self, "resize_handle"):
            self.resize_handle.set_theme(self.dark_mode)
        if hasattr(self, "jots_view"):
            self.jots_view.set_theme(self.dark_mode)
        self._style_ai_buttons()
        cursor_color = "#e8e8e8" if self.dark_mode else "#111111"
        if hasattr(self, "editor"):
            self.editor.set_cursor_color(cursor_color)
        if hasattr(self, "_prompt_blink_cursor"):
            self._set_prompt_cursor_color(cursor_color)
        if hasattr(self, "title_jot"):
            self.title_jot.set_underscore_color(cursor_color)
        if hasattr(self, "open_tabs"):
            self._rebuild_tabs_ui()
        if hasattr(self, "current_attachments"):
            self._refresh_attachments_ui()
        if self.dark_mode:
            central.setStyleSheet(_apply_btn_font("""
                #panel {
                    background: transparent;
                }
                #titleJot {
                    color: #f0f0f0;
                    font-size: 22px;
                    font-weight: 800;
                    font-family: 'Georgia';
                    letter-spacing: -0.5px;
                    padding: 2px 4px;
                    border-radius: 8px;
                }
                #titleJot:hover { background: #2e2e2e; }
                #closeBtn {
                    background: transparent;
                    color: #666666;
                    border: none;
                    border-radius: 14px;
                    font-size: 18px;
                    padding-bottom: 1px;
                }
                #closeBtn:hover { background: #2e2e2e; color: #cccccc; }
                #minimiseBtn {
                    background: transparent;
                    color: #666666;
                    border: none;
                    border-radius: 14px;
                    font-size: 18px;
                }
                #minimiseBtn:hover { background: #2e2e2e; color: #cccccc; }
                #fullscreenBtn {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                #fullscreenBtn:hover { background: #2e2e2e; }
                #divider { color: #333333; max-height: 1px; }
                QScrollArea#tabsScroll { background: transparent; border: none; }
                #tabsInner { background: transparent; }
                QScrollArea#attachScroll { background: transparent; border: none; }
                #attachInner { background: transparent; }
                #editor {
                    background: #2a2a2a;
                    color: #e8e8e8;
                    border: 1px solid #3a3a3a;
                    border-radius: 8px;
                    padding: 10px;
                    font-family: 'Georgia';
                    font-size: 13px;
                    selection-background-color: #4a4a4a;
                }
                #status {
                    color: #666666;
                    font-size: 11px;
                    font-family: 'Segoe UI', sans-serif;
                    min-height: 14px;
                }
                #apiKeyWarning {
                    color: #e08a5c;
                    font-size: 11px;
                    font-family: 'Segoe UI', sans-serif;
                    font-weight: 600;
                    padding: 4px 2px;
                }
                #sectionLabel {
                    color: #555555;
                    font-size: 9px;
                    font-family: 'Segoe UI', sans-serif;
                    font-weight: 700;
                    letter-spacing: 1px;
                }
                #modelCombo {
                    background: #2a2a2a;
                    color: #cccccc;
                    border: 1px solid #3a3a3a;
                    border-radius: 6px;
                    padding: 3px 8px;
                    font-size: 10px;
                    font-family: 'Segoe UI', sans-serif;
                    font-weight: 600;
                }
                #modelCombo:hover { background: #333333; }
                #modelCombo::drop-down { border: none; width: 0px; }
                #modelCombo::down-arrow { image: none; width: 0px; height: 0px; }
                #modelCombo QAbstractItemView {
                    background: #1e1e1e;
                    color: #e8e8e8;
                    border: 1px solid #3a3a3a;
                    selection-background-color: #3a3a3a;
                    selection-color: #ffffff;
                    outline: none;
                }
                #apiKeyBtn {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                }
                #apiKeyBtn:hover { background: #2e2e2e; }
                #aiBtn {
                    background: #2e2e2e;
                    color: #e8e8e8;
                    border: none;
                    border-radius: 7px;
                    padding: 8px 4px;
                    font-size: 11px;
                    font-family: '__BTNFONT__';
                    font-weight: 600;
                }
                #aiBtn:hover    { background: #3a3a3a; color: #ffffff; }
                #aiBtn:disabled { background: #232323; color: #555555; }
                #aiPromptBar {
                    background: #2a2a2a;
                    color: #e8e8e8;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 6px 10px;
                    font-family: 'Open Sans', sans-serif;
                    font-size: 12px;
                }
                #aiPromptBar:focus { border: 1px solid #555555; }
                #aiPromptBtn {
                    background: #e8e8e8;
                    color: #111111;
                    border: none;
                    border-radius: 7px;
                    font-size: 13px;
                }
                #aiPromptBtn:hover { background: #ffffff; }
                #capsBtn {
                    background: #2e2e2e;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 6px;
                    font-family: '__BTNFONT__';
                }
                #capsBtn:hover { background: #3a3a3a; color: #cccccc; }
                #fmtBtn {
                    background: #2e2e2e;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 6px;
                }
                #fmtBtn:hover   { background: #3a3a3a; color: #cccccc; }
                #fmtBtn:checked { background: #e8e8e8; color: #111111; border: 1px solid #e8e8e8; }
                #darkBtn {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                #darkBtn:hover { background: #2e2e2e; }
                #linkBtn {
                    background: transparent;
                    border: none;
                    border-radius: 12px;
                }
                #linkBtn:hover { background: #2e2e2e; }
                #copyBtn {
                    background: #2e2e2e;
                    color: #888888;
                    border: 1px solid #3a3a3a;
                    border-radius: 6px;
                }
                #copyBtn:hover { background: #3a3a3a; color: #cccccc; }
                #saveJotBtn {
                    background: #2e2e2e;
                    color: #aaaaaa;
                    border: 1px solid #3a3a3a;
                    border-radius: 6px;
                    padding: 0px 10px;
                    font-size: 11px;
                    font-family: '__BTNFONT__';
                    font-weight: 600;
                }
                #saveJotBtn:hover { background: #3a3a3a; color: #e0e0e0; }
                #clearBtn {
                    background: transparent;
                    color: #666666;
                    border: 1px solid #3a3a3a;
                    border-radius: 7px;
                    padding: 0px 14px;
                    font-size: 12px;
                    font-family: '__BTNFONT__';
                }
                #clearBtn:hover { background: #2e2e2e; color: #aaaaaa; }
            """))
        else:
            central.setStyleSheet(_apply_btn_font("""
                #panel {
                    background: transparent;
                }
                #titleJot {
                    color: #111111;
                    font-size: 22px;
                    font-weight: 800;
                    font-family: 'Georgia';
                    letter-spacing: -0.5px;
                    padding: 2px 4px;
                    border-radius: 8px;
                }
                #titleJot:hover { background: #e8e8e5; }
                #closeBtn {
                    background: transparent;
                    color: #aaaaaa;
                    border: none;
                    border-radius: 14px;
                    font-size: 18px;
                    padding-bottom: 1px;
                }
                #closeBtn:hover { background: #e8e8e5; color: #333333; }
                #minimiseBtn {
                    background: transparent;
                    color: #aaaaaa;
                    border: none;
                    border-radius: 14px;
                    font-size: 18px;
                }
                #minimiseBtn:hover { background: #e8e8e5; color: #333333; }
                #fullscreenBtn {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                #fullscreenBtn:hover { background: #e8e8e5; }
                #divider { color: #eeeeee; max-height: 1px; }
                QScrollArea#tabsScroll { background: transparent; border: none; }
                #tabsInner { background: transparent; }
                QScrollArea#attachScroll { background: transparent; border: none; }
                #attachInner { background: transparent; }
                #editor {
                    background: #f0f0ee;
                    color: #111111;
                    border: 1px solid #dcdcd9;
                    border-radius: 8px;
                    padding: 10px;
                    font-family: 'Georgia';
                    font-size: 13px;
                    selection-background-color: #d0d0d0;
                }
                #status {
                    color: #999999;
                    font-size: 11px;
                    font-family: 'Segoe UI', sans-serif;
                    min-height: 14px;
                }
                #apiKeyWarning {
                    color: #b5451b;
                    font-size: 11px;
                    font-family: 'Segoe UI', sans-serif;
                    font-weight: 600;
                    padding: 4px 2px;
                }
                #sectionLabel {
                    color: #bbbbbb;
                    font-size: 9px;
                    font-family: 'Segoe UI', sans-serif;
                    font-weight: 700;
                    letter-spacing: 1px;
                }
                #modelCombo {
                    background: #f0f0ee;
                    color: #333333;
                    border: 1px solid #dcdcd9;
                    border-radius: 6px;
                    padding: 3px 8px;
                    font-size: 10px;
                    font-family: 'Segoe UI', sans-serif;
                    font-weight: 600;
                }
                #modelCombo:hover { background: #e8e8e5; }
                #modelCombo::drop-down { border: none; width: 0px; }
                #modelCombo::down-arrow { image: none; width: 0px; height: 0px; }
                #modelCombo QAbstractItemView {
                    background: #f7f7f5;
                    color: #111111;
                    border: 1px solid #dcdcd9;
                    selection-background-color: #e0e0dd;
                    selection-color: #111111;
                    outline: none;
                }
                #apiKeyBtn {
                    background: transparent;
                    border: none;
                    border-radius: 10px;
                }
                #apiKeyBtn:hover { background: #e8e8e5; }
                #aiBtn {
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 8px 4px;
                    font-size: 11px;
                    font-family: '__BTNFONT__';
                    font-weight: 600;
                }
                #aiBtn:hover    { background: #333333; }
                #aiBtn:disabled { background: #dddddd; color: #aaaaaa; }
                #aiPromptBar {
                    background: #f0f0ee;
                    color: #111111;
                    border: 1px solid #dcdcd9;
                    border-radius: 7px;
                    padding: 6px 10px;
                    font-family: 'Open Sans', sans-serif;
                    font-size: 12px;
                }
                #aiPromptBar:focus { border: 1px solid #cccccc; }
                #aiPromptBtn {
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    font-size: 13px;
                }
                #aiPromptBtn:hover { background: #333333; }
                #capsBtn {
                    background: #e8e8e5;
                    color: #888888;
                    border: 1px solid #d9d9d6;
                    border-radius: 6px;
                    font-family: '__BTNFONT__';
                }
                #capsBtn:hover { background: #e4e4e4; color: #333333; }
                #fmtBtn {
                    background: #e8e8e5;
                    color: #888888;
                    border: 1px solid #d9d9d6;
                    border-radius: 6px;
                }
                #fmtBtn:hover   { background: #e4e4e4; color: #333333; }
                #fmtBtn:checked { background: #111111; color: #ffffff; border: 1px solid #111111; }
                #darkBtn {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                #darkBtn:hover { background: #e8e8e5; }
                #linkBtn {
                    background: transparent;
                    border: none;
                    border-radius: 12px;
                }
                #linkBtn:hover { background: #e8e8e5; }
                #copyBtn {
                    background: #e8e8e5;
                    color: #888888;
                    border: 1px solid #d9d9d6;
                    border-radius: 6px;
                }
                #copyBtn:hover { background: #e4e4e4; color: #333333; }
                #saveJotBtn {
                    background: #e8e8e5;
                    color: #555555;
                    border: 1px solid #d9d9d6;
                    border-radius: 6px;
                    padding: 0px 10px;
                    font-size: 11px;
                    font-family: '__BTNFONT__';
                    font-weight: 600;
                }
                #saveJotBtn:hover { background: #e4e4e4; color: #222222; }
                #clearBtn {
                    background: transparent;
                    color: #aaaaaa;
                    border: 1px solid #d9d9d6;
                    border-radius: 7px;
                    padding: 0px 14px;
                    font-size: 12px;
                    font-family: '__BTNFONT__';
                }
                #clearBtn:hover { background: #f5f5f5; color: #555555; }
            """))

        # Some Qt styles cache a widget's appearance and don't automatically
        # repaint every child when a parent's stylesheet is swapped — force a
        # repolish so buttons (like the AI action buttons) always pick up the
        # new theme colors immediately.
        central.style().unpolish(central)
        central.style().polish(central)
        for child in central.findChildren(QWidget):
            child.style().unpolish(child)
            child.style().polish(child)
            child.update()

    def _sync_fmt_buttons(self, fmt):
        self.bold_btn.setChecked(fmt.fontWeight() == QFont.Weight.Bold)
        self.italic_btn.setChecked(fmt.fontItalic())

    def _toggle_bold(self):
        fmt = self.editor.currentCharFormat()
        if fmt.fontWeight() == QFont.Weight.Bold:
            self.editor.setFontWeight(QFont.Weight.Normal)
            self.bold_btn.setChecked(False)
        else:
            self.editor.setFontWeight(QFont.Weight.Bold)
            self.bold_btn.setChecked(True)
        self.editor.setFocus()

    def _toggle_italic(self):
        is_italic = self.editor.fontItalic()
        self.editor.setFontItalic(not is_italic)
        self.italic_btn.setChecked(not is_italic)
        self.editor.setFocus()

    def _caps_pressed(self):
        self.caps_btn.setStyleSheet("background: #111111; color: #ffffff; border: 1px solid #111111; border-radius: 6px;")
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            selected = cursor.selectedText()
            # Toggle: if all uppercase, convert to lower; otherwise convert to upper
            if selected == selected.upper():
                cursor.insertText(selected.lower())
            else:
                cursor.insertText(selected.upper())
        self.editor.setFocus()

    def _caps_released(self):
        self.caps_btn.setStyleSheet("")  # revert to stylesheet default

    def _copy_to_clipboard(self):
        if self._editor_is_empty():
            self.status.setText("Jot something first...")
            return
        mime = ClipboardMimeData()
        mime.setHtml(self._editor_html())
        mime.setText(self._editor_plain())
        urls = [QUrl.fromLocalFile(a["path"]) for a in self.current_attachments if os.path.exists(a.get("path", ""))]
        if urls:
            mime.setUrls(urls)
        QApplication.clipboard().setMimeData(mime)
        self.status.setText("Copied!" + (" (with attachments)" if urls else ""))

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _style_ai_buttons(self):
        """MetalButton fully paints itself (extrusion, chrome rim, brushed
        face, engraved text) and looks the same regardless of light/dark
        mode, matching the reference design. Nothing to restyle here — this
        just triggers a repaint after a theme switch since Qt sometimes
        doesn't repaint custom-painted children automatically."""
        for btn in self.findChildren(MetalButton):
            btn.update()

    def _enforce_ai_button_min_width(self):
        """The frameless resize handle enforces its own MIN_W constant
        (rather than relying on Qt's automatic layout minimum sizing —
        see ResizeHandle.mouseMoveEvent), so raise it here to whatever the
        AI buttons actually need to display fully side by side, computed
        from each button's real on-screen size rather than a hardcoded
        guess, since font metrics vary by platform. This is a hard floor:
        the window simply can't be dragged narrower than this, so the
        AIButtonBar's own overflow/"…" logic is a backstop that should
        never actually trigger in normal use.

        Only the resize handle's own MIN_W is touched here — dragging that
        handle is the app's only resize path (there's no native title bar
        or OS resize grip on this frameless window), so that's sufficient
        on its own. Calling QMainWindow.setMinimumSize()/minimumHeight()
        from inside __init__ reliably crashed the app on exit in headless
        testing, so that redundant OS-level clamp was dropped rather than
        risk shipping it.
        """
        bar = self.ai_button_bar
        spacing = bar._layout.spacing()
        widths = [b.sizeHint().width() for b in bar._buttons]
        bar_min = sum(widths) + spacing * max(0, len(widths) - 1)
        chrome = 24 + 24  # main_layout's left + right content margins
        buffer = 12       # slack for borders/rounding/DPI differences
        required = bar_min + chrome + buffer
        required = max(required, ResizeHandle.MIN_W)
        self.resize_handle.MIN_W = required
        if self.width() < required:
            self.resize(required, self.height())

    # ── Blinking underscore cursor for the AI prompt bar ───────────────────
    # (JotEditor handles its own version of this internally — see JotEditor
    # above — since QLineEdit isn't subclassed here, the same behavior is
    # wired up manually against self.ai_prompt_bar in _setup_ui().)
    def _toggle_prompt_blink(self):
        self._prompt_blink_visible = not self._prompt_blink_visible
        self._prompt_blink_cursor.setVisible(self._prompt_blink_visible and self.ai_prompt_bar.hasFocus())

    def _reposition_prompt_blink(self):
        le = self.ai_prompt_bar
        fm = le.fontMetrics()
        pos = le.cursorPosition()
        text_before = le.text()[:pos]
        left_pad = 11  # matches #aiPromptBar's QSS padding (10px) + 1px border
        x = left_pad + fm.horizontalAdvance(text_before)
        w = fm.horizontalAdvance("_") + 2  # same length as before
        thickness = self.PROMPT_CURSOR_THICKNESS
        y = (le.height() + fm.ascent()) // 2 - thickness  # sits just under the text baseline
        self._prompt_blink_cursor.setFixedSize(w, thickness)
        self._prompt_blink_cursor.move(x, y)
        if le.hasFocus():
            self._prompt_blink_visible = True
            self._prompt_blink_cursor.setVisible(True)

    def _set_prompt_cursor_color(self, color: str):
        self._prompt_blink_cursor.setStyleSheet(f"background: {color}; border: none; border-radius: 1px;")

    def eventFilter(self, obj, event):
        if obj is getattr(self, "ai_prompt_bar", None):
            if event.type() == QEvent.Type.FocusIn:
                self.ai_prompt_bar.setPlaceholderText("")
                self._reposition_prompt_blink()
                self._prompt_blink_visible = True
                self._prompt_blink_cursor.show()
                self._prompt_blink_timer.start()
            elif event.type() == QEvent.Type.FocusOut:
                self.ai_prompt_bar.setPlaceholderText(self._prompt_logical_placeholder)
                self._prompt_blink_timer.stop()
                self._prompt_blink_cursor.hide()
        return super().eventFilter(obj, event)

    # ── Tray ──────────────────────────────────────────────────────────────
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(make_tray_icon(), self)

        menu = QMenu()
        menu.addAction("Show / Hide  (Alt+N)", self.toggle_panel)
        menu.addSeparator()

        self.startup_action = menu.addAction("Start with Windows")
        self.startup_action.setCheckable(True)
        self.startup_action.setChecked(is_startup_enabled())
        self.startup_action.triggered.connect(self._toggle_startup)

        menu.addSeparator()
        menu.addAction("Quit", QApplication.quit)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Jot_  —  Alt+N")
        self.tray.activated.connect(
            lambda r: self.toggle_panel()
            if r == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self.tray.show()

    def _toggle_startup(self, checked: bool):
        enable_startup() if checked else disable_startup()

    # ── Hotkey (global — works from anywhere on desktop) ──────────────────
    def _setup_hotkey(self):
        # keyboard library runs in a background thread, so we use a signal
        # to safely call toggle_panel on the main Qt thread
        self._hotkey_signal = HotkeySignal()
        self._hotkey_signal.triggered.connect(self.toggle_panel)
        keyboard.add_hotkey("alt+n", self._hotkey_signal.triggered.emit)

    # ── Positioning ───────────────────────────────────────────────────────
    def _screen_rect(self):
        return QApplication.primaryScreen().availableGeometry()

    def _position_offscreen(self):
        sr = self._screen_rect()
        self.move(sr.right() + self.width(), sr.center().y() - self.height() // 2)

    def _shown_pos(self):
        sr = self._screen_rect()
        return QPoint(sr.right() - self.width(),
                      sr.center().y() - self.height() // 2)

    def _hidden_pos(self):
        sr = self._screen_rect()
        return QPoint(sr.right() + self.width(), sr.center().y() - self.height() // 2)

    # ── Animation (high refresh rate — syncs to display) ─────────────────
    def _animate(self, to: QPoint):
        self._anim_start    = self.pos()
        self._anim_end      = to
        self._anim_elapsed  = 0
        self._anim_duration = 1000  # ms — slower but still 240hz smooth
        if not hasattr(self, "_anim_timer"):
            self._anim_timer = QTimer()
            self._anim_timer.setInterval(4)  # ~240fps
            self._anim_timer.timeout.connect(self._anim_step)
        self._anim_timer.start()

    def _anim_step(self):
        self._anim_elapsed += 8
        t = min(self._anim_elapsed / self._anim_duration, 1.0)
        ease = 1 - (1 - t) ** 3  # ease out cubic: fast start, smooth landing
        x = int(self._anim_start.x() + (self._anim_end.x() - self._anim_start.x()) * ease)
        self.move(x, self._anim_start.y())
        if t >= 1.0:
            self._anim_timer.stop()

    def show_panel(self):
        self._animate(self._shown_pos())
        self.is_visible = True

    def hide_panel(self):
        self._animate(self._hidden_pos())
        self.is_visible = False
        self.editor.clearFocus()
        self.setFocus()

    def toggle_panel(self):
        if self.is_visible:
            self.hide_panel()
        else:
            self.show_panel()

    # ── AI ────────────────────────────────────────────────────────────────
    def _set_ai_controls_enabled(self, enabled: bool):
        for btn in self.findChildren(QPushButton, "aiBtn"):
            btn.setEnabled(enabled)
        if hasattr(self, "ai_prompt_bar"):
            self.ai_prompt_bar.setEnabled(enabled)
        if hasattr(self, "ai_prompt_btn"):
            self.ai_prompt_btn.setEnabled(enabled)
        if not enabled:
            self._set_prompt_placeholder("Thinking...")

    def _set_prompt_placeholder(self, text: str):
        if hasattr(self, "ai_prompt_bar"):
            self._prompt_logical_placeholder = text
            # Keep it hidden while focused (see eventFilter) rather than
            # showing it immediately, so it doesn't overlap the blinking
            # cursor — it'll be applied automatically on blur.
            if not self.ai_prompt_bar.hasFocus():
                self.ai_prompt_bar.setPlaceholderText(text)

    def _restore_prompt_placeholder(self):
        self._set_prompt_placeholder(DEFAULT_AI_PROMPT_PLACEHOLDER)
        if hasattr(self, "ai_prompt_bar"):
            self.ai_prompt_bar.setToolTip("")

    def _flash_prompt_message(self, text: str, ms: int = 2500, tooltip: str = None):
        """Briefly shows a status/error message in the prompt bar. The
        placeholder update alone is invisible whenever the bar still holds
        the user's own typed text (e.g. they typed a prompt but the note
        is empty) — placeholders only render on an empty field — so this
        also pops the message up immediately as a floating tooltip, which
        shows regardless of what's currently typed. Long messages (like
        raw API errors) get truncated for display in `text`; pass the
        untruncated original as `tooltip` to show the full message."""
        self._set_prompt_placeholder(text)
        if hasattr(self, "ai_prompt_bar"):
            full_text = tooltip or text
            self.ai_prompt_bar.setToolTip(full_text)
            QToolTip.showText(
                self.ai_prompt_bar.mapToGlobal(QPoint(0, self.ai_prompt_bar.height() + 4)),
                full_text,
                self.ai_prompt_bar
            )
        QTimer.singleShot(ms, self._restore_prompt_placeholder)

    # ── API key ──────────────────────────────────────────────────────────
    def _resolve_api_key(self) -> str:
        """The key saved via the 🔑 dialog (settings.json) wins, since it's
        what you explicitly told Jot to use most recently — it takes
        priority over ANTHROPIC_API_KEY so a leftover/stale env var on the
        machine can't silently override a key you just verified. The env
        var is still honored as a fallback if no key has been saved yet."""
        return (str(self._settings.get("api_key", "")).strip()
                or os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def _prompt_for_api_key(self) -> str:
        """Opens the API key dialog and saves the result to settings.json
        on this machine. Returns the key, or '' if the user cancelled."""
        dialog = ApiKeyDialog(self, self.dark_mode, self._settings.get("api_key", ""))
        dialog.move(
            self.x() + (self.width() - dialog.width()) // 2,
            self.y() + (self.height() - dialog.height()) // 2
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_key:
            self._settings["api_key"] = dialog.result_key
            save_settings(self._settings)
            return dialog.result_key
        return ""

    def _open_api_key_dialog(self):
        key = self._prompt_for_api_key()
        if key:
            self._validate_api_key(key)

    def _validate_api_key(self, api_key: str):
        """Sends a minimal (1-token) request to confirm the key actually
        works, then flashes success/failure in the prompt bar. Only used
        for the manual 'set key' flow — when the dialog is triggered by an
        actual AI action instead, that action's own request serves as the
        validation, so this isn't run twice."""
        self._set_ai_controls_enabled(False)
        self._set_prompt_placeholder("Validating key...")
        self.api_key_test_worker = ApiKeyTestWorker(api_key, self.ai_model)
        self.api_key_test_worker.success.connect(self._on_api_key_valid)
        self.api_key_test_worker.error_occurred.connect(self._on_api_key_invalid)
        self.api_key_test_worker.start()

    def _on_api_key_valid(self):
        self._set_ai_controls_enabled(True)
        self._flash_prompt_message("API key saved.")
        self._hide_api_key_warning()

    def _on_api_key_invalid(self, error: str):
        self._set_ai_controls_enabled(True)
        self._flash_prompt_message(f"Key saved, but invalid: {error[:50]}", ms=4000, tooltip=error)
        self._show_api_key_warning(error)

    def _is_auth_error(self, error: str) -> bool:
        """Anthropic's SDK raises a 401 for a missing/invalid/outdated key —
        recognized here by the status code or the standard error type/text
        the API returns for that case."""
        e = error.lower()
        return "401" in e or "authentication_error" in e or "invalid x-api-key" in e or "invalid api key" in e

    def _show_api_key_warning(self, error: str = ""):
        if not hasattr(self, "api_key_warning"):
            return
        if self._is_auth_error(error) or not error:
            self.api_key_warning.setText(
                "⚠ Your API key appears to be invalid or outdated. Click the key icon above to update it."
            )
        else:
            self.api_key_warning.setText(f"⚠ AI request failed: {error[:80]}")
        self.api_key_warning.setToolTip(error)
        self.api_key_warning.show()

    def _hide_api_key_warning(self):
        if hasattr(self, "api_key_warning"):
            self.api_key_warning.hide()
            self.api_key_warning.setText("")

    def _run_ai(self, mode: str):
        text = self.editor.toPlainText().strip()
        api_key = self._resolve_api_key()
        if not api_key:
            api_key = self._prompt_for_api_key()
            if not api_key:
                return
        self._ai_pending_source = "button"
        self._ai_pending_prompt = None
        self._set_ai_controls_enabled(False)
        self.ai_worker = AIWorker(text, mode, api_key, self.ai_model)
        self.ai_worker.result_ready.connect(self._on_ai_result)
        self.ai_worker.error_occurred.connect(self._on_ai_error)
        self.ai_worker.start()

    def _run_ai_prompt(self):
        prompt = self.ai_prompt_bar.text().strip()
        if not prompt:
            self._flash_prompt_message("Type a prompt first...")
            return
        text = self.editor.toPlainText().strip()
        api_key = self._resolve_api_key()
        if not api_key:
            api_key = self._prompt_for_api_key()
            if not api_key:
                return
        self._ai_pending_source = "prompt"
        self._ai_pending_prompt = prompt
        self.ai_prompt_bar.clear()
        self._set_ai_controls_enabled(False)
        self.ai_worker = AIWorker(text, "custom", api_key, self.ai_model, instruction=prompt)
        self.ai_worker.result_ready.connect(self._on_ai_result)
        self.ai_worker.error_occurred.connect(self._on_ai_error)
        self.ai_worker.start()

    def _on_ai_result(self, result: str):
        self._set_ai_controls_enabled(True)
        self._restore_prompt_placeholder()
        self._hide_api_key_warning()
        if getattr(self, "_ai_pending_source", None) == "prompt":
            self._show_ai_preview(self._ai_pending_prompt, result)
        else:
            self.editor.setPlainText(result)
        self._ai_pending_source = None
        self._ai_pending_prompt = None

    def _show_ai_preview(self, prompt: str, result: str):
        """Prompt-bar results are staged in a preview dialog rather than
        applied straight away — the user can Accept (replace the note) or
        Discard (leave the editor untouched)."""
        dialog = AIPreviewDialog(self, self.dark_mode, prompt or "", result)
        dialog.move(
            self.x() + (self.width() - dialog.width()) // 2,
            self.y() + (self.height() - dialog.height()) // 2
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.editor.setPlainText(result)

    def _on_ai_error(self, error: str):
        self._set_ai_controls_enabled(True)
        if self._is_auth_error(error):
            self._flash_prompt_message("API key invalid or outdated — see warning below.", ms=4000, tooltip=error)
            self._show_api_key_warning(error)
        else:
            self._flash_prompt_message(f"Error: {error[:60]}", tooltip=error)
        self._ai_pending_source = None
        self._ai_pending_prompt = None


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep alive in tray when panel is hidden
    _load_button_font()  # registers fonts/ModularBlackBlockyBoldModern.ttf if present
    win = Jot()
    win.show_panel()
    sys.exit(app.exec())
