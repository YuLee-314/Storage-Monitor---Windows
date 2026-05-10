"""History module — records disk usage over time and renders trend charts."""

import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import matplotlib.font_manager as fm

# Use CJK-capable font on Windows (fallback to sans-serif)
_CJK_CANDIDATES = ["Microsoft YaHei", "SimHei", "SimSun", "WenQuanYi Micro Hei", "Noto Sans CJK SC"]
_available = {f.name for f in fm.fontManager.ttflist}
_cjk_font = next((f for f in _CJK_CANDIDATES if f in _available), "sans-serif")
_matplotlib_family = _cjk_font if _cjk_font != "sans-serif" else "sans-serif"

DB_DIR = Path(os.environ.get("APPDATA", "")) / "StorageMonitor"
DB_PATH = DB_DIR / "history.db"


def init_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            drive_letter TEXT NOT NULL,
            total_bytes INTEGER NOT NULL,
            used_bytes INTEGER NOT NULL,
            free_bytes INTEGER NOT NULL,
            usage_percent REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_usage_drive ON usage_log(drive_letter);
    """)
    conn.commit()
    conn.close()


def record_usage(drives):
    """Record current disk usage for all drives into the database."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for d in drives:
            conn.execute(
                "INSERT INTO usage_log (ts, drive_letter, total_bytes, used_bytes, free_bytes, usage_percent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, d.drive_letter, d.total_bytes, d.used_bytes, d.free_bytes, d.usage_percent),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't disrupt the main UI if recording fails


def query_history(drive_letter: str, hours: float):
    """Return (timestamps, usage_percents) for a drive over the given time window."""
    conn = sqlite3.connect(str(DB_PATH))
    since = datetime.now() - timedelta(hours=hours)
    cursor = conn.execute(
        "SELECT ts, usage_percent FROM usage_log "
        "WHERE drive_letter = ? AND ts >= ? "
        "ORDER BY ts ASC",
        (drive_letter, since.strftime("%Y-%m-%d %H:%M:%S")),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return [], []

    times = [datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S") for r in rows]
    vals = [r[1] for r in rows]
    return times, vals


class HistoryChart(QWidget):
    """Widget that shows a line chart of disk usage over time."""

    COLORS = ["#2ecc71", "#3498db", "#f39c12", "#e74c3c", "#9b59b6", "#1abc9c"]

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self._tr = tr

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Controls
        ctrl = QHBoxLayout()

        self.drive_selector = QComboBox()
        self.drive_selector.setMinimumWidth(80)
        ctrl.addWidget(QLabel(tr("label_drive") + ":"))
        ctrl.addWidget(self.drive_selector)

        self.range_selector = QComboBox()
        self.range_selector.addItem(tr("range_24h"), 24)
        self.range_selector.addItem(tr("range_7d"), 24 * 7)
        self.range_selector.addItem(tr("range_30d"), 24 * 30)
        self.range_selector.setCurrentIndex(0)
        ctrl.addWidget(QLabel(tr("label_range") + ":"))
        ctrl.addWidget(self.range_selector)

        self.refresh_btn = QPushButton(tr("refresh_btn"))
        self.refresh_btn.clicked.connect(self._redraw)
        ctrl.addWidget(self.refresh_btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # Matplotlib figure (use CJK-capable font)
        self.figure = Figure(figsize=(6, 3.5), facecolor="#1e1e1e")
        self.figure.subplotpars.update(
            left=0.1, right=0.95, top=0.92, bottom=0.15
        )
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        self._drives: list[str] = []

        self.drive_selector.currentIndexChanged.connect(self._redraw)
        self.range_selector.currentIndexChanged.connect(self._redraw)

        # Auto-refresh timer
        self._timer = QTimer()
        self._timer.timeout.connect(self._redraw)
        self._timer.start(60_000)  # every minute

    def set_tr(self, tr):
        self._tr = tr

    def update_drives(self, drives):
        current = [d.drive_letter for d in drives]
        if current != self._drives:
            self._drives = current
            self.drive_selector.clear()
            for d in current:
                self.drive_selector.addItem(d)
        self._redraw()

    def _redraw(self):
        drive = self.drive_selector.currentText()
        if not drive:
            return

        hours = self.range_selector.currentData()
        times, vals = query_history(drive, hours)

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("#1e1e1e")

        if times and vals:
            ax.plot(times, vals, color="#2ecc71", linewidth=1.5, marker=".", markersize=2)
            ax.fill_between(times, vals, alpha=0.15, color="#2ecc71")

            # Formatting
            if hours <= 24:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 6)))
            elif hours <= 24 * 7:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
                ax.xaxis.set_major_locator(mdates.DayLocator())
            else:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
                ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))

            ax.set_ylabel(self._tr("label_usage_pct"), color="#d4d4d4",
                          fontfamily=_matplotlib_family, fontsize=9)
            ax.set_ylim(0, 100)
            ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

            # Color-coded threshold lines
            ax.axhline(y=90, color="#e74c3c", linestyle="--", linewidth=0.8, alpha=0.5)
            ax.axhline(y=75, color="#f39c12", linestyle="--", linewidth=0.8, alpha=0.5)
        else:
            ax.text(0.5, 0.5, self._tr("status_no_data"),
                    transform=ax.transAxes, ha="center", va="center",
                    color="#888", fontsize=12, fontfamily=_matplotlib_family)

        ax.tick_params(colors="#888", labelsize=8)
        ax.spines["bottom"].set_color("#555")
        ax.spines["left"].set_color("#555")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        self.figure.tight_layout()
        self.canvas.draw()
