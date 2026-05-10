"""File Analyzer — interactive sunburst chart using cached Overview data."""

import os
import subprocess

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm

import threading
import queue as qmod

from gui.cache import get_cache

_CJK_CANDIDATES = ["Microsoft YaHei", "SimHei", "SimSun", "WenQuanYi Micro Hei", "Noto Sans CJK SC"]
_available = {f.name for f in fm.fontManager.ttflist}
_MATPLOTLIB_FAMILY = next((f for f in _CJK_CANDIDATES if f in _available), "sans-serif")

COLORS = ["#2ecc71", "#3498db", "#f39c12", "#e74c3c", "#9b59b6", "#1abc9c",
          "#e67e22", "#e91e63", "#00bcd4", "#ff5722",
          "#8bc34a", "#03a9f4", "#cddc39", "#ff9800", "#795548", "#607d8b"]

POLL_MS = 2000
DOUBLE_CLICK_MS = 350  # window for distinguishing single vs double click


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class SunburstChart(QWidget):
    """Sunburst with hover highlight + tooltip, single-click drill, double-click explorer."""

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self._tr = tr
        self._cache = get_cache()
        self._drive = ""
        self._stack: list[tuple[str, int]] = []
        self._wedges: list[tuple] = []
        self._wedge_paths: list[str] = []
        self._wedge_sizes: list[int] = []
        self._wedge_names: list[str] = []
        self._original_colors: list[str] = []
        self._hover_idx: int = -1
        self._scanning: bool = False
        self._scan_poll: QTimer | None = None
        self._scan_thread: threading.Thread | None = None
        # Double-click detection
        self._click_pending: int = -1
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        ctrl = QHBoxLayout()
        self.drive_selector = QComboBox()
        self.drive_selector.setMinimumWidth(80)
        self.drive_selector.currentTextChanged.connect(self._on_drive_changed)
        ctrl.addWidget(QLabel(tr("label_drive") + ":"))
        ctrl.addWidget(self.drive_selector)

        self.back_btn = QPushButton("← " + tr("btn_back"))
        self.back_btn.clicked.connect(self._go_up)
        self.back_btn.setEnabled(False)
        ctrl.addWidget(self.back_btn)

        self.breadcrumb = QLabel("")
        self.breadcrumb.setStyleSheet("color: #8ab4f8;")
        ctrl.addWidget(self.breadcrumb)

        self.hover_label = QLabel("")
        self.hover_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.hover_label.setStyleSheet("color: #f1c40f;")
        ctrl.addWidget(self.hover_label)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self.figure = Figure(figsize=(5.5, 5), facecolor="#1e1e1e")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_hover)
        layout.addWidget(self.canvas)

        self.hint = QLabel(tr("hint_sunburst"))
        self.hint.setFont(QFont("Segoe UI", 9))
        self.hint.setStyleSheet("color: #888;")
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._redraw)
        self._poll.start(POLL_MS)

    def set_tr(self, tr):
        self._tr = tr
        self.hint.setText(tr("hint_sunburst"))
        self.hover_label.setText("")

    def set_drives(self, drives: list):
        current = [self.drive_selector.itemText(i)
                   for i in range(self.drive_selector.count())]
        new = [d.drive_letter for d in drives]
        if current != new:
            self.drive_selector.blockSignals(True)
            self.drive_selector.clear()
            for d in drives:
                self.drive_selector.addItem(d.drive_letter)
            self.drive_selector.blockSignals(False)
            if drives and not self._drive:
                self.drive_selector.setCurrentIndex(0)
        for d in drives:
            self._cache.set_total(d.drive_letter, d.total_bytes)

    def _on_drive_changed(self, drive: str):
        if not drive:
            return
        self._drive = drive
        path = f"{drive}\\"
        total = self._cache.get_total(drive)
        self._stack = [(path, total)]

        if not self._cache.has(path):
            # No cache yet — start a scan immediately
            self._scan_and_drill(path, total)
        else:
            self._redraw()

    def _go_up(self):
        if len(self._stack) <= 1:
            return
        self._stack.pop()
        self._redraw()

    # ── hover ──────────────────────────────────────────────────────

    def _on_hover(self, event):
        if event.inaxes is None or not self._wedges:
            self._clear_hover()
            return
        found = -1
        for i, (w, _) in enumerate(self._wedges):
            if w.contains_point((event.x, event.y)):
                found = i
                break

        if found != self._hover_idx:
            self._clear_hover()
            if found >= 0:
                self._hover_idx = found
                w = self._wedges[found][0]
                w.set_linewidth(3)
                w.set_edgecolor("#ffffff")
                name = self._wedge_names[found]
                sz = format_bytes(self._wedge_sizes[found])
                self.hover_label.setText(f"{name}    {sz}")
                self.canvas.draw_idle()

    def _clear_hover(self):
        if self._hover_idx >= 0 and self._hover_idx < len(self._wedges):
            w = self._wedges[self._hover_idx][0]
            w.set_linewidth(1)
            w.set_edgecolor("#1e1e1e")
        self._hover_idx = -1
        self.hover_label.setText("")

    # ── click ──────────────────────────────────────────────────────

    def _on_press(self, event):
        if event.inaxes is None or not self._wedges:
            return
        idx = -1
        for i, (w, _) in enumerate(self._wedges):
            if w.contains_point((event.x, event.y)):
                idx = i
                break
        if idx < 0:
            self._clear_hover()
            return

        if self._click_pending == idx:
            # Second click → double click
            self._click_timer.stop()
            self._click_pending = -1
            self._on_double_click(idx)
        else:
            # First click → start timer
            self._click_pending = idx
            self._click_timer.start(DOUBLE_CLICK_MS)

    def _on_single_click(self):
        idx = self._click_pending
        self._click_pending = -1
        if idx < 0 or idx >= len(self._wedge_paths):
            return
        path = self._wedge_paths[idx]
        total = self._wedge_sizes[idx]
        if not path:
            return

        if self._cache.has(path):
            self._stack.append((path, total))
            self._redraw()
        else:
            # Not in cache → scan on demand
            self._scan_and_drill(path, total)

    def _scan_and_drill(self, path: str, total: int):
        """Background scan + drill when done."""
        if self._scanning:
            return  # already scanning
        self._scanning = True

        clean = os.path.basename(path.rstrip("\\")) or path[:2]
        self.hint.setText(f"{self._tr('status_scanning')} {clean}...")

        # Stop any previous poll timer
        if self._scan_poll is not None:
            self._scan_poll.stop()

        def _run():
            try:
                from disk_collector import get_dir_tree_sizes
                entries = get_dir_tree_sizes(path)
                self._cache.put(path, entries, total)
            except Exception:
                pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        self._scan_poll = QTimer(self)
        def _poll():
            if not thread.is_alive():
                self._scan_poll.stop()
                self._scanning = False
                # Only append if path not already the current top
                if not self._stack or self._stack[-1][0] != path:
                    self._stack.append((path, total))
                self._redraw()
        self._scan_poll.timeout.connect(_poll)
        self._scan_poll.start(500)

    def _on_double_click(self, idx: int):
        if idx < 0 or idx >= len(self._wedge_paths):
            return
        path = self._wedge_paths[idx]
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", os.path.normpath(path)])

    # ── redraw ────────────────────────────────────────────────────

    def _redraw(self):
        self._click_pending = -1
        self._hover_idx = -1
        self._wedges = []
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("#1e1e1e")
        ax.set_aspect("equal")
        ax.axis("off")

        if not self._stack:
            self.canvas.draw()
            return

        current_path, current_total = self._stack[-1]
        self.back_btn.setEnabled(len(self._stack) > 1)

        crumbs = "  →  ".join(
            os.path.basename(p.rstrip("\\")) or p.rstrip("\\")[:2]
            for p, _ in self._stack
        )
        self.breadcrumb.setText(crumbs)

        entry = self._cache.get(current_path)
        if not entry:
            self.hint.setText(self._tr("hint_no_data"))
            self.canvas.draw()
            return

        children = list(entry["children"].values())
        children.sort(key=lambda x: x["size"], reverse=True)

        MAX_SLICE = 20
        if len(children) > MAX_SLICE:
            others_size = sum(c["size"] for c in children[MAX_SLICE:])
            children = children[:MAX_SLICE]
            if others_size > 0:
                children.append({
                    "size": others_size, "is_dir": True, "path": "",
                    "_name": self._tr("group_others"),
                })

        sizes = [c["size"] for c in children]
        colors = [COLORS[i % len(COLORS)] for i in range(len(sizes))]
        total_shown = sum(sizes)
        remaining = current_total - total_shown

        sizes_all = list(sizes)
        colors_all = list(colors)
        if remaining > 0:
            sizes_all.append(remaining)
            colors_all.append("#444444")

        if not sizes_all or sum(sizes) == 0:
            self.hint.setText(self._tr("hint_no_data"))
            self.canvas.draw()
            return

        self.hint.setText(self._tr("hint_sunburst"))

        wedges, texts, _autotexts = ax.pie(
            sizes_all, startangle=90, counterclock=False,
            radius=1, wedgeprops={"width": 0.35, "edgecolor": "#1e1e1e", "linewidth": 1},
            colors=colors_all,
            autopct=lambda pct: f'{pct:.1f}%' if pct > 3 else '',
            pctdistance=0.82,
            textprops={"color": "#ffffff", "fontsize": 8, "fontfamily": _MATPLOTLIB_FAMILY},
        )

        # Center
        center = mpatches.Circle((0, 0), 0.65, color="#2d2d2d", ec="#555", lw=1)
        ax.add_patch(center)
        name = os.path.basename(current_path.rstrip("\\")) or current_path[:2]
        ax.text(0, 0, name, ha="center", va="center", fontsize=11,
                color="#d4d4d4", fontfamily=_MATPLOTLIB_FAMILY, fontweight="bold")
        ax.text(0, -0.15, format_bytes(current_total), ha="center", va="center",
                fontsize=8, color="#888")

        # Second ring
        if len(self._stack) > 1:
            ax.pie(
                sizes_all, startangle=90, counterclock=False,
                radius=1.05, wedgeprops={"width": 0.15, "edgecolor": "#1e1e1e", "linewidth": 0.5},
                colors=colors_all,
                autopct=lambda pct: f'{pct:.1f}%' if pct > 5 else '',
                pctdistance=1.07,
                textprops={"color": "#aaaaaa", "fontsize": 6, "fontfamily": _MATPLOTLIB_FAMILY},
            )

        # Store wedge metadata (exclude "free space" wedge)
        n = len(children)
        self._wedges = [(wedges[i], "") for i in range(n)]
        self._wedge_paths = [c.get("path", "") for c in children]
        self._wedge_sizes = [c["size"] for c in children]
        self._wedge_names = [c.get("_name", c.get("name", "?")) for c in children]
        self._original_colors = colors[:n]

        self.figure.tight_layout()
        self.canvas.draw()


class FileAnalyzerPage(QWidget):
    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self._tr = tr
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.chart = SunburstChart(tr)
        layout.addWidget(self.chart)

    def set_tr(self, tr):
        self._tr = tr
        self.chart.set_tr(tr)

    def update_drives(self, drives):
        self.chart.set_drives(drives)
