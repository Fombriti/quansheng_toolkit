"""
Scan List Manager: rename the 24 scan lists and assign channels to them.

Layout:
  Left  — vertical list of all 24 scan lists with editable 4-char name and
          a count badge (how many channels are currently in that list).
          Plus virtual entries for "OFF" (no list) and "ALL" (always scan).
  Right — table of configured channels filtered by the currently-selected
          list, with a toggle column to add/remove the channel from the
          currently-selected list.

Every edit writes back into the in-memory EEPROM image and flips the dirty
flag so Apply Changes can upload.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...kradio import memory as mem
from ..icons import svg_icon
from ..state import AppState
from ..theme import Palette
from ..widgets import PageHeader


# Sentinel "lists" used in the sidebar besides the real ones.
SLOT_OFF = 0


class ListSlotItem(QFrame):
    """A row inside the left list: editable name + count badge."""
    rename_requested = Signal(int, str)   # list_index (1..24), new_name

    def __init__(self, list_idx: int, name: str, count: int,
                 *, editable_name: bool, label: str | None = None,
                 tag: str | None = None, is_all: bool = False):
        super().__init__()
        self.list_idx = list_idx
        self.setObjectName("Card")
        self.setMinimumHeight(56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        if list_idx == SLOT_OFF:
            tag = tag or "OFF"
            color = "#7f849c"
        elif is_all:
            tag = tag or "ALL"
            color = "#f9e2af"
        else:
            tag = tag or f"L{list_idx}"
            color = "#a6e3a1"

        self.tag = QLabel(tag)
        self.tag.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 12px; "
            f"letter-spacing: 1.5px; min-width: 32px;"
        )
        self.tag.setFixedWidth(38)
        layout.addWidget(self.tag)

        if editable_name:
            self.name_edit = QLineEdit(name)
            self.name_edit.setMaxLength(4)
            self.name_edit.setPlaceholderText(label or "—")
            self.name_edit.setStyleSheet(
                "background: transparent; border: none; font-size: 14px; "
                "font-weight: 500; padding: 2px 0;"
            )
            self.name_edit.editingFinished.connect(self._emit_rename)
            layout.addWidget(self.name_edit, 1)
        else:
            self.name_edit = None
            text = QLabel(label or "—")
            text.setStyleSheet("color: #a6adc8; font-size: 14px;")
            layout.addWidget(text, 1)

        self.count_badge = QLabel(str(count))
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_badge.setMinimumWidth(28)
        self.count_badge.setStyleSheet(
            "background: rgba(137, 180, 250, 25); color: palette(window-text);"
            "border-radius: 9px; padding: 2px 8px; font-size: 12px; font-weight: 600;"
        )
        layout.addWidget(self.count_badge)

    def set_count(self, count: int) -> None:
        self.count_badge.setText(str(count))

    def _emit_rename(self) -> None:
        if self.name_edit is None:
            return
        self.rename_requested.emit(self.list_idx, self.name_edit.text().strip())


class ScanListsView(QWidget):
    """Top-level page for managing scan list assignments + names."""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._slot_items: dict[int, ListSlotItem] = {}
        self._selected_slot: int = 1   # default: List 1
        self._show_only_members: bool = True
        self._search_text: str = ""

        self._build_ui()
        self.state.eeprom_loaded.connect(self.reload)
        self.reload()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        root.addWidget(PageHeader(
            "Scan Lists",
            "Rename the 24 lists and assign channels to them. Click a list "
            "on the left, then toggle channels on the right.",
        ))

        self.empty = QLabel("Read the EEPROM first (Dashboard tab).")
        self.empty.setStyleSheet("color:#7f849c; padding:30px;")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty)

        self.split_root = QSplitter(Qt.Orientation.Horizontal)
        self.split_root.setHandleWidth(8)
        self.split_root.setChildrenCollapsible(False)

        # ---- LEFT pane ----------------------------------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        cap = QLabel("LISTS")
        cap.setStyleSheet("color:#7f849c; font-size:11px; font-weight:700; "
                          "letter-spacing:1.5px;")
        left_layout.addWidget(cap)

        self.lists_widget = QListWidget()
        self.lists_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lists_widget.setSpacing(4)
        self.lists_widget.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { background: transparent; padding: 0; "
            "  border: none; margin: 0; }"
            "QListWidget::item:selected { background: transparent; }"
        )
        self.lists_widget.currentRowChanged.connect(self._on_slot_selected)
        left_layout.addWidget(self.lists_widget, 1)

        self.split_root.addWidget(left)

        # ---- RIGHT pane ---------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.right_caption = QLabel("CHANNELS IN — L1")
        self.right_caption.setStyleSheet(
            "color:#7f849c; font-size:11px; font-weight:700; letter-spacing:1.5px;"
        )
        right_layout.addWidget(self.right_caption)

        # Filter bar: members-only / all toggle + search box
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.btn_members = QRadioButton("In this list")
        self.btn_members.setChecked(True)
        self.btn_members.toggled.connect(
            lambda checked: checked and self._set_view_mode(True)
        )
        toolbar.addWidget(self.btn_members)

        self.btn_all = QRadioButton("All channels (to add)")
        self.btn_all.toggled.connect(
            lambda checked: checked and self._set_view_mode(False)
        )
        toolbar.addWidget(self.btn_all)

        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.btn_members)
        self._mode_group.addButton(self.btn_all)

        toolbar.addSpacing(20)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by name or frequency…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_box, 1)

        right_layout.addLayout(toolbar)

        self.channels_table = QTableWidget(0, 4)
        self.channels_table.setHorizontalHeaderLabels(
            ["#", "Name", "Frequency", "In list?"]
        )
        self.channels_table.verticalHeader().setVisible(False)
        self.channels_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.channels_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.channels_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.channels_table.setAlternatingRowColors(True)
        self.channels_table.cellClicked.connect(self._on_cell_clicked)
        h = self.channels_table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.channels_table.setColumnWidth(0, 60)
        self.channels_table.setColumnWidth(2, 130)
        self.channels_table.setColumnWidth(3, 100)
        right_layout.addWidget(self.channels_table, 1)

        # Bulk action row
        bulk_bar = QHBoxLayout()
        bulk_bar.setSpacing(8)
        self.btn_add_selected = QPushButton(" Add selected to this list")
        self.btn_add_selected.setObjectName("PrimaryBtn")
        self.btn_add_selected.setIconSize(QSize(16, 16))
        self.btn_add_selected.clicked.connect(lambda: self._bulk_set_membership(True))
        bulk_bar.addWidget(self.btn_add_selected)

        self.btn_remove_selected = QPushButton(" Remove selected from this list")
        self.btn_remove_selected.setObjectName("SecondaryBtn")
        self.btn_remove_selected.setIconSize(QSize(16, 16))
        self.btn_remove_selected.clicked.connect(lambda: self._bulk_set_membership(False))
        bulk_bar.addWidget(self.btn_remove_selected)
        bulk_bar.addStretch()
        right_layout.addLayout(bulk_bar)

        self.split_root.addWidget(right)
        self.split_root.setStretchFactor(0, 1)
        self.split_root.setStretchFactor(1, 2)
        self.split_root.setSizes([360, 760])

        root.addWidget(self.split_root, 1)

    def refresh_icons(self, palette: Palette) -> None:
        primary_text = "#ffffff" if palette.name == "light" else palette.base
        self.btn_add_selected.setIcon(svg_icon("send", primary_text, 16))
        self.btn_remove_selected.setIcon(svg_icon("alert", palette.text, 16))

    # ---------------------------------------------------------------- Data

    def reload(self) -> None:
        has = self.state.has_image
        self.empty.setVisible(not has)
        self.split_root.setVisible(has)
        if not has:
            return

        mm = self.state.profile.memory_module
        self._channels = mm.decode_all_channels(bytes(self.state.eeprom))
        # Only F4HWN has separately-stored 4-char list names. Stock K5 V1
        # just has 2 anonymous lists (SL1 / SL2); fall back to []
        # gracefully so the rest of the view keeps working.
        if hasattr(mm, "decode_listnames"):
            self._listnames = mm.decode_listnames(bytes(self.state.eeprom))
        else:
            self._listnames = []
        self._counts = self._compute_counts()

        # Repopulate left pane — entries depend on the active profile.
        self.lists_widget.blockSignals(True)
        self.lists_widget.clear()
        self._slot_items = {}
        labels = list(getattr(mm, "SCAN_LIST_LABELS",
                              ["OFF"] + [f"L{i}" for i in range(1, 25)]
                              + ["ALL"]))
        # Reserved slot indices: 0 is always OFF, last slot is "all".
        for i, label in enumerate(labels):
            tag = label if label != f"L{i}" else None
            if i == 0:
                self._add_slot_row(0, "(no scan list)", editable=False,
                                   tag=tag)
            elif i == len(labels) - 1 and label.upper() in ("ALL", "SL1+SL2"):
                desc = "(scanned always)" if label == "ALL" else "(both lists)"
                self._add_slot_row(i, desc, editable=False,
                                   tag=tag, is_all=True)
            else:
                # Only F4HWN has editable per-list name strings.
                user_name = (self._listnames[i - 1]
                             if i - 1 < len(self._listnames) else "")
                self._add_slot_row(i, user_name,
                                   editable=bool(self._listnames),
                                   tag=tag)
        self.lists_widget.blockSignals(False)

        # Restore selection (clamp to valid range for this profile).
        max_slot = len(labels) - 1
        if self._selected_slot is None or self._selected_slot > max_slot:
            self._selected_slot = 1 if max_slot >= 1 else 0
        self.lists_widget.setCurrentRow(self._selected_slot)

        self._refresh_right_pane()

    def _compute_counts(self) -> dict[int, int]:
        mm = self.state.profile.memory_module
        labels = list(getattr(mm, "SCAN_LIST_LABELS", []))
        max_slot = len(labels) - 1 if labels else 25
        counts: dict[int, int] = {i: 0 for i in range(max_slot + 1)}
        for ch in self._channels:
            if ch.is_empty:
                continue
            v = ch.scanlist
            if 0 <= v <= max_slot:
                counts[v] = counts.get(v, 0) + 1
        return counts

    def _add_slot_row(self, slot_idx: int, name_or_label: str,
                      *, editable: bool,
                      tag: str | None = None,
                      is_all: bool = False) -> None:
        item = ListSlotItem(
            slot_idx,
            name=name_or_label if editable else "",
            count=self._counts.get(slot_idx, 0),
            editable_name=editable,
            label=name_or_label,
            tag=tag,
            is_all=is_all,
        )
        if editable:
            item.rename_requested.connect(self._rename_list)
        wrap = QListWidgetItem()
        wrap.setSizeHint(QSize(item.sizeHint().width(), 60))
        self.lists_widget.addItem(wrap)
        self.lists_widget.setItemWidget(wrap, item)
        self._slot_items[slot_idx] = item

    # -------------------------------------------------------- Interaction

    def _on_slot_selected(self, row: int) -> None:
        # Row index in the left widget is the same as the scanlist slot
        # index (0=OFF, last=ALL/Both, mid=L1..LN).
        if row < 0:
            return
        self._selected_slot = row
        self._refresh_right_pane()

    def _set_view_mode(self, members_only: bool) -> None:
        if self._show_only_members == members_only:
            return
        self._show_only_members = members_only
        self._refresh_right_pane()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text
        self._refresh_right_pane()

    def _refresh_right_pane(self) -> None:
        slot = self._selected_slot
        mm = self.state.profile.memory_module if self.state.has_image else None
        labels = list(getattr(mm, "SCAN_LIST_LABELS", []))
        if not labels:
            return
        if slot == 0:
            cap_label = "OFF (no scan list)"
        elif slot == len(labels) - 1 and labels[slot] == "ALL":
            cap_label = "ALL (always scanned)"
        elif slot == len(labels) - 1:
            cap_label = labels[slot]
        else:
            named = ""
            if self._listnames and slot - 1 < len(self._listnames) \
                    and self._listnames[slot - 1]:
                named = f" — {self._listnames[slot - 1]!r}"
            cap_label = f"{labels[slot]}{named}"

        configured = [c for c in self._channels if not c.is_empty]
        members = [c for c in configured if c.scanlist == slot]

        # Honor the members-only / all toggle and search filter.
        if self._show_only_members:
            shown = members
        else:
            shown = configured
        q = self._search_text.strip().lower()
        if q:
            def matches(c) -> bool:
                if q in c.name.lower():
                    return True
                # Also match raw frequency in MHz with tolerance for partial typing.
                fstr = f"{c.freq_mhz:.4f}"
                return q in fstr
            shown = [c for c in shown if matches(c)]

        # Caption with count: "L1 — 12 channels  (153 total)"
        if self._show_only_members:
            self.right_caption.setText(
                f"CHANNELS — {cap_label}   ·   {len(members)} in list"
            )
        else:
            self.right_caption.setText(
                f"ALL CHANNELS · {cap_label} membership shown   ·   "
                f"{len(members)} of {len(configured)} are in list"
            )

        self.channels_table.setRowCount(len(shown))
        for row, ch in enumerate(shown):
            in_list = (ch.scanlist == slot)
            self._fill_row(row, ch, in_list)

        # Bulk-action labels swap depending on mode (in members-only the
        # natural action is "remove", in all-mode it's "add").
        if self._show_only_members:
            self.btn_add_selected.setText(" Add selected to this list")
            self.btn_remove_selected.setText(" Remove selected from this list")
        else:
            self.btn_add_selected.setText(" Add selected to this list")
            self.btn_remove_selected.setText(" Remove selected from this list")

    def _fill_row(self, row: int, ch: mem.Channel, in_list: bool) -> None:
        idx_item = QTableWidgetItem(str(ch.index + 1))
        idx_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        idx_item.setData(Qt.ItemDataRole.UserRole, ch.index)
        self.channels_table.setItem(row, 0, idx_item)

        name_item = QTableWidgetItem(ch.name)
        self.channels_table.setItem(row, 1, name_item)

        freq_item = QTableWidgetItem(f"{ch.freq_mhz:.4f} MHz   {ch.mode}")
        freq_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.channels_table.setItem(row, 2, freq_item)

        membership_item = QTableWidgetItem("✓ in this list" if in_list else "—")
        membership_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if in_list:
            membership_item.setForeground(QColor("#a6e3a1"))
        else:
            membership_item.setForeground(QColor("#7f849c"))
        # Annotate the channel index for the click handler.
        membership_item.setData(Qt.ItemDataRole.UserRole, ch.index)
        self.channels_table.setItem(row, 3, membership_item)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column != 3:
            return
        idx_item = self.channels_table.item(row, 0)
        if idx_item is None:
            return
        ch_idx = idx_item.data(Qt.ItemDataRole.UserRole)
        ch = next((c for c in self._channels if c.index == ch_idx), None)
        if ch is None:
            return
        # Toggle membership.
        new_value = self._selected_slot if ch.scanlist != self._selected_slot else SLOT_OFF
        self._apply_scanlist(ch.index, new_value)

    def _bulk_set_membership(self, add: bool) -> None:
        """Apply add/remove of the current list to every selected row."""
        new_value = self._selected_slot if add else SLOT_OFF
        rows_seen: set[int] = set()
        for it in self.channels_table.selectedItems():
            rows_seen.add(it.row())
        for row in sorted(rows_seen):
            idx_item = self.channels_table.item(row, 0)
            if idx_item is None:
                continue
            ch_idx = idx_item.data(Qt.ItemDataRole.UserRole)
            self._apply_scanlist(ch_idx, new_value)

    # -------------------------------------------------------- Mutators

    def _apply_scanlist(self, ch_idx: int, new_value: int) -> None:
        mm = self.state.profile.memory_module
        addr = mm.addr_scanlist_byte(ch_idx)
        if mm.__name__.endswith("memory_uvk5_v1"):
            # K5 V1 packs the 2 scanlist bits inside the same byte as
            # compander+band; preserve the other bits.
            cur = self.state.eeprom[addr]
            patched = mm.patch_scanlist(cur, new_value)
            if patched == cur:
                return
            self.state.eeprom[addr] = patched
        else:
            if self.state.eeprom[addr] == (new_value & 0xFF):
                return
            self.state.eeprom[addr] = new_value & 0xFF
        # Update local snapshot
        for i, c in enumerate(self._channels):
            if c.index == ch_idx:
                rec = bytes(self.state.eeprom[mm.addr_channel(c.index):
                                               mm.addr_channel(c.index) + mm.CHANNEL_SIZE])
                name_raw = bytes(self.state.eeprom[mm.addr_channel_name(c.index):
                                                    mm.addr_channel_name(c.index)
                                                    + mm.CHANNEL_NAME_SIZE])
                attr = bytes(self.state.eeprom[mm.addr_ch_attr(c.index):
                                                mm.addr_ch_attr(c.index) + mm.CH_ATTR_SIZE])
                self._channels[i] = mm._decode_record(c.index, rec, name_raw, attr)
                break
        self.state.mark_dirty()
        self._counts = self._compute_counts()
        for slot, item in self._slot_items.items():
            item.set_count(self._counts.get(slot, 0))
        self._refresh_right_pane()

    def _rename_list(self, list_idx: int, new_name: str) -> None:
        # Only supported on F4HWN (which stores 4-char names per list).
        # K5 V1 stock has no listname storage; the editable field is
        # disabled in that case so this slot won't fire.
        mm = self.state.profile.memory_module
        if not (hasattr(mm, "LISTNAME_BASE") and hasattr(mm, "NUM_LISTS")):
            return
        if not 1 <= list_idx <= mm.NUM_LISTS:
            return
        encoded = new_name.encode("ascii", errors="replace")[:mm.LISTNAME_SIZE]
        encoded = encoded.ljust(mm.LISTNAME_SIZE, b"\xFF")
        addr = mm.LISTNAME_BASE + (list_idx - 1) * mm.LISTNAME_SIZE
        if self.state.eeprom[addr:addr + mm.LISTNAME_SIZE] == encoded:
            return
        self.state.eeprom[addr:addr + mm.LISTNAME_SIZE] = encoded
        self.state.mark_dirty()
        self._listnames = mm.decode_listnames(bytes(self.state.eeprom))
        if list_idx == self._selected_slot:
            self._refresh_right_pane()
