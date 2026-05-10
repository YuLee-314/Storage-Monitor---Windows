"""Directory tree with instant expansion + progressive size fill.

Architecture:
  _direct_load()    → synchronous get_dir_contents (0.00s) → tree appears
  TreeSizeFiller    → threading.Thread + get_dir_tree_sizes (single-pass)
  _poll_results()   → QTimer polls queue, updates tree items progressively
"""

import os
import subprocess
import threading
import queue
from pathlib import Path

from PySide6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QLabel,
    QHBoxLayout, QPushButton,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor, QBrush

from gui.cache import get_cache

SPLIT_THRESHOLD = 40
MERGE_RATIO = 0.005
POLL_MS = 150  # how often to check for filled sizes


def format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ── Background size filler ─────────────────────────────────────────

class TreeSizeFiller:
    """Single-pass size computation in a Python thread. Results go to a queue."""

    def __init__(self, path: str, total: int):
        self._path = path
        self._total = total
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._done = False
        self.entries: list = []  # stored after completion for cache

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def results(self) -> queue.Queue:
        return self._queue

    @property
    def done(self) -> bool:
        return self._done

    @property
    def path(self) -> str:
        return self._path

    def _run(self):
        try:
            from disk_collector import get_dir_tree_sizes

            def progress(msg: str):
                self._queue.put(("__progress__", msg, 0))

            self.entries = get_dir_tree_sizes(self._path, progress)
            for e in self.entries:
                if e.size_bytes > 0:
                    pct = (e.size_bytes / self._total * 100) if self._total else 0
                    self._queue.put((e.name, format_bytes(e.size_bytes), pct))
        except Exception:
            pass
        finally:
            self._done = True


# ── Tree widget ────────────────────────────────────────────────────

SENTINEL = "__loading__"


