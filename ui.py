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
    QEasingCurve, QEvent, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
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

_DEFAULT_W, _DEFAULT_H = 1176, 840
_MIN_W,     _MIN_H     = 984, 696
_LEFT_W  = 180
_RIGHT_W = 400

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
        self._standby_blend = 0.0

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
        self._wake_up_external = False

        # Audio visualizer state
        self._audio_level_input = 0.0
        self._audio_level_output = 0.0
        self._audio_history = [0.0] * 32  # History for waveform visualization

        # Orbital arc rings: (radius_factor, deg/sec, thickness, segment_count)
        self._arc_rings = [
            (0.36,  22.0, 2.2, 8),
            (0.49, -28.0, 1.8, 10),
            (0.62,  18.0, 1.5, 6),
            (0.76, -14.0, 1.2, 12),
        ]
        self._arc_offsets = [random.uniform(0, 360) for _ in self._arc_rings]
        self._wave_heights = [3] * 36
        self._last_step_t = time.perf_counter()
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
        self._tmr.start(20)

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
        """Set wake-up progress from MainWindow / boot overlay to sync animations."""
        self._wake_up_external = True
        self._wake_up_progress = max(0.0, min(1.0, progress))
        if self._wake_up_progress >= 1.0:
            self._wake_up_active = False

    def set_standby_blend(self, blend: float):
        self._standby_blend = max(0.0, min(1.0, blend))

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
        now_t = time.perf_counter()
        dt = min(0.05, now_t - self._last_step_t)
        self._last_step_t = now_t
        self._tick += 1

        # Wake-up animation progress (skipped when boot overlay drives it)
        if self._wake_up_active and not getattr(self, "_wake_up_external", False):
            self._wake_up_progress = min(1.0, self._wake_up_progress + 0.005)
            if self._wake_up_progress >= 1.0:
                self._wake_up_active = False

        # Smooth Color Interpolation (Lerp)
        is_sleeping = (self.state == "STANDBY") or self._standby_blend > 0.5
        active_hex = C.MUTED_C if self.muted else C.PRI
        target_hex = "#ffb000" if is_sleeping else active_hex
        if 0.01 < self._standby_blend < 0.99:
            t = self._standby_blend
            ac = QColor(active_hex)
            sb = QColor("#ffb000")
            target_hex = QColor(
                int(ac.red()   + (sb.red()   - ac.red())   * t),
                int(ac.green() + (sb.green() - ac.green()) * t),
                int(ac.blue()  + (sb.blue()  - ac.blue())  * t),
            ).name()
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

        # Rotate orbital arc rings (degrees/sec, frame-rate independent)
        speak_mult = 2.0 if self.speaking else 1.0
        sleep_mult = 0.35 if is_sleeping else 1.0
        for i, (_, rot_spd, _, _) in enumerate(self._arc_rings):
            self._arc_offsets[i] = (self._arc_offsets[i] + rot_spd * speak_mult * sleep_mult * dt) % 360

        scan_rate = 220.0 if self.speaking else 72.0
        self._scan  = (self._scan  + scan_rate * dt) % 360
        self._scan2 = (self._scan2 - scan_rate * 0.85 * dt) % 360
        self._scan3 = (self._scan3 + scan_rate * 0.55 * dt) % 360

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

        N = 36
        for i in range(N):
            if self.muted:
                self._wave_heights[i] = 2
            elif self.speaking:
                self._wave_heights[i] = max(2, min(20, self._wave_heights[i] + random.randint(-4, 5)))
            else:
                self._wave_heights[i] = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))

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

        # ─── 1. ORBITAL ARC RINGS (batched paths — 8 draws vs ~100) ───
        p.setBrush(Qt.BrushStyle.NoBrush)
        cap = Qt.PenCapStyle.RoundCap
        for i, (rad_f, _, thick, n_seg) in enumerate(self._arc_rings):
            ring_progress = self._wake_up_progress
            if self._wake_up_active:
                ring_start = i * 0.18
                ring_progress = max(0, min(1, (self._wake_up_progress - ring_start) / (1 - ring_start)))
                expansion_scale = 0.25 + 0.75 * ring_progress
            else:
                expansion_scale = 1.0

            radius = fw * rad_f * self._scale * expansion_scale
            offset = self._arc_offsets[i]
            seg_deg = 360.0 / n_seg
            gap_deg = seg_deg * (0.40 if self.speaking else 0.50)
            draw_deg = max(2.0, seg_deg - gap_deg)

            glow_a = min(255, int(self._halo * 0.40))
            core_a = min(255, int(self._halo * 0.90))
            if is_sleeping:
                glow_a = int(glow_a * 0.3)
                core_a = int(core_a * 0.4)
            if self._wake_up_active:
                glow_a = int(glow_a * ring_progress)
                core_a = int(core_a * ring_progress)

            ring_path = _arc_ring_path(cx, cy, radius, offset, n_seg, draw_deg, seg_deg)

            glow_col = QColor(main_clr)
            glow_col.setAlpha(glow_a)
            p.setPen(QPen(glow_col, thick + 2, Qt.PenStyle.SolidLine, cap))
            p.drawPath(ring_path)

            p.setPen(QPen(alpha_clr(core_a), thick, Qt.PenStyle.SolidLine, cap))
            p.drawPath(ring_path)

            if i == len(self._arc_rings) - 1:
                tick_r = radius - thick * 2
                tick_path = QPainterPath()
                for t in range(0, 360, 30):
                    ta = math.radians(t + offset * 0.4)
                    x1 = cx + tick_r * math.cos(ta)
                    y1 = cy + tick_r * math.sin(ta)
                    x2 = cx + (tick_r - 4) * math.cos(ta)
                    y2 = cy + (tick_r - 4) * math.sin(ta)
                    tick_path.moveTo(x1, y1)
                    tick_path.lineTo(x2, y2)
                p.setPen(QPen(alpha_clr(int(core_a * 0.35)), 1))
                p.drawPath(tick_path)

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

        # waveform (heights precomputed in _step — no random in paint)
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            hgt = self._wave_heights[i]
            if self.muted:
                cl = self._cur_clr
            elif self.speaking:
                cl = self._cur_clr if hgt > 12 else alpha_clr(100)
            else:
                cl = alpha_clr(150)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

def _arc_ring_path(
    cx: float, cy: float, radius: float,
    offset_deg: float, n_seg: int, draw_deg: float, seg_deg: float,
) -> QPainterPath:
    """Single path for all arc segments on one ring — one draw call instead of dozens."""
    path = QPainterPath()
    rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
    for s in range(n_seg):
        start = offset_deg + s * seg_deg
        seg = QPainterPath()
        seg.arcMoveTo(rect, start)
        seg.arcTo(rect, start, -draw_deg)
        path.addPath(seg)
    return path


def _hex_path(cx: float, cy: float, radius: float, rotation: float = -90) -> QPainterPath:
    path = QPainterPath()
    for i in range(6):
        theta = math.radians(rotation + i * 60)
        pt = QPointF(cx + radius * math.cos(theta), cy + radius * math.sin(theta))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


def _chamfer_rect(x: float, y: float, w: float, h: float, cut: float = 6) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - cut, y)
    path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x + cut, y + h)
    path.lineTo(x, y + h - cut)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


