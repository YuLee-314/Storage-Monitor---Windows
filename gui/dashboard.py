"""Storage Monitor Dashboard — tabbed main window (Overview / History / File Analysis)."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QProgressBar, QFrame, QSystemTrayIcon, QMenu,
    QApplication, QPushButton, QStyle,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QAction, QMouseEvent
from datetime import datetime
import sys

from gui.i18n import make_tr, DRIVE_TYPE_MAP
from gui.settings import load as load_settings, save as save_settings
from gui.history import HistoryChart, init_db, record_usage
from gui.file_analyzer import FileAnalyzerPage
from gui.dir_tree import DriveExpansionPanel, DirTreeWidget
from gui.cache import get_cache


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ── Overview tab ────────────────────────────────────────────────────

class DriveCard(QFrame):
    """Clickable drive card — expands to show directory tree on click."""

    clicked = None  # callback set by OverviewTab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)
        self.setMinimumHeight(100)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self.drive_label = QLabel()
        self.drive_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
        top.addWidget(self.drive_label)

        self.volume_label = QLabel()
        self.volume_label.setFont(QFont("Segoe UI", 10))
        self.volume_label.setStyleSheet("color: #888;")
        top.addWidget(self.volume_label)
        top.addStretch()

        self.type_label = QLabel()
        self.type_label.setFont(QFont("Segoe UI", 9))
        self.type_label.setStyleSheet("color: #888;")
        top.addWidget(self.type_label)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(22)
        layout.addWidget(self.bar)

        bottom = QHBoxLayout()
        self.used_label = QLabel()
        self.used_label.setFont(QFont("Segoe UI", 9))
        bottom.addWidget(self.used_label)
        bottom.addStretch()

        self.free_label = QLabel()
        self.free_label.setFont(QFont("Segoe UI", 9))
        bottom.addWidget(self.free_label)
        bottom.addStretch()

        self.total_label = QLabel()
        self.total_label.setFont(QFont("Segoe UI", 9))
        self.total_label.setStyleSheet("color: #888;")
        bottom.addWidget(self.total_label)
        layout.addLayout(bottom)

        self._tr = None
        self._drive_info = None

        # Children transparent to mouse so clicks always reach the card
        for child in self.children():
            if isinstance(child, (QLabel, QProgressBar)):
                child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_tr(self, tr):
        self._tr = tr

    @property
    def drive_letter(self):
        return self._drive_info.drive_letter if self._drive_info else ""

    @property
    def total_bytes(self):
        return self._drive_info.total_bytes if self._drive_info else 1

    def update_drive(self, info):
        self._drive_info = info
        tr = self._tr or (lambda k: k)
        self.drive_label.setText(info.drive_letter)

        label_text = info.label if info.label else tr("no_label")
        self.volume_label.setText(label_text)

        type_key = DRIVE_TYPE_MAP.get(info.drive_type, "drive_other")
        self.type_label.setText(tr(type_key))

        self.used_label.setText(f"{tr('label_used')}: {format_bytes(info.used_bytes)}")
        self.free_label.setText(f"{tr('label_free')}: {format_bytes(info.free_bytes)}")
        self.total_label.setText(f"{tr('label_total')}: {format_bytes(info.total_bytes)}")

        val = int(info.usage_percent * 10)
        self.bar.setValue(val)

        if info.usage_percent >= 90:
            color = "#e74c3c"
        elif info.usage_percent >= 75:
            color = "#f39c12"
        elif info.usage_percent >= 50:
            color = "#f1c40f"
        else:
            color = "#2ecc71"

        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #3a3a3a;
                border: 1px solid #555;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 2px;
            }}
        """)

    def mousePressEvent(self, event: QMouseEvent):
        if self.clicked:
            self.clicked(self)
        super().mousePressEvent(event)


