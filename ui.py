from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut, QPolygonF,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    STANDBY_C = "#ff8800" # More vibrant Amber/Orange
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._scan3      = 90.0

        # Wake-up animation state
        self._wake_up_progress = 0.0
        self._wake_up_active = True

        # Audio visualizer state
        self._audio_level_input = 0.0
        self._audio_level_output = 0.0
        self._audio_history = [0.0] * 32  # History for waveform visualization

        # Simplified hexagonal ring definitions: (radius_factor, rotation_speed, thickness)
        self._hex_rings = [
            (0.38, 0.6, 2.0),   # inner
            (0.50, -0.8, 1.8),  # mid
            (0.62, 0.4, 1.5),   # outer
            (0.74, -0.5, 1.2),  # far
        ]
        self._hex_angles = [random.uniform(0, 360) for _ in self._hex_rings]
        self._pulses: list[float] = [0.0, 40.0, 80.0]

        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._cur_clr = QColor(C.PRI)
        self._halo  = 1.0
        self._scale = 1.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def set_wake_up_progress(self, progress: float):
        """Set wake-up progress from MainWindow to sync animations."""
        self._wake_up_progress = progress
        if progress >= 1.0:
            self._wake_up_active = False

    def update_audio_level(self, level: float, is_input: bool = True):
        """Update audio level for visualization."""
        if is_input:
            self._audio_level_input = level
        else:
            self._audio_level_output = level
        
        # Update history for waveform
        self._audio_history.pop(0)
        self._audio_history.append(level)

    def _step(self):
        self._tick += 1

        # Wake-up animation progress
        if self._wake_up_active:
            self._wake_up_progress = min(1.0, self._wake_up_progress + 0.005)
            if self._wake_up_progress >= 1.0:
                self._wake_up_active = False

        # Smooth Color Interpolation (Lerp)
        is_sleeping = (self.state == "STANDBY")
        target_hex = "#ffb000" if is_sleeping else (C.MUTED_C if self.muted else C.PRI)
        target_qcol = QColor(target_hex)

        def lerp(c, t):
            step = 12
            r = c.red()   + (t.red()   - c.red())   * step // 255
            g = c.green() + (t.green() - c.green()) * step // 255
            b = c.blue()  + (t.blue()  - c.blue())  * step // 255
            if abs(r - t.red())   < 2: r = t.red()
            if abs(g - t.green()) < 2: g = t.green()
            if abs(b - t.blue())  < 2: b = t.blue()
            return QColor(r, g, b)

        self._cur_clr = lerp(self._cur_clr, target_qcol)

        now = time.time()
        if now - self._last_t > (0.10 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.04, 1.12)
                self._tgt_halo  = random.uniform(160, 220)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        # Rotate hexagonal rings
        speak_mult = 3.5 if self.speaking else 1.0
        sleep_mult = 0.3 if is_sleeping else 1.0
        for i, (_, rot_spd, _) in enumerate(self._hex_rings):
            self._hex_angles[i] = (self._hex_angles[i] + rot_spd * speak_mult * sleep_mult) % 360

        self._scan  = (self._scan  + (4.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-3.0 if self.speaking else -0.75)) % 360
        self._scan3 = (self._scan3 + (2.5 if self.speaking else 0.5)) % 360

        fw  = min(self.width(), self.height())

        # Expanding pulse rings
        lim = fw * 0.74
        pulse_spd = 4.5 if self.speaking else 2.0
        self._pulses = [r + pulse_spd for r in self._pulses if r + pulse_spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.09 if self.speaking else 0.025):
            self._pulses.append(0.0)

        # Particles
        if self.speaking and random.random() < 0.35:
            cx_p, cy_p = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.32
            self._particles.append([
                cx_p + math.cos(ang) * r_s, cy_p + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(1.2, 3.0),
                math.sin(ang) * random.uniform(1.2, 3.0) - 0.5, 1.0,
            ])
        self._particles = [
            [pt[0]+pt[2], pt[1]+pt[3], pt[2]*0.96, pt[3]*0.96, pt[4]-0.025]
            for pt in self._particles if pt[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        is_sleeping = (self.state == "STANDBY")
        main_clr = self._cur_clr

        def alpha_clr(a):
            c = QColor(main_clr)
            c.setAlpha(min(255, max(0, a)))
            return c

        # ─── 1. HEXAGONAL RINGS ───
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, (rad_f, _, thick) in enumerate(self._hex_rings):
            # Wake-up animation: stagger ring appearance and expansion
            ring_progress = self._wake_up_progress
            if self._wake_up_active:
                # Stagger rings: each ring starts at different progress
                ring_start = i * 0.2
                ring_progress = max(0, min(1, (self._wake_up_progress - ring_start) / (1 - ring_start)))
                # Expand from center during wake-up
                expansion_scale = 0.3 + 0.7 * ring_progress
            else:
                expansion_scale = 1.0
            
            radius = fw * rad_f * self._scale * expansion_scale
            angle = self._hex_angles[i]
            
            # Calculate hexagon vertices
            hex_pts = []
            for j in range(6):
                theta = math.radians(angle + j * 60)
                x = cx + radius * math.cos(theta)
                y = cy + radius * math.sin(theta)
                hex_pts.append(QPointF(x, y))
            
            # Draw hexagon with glow
            glow_a = min(255, int(self._halo * 0.5))
            if is_sleeping: glow_a = int(glow_a * 0.3)
            # Fade in during wake-up
            if self._wake_up_active:
                glow_a = int(glow_a * ring_progress)
            
            # Glow layer
            glow_col = QColor(main_clr)
            glow_col.setAlpha(glow_a)
            pen_glow = QPen(glow_col, thick + 3)
            pen_glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen_glow)
            path = QPainterPath()
            path.moveTo(hex_pts[0])
            for pt in hex_pts[1:]:
                path.lineTo(pt)
            path.closeSubpath()
            p.drawPath(path)
            
            # Core layer
            core_a = min(255, int(self._halo * 1.0))
            if is_sleeping: core_a = int(core_a * 0.4)
            # Fade in during wake-up
            if self._wake_up_active:
                core_a = int(core_a * ring_progress)
            pen_core = QPen(alpha_clr(core_a), thick)
            pen_core.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen_core)
            p.drawPath(path)

        # ─── 2. CROSSHAIR LINES ───
        xh_len = fw * 0.42
        xh_gap = fw * 0.14
        xh_a = 60 if is_sleeping else min(255, int(self._halo * 0.7))
        p.setPen(QPen(alpha_clr(xh_a), 1))
        # Horizontal
        p.drawLine(QPointF(cx - xh_len, cy), QPointF(cx - xh_gap, cy))
        p.drawLine(QPointF(cx + xh_gap, cy), QPointF(cx + xh_len, cy))
        # Vertical
        p.drawLine(QPointF(cx, cy - xh_len), QPointF(cx, cy - xh_gap))
        p.drawLine(QPointF(cx, cy + xh_gap), QPointF(cx, cy + xh_len))
        # Tick marks on crosshairs
        for d in [-1, 1]:
            for t in range(3):
                off = xh_gap + (xh_len - xh_gap) * t / 3
                mk = 6
                p.drawLine(QPointF(cx + d * off, cy - mk), QPointF(cx + d * off, cy + mk))
                p.drawLine(QPointF(cx - mk, cy + d * off), QPointF(cx + mk, cy + d * off))

        # ─── 3. SCANNING BEAM ───
        scan_r = fw * 0.48
        sx = cx + math.cos(math.radians(self._scan)) * scan_r
        sy_beam = cy + math.sin(math.radians(self._scan)) * scan_r
        beam_a = 40 if is_sleeping else min(255, int(self._halo * 0.6))
        pen_beam = QPen(alpha_clr(beam_a), 1.5)
        pen_beam.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen_beam)
        p.drawLine(QPointF(cx, cy), QPointF(sx, sy_beam))
        # Second scanner (opposite direction)
        sx2 = cx + math.cos(math.radians(self._scan2)) * scan_r * 0.8
        sy2 = cy + math.sin(math.radians(self._scan2)) * scan_r * 0.8
        p.drawLine(QPointF(cx, cy), QPointF(sx2, sy2))

        # ─── 4. PULSING RINGS ───
        for pr in self._pulses:
            pulse_a = max(0, int(120 * (1.0 - pr / (fw * 0.74))))
            if is_sleeping: pulse_a = int(pulse_a * 0.3)
            p.setPen(QPen(alpha_clr(pulse_a), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), pr, pr)

        # ─── 5. CORNER BRACKETS ───
        bl = 45
        bracket_a = 120 if is_sleeping else min(255, int(self._halo * 1.0))
        p.setPen(QPen(alpha_clr(bracket_a), 2))
        for sx_dir in [-1, 1]:
            for sy_dir in [-1, 1]:
                bx = cx + sx_dir * fw * 0.46
                by = cy + sy_dir * fw * 0.38
                p.drawLine(QPointF(bx, by), QPointF(bx - sx_dir * bl, by))
                p.drawLine(QPointF(bx, by), QPointF(bx, by - sy_dir * bl))

        # ─── 6. CENTER FACE (Hexagonal Clip) ───
        if self._face_px:
            fsz = int(fw * 0.16 * self._scale)
            if is_sleeping: fsz = int(fsz * 0.85)
            path = QPainterPath()
            hex_pts = [QPointF(cx + fsz * math.cos(math.radians(60 * i - 90)),
                               cy + fsz * math.sin(math.radians(60 * i - 90))) for i in range(6)]
            path.moveTo(hex_pts[0])
            for hp in hex_pts[1:]:
                path.lineTo(hp)
            path.closeSubpath()

            # Hex border glow
            hex_glow_a = 80 if is_sleeping else min(255, int(self._halo * 0.8))
            p.setPen(QPen(alpha_clr(hex_glow_a), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            p.setClipPath(path)
            op = 0.3 if is_sleeping else 0.9
            p.setOpacity(op)
            scaled = self._face_px.scaled(fsz*2, fsz*2, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(int(cx - fsz), int(cy - fsz), scaled)
            p.setOpacity(1.0)
            p.setClipping(False)
        else:
            p.setPen(QPen(alpha_clr(120 if is_sleeping else min(255, int(self._halo * 2))), 1))
            p.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
            txt = "STANDBY" if is_sleeping else "CORE ACTIVE"
            p.drawText(QRectF(cx - 100, cy - 20, 200, 40), Qt.AlignmentFlag.AlignCenter, txt)

        # ─── 7. PARTICLES ───
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setBrush(QBrush(alpha_clr(a)))
            sz = 2.0 + pt[4] * 2.0
            p.drawEllipse(QPointF(pt[0], pt[1]), sz, sz)

        # ─── 8. AUDIO VISUALIZER ───
        if not is_sleeping:
            # Draw input level bar (left side)
            input_h = fw * 0.05 + self._audio_level_input * fw * 0.25
            input_x = cx - fw * 0.35
            input_y = cy + fw * 0.35
            p.setPen(QPen(alpha_clr(200), 2))
            p.setBrush(QBrush(alpha_clr(180)))
            p.drawRect(QRectF(input_x, input_y - input_h, 8, input_h))
            
            # Draw output level bar (right side)
            output_h = fw * 0.05 + self._audio_level_output * fw * 0.25
            output_x = cx + fw * 0.35 - 8
            output_y = cy + fw * 0.35
            p.setPen(QPen(alpha_clr(200), 2))
            p.setBrush(QBrush(alpha_clr(180)))
            p.drawRect(QRectF(output_x, output_y - output_h, 8, output_h))
            
            # Draw waveform at bottom
            wave_y = cy + fw * 0.42
            wave_w = fw * 0.5
            wave_x_start = cx - wave_w / 2
            p.setPen(QPen(alpha_clr(150), 2))
            for i, level in enumerate(self._audio_history):
                x = wave_x_start + (i / len(self._audio_history)) * wave_w
                h = level * fw * 0.05
                p.drawLine(QPointF(x, wave_y), QPointF(x, wave_y - h))

        # status text
        sy = cy + fw * 0.40
        if self.state == "STANDBY":
            txt, col = "💤  STANDBY MODE", self._cur_clr
        elif self.muted:
            txt, col = "⊘  MUTED",     self._cur_clr
        elif self.speaking:
            txt, col = "●  SPEAKING",  self._cur_clr
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   self._cur_clr
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", self._cur_clr
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  self._cur_clr
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", self._cur_clr

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, self._cur_clr
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = self._cur_clr if hgt > 12 else alpha_clr(100)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = alpha_clr(150)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

class CircularGauge(QWidget):
    def __init__(self, label: str, color: str = C.PRI, size: int = 70, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text = "--"
        self._target_value = 0.0
        self._size = size
        self.setFixedSize(size, size + 20)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(30)

    def set_color(self, color: str):
        self._color = color
        self.update()

    def set_value(self, pct: float, text: str):
        self._target_value = max(0.0, min(100.0, pct))
        self._text = text

    def _animate(self):
        if abs(self._value - self._target_value) > 0.5:
            self._value += (self._target_value - self._value) * 0.15
            self.update()
        else:
            self._value = self._target_value

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, (H - 20) / 2
        radius = (W - 12) / 2

        # Determine color based on value
        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        # Background ring
        p.setPen(QPen(qcol(C.BORDER, 80), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Progress arc
        start_angle = 135 * 16
        span_angle = int((self._value / 100) * 270 * 16)
        
        # Glow effect
        glow_col = QColor(bar_col)
        glow_col.setAlpha(60)
        p.setPen(QPen(glow_col, 5))
        p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), start_angle, span_angle)
        
        # Main arc
        p.setPen(QPen(bar_col, 3))
        p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), start_angle, span_angle)

        # Center value
        p.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, cy - 8, W, 16), Qt.AlignmentFlag.AlignCenter, self._text)

        # Label
        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, H - 18, W, 14), Qt.AlignmentFlag.AlignCenter, self._label)


class SystemStatusCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pulse_phase = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(50)
        self.setFixedHeight(85)

    def _animate(self):
        self._pulse_phase = (self._pulse_phase + 0.08) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Background with gradient
        grad = QLinearGradient(0, 0, W, H)
        grad.setColorAt(0, qcol("#010810"))
        grad.setColorAt(1, qcol("#011018"))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(qcol(C.BORDER, 100), 1))
        p.drawRoundedRect(QRectF(2, 2, W - 4, H - 4), 4, 4)

        # Animated pulse indicator
        pulse_alpha = int(100 + 80 * math.sin(self._pulse_phase))
        pulse_col = QColor(C.PRI)
        pulse_col.setAlpha(pulse_alpha)
        
        cx, cy = 18, H / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(pulse_col))
        p.drawEllipse(QPointF(cx, cy), 6, 6)

        # Outer glow ring
        glow_radius = 10 + 3 * math.sin(self._pulse_phase)
        glow_col = QColor(C.PRI)
        glow_col.setAlpha(40)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(glow_col, 1.5))
        p.drawEllipse(QPointF(cx, cy), glow_radius, glow_radius)

        # Status text
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.GREEN), 1))
        p.drawText(QRectF(32, 8, W - 36, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "SYSTEM ONLINE")

        p.setFont(QFont("Consolas", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(32, 24, W - 36, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "All protocols nominal")

        # Decorative lines
        p.setPen(QPen(qcol(C.BORDER, 60), 1))
        p.drawLine(QPointF(32, 42), QPointF(W - 8, 42))
        
        # Animated data stream
        for i in range(3):
            x = 32 + (self._pulse_phase * 20 + i * 30) % (W - 48)
            y = 52 + 4 * math.sin(self._pulse_phase + i)
            p.setPen(QPen(qcol(C.PRI_DIM), 1))
            p.drawPoint(QPointF(x, y))


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text  = "--"
        self.setFixedHeight(32)
        self.setMinimumWidth(80)

    def set_color(self, color: str):
        self._color = color
        self.update()

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Background
        p.setBrush(QBrush(qcol("#010a10")))
        p.setPen(QPen(qcol(C.BORDER, 60), 1))
        p.drawRect(QRectF(0, 0, W, H))

        # Bar track
        bar_h   = 3
        bar_y   = H - bar_h - 4
        bar_w   = W - 10
        bar_x   = 5
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(bar_x, bar_y, bar_w, bar_h))

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            # Glow effect on bar
            glow_col = QColor(bar_col); glow_col.setAlpha(40)
            p.setBrush(QBrush(glow_col))
            p.drawRect(QRectF(bar_x, bar_y - 2, fill_w, bar_h + 4))
            p.setBrush(QBrush(bar_col))
            p.drawRect(QRectF(bar_x, bar_y, fill_w, bar_h))

        # Label (left)
        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(6, 3, 40, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        # Value (right)
        p.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 3, W - 6, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 0px;
                padding: 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        raw = self._queue.pop(0)

        # Determine tag and prefix formatting
        tl = raw.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        elif tl.startswith("sys:"):    self._tag = "sys"
        else:                          self._tag = "info"

        # Add a visual prefix marker
        prefix_map = {
            "you":  "▸ ",
            "ai":   "◆ ",
            "err":  "✖ ",
            "file": "◎ ",
            "sys":  "│ ",
            "info": "  ",
        }
        self._text = prefix_map.get(self._tag, "  ") + raw
        self._pos  = 0
        self._tmr.start(4)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
                "info": qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(15, self._next)


_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)   # Terminal Activity
    _chat_sig  = pyqtSignal(str)   # Communications Log
    _state_sig = pyqtSignal(str)
    _play_sig  = pyqtSignal(str)


    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S — MARK XXXIX")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_interrupt     = None
        self._muted           = False
        self._current_file: str | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        center_col = QVBoxLayout()
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(0)

        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center_col.addWidget(self.hud, stretch=5)

        self._activity_log = LogWidget()
        self._activity_log.setFixedHeight(120)
        self._activity_log.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._activity_log.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                border: none;
                border-top: 1px solid {C.BORDER};
                color: {C.PRI};
                padding: 4px 8px;
            }}
        """)
        center_col.addWidget(self._activity_log, stretch=0)

        body.addLayout(center_col, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._activity_log.append_log)
        self._chat_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._play_sig.connect(self._on_play_requested)

        self._overlay: SetupOverlay | None = None

        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._gauge_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._gauge_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._gauge_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._gauge_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._gauge_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._gauge_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(56)
        w.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {C.DARK}, stop:0.5 {C.PANEL2}, stop:1 {C.DARK});
            border-bottom: 1px solid {C.BORDER_B};
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 18, 0)

        # Left badge with glow effect
        badge_container = QWidget()
        badge_container.setFixedWidth(90)
        badge_lay = QVBoxLayout(badge_container)
        badge_lay.setContentsMargins(0, 0, 0, 0)
        badge_lay.setSpacing(2)
        
        badge = QLabel("MARK XXXIX")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        badge.setStyleSheet(f"""
            color: {C.PRI}; 
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {C.PRI_GHO}, stop:1 {C.DARK});
            padding: 4px 8px; 
            border: 1px solid {C.PRI_DIM}; 
            border-radius: 4px;
        """)
        badge_lay.addWidget(badge)
        
        version = QLabel("v39.0")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setFont(QFont("Consolas", 6))
        version.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        badge_lay.addWidget(version)
        
        lay.addWidget(badge_container)
        lay.addStretch()

        # Center title with enhanced styling
        mid = QVBoxLayout(); mid.setSpacing(2)
        title = QLabel("J.A.R.V.I.S")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Consolas", 18, QFont.Weight.Bold))
        title.setStyleSheet(f"""
            color: {C.PRI}; 
            background: transparent; 
            letter-spacing: 6px;
        """)
        mid.addWidget(title)
        sub = QLabel("Just A Rather Very Intelligent System")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Consolas", 7))
        sub.setStyleSheet(f"""
            color: {C.TEXT_MED}; 
            background: transparent;
            letter-spacing: 1px;
        """)
        mid.addWidget(sub)
        lay.addLayout(mid)
        lay.addStretch()

        # Right clock with modern styling
        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Consolas", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"""
            color: {C.PRI}; 
            background: transparent;
        """)
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Consolas", 7))
        self._date_lbl.setStyleSheet(f"""
            color: {C.TEXT_MED}; 
            background: transparent;
        """)
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y").upper())

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(8)

        # System Status Card
        status_card = SystemStatusCard()
        lay.addWidget(status_card)

        # Section header
        hdr = QLabel("◈ SYSTEM METRICS")
        hdr.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; padding-bottom: 2px;")
        lay.addWidget(hdr)

        # Separator
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep)
        lay.addSpacing(4)

        # Circular gauges for primary metrics (2x2 grid)
        gauges_layout = QHBoxLayout()
        gauges_layout.setSpacing(6)
        
        self._gauge_cpu = CircularGauge("CPU", C.PRI, size=62)
        self._gauge_mem = CircularGauge("MEM", C.ACC2, size=62)
        gauges_layout.addWidget(self._gauge_cpu)
        gauges_layout.addWidget(self._gauge_mem)
        lay.addLayout(gauges_layout)

        gauges_layout2 = QHBoxLayout()
        gauges_layout2.setSpacing(6)
        
        self._gauge_gpu = CircularGauge("GPU", C.ACC, size=62)
        self._gauge_tmp = CircularGauge("TMP", "#ff6688", size=62)
        gauges_layout2.addWidget(self._gauge_gpu)
        gauges_layout2.addWidget(self._gauge_tmp)
        lay.addLayout(gauges_layout2)

        lay.addSpacing(6)

        # Secondary metrics as compact bars
        self._bar_net = MetricBar("NET", C.GREEN)
        lay.addWidget(self._bar_net)

        lay.addSpacing(8)

        # System info panel
        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: #010810; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(8, 6, 8, 6)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Consolas", 7))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Consolas", 7))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addStretch()

        # Status badges
        for txt, col in [
            ("AI CORE\nACTIVE",     C.GREEN),
            ("SEC\nCLEARED",        C.PRI),
            ("PROTOCOL\nXXXIX",     C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: #010810;"
                f"border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # Communications Log
        log_lbl = QLabel("COMMUNICATIONS")
        log_lbl.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        log_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; padding: 2px 0;")
        lay.addWidget(log_lbl)
        
        self._log = LogWidget()
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                color: {C.TEXT};
                padding: 6px;
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 6px;
                border: none;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        lay.addWidget(self._log, stretch=1)

        # Separator
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep)

        # File Upload
        file_lbl = QLabel("FILE UPLOAD")
        file_lbl.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        file_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent; padding: 2px 0;")
        lay.addWidget(file_lbl)
        
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded")
        self._file_hint.setFont(QFont("Consolas", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._file_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._file_hint)

        # Separator
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep2)

        # Command Input
        cmd_lbl = QLabel("COMMAND INPUT")
        cmd_lbl.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        cmd_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; padding: 2px 0;")
        lay.addWidget(cmd_lbl)
        
        lay.addLayout(self._build_input_row())

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        
        self._mute_btn = QPushButton("🎙 Mic")
        self._mute_btn.setFixedHeight(28)
        self._mute_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        btn_row.addWidget(self._mute_btn)

        self._interrupt_btn = QPushButton("🛑 Stop")
        self._interrupt_btn.setFixedHeight(28)
        self._interrupt_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #0a0000; color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: #1a0000; border: 1px solid #ff0000; }}
            QPushButton:pressed {{ background: #2a0000; }}
        """)
        self._interrupt_btn.clicked.connect(self._interrupt)
        btn_row.addWidget(self._interrupt_btn)
        
        fs_btn = QPushButton("⛶ Full")
        fs_btn.setFixedHeight(28)
        fs_btn.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {C.PRI}; border: 1px solid {C.BORDER_B};
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        btn_row.addWidget(fs_btn)
        
        lay.addLayout(btn_row)

        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(4)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command...")
        self._input.setFont(QFont("Consolas", 9))
        self._input.setFixedHeight(28)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000810; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 0px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(28, 28)
        send.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 0px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(20)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_DIM):
            l = QLabel(txt); l.setFont(QFont("Consolas", 6))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] MUTE  ·  [F11] FULLSCREEN"))
        lay.addStretch()
        lay.addWidget(_fl("BUDAQUE INDUSTRIES  ·  MARK XXXIX  ·  CLASSIFIED"))
        lay.addStretch()
        lay.addWidget(_fl("© BudaqueCreations", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell JARVIS what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇 Mic")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 4px;
                }}
                QPushButton:hover {{ background: #1a0008; }}
            """)
        else:
            self._mute_btn.setText("🎙 Mic")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 4px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _interrupt(self):
        self._log.append_log("SYS: Interruption requested.")
        if self.on_interrupt:
            threading.Thread(target=self.on_interrupt, daemon=True).start()

    def _on_play_requested(self, file_name: str):
        """Handler for sound requests in the GUI thread."""
        path = BASE_DIR / "assets" / file_name
        if not path.exists():
            print(f"[UI] Sound file not found: {path}")
            return

        # Keep references in the window object to avoid GC
        if not hasattr(self, "_active_players"):
            self._active_players = []
            
        player = QMediaPlayer(self)
        audio  = QAudioOutput(self)
        player.setAudioOutput(audio)
        player.setSource(QUrl.fromLocalFile(str(path)))
        audio.setVolume(0.9)
        
        # Cleanup routine for finished sounds
        self._active_players = [p for p in self._active_players if p[0].playbackState() != QMediaPlayer.PlaybackState.StoppedState]
        
        self._active_players.append((player, audio))
        player.play()
        print(f"[UI] Sound sequence: {file_name}")

    def _apply_state(self, state: str):
        # print(f"[UI] State Transition: {state}")
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        
        # Globally update UI accent if in Standby
        if state == "STANDBY":
            self.setStyleSheet(f"""
                QMainWindow {{ background: {C.BG}; }}
                QWidget#sidePanel {{ border-right: 1px solid {C.STANDBY_C}; }}
                QLabel#panelTitle {{ color: {C.STANDBY_C}; }}
            """)
            self._log.setStyleSheet(f"border-left: 1px solid {C.STANDBY_C}; background: {C.PANEL};")
            self._activity_log.setStyleSheet(f"QTextEdit {{ background: transparent; border: none; color: {C.STANDBY_C}; }}")
            # Update all circular gauges and metric bars
            for gauge in [self._gauge_cpu, self._gauge_mem, self._gauge_gpu, self._gauge_tmp]:
                gauge.set_color(C.STANDBY_C)
            self._bar_net.set_color(C.STANDBY_C)
        else:
            self.setStyleSheet(f"""
                QMainWindow {{ background: {C.BG}; }}
                QWidget#sidePanel {{ border-right: 1px solid {C.BORDER_B}; }}
                QLabel#panelTitle {{ color: {C.PRI}; }}
            """)
            self._log.setStyleSheet(f"border-left: 1px solid {C.BORDER_B}; background: {C.PANEL};")
            self._activity_log.setStyleSheet(f"QTextEdit {{ background: transparent; border: none; color: {C.PRI}; }}")
            # Restore original colors
            self._gauge_cpu.set_color(C.PRI)
            self._gauge_mem.set_color(C.ACC2)
            self._gauge_gpu.set_color(C.ACC)
            self._gauge_tmp.set_color("#ff6688")
            self._bar_net.set_color(C.GREEN)

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        # Route setup log to chat log instead of activity log
        self._chat_sig.emit(f"SYS: Initialised. OS={os_name.upper()}. JARVIS online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def play_sound(self, file_name: str):
        self._win._play_sig.emit(file_name)

    def play_startup_sound(self):
        self.play_sound("startup.mp3")

    def write_log(self, text: str):
        if text.startswith("Jarvis:") or text.startswith("You:") or text.startswith("SYS: JARVIS"):
            self._win._chat_sig.emit(text)
        else:
            self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