class LeftPanelRail(QWidget):
    """Custom-painted tactical rail background for the left panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._phase = 0.0
        self._scan_y = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(40)

    def _tick(self):
        self._phase = (self._phase + 0.06) % (2 * math.pi)
        self._scan_y = (self._scan_y + 1.2) % max(self.height(), 1)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        grad = QLinearGradient(0, 0, W, 0)
        grad.setColorAt(0.0, qcol("#000408"))
        grad.setColorAt(0.12, qcol("#000c14"))
        grad.setColorAt(1.0, qcol(C.DARK))
        p.fillRect(self.rect(), QBrush(grad))

        spine_x = 10.0
        pulse = 0.55 + 0.45 * math.sin(self._phase)
        spine_grad = QLinearGradient(spine_x, 0, spine_x, H)
        spine_grad.setColorAt(0.0, qcol(C.PRI, 0))
        spine_grad.setColorAt(0.15, qcol(C.PRI, int(90 * pulse)))
        spine_grad.setColorAt(0.5, qcol(C.PRI, int(180 * pulse)))
        spine_grad.setColorAt(0.85, qcol(C.PRI, int(90 * pulse)))
        spine_grad.setColorAt(1.0, qcol(C.PRI, 0))
        p.setPen(QPen(QBrush(spine_grad), 2))
        p.drawLine(QPointF(spine_x, 8), QPointF(spine_x, H - 8))

        for y in range(18, H - 12, 14):
            tick_a = 50 + int(30 * math.sin(self._phase + y * 0.08))
            p.setPen(QPen(qcol(C.PRI_DIM, tick_a), 1))
            p.drawLine(QPointF(spine_x - 3, y), QPointF(spine_x + 3, y))

        p.setPen(QPen(qcol(C.BORDER, 35), 1))
        for gx in range(24, W, 18):
            p.drawLine(QPointF(gx, 0), QPointF(gx, H))
        for gy in range(0, H, 18):
            p.drawLine(QPointF(18, gy), QPointF(W, gy))

        scan_a = int(35 + 25 * math.sin(self._phase * 2))
        p.setPen(QPen(qcol(C.PRI, scan_a), 1))
        p.drawLine(QPointF(16, self._scan_y), QPointF(W - 4, self._scan_y))

        p.setPen(QPen(qcol(C.BORDER_B, 120), 1))
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            ox = 4 if sx < 0 else W - 14
            oy = 4 if sy < 0 else H - 14
            p.drawLine(QPointF(ox, oy), QPointF(ox + 8 * sx, oy))
            p.drawLine(QPointF(ox, oy), QPointF(ox, oy + 8 * sy))

        p.setPen(QPen(qcol(C.BORDER, 180), 1))
        p.drawLine(QPointF(W - 1, 0), QPointF(W - 1, H))


class LinkStatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(45)
        self.setFixedHeight(46)

    def _tick(self):
        self._phase = (self._phase + 0.1) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(4, 3, W - 8, H - 6)

        p.setBrush(QBrush(qcol("#000a12", 200)))
        p.setPen(QPen(qcol(C.BORDER, 90), 1))
        p.drawPath(_chamfer_rect(rect.x(), rect.y(), rect.width(), rect.height(), 5))

        pulse = int(120 + 80 * math.sin(self._phase))
        p.setPen(QPen(qcol(C.GREEN, pulse), 1.5))
        chev_x = 12 + (self._phase * 6) % 10
        for i in range(3):
            x = chev_x + i * 7
            p.drawLine(QPointF(x, H / 2 - 4), QPointF(x + 4, H / 2))
            p.drawLine(QPointF(x + 4, H / 2), QPointF(x, H / 2 + 4))

        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.GREEN), 1))
        p.drawText(QRectF(34, 8, W - 38, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "UPLINK // NOMINAL")

        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        code = f"0x{int((self._phase * 40) % 256):02X} · SYNC LOCK"
        p.drawText(QRectF(34, 24, W - 38, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, code)

        p.setPen(QPen(qcol(C.BORDER, 70), 1))
        p.drawLine(QPointF(34, H - 10), QPointF(W - 10, H - 10))
        stream_x = 34 + (self._phase * 18) % (W - 50)
        p.setPen(QPen(qcol(C.PRI_DIM, 180), 1))
        p.drawPoint(QPointF(stream_x, H - 10))


class HexMetricCell(QWidget):
    """Hexagonal perimeter gauge — matches the HUD core aesthetic."""

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._target = 0.0
        self._text = "--"
        self.setFixedHeight(54)
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._animate)
        self._tmr.start(30)

    def set_color(self, color: str):
        self._color = color
        self.update()

    def set_value(self, pct: float, text: str):
        self._target = max(0.0, min(100.0, pct))
        self._text = text

    def _animate(self):
        if abs(self._value - self._target) > 0.4:
            self._value += (self._target - self._value) * 0.18
            self.update()
        else:
            self._value = self._target

    def _bar_color(self) -> QColor:
        if self._value > 85:
            return qcol(C.RED)
        if self._value > 65:
            return qcol(C.ACC)
        return qcol(self._color)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W * 0.36, H / 2
        radius = min(W * 0.28, H * 0.38)
        bar_col = self._bar_color()

        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(4, 4, 28, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(W - 58, 6, 54, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        track = _hex_path(cx, cy, radius)
        p.setPen(QPen(qcol(C.BORDER, 70), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(track)

        filled_edges = int((self._value / 100.0) * 6 + 0.001)
        partial = ((self._value / 100.0) * 6) % 1.0
        rot = -90
        for edge in range(filled_edges + (1 if partial > 0.01 and filled_edges < 6 else 0)):
            i = edge % 6
            t0 = math.radians(rot + i * 60)
            t1 = math.radians(rot + (i + 1) * 60)
            p0 = QPointF(cx + radius * math.cos(t0), cy + radius * math.sin(t0))
            p1 = QPointF(cx + radius * math.cos(t1), cy + radius * math.sin(t1))
            if edge < filled_edges:
                frac = 1.0
            else:
                frac = partial
            mid = QPointF(p0.x() + (p1.x() - p0.x()) * frac, p0.y() + (p1.y() - p0.y()) * frac)
            glow = QColor(bar_col)
            glow.setAlpha(50)
            p.setPen(QPen(glow, 4))
            p.drawLine(p0, mid)
            p.setPen(QPen(bar_col, 2))
            p.drawLine(p0, mid)

        inner_r = radius * 0.55
        p.setBrush(QBrush(qcol("#000c14", 220)))
        p.setPen(QPen(qcol(C.BORDER, 50), 1))
        p.drawPath(_hex_path(cx, cy, inner_r))

        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(bar_col, 1))
        pct_txt = f"{self._value:.0f}" if self._text != "--" else "--"
        p.drawText(QRectF(cx - inner_r, cy - 8, inner_r * 2, 16),
                   Qt.AlignmentFlag.AlignCenter, pct_txt)

        p.setPen(QPen(qcol(C.BORDER, 50), 1))
        p.drawLine(QPointF(4, H - 3), QPointF(W - 4, H - 3))


class SegmentTelemetryBar(QWidget):
    def __init__(self, label: str, color: str = C.PRI, segments: int = 10, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._segments = segments
        self._value = 0.0
        self._text = "--"
        self.setFixedHeight(38)

    def set_color(self, color: str):
        self._color = color
        self.update()

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        bar_col = qcol(self._color)
        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)

        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(6, 2, 30, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(W - 62, 2, 56, 12),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        seg_area = QRectF(8, 18, W - 16, 12)
        gap = 2
        seg_w = (seg_area.width() - gap * (self._segments - 1)) / self._segments
        lit = int((self._value / 100.0) * self._segments + 0.001)

        for i in range(self._segments):
            x = seg_area.x() + i * (seg_w + gap)
            seg_rect = _chamfer_rect(x, seg_area.y(), seg_w, seg_area.height(), 2)
            if i < lit:
                glow = QColor(bar_col)
                glow.setAlpha(35)
                p.setBrush(QBrush(glow))
                p.setPen(QPen(bar_col, 1))
            else:
                p.setBrush(QBrush(qcol("#010c12")))
                p.setPen(QPen(qcol(C.BORDER, 45), 1))
            p.drawPath(seg_rect)


class TelemetryReadout(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list[tuple[str, str]] = []
        self.setFixedHeight(58)

    def set_line(self, index: int, key: str, value: str, color: str = C.TEXT_MED):
        while len(self._lines) <= index:
            self._lines.append(("", "", C.TEXT_MED))
        self._lines[index] = (key, value, color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(4, 2, W - 8, H - 4)
        p.setBrush(QBrush(qcol("#000810", 210)))
        p.setPen(QPen(qcol(C.BORDER, 80), 1))
        p.drawPath(_chamfer_rect(rect.x(), rect.y(), rect.width(), rect.height(), 4))

        y = 10
        for key, val, col in self._lines:
            p.setFont(QFont("Consolas", 6))
            p.setPen(QPen(qcol(C.TEXT_DIM), 1))
            p.drawText(QRectF(10, y, 36, 12),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, key)
            p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
            p.setPen(QPen(qcol(col), 1))
            p.drawText(QRectF(46, y, W - 54, 12),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, val)
            y += 14


class StatusLedColumn(QWidget):
    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._items = [(label, color) for label, color in items]
        self._phase = 0.0
        self._colors = [color for _, color in items]
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(60)
        self.setFixedHeight(22 * len(items) + 8)

    def set_colors(self, colors: list[str]):
        self._colors = colors
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.12) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rail_x = 14.0

        p.setPen(QPen(qcol(C.BORDER, 60), 1))
        p.drawLine(QPointF(rail_x, 6), QPointF(rail_x, H - 6))

        row_h = 22
        for i, (label, _) in enumerate(self._items):
            y = 10 + i * row_h
            col = qcol(self._colors[i] if i < len(self._colors) else C.TEXT_DIM)
            pulse = 0.7 + 0.3 * math.sin(self._phase + i * 0.8)
            glow = QColor(col)
            glow.setAlpha(int(50 * pulse))
            p.setBrush(QBrush(glow))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(rail_x, y), 7, 7)
            p.setBrush(QBrush(col))
            p.drawEllipse(QPointF(rail_x, y), 4, 4)

            p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
            p.setPen(QPen(col, 1))
            p.drawText(QRectF(26, y - 7, W - 30, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

            if i < len(self._items) - 1:
                p.setPen(QPen(qcol(C.BORDER, 40), 1))
                p.drawLine(QPointF(rail_x, y + 8), QPointF(rail_x, y + row_h - 8))


class OrbitalCommandStrip(QWidget):
    """Asymmetric command strip — no legacy badge / dotted title layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFixedHeight(54)
        self._phase = 0.0
        self._accent = C.PRI
        self._blend = 0.0
        self._time_str = "00:00:00"
        self._date_str = ""
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(40)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def set_blend(self, blend: float):
        self._blend = max(0.0, min(1.0, blend))
        self.update()

    def set_clock(self, time_str: str, date_str: str):
        self._time_str = time_str
        self._date_str = date_str
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.08) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        accent = QColor(self._accent)
        if self._blend > 0.01:
            standby = QColor(C.STANDBY_C)
            accent = QColor(
                int(accent.red()   + (standby.red()   - accent.red())   * self._blend),
                int(accent.green() + (standby.green() - accent.green()) * self._blend),
                int(accent.blue()  + (standby.blue()  - accent.blue())  * self._blend),
            )

        p.fillRect(self.rect(), qcol("#000408"))

        rail_w = 148
        rail = QLinearGradient(0, 0, rail_w, 0)
        rail.setColorAt(0.0, qcol("#001018"))
        rail.setColorAt(1.0, qcol("#000408", 0))
        p.fillRect(QRectF(0, 0, rail_w, H), QBrush(rail))

        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 200), 2))
        p.drawLine(QPointF(0, H - 1), QPointF(W, H - 1))
        for i in range(0, int(W), 14):
            h = 4 if i % 28 == 0 else 2
            p.drawLine(QPointF(i, H - 1), QPointF(i, H - 1 - h))

        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 90), 1))
        p.drawLine(QPointF(18, 6), QPointF(18, H - 8))
        for y in range(12, H - 10, 9):
            w = 6 + int(4 * math.sin(self._phase + y * 0.1))
            p.drawLine(QPointF(18 - w, y), QPointF(18 + w, y))

        p.setFont(QFont("Consolas", 5, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(28, 10, 100, 10),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "SESSION // ACTIVE")
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(accent, 1))
        p.drawText(QRectF(28, 22, 110, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "MARK-39 CORE")

        tick = int((self._phase * 30) % 999)
        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(28, 36, 110, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"SYNC {tick:03d} · SECURE")

        cx = W * 0.46
        wing = 120
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 60), 1))
        p.drawLine(QPointF(cx - wing, 16), QPointF(cx - 40, 16))
        p.drawLine(QPointF(cx + 40, 16), QPointF(cx + wing, 16))
        p.drawLine(QPointF(cx - wing, H - 14), QPointF(cx - 50, H - 14))
        p.drawLine(QPointF(cx + 50, H - 14), QPointF(cx + wing, H - 14))

        p.setFont(QFont("Consolas", 22, QFont.Weight.Bold))
        p.setPen(QPen(accent, 1))
        title = "JARVIS"
        tw = p.fontMetrics().horizontalAdvance(title)
        p.drawText(QRectF(cx - tw / 2, 10, tw, 30), Qt.AlignmentFlag.AlignCenter, title)

        beam_y = 42
        beam_w = 90
        bx = cx - beam_w / 2 + (self._phase * 18) % (beam_w - 20)
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 180), 2))
        p.drawLine(QPointF(cx - beam_w / 2, beam_y), QPointF(bx, beam_y))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 80), 1))
        p.drawLine(QPointF(bx + 20, beam_y), QPointF(cx + beam_w / 2, beam_y))

        rx = W - 20
        box_w, box_h = 22, 26
        bx0 = rx - box_w * 4 - 18
        by0 = (H - box_h) / 2
        digits = self._time_str.replace(":", "")
        for i, d in enumerate(digits[:6]):
            x = bx0 + i * (box_w + 3)
            p.setBrush(QBrush(qcol("#000c14")))
            p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 100), 1))
            p.drawPath(_chamfer_rect(x, by0, box_w, box_h, 3))
            p.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            p.setPen(QPen(accent, 1))
            p.drawText(QRectF(x, by0, box_w, box_h), Qt.AlignmentFlag.AlignCenter, d)

        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(bx0, H - 12, box_w * 4 + 18, 10),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._date_str)

        sweep = (self._phase * 50) % W
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 35), 1))
        p.drawLine(QPointF(sweep, 0), QPointF(sweep - 40, H))