class OverviewTab(QWidget):
    """Drive cards overview with expandable directory trees."""

    def __init__(self, tr, parent=None):
        super().__init__(parent)
        self._tr = tr

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(6)

        self.status_label = QLabel()
        self.status_label.setFont(QFont("Segoe UI", 9))
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        self.entries_layout = QVBoxLayout()
        self.entries_layout.setSpacing(4)
        layout.addLayout(self.entries_layout)
        layout.addStretch()

        # (drive_card, expansion_panel) tuples
        self._rows: list[tuple[DriveCard, DriveExpansionPanel]] = []
        self._expanded_idx: int = -1

    def set_tr(self, tr):
        self._tr = tr
        for card, panel in self._rows:
            card.set_tr(tr)
            panel.set_tr(tr)

    def _on_card_clicked(self, card: DriveCard):
        for i, (c, panel) in enumerate(self._rows):
            if c is card:
                if self._expanded_idx == i:
                    # Collapse
                    panel.collapse()
                    self._expanded_idx = -1
                else:
                    # Collapse previous
                    if self._expanded_idx >= 0:
                        self._rows[self._expanded_idx][1].collapse()
                    # Expand this one
                    panel.expand()
                    self._expanded_idx = i
                return

    def refresh(self, drives):
        tr = self._tr
        seen_letters = set()

        # Index existing rows by drive letter (track by stored letter, not card property)
        row_map: dict[str, tuple[DriveCard, DriveExpansionPanel]] = {}
        for card, panel in self._rows:
            key = card.drive_letter if card._drive_info else ""
            if key:
                row_map[key] = (card, panel)

        for info in drives:
            seen_letters.add(info.drive_letter)

            if info.drive_letter in row_map:
                card, panel = row_map[info.drive_letter]
            else:
                card = DriveCard()
                card.clicked = self._on_card_clicked
                card.set_tr(tr)
                panel = DriveExpansionPanel(tr, info.drive_letter, info.total_bytes)
                panel.collapse_btn.clicked.connect(lambda c=card: self._on_card_clicked(c))
                self._rows.append((card, panel))
                self.entries_layout.addWidget(card)
                self.entries_layout.addWidget(panel)
                row_map[info.drive_letter] = (card, panel)

            card.update_drive(info)

        # Remove cards for removed drives
        removed = [(c, p) for c, p in self._rows if c.drive_letter not in seen_letters]
        for card, panel in removed:
            self._rows.remove((card, panel))
            self.entries_layout.removeWidget(card)
            self.entries_layout.removeWidget(panel)
            card.deleteLater()
            panel.deleteLater()

        # Update timestamps
        ts = datetime.now().strftime("%H:%M:%S")
        self.status_label.setText(f"{tr('status_updated')}: {ts}")


# ── Main window ─────────────────────────────────────────────────────

