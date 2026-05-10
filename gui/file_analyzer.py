"""File Analyzer — sunburst chart using cached Overview data."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
import numpy as np

from gui.cache import get_cache

# CJK font setup
_CJK_CANDIDATES = ["Microsoft YaHei", "SimHei", "SimSun", "WenQuanYi Micro Hei", "Noto Sans CJK SC"]
_available = {f.name for f in fm.fontManager.ttflist}
_MATPLOTLIB_FAMILY = next((f for f in _CJK_CANDIDATES if f in _available), "sans-serif")

COLORS = ["#2ecc71", "#3498db", "#f39c12", "#e74c3c", "#9b59b6", "#1abc9c",
          "#e67e22", "#2c3e50", "#f1c40f", "#e91e63", "#00bcd4", "#ff5722",
          "#8bc34a", "#03a9f4", "#cddc39", "#ff9800"]

POLL_MS = 2000  # refresh sunburst every 2s while data is loading


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class SunburstChart(QWidget):
    """Multi-ring sunburst chart. Click to drill down, back button to go up."""

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self._tr = tr
        self._cache = get_cache()
        self._drive = ""
        self._stack: list[tuple[str, int]] = []  # [(path, total), ...] breadcrumb

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Controls
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
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # Figure
        self.figure = Figure(figsize=(5.5, 5), facecolor="#1e1e1e")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        layout.addWidget(self.canvas)

        # Hint
        self.hint = QLabel(tr("hint_sunburst"))
        self.hint.setFont(QFont("Segoe UI", 9))
        self.hint.setStyleSheet("color: #888;")
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        # Poll timer for cache updates
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._redraw)
        self._poll.start(POLL_MS)

    def set_tr(self, tr):
        self._tr = tr
        self.hint.setText(tr("hint_sunburst"))

    def set_drives(self, drives: list):
        """Called from dashboard refresh to keep drive list current."""
        current = [self.drive_selector.itemText(i)
                   for i in range(self.drive_selector.count())]
        new = [d.drive_letter for d in drives]
        if current != new:
            self.drive_selector.blockSignals(True)
            self.drive_selector.clear()
            for d in drives:
                self.drive_selector.addItem(d.drive_letter)
            self.drive_selector.blockSignals(False)
            # Store total bytes
            for d in drives:
                self._cache.set_total(d.drive_letter, d.total_bytes)

    def _on_drive_changed(self, drive: str):
        if not drive:
            return
        self._drive = drive
        self._stack = [(f"{drive}\\\\", self._cache.get_total(drive))]
        self._redraw()

    def _go_up(self):
        if len(self._stack) <= 1:
            return
        self._stack.pop()
        self._redraw()

    def _on_click(self, event):
        if event.inaxes is None:
            return
        # Find which wedge was clicked
        for i, (wedge, label) in enumerate(getattr(self, '_wedges', [])):
            if wedge.contains_point((event.x, event.y)):
                # Drill into this directory
                path = getattr(self, '_wedge_paths', [""] * len(self._wedges))[i]
                total = getattr(self, '_wedge_sizes', [0] * len(self._wedges))[i]
                if path and self._cache.has(path):
                    self._stack.append((path, total))
                    self._redraw()
                return

    def _redraw(self):
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

        # Breadcrumb
        crumbs = "  →  ".join(
            os.path.basename(p.rstrip("\\")) or p.rstrip("\\")[:2]
            for p, _ in self._stack
        )
        self.breadcrumb.setText(crumbs)

        # Get children from cache
        entry = self._cache.get(current_path)
        if not entry:
            self.hint.setText(self._tr("hint_no_data"))
            self.canvas.draw()
            return

        children = list(entry["children"].values())
        children.sort(key=lambda x: x["size"], reverse=True)

        # Limit to top 20 + "Others"
        MAX_SLICE = 20
        if len(children) > MAX_SLICE:
            others_size = sum(c["size"] for c in children[MAX_SLICE:])
            children = children[:MAX_SLICE]
            if others_size > 0:
                children.append({
                    "size": others_size,
                    "is_dir": True,
                    "path": "",
                    "_name": self._tr("group_others"),
                })

        sizes = [c["size"] for c in children]
        labels = [c.get("_name", c.get("name", "?")) for c in children]
        total_shown = sum(sizes)
        remaining = current_total - total_shown

        if remaining > 0:
            sizes.append(remaining)
            labels.append(self._tr("label_free"))

        if not sizes or total_shown == 0:
            self.hint.setText(self._tr("hint_no_data"))
            self.canvas.draw()
            return

        self.hint.setText(self._tr("hint_sunburst"))

        # Draw rings: inner pie = children, outer ring = selected's children
        # Ring 0 (center): white circle showing current dir name
        # Ring 1: pie of children
        colors = [COLORS[i % len(COLORS)] for i in range(len(sizes))]

        wedges, texts = ax.pie(
            sizes, labels=None, startangle=90, counterclock=False,
            radius=1, wedgeprops={"width": 0.35, "edgecolor": "#1e1e1e", "linewidth": 1},
            colors=colors,
        )

        # Inner white circle
        center = mpatches.Circle((0, 0), 0.65, color="#2d2d2d", ec="#555", lw=1)
        ax.add_patch(center)
        name = os.path.basename(current_path.rstrip("\\")) or current_path[:2]
        ax.text(0, 0, name, ha="center", va="center", fontsize=11,
                color="#d4d4d4", fontfamily=_MATPLOTLIB_FAMILY, fontweight="bold")
        ax.text(0, -0.15, format_bytes(current_total), ha="center", va="center",
                fontsize=8, color="#888")

        # Store wedge info for click handling
        self._wedges = [(w, l) for w, l in zip(wedges, labels)]
        self._wedge_paths = [c.get("path", "") for c in children]
        self._wedge_sizes = [c["size"] for c in children]

        # Second ring: children of selected item (if any)
        if len(self._stack) > 1:
            parent_children = list(entry["children"].values())
            parent_children.sort(key=lambda x: x["size"], reverse=True)
            # Show children for items that have cached data
            ax.pie(
                sizes, labels=None, startangle=90, counterclock=False,
                radius=1.05, wedgeprops={"width": 0.15, "edgecolor": "#1e1e1e", "linewidth": 0.5},
                colors=colors,
            )

        self.figure.tight_layout()
        self.canvas.draw()

    def refresh_from_cache(self):
        self._redraw()


# For os.path import in _redraw
import os


class FileAnalyzerPage(QWidget):
    """Sunburst-based file analyzer using Overview cache."""

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