class BootSequenceOverlay(QWidget):
    """Full startup initialization sequence on first boot."""

    finished = pyqtSignal()

    _PHASES = (
        (0.18, "POWERING CORE SYSTEMS"),
        (0.38, "LOADING NEURAL MATRIX"),
        (0.58, "CALIBRATING SENSORS"),
        (0.78, "ESTABLISHING UPLINK"),
        (1.00, "MARK XXXIX — ONLINE"),
    )

    def __init__(self, hud: HudCanvas, parent=None):
        super().__init__(parent)
        self._hud = hud
        self._progress = 0.0
        self._fade = 1.0
        self._phase = 0.0
        self._hex_rot = 0.0
        self._scan_y = 0.0
        self._log_lines: list[str] = []
        self._running = False
        self._fading = False
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self.hide()

    def start(self):
        self._progress = 0.0
        self._fade = 1.0
        self._fading = False
        self._running = True
        self._log_lines = []
        self._hud.state = "INITIALISING"
        self._hud._wake_up_active = True
        self._hud._wake_up_external = True
        self._hud.set_wake_up_progress(0.0)
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self._tmr.start(18)

    def _current_phase_text(self) -> str:
        for threshold, label in self._PHASES:
            if self._progress <= threshold:
                return label
        return self._PHASES[-1][1]

    def _step(self):
        if self._fading:
            self._fade = max(0.0, self._fade - 0.07)
            self.update()
            if self._fade <= 0.0:
                self._tmr.stop()
                self._running = False
                self._fading = False
                self.hide()
                self.finished.emit()
            return

        self._progress = min(1.0, self._progress + 0.009)
        self._phase = (self._phase + 0.1) % (2 * math.pi)
        self._hex_rot = (self._hex_rot + 1.4) % 360
        self._scan_y = (self._scan_y + 2.5) % max(self.height(), 1)
        self._hud.set_wake_up_progress(self._progress)

        phase = self._current_phase_text()
        if not self._log_lines or not self._log_lines[-1].endswith(phase):
            self._log_lines.append(f"[{int(self._progress * 100):03d}%] {phase}")
            if len(self._log_lines) > 6:
                self._log_lines.pop(0)

        self.update()

        if self._progress >= 1.0:
            self._fading = True

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        opacity = self._fade

        p.fillRect(self.rect(), qcol("#000408", int(240 * opacity)))

        p.setPen(QPen(qcol(C.BORDER, int(40 * opacity)), 1))
        for gx in range(0, W, 28):
            p.drawLine(QPointF(gx, 0), QPointF(gx, H))
        for gy in range(0, H, 28):
            p.drawLine(QPointF(0, gy), QPointF(W, gy))

        scan_a = int(50 * opacity)
        p.setPen(QPen(qcol(C.PRI, scan_a), 1))
        p.drawLine(QPointF(0, self._scan_y), QPointF(W, self._scan_y))

        cx, cy = W / 2, H * 0.42
        pulse = 0.5 + 0.5 * math.sin(self._phase)

        for i, rad_f in enumerate((0.22, 0.32, 0.42, 0.52)):
            r = min(W, H) * rad_f * (0.35 + 0.65 * self._progress)
            ring = _hex_path(cx, cy, r, self._hex_rot + i * 22)
            a = int((60 + i * 25) * pulse * opacity)
            p.setPen(QPen(qcol(C.PRI, a), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(ring)

        p.setBrush(QBrush(qcol(C.PRI, int(30 * opacity))))
        p.setPen(QPen(qcol(C.PRI, int(180 * opacity)), 2))
        core_r = min(W, H) * 0.08 * (0.4 + 0.6 * self._progress)
        p.drawPath(_hex_path(cx, cy, core_r, -self._hex_rot * 1.5))

        p.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI, int(255 * opacity)), 1))
        title = "SYSTEM INITIALISATION"
        tw = p.fontMetrics().horizontalAdvance(title)
        p.drawText(QRectF(cx - tw / 2, cy - min(W, H) * 0.34, tw, 20),
                   Qt.AlignmentFlag.AlignCenter, title)

        phase = self._current_phase_text()
        p.setFont(QFont("Consolas", 8))
        p.setPen(QPen(qcol(C.TEXT_MED, int(220 * opacity)), 1))
        pw = p.fontMetrics().horizontalAdvance(phase)
        p.drawText(QRectF(cx - pw / 2, cy + min(W, H) * 0.30, pw, 18),
                   Qt.AlignmentFlag.AlignCenter, phase)

        bar_w = min(420, W - 80)
        bar_x = cx - bar_w / 2
        bar_y = cy + min(W, H) * 0.38
        bar_h = 8
        p.setBrush(QBrush(qcol("#001018", int(200 * opacity))))
        p.setPen(QPen(qcol(C.BORDER, int(100 * opacity)), 1))
        p.drawPath(_chamfer_rect(bar_x, bar_y, bar_w, bar_h, 3))

        fill_w = bar_w * self._progress
        if fill_w > 2:
            fill_grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
            fill_grad.setColorAt(0.0, qcol(C.PRI_DIM, int(200 * opacity)))
            fill_grad.setColorAt(1.0, qcol(C.PRI, int(255 * opacity)))
            p.setBrush(QBrush(fill_grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(_chamfer_rect(bar_x, bar_y, fill_w, bar_h, 3))

        pct = f"{int(self._progress * 100)}%"
        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI, int(200 * opacity)), 1))
        p.drawText(QRectF(bar_x, bar_y + 14, bar_w, 14),
                   Qt.AlignmentFlag.AlignCenter, pct)

        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM, int(180 * opacity)), 1))
        ly = H * 0.72
        for i, line in enumerate(self._log_lines[-5:]):
            p.drawText(QRectF(40, ly + i * 14, W - 80, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)

        p.setPen(QPen(qcol(C.BORDER_B, int(90 * opacity)), 1))
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            ox = 16 if sx < 0 else W - 28
            oy = 16 if sy < 0 else H - 28
            p.drawLine(QPointF(ox, oy), QPointF(ox + 14 * sx, oy))
            p.drawLine(QPointF(ox, oy), QPointF(ox, oy + 14 * sy))


class StateTransitionOverlay(QWidget):
    """Full-window sweep when switching active ↔ standby."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._progress = 0.0
        self._to_standby = False
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)

    def play(self, to_standby: bool, on_tick=None, on_done=None):
        self._to_standby = to_standby
        self._progress = 0.0
        self._on_tick = on_tick
        self._on_done = on_done
        self.show()
        self.raise_()
        self._tmr.start(16)

    def _step(self):
        self._progress = min(1.0, self._progress + 0.045)
        if self._on_tick:
            self._on_tick(self._progress, self._to_standby)
        self.update()
        if self._progress >= 1.0:
            self._tmr.stop()
            self.hide()
            if self._on_done:
                self._on_done()

    def paintEvent(self, _):
        if self._progress <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        col = C.STANDBY_C if self._to_standby else C.PRI
        alpha = int(55 * math.sin(self._progress * math.pi))
        p.fillRect(self.rect(), qcol(col, alpha))
        y = int(H * self._progress)
        grad = QLinearGradient(0, y - 40, 0, y + 40)
        grad.setColorAt(0.0, qcol(col, 0))
        grad.setColorAt(0.5, qcol(col, 140))
        grad.setColorAt(1.0, qcol(col, 0))
        p.fillRect(QRectF(0, y - 40, W, 80), QBrush(grad))
        p.setPen(QPen(qcol(col, 200), 2))
        p.drawLine(QPointF(0, y), QPointF(W, y))


class SysTerminalHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._accent = C.ACC2
        self._line = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(55)
        self.setFixedHeight(38)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.12) % (2 * math.pi)
        self._line = (self._line + 1) % 999
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(4, 2, W - 8, H - 4)

        p.setBrush(QBrush(qcol("#000a0c", 220)))
        p.setPen(QPen(qcol(self._accent, 90), 1))
        p.drawPath(_chamfer_rect(rect.x(), rect.y(), rect.width(), rect.height(), 5))

        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(self._accent), 1))
        p.drawText(QRectF(10, 6, W - 20, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "CORE TERMINAL // SYS STREAM")

        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(10, 20, W - 20, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"LN {self._line:04d} · DAEMON OUTPUT")

        bx = W - 54
        for i in range(6):
            h = 2 + abs(math.sin(self._phase + i * 0.8)) * 7
            p.setPen(QPen(qcol(self._accent, 130), 1.5))
            p.drawLine(QPointF(bx + i * 4, H / 2 - h / 2), QPointF(bx + i * 4, H / 2 + h / 2))


class RightPanelRail(QWidget):
    """Mirrored tactical rail for the command / comms side."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._phase = 0.0
        self._scan_y = 0.0
        self._accent = C.PRI
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(40)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.055) % (2 * math.pi)
        self._scan_y = (self._scan_y + 1.0) % max(self.height(), 1)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        grad = QLinearGradient(W, 0, 0, 0)
        grad.setColorAt(0.0, qcol("#000408"))
        grad.setColorAt(0.12, qcol("#000c14"))
        grad.setColorAt(1.0, qcol(C.DARK))
        p.fillRect(self.rect(), QBrush(grad))

        spine_x = W - 11.0
        pulse = 0.55 + 0.45 * math.sin(self._phase)
        spine_grad = QLinearGradient(spine_x, 0, spine_x, H)
        spine_grad.setColorAt(0.0, qcol(self._accent, 0))
        spine_grad.setColorAt(0.2, qcol(self._accent, int(80 * pulse)))
        spine_grad.setColorAt(0.5, qcol(self._accent, int(170 * pulse)))
        spine_grad.setColorAt(0.8, qcol(self._accent, int(80 * pulse)))
        spine_grad.setColorAt(1.0, qcol(self._accent, 0))
        p.setPen(QPen(QBrush(spine_grad), 2))
        p.drawLine(QPointF(spine_x, 8), QPointF(spine_x, H - 8))

        for y in range(22, H - 14, 16):
            tick_a = 45 + int(35 * math.sin(self._phase + y * 0.07))
            p.setPen(QPen(qcol(self._accent, tick_a), 1))
            p.drawLine(QPointF(spine_x - 3, y), QPointF(spine_x + 3, y))

        p.setPen(QPen(qcol(C.BORDER, 28), 1))
        for gx in range(0, W - 20, 16):
            p.drawLine(QPointF(gx, 0), QPointF(gx, H))
        for gy in range(0, H, 16):
            p.drawLine(QPointF(0, gy), QPointF(W - 18, gy))

        scan_a = int(30 + 20 * math.sin(self._phase * 2.2))
        p.setPen(QPen(qcol(self._accent, scan_a), 1))
        p.drawLine(QPointF(4, self._scan_y), QPointF(W - 20, self._scan_y))

        p.setPen(QPen(qcol(C.BORDER_B, 110), 1))
        p.drawLine(QPointF(0, 0), QPointF(0, H))
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            ox = 4 if sx < 0 else W - 14
            oy = 4 if sy < 0 else H - 14
            p.drawLine(QPointF(ox, oy), QPointF(ox + 9 * sx, oy))
            p.drawLine(QPointF(ox, oy), QPointF(ox, oy + 9 * sy))


class NeuralLinkHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._accent = C.PRI
        self._packet = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(50)
        self.setFixedHeight(40)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.14) % (2 * math.pi)
        if random.random() < 0.15:
            self._packet = (self._packet + random.randint(1, 9)) % 9999
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        rect = QRectF(4, 2, W - 8, H - 4)

        p.setBrush(QBrush(qcol("#000a10", 215)))
        p.setPen(QPen(qcol(self._accent, 100), 1))
        p.drawPath(_chamfer_rect(rect.x(), rect.y(), rect.width(), rect.height(), 6))

        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(self._accent), 1))
        p.drawText(QRectF(10, 6, W - 20, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "NEURAL LINK // COMMS")

        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(10, 20, W - 70, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"PKT {self._packet:04d} · ENCRYPTED")

        wave_x = W - 58
        wave_y = H / 2 + 1
        for i in range(8):
            h = 3 + abs(math.sin(self._phase + i * 0.7)) * 8
            col = qcol(self._accent, int(120 + 80 * math.sin(self._phase + i)))
            p.setPen(QPen(col, 2))
            p.drawLine(QPointF(wave_x + i * 5, wave_y - h / 2),
                       QPointF(wave_x + i * 5, wave_y + h / 2))


class CommsViewport(QWidget):
    """Framed comms terminal — log sits inside a painted HUD shell."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = C.PRI
        self._phase = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(55)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 16, 12)
        lay.setSpacing(0)
        self.log = LogWidget(realtime=True)
        self.log.setStyleSheet("""
            QTextEdit {
                background: transparent;
                border: none;
                padding: 8px 10px;
                line-height: 1.45;
                selection-background-color: #001f2e;
            }
            QScrollBar:vertical { width: 0px; background: transparent; }
            QScrollBar::handle:vertical { background: transparent; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        self.log.setFont(QFont("Consolas", 9))
        lay.addWidget(self.log)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.09) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        outer = QRectF(3, 2, W - 6, H - 4)
        inner = QRectF(8, 8, W - 16, H - 16)

        p.setBrush(QBrush(qcol("#00060c", 230)))
        p.setPen(QPen(qcol(self._accent, 90), 1))
        p.drawPath(_chamfer_rect(outer.x(), outer.y(), outer.width(), outer.height(), 8))

        p.setPen(QPen(qcol(C.BORDER, 50), 1))
        p.drawPath(_chamfer_rect(inner.x(), inner.y(), inner.width(), inner.height(), 5))

        for i in range(5):
            y = inner.y() + 6 + i * ((inner.height() - 12) / 4)
            dash_x = inner.x() + 4 + (self._phase * 12 + i * 18) % (inner.width() - 12)
            p.setPen(QPen(qcol(self._accent, 35), 1))
            p.drawPoint(QPointF(dash_x, y))

        p.setPen(QPen(qcol(self._accent, 160), 1.5))
        for corner, ox, oy in [("tl", inner.left(), inner.top()),
                               ("tr", inner.right(), inner.top()),
                               ("bl", inner.left(), inner.bottom()),
                               ("br", inner.right(), inner.bottom())]:
            dx = 1 if "l" in corner else -1
            dy = 1 if "t" in corner else -1
            p.drawLine(QPointF(ox, oy), QPointF(ox + 10 * dx, oy))
            p.drawLine(QPointF(ox, oy), QPointF(ox, oy + 10 * dy))

        stream_y = inner.bottom() - 4
        stream_x = inner.x() + 6 + (self._phase * 22) % (inner.width() - 14)
        p.setPen(QPen(qcol(self._accent, 120), 1))
        p.drawLine(QPointF(stream_x, stream_y), QPointF(stream_x + 8, stream_y))


class SectionTag(QWidget):
    def __init__(self, text: str, accent: str = C.ACC, parent=None):
        super().__init__(parent)
        self._text = text
        self._accent = accent
        self.setFixedHeight(18)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        p.setPen(QPen(qcol(self._accent), 1))
        p.drawLine(QPointF(6, 14), QPointF(18, 14))
        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.drawText(QRectF(22, 2, W - 24, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
        p.setPen(QPen(qcol(C.BORDER, 60), 1))
        p.drawLine(QPointF(22, 15), QPointF(W - 6, 15))


class DataVaultPort(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(92)
        self._current_file: str | None = None
        self._hovering = False
        self._drag_over = False
        self._accent = C.ACC
        self._rotation = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._animate)
        self._tmr.start(35)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._canvas = _VaultCanvas(self)
        lay.addWidget(self._canvas)

    def set_accent(self, color: str):
        self._accent = color
        self._canvas.update()

    def _animate(self):
        self._rotation = (self._rotation + (1.8 if self._drag_over else 0.6)) % 360
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True
            self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self._canvas.update()

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
        self._hovering = True
        self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False
        self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._canvas.update()

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


class _VaultCanvas(QWidget):
    def __init__(self, port: DataVaultPort):
        super().__init__(port)
        self._p = port

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z = self._p
        W, H = self.width(), self.height()
        cx, cy = W * 0.28, H / 2
        accent = z._accent
        portal_r = min(H * 0.38, 34)

        bg = QRectF(4, 4, W - 8, H - 8)
        p.setBrush(QBrush(qcol("#000810", 220)))
        p.setPen(QPen(qcol(accent, 70 if not z._drag_over else 140), 1))
        p.drawPath(_chamfer_rect(bg.x(), bg.y(), bg.width(), bg.height(), 5))

        outer = _hex_path(cx, cy, portal_r + 6, z._rotation)
        inner = _hex_path(cx, cy, portal_r - 4, -z._rotation * 0.6)
        ring_col = qcol(accent, 200 if z._drag_over else (120 if z._hovering else 70))
        p.setPen(QPen(ring_col, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(outer)
        p.setPen(QPen(qcol(accent, 50), 1))
        p.drawPath(inner)

        if z._current_file:
            self._paint_loaded(p, W, H, cx, cy, portal_r)
        elif z._drag_over:
            self._paint_ingest(p, W, H, cx, cy, portal_r, accent)
        else:
            self._paint_idle(p, W, H, cx, cy, portal_r, accent)

    def _paint_idle(self, p, W, H, cx, cy, r, accent):
        p.setBrush(QBrush(qcol(accent, 25)))
        p.setPen(QPen(qcol(accent, 100), 1))
        p.drawPath(_hex_path(cx, cy, r * 0.55))
        p.setFont(QFont("Consolas", 14, QFont.Weight.Bold))
        p.setPen(QPen(qcol(accent), 1))
        p.drawText(QRectF(cx - r, cy - 10, r * 2, 20), Qt.AlignmentFlag.AlignCenter, "◇")
        tx = cx + r + 14
        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.drawText(QRectF(tx, cy - 18, W - tx - 8, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "INJECT PAYLOAD")
        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, cy - 2, W - tx - 8, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "DROP · CLICK · SCAN")

    def _paint_ingest(self, p, W, H, cx, cy, r, accent):
        for i in range(3):
            rr = r + 8 + i * 7
            a = int(90 - i * 25)
            p.setPen(QPen(qcol(accent, a), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(_hex_path(cx, cy, rr, self._p._rotation + i * 20))
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(accent), 1))
        p.drawText(QRectF(cx + r + 10, cy - 8, W - cx - r - 14, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "INGESTING...")

    def _paint_loaded(self, p, W, H, cx, cy, r):
        path = Path(self._p._current_file)
        cat = _file_category(path)
        _, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str = path.suffix.upper().lstrip(".") or "RAW"

        p.setBrush(QBrush(qcol(icon_col, 40)))
        p.setPen(QPen(qcol(icon_col), 1.5))
        p.drawPath(_hex_path(cx, cy, r * 0.5))
        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(cx - r, cy - 7, r * 2, 14), Qt.AlignmentFlag.AlignCenter, ext_str[:4])

        tx = cx + r + 12
        tw = W - tx - 28
        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 28 else path.name[:25] + "..."
        p.drawText(QRectF(tx, cy - 20, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, cy - 4, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, size_str)
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 32:
            par = "…" + par[-31:]
        p.drawText(QRectF(tx, cy + 10, tw, 11),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setPen(QPen(qcol(C.RED, 160), 1))
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.drawText(QRectF(W - 26, cy - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, "×")

    def mousePressEvent(self, e):
        z = self._p
        if z._current_file and e.pos().x() > self.width() - 30:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class VaultStatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "VAULT EMPTY · AWAITING PAYLOAD"
        self._accent = C.TEXT_DIM
        self._dots = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(400)
        self.setFixedHeight(20)

    def set_text(self, text: str, accent: str = C.GREEN):
        self._text = text
        self._accent = accent
        self.update()

    def set_idle(self):
        self._text = "VAULT EMPTY · AWAITING PAYLOAD"
        self._accent = C.TEXT_DIM
        self.update()

    def _tick(self):
        self._dots = (self._dots + 1) % 4
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        p.setFont(QFont("Consolas", 6))
        p.setPen(QPen(qcol(self._accent), 1))
        suffix = "." * self._dots if "AWAITING" in self._text else ""
        p.drawText(QRectF(8, 2, W - 12, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._text + suffix)


class TransmitConsole(QWidget):
    transmit = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._accent = C.GREEN
        self._phase = 0.0
        self._focused = False
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(60)
        self.setFixedHeight(52)

        self.input = QLineEdit()
        self.input.setPlaceholderText("enter directive...")
        self.input.setFont(QFont("Consolas", 9))
        self.input.setStyleSheet("""
            QLineEdit {
                background: transparent;
                color: #d8f8ff;
                border: none;
                padding: 0px 4px;
            }
        """)
        self.input.installEventFilter(self)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 22, 46, 6)
        lay.addWidget(self.input)

        self._send_btn = QPushButton(self)
        self._send_btn.setFixedSize(34, 34)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setStyleSheet("background: transparent; border: none;")
        self._send_btn.clicked.connect(self.transmit.emit)
        self.input.returnPressed.connect(self.transmit.emit)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def eventFilter(self, obj, event):
        if obj is self.input:
            if event.type() == QEvent.Type.FocusIn:
                self._focused = True
                self.update()
            elif event.type() == QEvent.Type.FocusOut:
                self._focused = False
                self.update()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._send_btn.move(self.width() - 42, self.height() - 38)

    def _tick(self):
        self._phase = (self._phase + 0.1) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        field = QRectF(6, 18, W - 12, H - 22)

        p.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        p.setPen(QPen(qcol(self._accent), 1))
        p.drawText(QRectF(10, 4, W - 16, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "TRANSMIT")

        border_a = 140 if self._focused else 70
        p.setBrush(QBrush(qcol("#000a0e", 230)))
        p.setPen(QPen(qcol(self._accent, border_a), 1))
        p.drawPath(_chamfer_rect(field.x(), field.y(), field.width(), field.height(), 4))

        if self._focused:
            blink = int(180 + 75 * math.sin(self._phase * 3))
            p.setPen(QPen(qcol(self._accent, blink), 1))
            p.drawLine(QPointF(field.x() + 4, field.y() + 3),
                       QPointF(field.right() - 4, field.y() + 3))

        sx = W - 38
        sy = field.center().y()
        send_path = _hex_path(sx, sy, 13)
        p.setBrush(QBrush(qcol(self._accent, 35)))
        p.setPen(QPen(qcol(self._accent), 1.5))
        p.drawPath(send_path)
        p.setPen(QPen(qcol(self._accent), 2))
        p.drawLine(QPointF(sx - 5, sy), QPointF(sx + 6, sy))
        p.drawLine(QPointF(sx + 2, sy - 5), QPointF(sx + 6, sy))
        p.drawLine(QPointF(sx + 2, sy + 5), QPointF(sx + 6, sy))


class GlyphButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, glyph: str, label: str, accent: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._label = label
        self._accent = accent
        self._hover = False
        self._active = False
        self.setFixedSize(72, 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_accent(self, color: str):
        self._accent = color
        self.update()

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2 - 4
        col = qcol(self._accent)
        if self._hover:
            col = QColor(col)
            col.setAlpha(255)
        bg_a = 55 if self._active else (35 if self._hover else 15)
        p.setBrush(QBrush(qcol(self._accent, bg_a)))
        p.setPen(QPen(qcol(self._accent, 160 if self._hover else 90), 1))
        p.drawPath(_chamfer_rect(4, 4, W - 8, H - 10, 5))

        p.setPen(QPen(col, 1.5))
        if self._glyph == "mic":
            p.setBrush(QBrush(qcol(self._accent, 50)))
            p.drawEllipse(QPointF(cx, cy - 2), 5, 7)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(cx, cy + 6), QPointF(cx, cy + 11))
            p.drawLine(QPointF(cx - 5, cy + 11), QPointF(cx + 5, cy + 11))
            if self._active:
                p.setPen(QPen(qcol(C.MUTED_C), 2))
                p.drawLine(QPointF(cx - 7, cy - 7), QPointF(cx + 7, cy + 7))
        elif self._glyph == "halt":
            p.setBrush(QBrush(qcol(C.RED, 60)))
            p.setPen(QPen(qcol(C.RED), 1.5))
            p.drawRect(QRectF(cx - 6, cy - 6, 12, 12))
        elif self._glyph == "expand":
            p.setBrush(Qt.BrushStyle.NoBrush)
            for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
                ox, oy = cx + dx * 8, cy + dy * 6
                p.drawLine(QPointF(ox, oy), QPointF(ox + 5 * dx, oy))
                p.drawLine(QPointF(ox, oy), QPointF(ox, oy + 5 * dy))

        p.setFont(QFont("Consolas", 5, QFont.Weight.Bold))
        p.setPen(QPen(qcol(self._accent if self._hover else C.TEXT_DIM), 1))
        p.drawText(QRectF(0, H - 14, W, 12), Qt.AlignmentFlag.AlignCenter, self._label)


class GlyphControlDeck(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)
        self.mic = GlyphButton("mic", "VOICE", C.GREEN)
        self.halt = GlyphButton("halt", "HALT", C.RED)
        self.expand = GlyphButton("expand", "VIEW", C.TEXT_DIM)
        for btn in (self.mic, self.halt, self.expand):
            lay.addWidget(btn)

    def set_accent(self, color: str):
        self.mic.set_accent(color)

    def set_mute_active(self, muted: bool):
        self.mic.set_accent(C.MUTED_C if muted else C.GREEN)
        self.mic.set_active(muted)
        self.mic._label = "MUTED" if muted else "VOICE"
        self.mic.update()


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)
    _stream_sig = pyqtSignal(str, str)
    _stream_end_sig = pyqtSignal(str)

    def __init__(self, parent=None, *, realtime: bool = False):
        super().__init__(parent)
        self.setReadOnly(True)
        self._realtime = realtime
        self._stream_tag: str | None = None
        self._stream_accum = ""
        self._stream_last_char = ""
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
        self._stream_sig.connect(self._on_stream_chunk)
        self._stream_end_sig.connect(self._on_stream_end)

    def append_log(self, text: str):
        if self._realtime:
            self._append_instant(text)
        else:
            self._sig.emit(text)

    def stream_chunk(self, speaker: str, chunk: str):
        if not chunk:
            return
        if self._realtime:
            self._stream_sig.emit(speaker, chunk)
        else:
            self.append_log(f"{'You' if speaker == 'you' else 'Jarvis'}: {chunk}")

    def stream_finish(self, speaker: str):
        if self._realtime:
            self._stream_end_sig.emit(speaker)

    def _tag_for_speaker(self, speaker: str) -> tuple[str, str]:
        if speaker == "you":
            return "you", "You: "
        return "ai", "Jarvis: "

    def _fmt_for_tag(self, tag: str) -> QColor:
        return {
            "you":  qcol(C.WHITE),
            "ai":   qcol(C.PRI),
            "err":  qcol(C.RED),
            "file": qcol(C.GREEN),
            "sys":  qcol(C.ACC2),
            "info": qcol(C.TEXT_MED),
        }.get(tag, qcol(C.TEXT))

    def _speaker_label(self, tag: str) -> str:
        return {
            "you": "YOU", "ai": "JARVIS", "sys": "SYSTEM",
            "file": "FILE", "err": "ERROR", "info": "INFO",
        }.get(tag, "LOG")

    def _insert_block_header(self, tag: str):
        label = self._speaker_label(tag)
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        rule = f"── {label} " + "─" * max(8, 42 - len(label))
        hdr_fmt = cur.charFormat()
        hdr_fmt.setForeground(QBrush(self._fmt_for_tag(tag)))
        hdr_fmt.setFontWeight(QFont.Weight.Bold)
        cur.insertText(f"\n{rule}\n", hdr_fmt)
        self.setTextCursor(cur)

    def _insert_body(self, tag: str, text: str):
        cur = self.textCursor()
        body_fmt = cur.charFormat()
        body_fmt.setForeground(QBrush(self._fmt_for_tag(tag)))
        body_fmt.setFontWeight(QFont.Weight.Normal)
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(f"  {text.strip()}\n\n", body_fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def _delta_chunk(self, chunk: str) -> str:
        chunk = chunk.replace("\n", " ").strip()
        if not chunk:
            return ""
        if self._stream_accum and chunk.startswith(self._stream_accum):
            return chunk[len(self._stream_accum):]
        if (
            self._stream_last_char
            and self._stream_last_char.isalnum()
            and chunk[0].isalnum()
        ):
            return " " + chunk
        return chunk

    def _append_instant(self, text: str):
        tl = text.lower()
        if   tl.startswith("you:"):    tag, body = "you", text[4:].strip()
        elif tl.startswith("jarvis:"): tag, body = "ai", text[7:].strip()
        elif tl.startswith("file:"):   tag, body = "file", text[5:].strip()
        elif "err" in tl:              tag, body = "err", text
        elif tl.startswith("sys:"):    tag, body = "sys", text[4:].strip()
        else:                          tag, body = "info", text
        self._insert_block_header(tag)
        self._insert_body(tag, body)

    def _on_stream_chunk(self, speaker: str, chunk: str):
        tag, _ = self._tag_for_speaker(speaker)
        delta = self._delta_chunk(chunk)
        if not delta:
            return
        if self._stream_tag != tag:
            self._on_stream_end(speaker)
            self._stream_tag = tag
            self._stream_accum = ""
            self._stream_last_char = ""
            self._insert_block_header(tag)
            cur = self.textCursor()
            body_fmt = cur.charFormat()
            body_fmt.setForeground(QBrush(self._fmt_for_tag(tag)))
            body_fmt.setFontWeight(QFont.Weight.Normal)
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("  ", body_fmt)
            self.setTextCursor(cur)
        cur = self.textCursor()
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(self._fmt_for_tag(tag)))
        fmt.setFontWeight(QFont.Weight.Normal)
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(delta, fmt)
        self.setTextCursor(cur)
        self._stream_accum += delta
        self._stream_last_char = delta[-1]
        self.ensureCursorVisible()

    def _on_stream_end(self, _speaker: str = ""):
        if self._stream_tag is None:
            return
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText("\n\n")
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        self._stream_tag = None
        self._stream_accum = ""
        self._stream_last_char = ""

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
    _chat_stream_sig     = pyqtSignal(str, str)
    _chat_stream_end_sig = pyqtSignal(str)
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

        self._neural_header = NeuralLinkHeader()
        center_col.addWidget(self._neural_header)

        self._comms_viewport = CommsViewport()
        self._comms_viewport.setFixedHeight(172)
        self._log = self._comms_viewport.log
        center_col.addWidget(self._comms_viewport, stretch=0)

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
        self._chat_stream_sig.connect(self._log.stream_chunk)
        self._chat_stream_end_sig.connect(self._log.stream_finish)
        self._set_center_accent(C.PRI)
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

        self._state_blend = 0.0
        self._state_target = 0.0
        self._pending_state = "LISTENING"
        self._boot_complete = False
        self._boot_running = False
        self._transition_overlay = StateTransitionOverlay(central)
        self._transition_overlay.setGeometry(central.rect())
        self._boot_overlay = BootSequenceOverlay(self.hud, central)
        self._boot_overlay.setGeometry(central.rect())
        self._boot_overlay.finished.connect(self._on_boot_finished)
        central.installEventFilter(self)

        if self._ready:
            QTimer.singleShot(250, self._start_boot_sequence)

    def eventFilter(self, obj, event):
        if obj is self.centralWidget() and event.type() == QEvent.Type.Resize:
            rect = self.centralWidget().rect()
            self._transition_overlay.setGeometry(rect)
            self._boot_overlay.setGeometry(rect)
        return super().eventFilter(obj, event)

    def _start_boot_sequence(self):
        if self._boot_running or self._boot_complete:
            return
        self._boot_running = True
        self._apply_state("INITIALISING")
        self._boot_overlay.start()

    def _on_boot_finished(self):
        self._boot_running = False
        self._boot_complete = True
        self.hud.set_wake_up_progress(1.0)
        self._log_sig.emit("SYS: Boot sequence complete.")
        self._apply_state("LISTENING")

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
            self._telemetry.set_line(0, "UP", f"{h:02d}:{m:02d}", C.GREEN)
        except Exception:
            self._telemetry.set_line(0, "UP", "--:--", C.TEXT_DIM)

        try:
            proc_count = len(psutil.pids())
            self._telemetry.set_line(1, "PROC", str(proc_count), C.TEXT_MED)
        except Exception:
            self._telemetry.set_line(1, "PROC", "--", C.TEXT_DIM)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        self._telemetry.set_line(2, "OS", os_name, C.ACC2)


    def _build_header(self) -> QWidget:
        self._header = OrbitalCommandStrip()
        self._tick_clock()
        return self._header

    def _tick_clock(self):
        if hasattr(self, "_header"):
            self._header.set_clock(
                time.strftime("%H:%M:%S"),
                time.strftime("%a %d %b %Y").upper(),
            )

    def _build_left_panel(self) -> QWidget:
        w = LeftPanelRail()
        w.setObjectName("sidePanel")
        w.setFixedWidth(_LEFT_W)

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 8, 12)
        lay.setSpacing(6)

        lay.addWidget(LinkStatusStrip())

        hdr = QLabel("TELEMETRY STACK")
        hdr.setObjectName("panelTitle")
        hdr.setFont(QFont("Consolas", 6, QFont.Weight.Bold))
        hdr.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 2px; padding: 2px 0;"
        )
        lay.addWidget(hdr)

        self._gauge_cpu = HexMetricCell("CPU", C.PRI)
        self._gauge_mem = HexMetricCell("MEM", C.ACC2)
        self._gauge_gpu = HexMetricCell("GPU", C.ACC)
        self._gauge_tmp = HexMetricCell("TMP", "#ff6688")
        for gauge in (self._gauge_cpu, self._gauge_mem, self._gauge_gpu, self._gauge_tmp):
            lay.addWidget(gauge)

        self._bar_net = SegmentTelemetryBar("NET", C.GREEN)
        lay.addWidget(self._bar_net)

        lay.addSpacing(4)

        self._telemetry = TelemetryReadout()
        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        self._telemetry.set_line(0, "UP", "--:--", C.GREEN)
        self._telemetry.set_line(1, "PROC", "--", C.TEXT_MED)
        self._telemetry.set_line(2, "OS", os_name, C.ACC2)
        lay.addWidget(self._telemetry)

        lay.addStretch()

        self._status_leds = StatusLedColumn([
            ("AI CORE · ACTIVE", C.GREEN),
            ("SEC · CLEARED", C.PRI),
            ("PROTOCOL · XXXIX", C.TEXT_DIM),
        ])
        lay.addWidget(self._status_leds)

        return w

    def _build_right_panel(self) -> QWidget:
        w = RightPanelRail()
        w.setObjectName("rightPanel")
        w.setFixedWidth(_RIGHT_W)
        self._right_rail = w

        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 10, 10)
        lay.setSpacing(8)

        self._terminal_header = SysTerminalHeader()
        lay.addWidget(self._terminal_header)

        self._terminal_viewport = CommsViewport()
        self._activity_log = self._terminal_viewport.log
        lay.addWidget(self._terminal_viewport, stretch=1)

        self._vault_tag = SectionTag("DATA VAULT // PAYLOAD INJECTION", C.ACC)
        lay.addWidget(self._vault_tag)

        self._drop_zone = DataVaultPort()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._vault_status = VaultStatusStrip()
        lay.addWidget(self._vault_status)

        self._transmit = TransmitConsole()
        self._input = self._transmit.input
        self._transmit.transmit.connect(self._send)
        lay.addWidget(self._transmit)

        self._controls = GlyphControlDeck()
        self._controls.mic.clicked.connect(self._toggle_mute)
        self._controls.halt.clicked.connect(self._interrupt)
        self._controls.expand.clicked.connect(self._toggle_fullscreen)
        self._style_mute_btn()
        lay.addWidget(self._controls)

        return w

    def _set_center_accent(self, color: str):
        self._neural_header.set_accent(color)
        self._comms_viewport.set_accent(color)

    def _set_right_accent(self, color: str):
        self._right_rail.set_accent(color)
        self._terminal_header.set_accent(C.ACC2 if color == C.PRI else color)
        self._terminal_viewport.set_accent(C.ACC2 if color == C.PRI else color)
        self._vault_tag.set_accent(C.ACC if color == C.PRI else color)
        self._drop_zone.set_accent(C.ACC if color == C.PRI else color)
        self._transmit.set_accent(C.GREEN if color == C.PRI else color)
        self._controls.set_accent(color)

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
        size = _fmt_size(p.stat().st_size)
        self._vault_status.set_text(
            f"PAYLOAD LOCKED · {p.name.upper()[:22]} · {size}", C.GREEN
        )
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
        self._controls.set_mute_active(self._muted)

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

    def _lerp_color(self, a: str, b: str, t: float) -> str:
        ca, cb = QColor(a), QColor(b)
        return QColor(
            int(ca.red()   + (cb.red()   - ca.red())   * t),
            int(ca.green() + (cb.green() - ca.green()) * t),
            int(ca.blue()  + (cb.blue()  - ca.blue())  * t),
        ).name()

    def _apply_accent_blend(self, blend: float):
        accent = self._lerp_color(C.PRI, C.STANDBY_C, blend)
        border = self._lerp_color(C.BORDER_B, C.STANDBY_C, blend)
        self.setStyleSheet(f"""
            QMainWindow {{ background: {C.BG}; }}
            QWidget#sidePanel {{ border-right: 1px solid {border}; }}
            QWidget#rightPanel {{ border-left: 1px solid {border}; }}
            QLabel#panelTitle {{ color: {accent}; }}
        """)
        self._header.set_blend(blend)
        self._header.set_accent(C.PRI)
        self.hud.set_standby_blend(blend)
        if blend > 0.5:
            for gauge in [self._gauge_cpu, self._gauge_mem, self._gauge_gpu, self._gauge_tmp]:
                gauge.set_color(C.STANDBY_C)
            self._bar_net.set_color(C.STANDBY_C)
            self._status_leds.set_colors([C.STANDBY_C, C.STANDBY_C, C.TEXT_DIM])
            self._set_center_accent(C.STANDBY_C)
            self._set_right_accent(C.STANDBY_C)
        else:
            self._gauge_cpu.set_color(C.PRI)
            self._gauge_mem.set_color(C.ACC2)
            self._gauge_gpu.set_color(C.ACC)
            self._gauge_tmp.set_color("#ff6688")
            self._bar_net.set_color(C.GREEN)
            self._status_leds.set_colors([C.GREEN, C.PRI, C.TEXT_DIM])
            self._set_center_accent(C.PRI)
            self._set_right_accent(C.PRI)

    def _apply_state(self, state: str):
        self.hud.state = state
        self.hud.speaking = (state == "SPEAKING")
        self._pending_state = state

        to_standby = state == "STANDBY"
        from_standby = self._state_blend > 0.5 and not to_standby
        if to_standby or from_standby or abs(self._state_target - (1.0 if to_standby else 0.0)) > 0.01:
            self._state_target = 1.0 if to_standby else 0.0
            self._transition_overlay.play(
                to_standby,
                on_tick=self._on_state_transition_tick,
                on_done=self._finish_state_transition,
            )
        else:
            self._apply_accent_blend(self._state_target)

    def _on_state_transition_tick(self, progress: float, to_standby: bool):
        if to_standby:
            blend = progress
        else:
            blend = 1.0 - progress
        self._state_blend = blend
        self._apply_accent_blend(blend)

    def _finish_state_transition(self):
        self._state_blend = self._state_target
        self._apply_accent_blend(self._state_blend)

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
        self._chat_sig.emit(f"SYS: Configured. OS={os_name.upper()}.")
        QTimer.singleShot(200, self._start_boot_sequence)

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
        self._vision_start_cb = None
        self._vision_end_cb = None

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

    def stream_chat(self, speaker: str, chunk: str):
        self._win._chat_stream_sig.emit(speaker, chunk)

    def finish_chat_stream(self, speaker: str):
        self._win._chat_stream_end_sig.emit(speaker)

    def vision_started(self):
        if self._vision_start_cb:
            self._vision_start_cb()

    def vision_ended(self):
        if self._vision_end_cb:
            self._vision_end_cb()

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def wait_for_boot(self):
        while not self._win._boot_complete:
            time.sleep(0.05)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