class DirTreeWidget(QTreeWidget):
    """Tree with synchronous instant expansion + background single-pass size fill."""

    COL_NAME = 0
    COL_SIZE = 1
    COL_PCT = 2

    def __init__(self, tr, drive_letter: str, total_bytes: int, parent=None):
        super().__init__(parent)
        self._tr = tr
        self._drive = drive_letter
        self._total = total_bytes
        self._fillers: list[TreeSizeFiller] = []
        self._fill_parents: dict[str, QTreeWidgetItem] = {}  # path → item for updates
        self._progress_label: QLabel | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_results)
        self._poll_timer.start(POLL_MS)

        self.setHeaderLabels([tr("col_name"), tr("col_size"), tr("col_percent")])
        self.setRootIsDecorated(True)
        self.setAnimated(True)
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0, 280)
        self.setColumnWidth(1, 100)
        self.setColumnWidth(2, 70)
        self.setMinimumHeight(160)

        self.setStyleSheet("""
            QTreeWidget {
                background-color: #252525; color: #d4d4d4;
                border: 1px solid #444; border-radius: 3px;
                alternate-background-color: #2a2a2a;
            }
            QTreeWidget::item:selected { background-color: #0e639c; }
            QHeaderView::section {
                background-color: #333; color: #d4d4d4;
                border: 1px solid #555; padding: 3px 6px;
            }
        """)

        self.itemExpanded.connect(self._on_expand)
        self.itemDoubleClicked.connect(self._on_double_click)

    def set_progress_label(self, label: QLabel):
        self._progress_label = label

    def set_tr(self, tr):
        self._tr = tr
        self.setHeaderLabels([tr("col_name"), tr("col_size"), tr("col_percent")])

    # ── polling ───────────────────────────────────────────────────

    def _find_and_update(self, item: QTreeWidgetItem, name: str,
                          size_str: str, pct: float) -> bool:
        """Recursively search for a directory item by name and update its size."""
        cdata = item.data(0, Qt.UserRole)
        if cdata and cdata != SENTINEL:
            cname = item.text(self.COL_NAME).strip("[]")
            if cname == name and cdata.get("type") == "dir":
                item.setText(self.COL_SIZE, size_str)
                item.setText(self.COL_PCT, f"{pct:.2f}%" if pct > 0 else "")
                return True
        for i in range(item.childCount()):
            if self._find_and_update(item.child(i), name, size_str, pct):
                return True
        return False

    def _poll_results(self):
        if not self._fillers:
            return
        # Process all active fillers
        for filler in self._fillers[:]:
            q = filler.results
            while not q.empty():
                try:
                    name, size_str, pct = q.get_nowait()
                except queue.Empty:
                    break

                if name == "__progress__" and self._progress_label:
                    self._progress_label.setText(size_str)
                    continue

                # Search all expanded parent trees
                for _path, parent_item in self._fill_parents.items():
                    if self._find_and_update(parent_item, name, size_str, pct):
                        if self._progress_label:
                            self._progress_label.setText(f"{name}  {size_str}")
                        break

        # Remove completed fillers and write to cache
        active = []
        for f in self._fillers:
            if f.done:
                if f.entries:
                    cache = get_cache()
                    cache.put(f.path, f.entries, self._total)
            else:
                active.append(f)
        self._fillers = active
        if not self._fillers and self._progress_label:
            self._progress_label.setText("")

    # ── direct load (synchronous) ──────────────────────────────────

    def _direct_load(self, parent_item: QTreeWidgetItem, path: str):
        """Load and populate children synchronously — 0.00s."""
        try:
            from disk_collector import get_dir_contents
            entries = get_dir_contents(path)
        except Exception:
            entries = []
        if entries:
            self._populate_children(parent_item, entries)
            self._start_fill(parent_item, path)
        else:
            QTreeWidgetItem(parent_item, [self._tr("status_empty"), "", ""])

    def load_root(self):
        self.clear()
        self._fill_parents.clear()
        self._fillers.clear()
        root_path = f"{self._drive}\\"
        root_item = QTreeWidgetItem(self, [self._drive, "", ""])
        root_item.setData(0, Qt.UserRole, {"type": "dir", "path": root_path})
        root_item.setFont(0, QFont("Segoe UI", 12, QFont.Bold))
        root_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self._fill_parents[root_path] = root_item
        self._direct_load(root_item, root_path)
        self.expandItem(root_item)

    # ── expand ─────────────────────────────────────────────────────

    def _on_expand(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.UserRole)
        if not data or data == SENTINEL:
            return
        dtype = data.get("type", "")

        # Remove all dummy/sentinel children
        for i in range(item.childCount() - 1, -1, -1):
            c = item.child(i)
            cd = c.data(0, Qt.UserRole)
            if cd is None or cd == SENTINEL or (c.text(0) == "" and c.text(1) == ""):
                item.removeChild(c)

        if dtype == "dir" and item.childCount() == 0:
            path = data["path"]
            self._fill_parents[path] = item
            self._direct_load(item, path)
        elif dtype == "group" and item.childCount() == 0:
            children = data.get("children", [])
            self._populate_children(item, children)

    def _start_fill(self, parent_item: QTreeWidgetItem, path: str):
        """Start single-pass background size computation for this level."""
        filler = TreeSizeFiller(path, self._total)
        self._fillers.append(filler)
        filler.start()

    @staticmethod
    def _dummy_child(parent: QTreeWidgetItem):
        c = QTreeWidgetItem(parent, ["", "", ""])
        c.setData(0, Qt.UserRole, SENTINEL)

    # ── double-click → explorer ───────────────────────────────────

    def _on_double_click(self, item: QTreeWidgetItem, _column: int):
        data = item.data(0, Qt.UserRole)
        if not data or data == SENTINEL:
            return
        path = data.get("path", "")
        if path and os.path.exists(path):
            subprocess.Popen(["explorer", os.path.normpath(path)])

    # ── populate children ─────────────────────────────────────────

    def _populate_children(self, parent_item: QTreeWidgetItem, entries):
        n = len(entries)
        if n == 0:
            return

        # Separate small files for merging
        file_total = sum(e.size_bytes for e in entries if not e.is_dir)
        threshold = int(file_total * MERGE_RATIO) if file_total > 0 else 0

        main_entries = []
        small_files = []
        for e in entries:
            if e.is_dir:
                main_entries.append(e)
            elif threshold > 0 and e.size_bytes < threshold:
                small_files.append(e)
            else:
                main_entries.append(e)

        count = len(main_entries)
        if count <= SPLIT_THRESHOLD:
            self._add_entry_items(parent_item, main_entries)
        else:
            mid = count // 2
            self._make_group_item(parent_item, main_entries[:mid],
                                  self._tr("group_larger"))
            self._make_group_item(parent_item, main_entries[mid:],
                                  self._tr("group_smaller"))

        if small_files:
            sf_total = sum(e.size_bytes for e in small_files)
            pct = f"{(sf_total / self._total * 100):.1f}%" if self._total else ""
            desc = f"{self._tr('group_others')} ({len(small_files)} {self._tr('label_items')}, {format_bytes(sf_total)})"
            sitem = QTreeWidgetItem(parent_item, [desc, format_bytes(sf_total), pct])
            sitem.setData(0, Qt.UserRole, {"type": "group", "children": small_files})
            sitem.setForeground(0, QBrush(QColor("#666")))
            sitem.setFont(0, QFont("Segoe UI", 9))
            sitem.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            QTreeWidgetItem(sitem, ["", "", ""])

    def _make_group_item(self, parent, entries, label):
        count = len(entries)
        total_sz = sum(e.size_bytes for e in entries if not e.is_dir)
        pct = f"{(total_sz / self._total * 100):.1f}%" if self._total else ""
        desc = f"{label} ({count} {self._tr('label_items')}, {format_bytes(total_sz)})"
        item = QTreeWidgetItem(parent, [desc, format_bytes(total_sz), pct])
        item.setData(0, Qt.UserRole, {"type": "group", "children": entries})
        item.setForeground(0, QBrush(QColor("#88c0ff")))
        item.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        self._dummy_child(item)
        return item

    def _add_entry_items(self, parent_item, entries):
        for e in entries:
            name = f"[{e.name}]" if e.is_dir else e.name
            size_str = format_bytes(e.size_bytes) if not e.is_dir and e.size_bytes > 0 else ("-" if e.is_dir else "0 B")
            pct = ""
            if self._total and e.size_bytes > 0 and not e.is_dir:
                pct = f"{(e.size_bytes / self._total * 100):.2f}%"

            item = QTreeWidgetItem(parent_item, [name, size_str, pct])
            item.setData(0, Qt.UserRole, {
                "type": "dir" if e.is_dir else "file",
                "path": e.path,
            })

            if e.is_dir:
                item.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                # Dummy child for expand arrow (not SENTINEL, just empty)
                self._dummy_child(item)
            else:
                item.setForeground(0, QBrush(QColor("#ccc")))


# ── Panel wrapper ──────────────────────────────────────────────────

class DriveExpansionPanel(QWidget):
    def __init__(self, tr, drive_letter: str, total_bytes: int, parent=None):
        super().__init__(parent)
        self._tr = tr

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        self.collapse_btn = QPushButton(tr("btn_collapse"))
        bar.addWidget(self.collapse_btn)
        bar.addStretch()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color: #8ab4f8;")
        bar.addWidget(self.progress_lbl)
        layout.addLayout(bar)

        self.tree = DirTreeWidget(tr, drive_letter, total_bytes)
        self.tree.set_progress_label(self.progress_lbl)
        layout.addWidget(self.tree)

        self.hide()

    def set_tr(self, tr):
        self._tr = tr
        self.tree.set_tr(tr)
        self.collapse_btn.setText(tr("btn_collapse"))

    def expand(self):
        self.show()
        self.tree.load_root()

    def collapse(self):
        self.hide()