class Dashboard(QMainWindow):
    """Tabbed main window: Overview | History | File Analysis."""

    _RECORD_INTERVAL = 5  # record history every N refreshes

    def __init__(self):
        super().__init__()

        self._settings = load_settings()
        self._tr = make_tr(self._settings["language"])

        self.setWindowTitle(self._tr("app_title"))
        self.setMinimumSize(620, 500)
        self.resize(680, 580)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 0)
        root.setSpacing(4)

        # Header
        header = QHBoxLayout()
        self.title_label = QLabel(self._tr("app_title"))
        self.title_label.setFont(QFont("Segoe UI", 18, QFont.Bold))
        header.addWidget(self.title_label)
        header.addStretch()

        self.settings_btn = QPushButton(self._tr("settings"))
        self.settings_btn.clicked.connect(self._show_settings_menu)
        header.addWidget(self.settings_btn)

        self.refresh_btn = QPushButton(self._tr("refresh_btn"))
        self.refresh_btn.clicked.connect(self._refresh)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        # Tab widget
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # ── Overview tab ──
        self.overview = OverviewTab(self._tr)
        self.tabs.addTab(self.overview, self._tr("tab_overview"))

        # ── History tab ──
        init_db()
        self.history = HistoryChart(self._tr)
        self.tabs.addTab(self.history, self._tr("tab_history"))

        # ── File Analyzer tab ──
        self.file_analyzer = FileAnalyzerPage(self._tr)
        self.tabs.addTab(self.file_analyzer, self._tr("tab_files"))

        self._apply_dark_theme()

        # Timer
        self._ticks = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(5000)

        # Tray
        self._setup_tray()

        # Initial load
        self._refresh()

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
                color: #d4d4d4;
            }
            QLabel {
                color: #d4d4d4;
            }
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #1e1e1e;
            }
            QTabBar::tab {
                background-color: #2d2d2d;
                color: #d4d4d4;
                padding: 8px 20px;
                border: 1px solid #555;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #1e1e1e;
                border-bottom: 1px solid #1e1e1e;
            }
            QTabBar::tab:hover {
                background-color: #3a3a3a;
            }
            QGroupBox {
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                padding: 6px 16px;
                border-radius: 3px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1177bb;
            }
            QPushButton:pressed {
                background-color: #0a5080;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
            QComboBox {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 8px;
            }
            QComboBox:hover {
                border: 1px solid #888;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #2d2d2d;
                color: #d4d4d4;
                selection-background-color: #0e639c;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #d4d4d4;
                border: 1px solid #555;
            }
            QMenu::item:selected {
                background-color: #0e639c;
            }
        """)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip(self._tr("app_title"))
        self.tray.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_ComputerIcon
        ))
        self._tray_menu = QMenu()
        self._show_action = QAction(self._tr("show_window"), self)
        self._show_action.triggered.connect(self.show)
        self._tray_menu.addAction(self._show_action)
        self._quit_action = QAction(self._tr("exit"), self)
        self._quit_action.triggered.connect(QApplication.quit)
        self._tray_menu.addAction(self._quit_action)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show()
            self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def _show_settings_menu(self):
        menu = QMenu(self)
        lang_menu = QMenu(self._tr("language"), self)
        en_action = QAction(self._tr("lang_en"), self)
        en_action.triggered.connect(lambda: self._set_language("en"))
        lang_menu.addAction(en_action)
        zh_action = QAction(self._tr("lang_zh"), self)
        zh_action.triggered.connect(lambda: self._set_language("zh"))
        lang_menu.addAction(zh_action)
        menu.addMenu(lang_menu)
        menu.exec(self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomLeft()))

    def _set_language(self, lang: str):
        if lang == self._settings["language"]:
            return
        self._settings["language"] = lang
        save_settings(self._settings)
        self._tr = make_tr(lang)
        self._retranslate_ui()
        self._refresh()

    def _retranslate_ui(self):
        tr = self._tr
        self.setWindowTitle(tr("app_title"))
        self.title_label.setText(tr("app_title"))
        self.settings_btn.setText(tr("settings"))
        self.refresh_btn.setText(tr("refresh_btn"))
        self.tabs.setTabText(0, tr("tab_overview"))
        self.tabs.setTabText(1, tr("tab_history"))
        self.tabs.setTabText(2, tr("tab_files"))
        self.overview.set_tr(tr)
        self.history.set_tr(tr)
        self.file_analyzer.set_tr(tr)
        self.tray.setToolTip(tr("app_title"))
        self._show_action.setText(tr("show_window"))
        self._quit_action.setText(tr("exit"))

    def _refresh(self):
        try:
            from disk_collector import get_all_disk_info
            drives = get_all_disk_info()
        except (ImportError, Exception):
            return

        self.overview.refresh(drives)

        # Update cache with current drive totals
        cache = get_cache()
        for d in drives:
            cache.set_total(d.drive_letter, d.total_bytes)

        self._ticks += 1
        if self._ticks % self._RECORD_INTERVAL == 0:
            record_usage(drives)
        self.history.update_drives(drives)
        self.file_analyzer.update_drives(drives)

    def closeEvent(self, event):
        get_cache().clear()
        event.ignore()
        self.hide()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Storage Monitor")
    app.setOrganizationName("StorageMonitor")
    app.aboutToQuit.connect(lambda: get_cache().clear())
    window = Dashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
