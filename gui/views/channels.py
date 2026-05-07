"""
Channels view: a sortable, filterable table of all configured channels with
inline scan-list editing.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...kradio import memory as mem
from ...kradio import tones as tonesmod
from ..state import AppState
from ..widgets import PageHeader


COLUMNS = ["#", "Name", "Frequency (MHz)", "Mode",
           "Duplex", "Offset (MHz)", "Power", "Step",
           "Scan List",
           "RX Type", "RX Value", "TX Type", "TX Value"]
(COL_INDEX, COL_NAME, COL_FREQ, COL_MODE,
 COL_DUPLEX, COL_OFFSET, COL_POWER, COL_STEP,
 COL_SCAN,
 COL_RX_TYPE, COL_RX_VALUE, COL_TX_TYPE, COL_TX_VALUE) = range(13)


class ChannelsModel(QAbstractTableModel):
    """Bridges the in-memory EEPROM to a table view."""

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.channels: list[mem.Channel] = []
        self.state.eeprom_loaded.connect(self.reload)
        if state.has_image:
            self.reload()

    show_empty: bool = False  # set by the toggle in the search bar

    def reload(self) -> None:
        self.beginResetModel()
        if self.state.has_image:
            mm = self.state.profile.memory_module
            all_channels = mm.decode_all_channels(bytes(self.state.eeprom))
            if self.show_empty:
                self.channels = list(all_channels)
            else:
                self.channels = [c for c in all_channels if not c.is_empty]
        else:
            self.channels = []
        self.endResetModel()

    # ---- Qt model API -----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.channels)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        ch = self.channels[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_INDEX:
                return ch.index + 1
            if ch.is_empty:
                # When the toggle "show empty slots" is on, present each
                # unprogrammed slot with a clear placeholder so it can't
                # be confused with a configured-but-blank-name channel.
                if col == COL_NAME:
                    return "(empty slot)"
                return "—"
            if col == COL_NAME:
                return ch.name
            if col == COL_FREQ:
                return f"{ch.freq_mhz:.4f}"
            if col == COL_MODE:
                return ch.mode
            if col == COL_DUPLEX:
                return ch.duplex if ch.duplex else "—"
            if col == COL_OFFSET:
                if ch.duplex and ch.offset_hz:
                    return f"{ch.offset_hz / 1_000_000:.4f}"
                return "—"
            if col == COL_POWER:
                mm = self.state.profile.memory_module
                lbls = getattr(mm, "POWER_LEVELS", [])
                return lbls[ch.tx_power] if 0 <= ch.tx_power < len(lbls) \
                    else f"?{ch.tx_power}"
            if col == COL_STEP:
                mm = self.state.profile.memory_module
                steps = getattr(mm, "STEPS_KHZ", [])
                if 0 <= ch.step_idx < len(steps):
                    return f"{steps[ch.step_idx]:g} kHz"
                return "—"
            if col == COL_SCAN:
                return ch.scanlist_label
            if col == COL_RX_TYPE:
                return tonesmod.tone_type_for_tmode(ch.rx_tmode)
            if col == COL_RX_VALUE:
                return ch.rx_tone_label or "—"
            if col == COL_TX_TYPE:
                return tonesmod.tone_type_for_tmode(ch.tx_tmode)
            if col == COL_TX_VALUE:
                return ch.tx_tone_label or "—"
        if role == Qt.ItemDataRole.EditRole:
            if col == COL_DUPLEX:
                return ch.duplex
            if col == COL_OFFSET:
                return f"{ch.offset_hz / 1_000_000:.4f}" if ch.duplex else ""
            if col == COL_POWER:
                return ch.tx_power
            if col == COL_STEP:
                return ch.step_idx
            if col == COL_SCAN:
                return ch.scanlist
            if col == COL_RX_TYPE:
                return tonesmod.tone_type_for_tmode(ch.rx_tmode)
            if col == COL_RX_VALUE:
                return ch.rx_tone_label or ""
            if col == COL_TX_TYPE:
                return tonesmod.tone_type_for_tmode(ch.tx_tmode)
            if col == COL_TX_VALUE:
                return ch.tx_tone_label or ""
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_INDEX, COL_FREQ):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_SCAN:
                return self._scanlist_color(ch.scanlist)
            if col in (COL_RX_TYPE, COL_RX_VALUE):
                tmode = ch.rx_tmode
                return (QColor("#7f849c") if tmode == tonesmod.TMODE_NONE
                        else QColor("#a6e3a1"))
            if col in (COL_TX_TYPE, COL_TX_VALUE):
                tmode = ch.tx_tmode
                return (QColor("#7f849c") if tmode == tonesmod.TMODE_NONE
                        else QColor("#a6e3a1"))
        if role == Qt.ItemDataRole.UserRole:
            # Expose the underlying Channel for the delegate.
            return ch
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        col = index.column()
        editable = (COL_DUPLEX, COL_OFFSET, COL_POWER, COL_STEP, COL_MODE,
                    COL_SCAN, COL_RX_TYPE, COL_RX_VALUE,
                    COL_TX_TYPE, COL_TX_VALUE)
        if col not in editable:
            return False
        ch = self.channels[index.row()]
        if ch.is_empty:
            return False
        mm = self.state.profile.memory_module
        if col in (COL_RX_TYPE, COL_RX_VALUE, COL_TX_TYPE, COL_TX_VALUE):
            return self._set_tone_field(index.row(), ch, col, value, mm)
        if col in (COL_DUPLEX, COL_OFFSET, COL_POWER, COL_STEP, COL_MODE):
            return self._set_record_field(index.row(), ch, col, value, mm)
        # Profile-aware range: F4HWN scanlists are 0..25 (OFF/L1..L24/ALL),
        # stock K5 V1 are 0..3 (OFF/SL1/SL2/SL1+SL2).
        max_scanlist = (
            getattr(mm, "SCAN_ALL", 25) if hasattr(mm, "SCAN_ALL")
            else len(getattr(mm, "SCAN_LIST_LABELS", [])) - 1
        )
        try:
            new_value = int(value) if isinstance(value, (int, str)) else value
            if not (0 <= new_value <= max_scanlist):
                return False
        except (ValueError, TypeError):
            return False
        if new_value == ch.scanlist:
            return False
        # Persist into the AppState image (profile-aware addressing).
        if hasattr(mm, "addr_scanlist_byte"):
            addr = mm.addr_scanlist_byte(ch.index)
            # F4HWN stores scanlist in its own byte; K5 V1 packs the 2
            # scanlist-flag bits into the same byte as compander+band.
            if mm.__name__.endswith("memory_uvk5_v1"):
                # Use the patch helper that preserves the other bitfields.
                cur = self.state.eeprom[addr]
                self.state.eeprom[addr] = mm.patch_scanlist(cur, new_value)
            else:
                self.state.eeprom[addr] = new_value & 0xFF
        # Reload that row's snapshot from the image.
        rec = bytes(self.state.eeprom[mm.addr_channel(ch.index):
                                       mm.addr_channel(ch.index) + mm.CHANNEL_SIZE])
        name_raw = bytes(self.state.eeprom[mm.addr_channel_name(ch.index):
                                            mm.addr_channel_name(ch.index)
                                            + mm.CHANNEL_NAME_SIZE])
        attr = bytes(self.state.eeprom[mm.addr_ch_attr(ch.index):
                                        mm.addr_ch_attr(ch.index) + mm.CH_ATTR_SIZE])
        self.channels[index.row()] = mm._decode_record(ch.index, rec, name_raw, attr)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        self.state.mark_dirty()
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        col = index.column()
        editable = (COL_DUPLEX, COL_OFFSET, COL_POWER, COL_STEP, COL_MODE,
                    COL_SCAN, COL_RX_TYPE, COL_RX_VALUE,
                    COL_TX_TYPE, COL_TX_VALUE)
        if col in editable:
            # Empty slots can't be edited via the inline grid — set
            # frequency + mode first via CSV import or the radio's UI.
            ch = self.channels[index.row()]
            if ch.is_empty:
                return flags
            # Offset is read-only when duplex is "" (simplex).
            if col == COL_OFFSET:
                ch = self.channels[index.row()]
                if not ch.duplex:
                    return flags
            # Tone VALUE columns become read-only when the corresponding
            # type is OFF. The user must pick a non-OFF type first.
            if col == COL_RX_VALUE:
                ch = self.channels[index.row()]
                if ch.rx_tmode == tonesmod.TMODE_NONE:
                    return flags
            if col == COL_TX_VALUE:
                ch = self.channels[index.row()]
                if ch.tx_tmode == tonesmod.TMODE_NONE:
                    return flags
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def _set_record_field(self, row: int, ch, col: int, value, mm) -> bool:
        """
        Patch the Duplex / Offset / Power / Step fields. These all live
        inside the 16-byte channel record (bytes 0x0B / 0x0C / 0x0E),
        so we re-encode the whole record via patch_channel_in_image
        which preserves tones and other untouched fields.
        """
        kwargs = dict(idx=ch.index)
        if col == COL_MODE:
            mode = str(value or "").strip().upper()
            if mode not in mm.MODE_TABLE:
                return False
            kwargs["mode"] = mode
            kwargs["freq_hz"] = ch.freq_hz
            kwargs["duplex"] = ch.duplex
            kwargs["offset_hz"] = ch.offset_hz
        elif col == COL_DUPLEX:
            duplex = str(value or "").strip()
            if duplex not in mm.DUPLEX_LABELS:
                return False
            kwargs["duplex"] = duplex
            # When clearing duplex (going simplex) zero out the offset.
            kwargs["offset_hz"] = ch.offset_hz if duplex else 0
            # encode_channel_record needs freq/mode too — pass current.
            kwargs["freq_hz"] = ch.freq_hz
            kwargs["mode"] = ch.mode
        elif col == COL_OFFSET:
            try:
                mhz = float(str(value or "0").strip())
            except ValueError:
                return False
            if mhz < 0 or mhz > 99:
                return False
            kwargs["offset_hz"] = int(round(mhz * 1_000_000))
            kwargs["duplex"] = ch.duplex
            kwargs["freq_hz"] = ch.freq_hz
            kwargs["mode"] = ch.mode
        elif col == COL_POWER:
            try:
                lvl = int(value)
            except (TypeError, ValueError):
                return False
            if not 0 <= lvl < len(mm.POWER_LEVELS):
                return False
            kwargs["tx_power"] = lvl
            kwargs["freq_hz"] = ch.freq_hz
            kwargs["mode"] = ch.mode
            kwargs["duplex"] = ch.duplex
            kwargs["offset_hz"] = ch.offset_hz
        elif col == COL_STEP:
            try:
                step = int(value)
            except (TypeError, ValueError):
                return False
            if not 0 <= step < len(mm.STEPS_KHZ):
                return False
            kwargs["tuning_step_idx"] = step
            kwargs["freq_hz"] = ch.freq_hz
            kwargs["mode"] = ch.mode
            kwargs["duplex"] = ch.duplex
            kwargs["offset_hz"] = ch.offset_hz
        else:
            return False

        try:
            mm.patch_channel_in_image(self.state.eeprom, **kwargs)
        except (ValueError, KeyError):
            return False

        # Refresh the row from the patched bytes.
        rec = bytes(self.state.eeprom[mm.addr_channel(ch.index):
                                       mm.addr_channel(ch.index) + mm.CHANNEL_SIZE])
        name_raw = bytes(self.state.eeprom[mm.addr_channel_name(ch.index):
                                            mm.addr_channel_name(ch.index)
                                            + mm.CHANNEL_NAME_SIZE])
        attr = bytes(self.state.eeprom[mm.addr_ch_attr(ch.index):
                                        mm.addr_ch_attr(ch.index) + mm.CH_ATTR_SIZE])
        self.channels[row] = mm._decode_record(ch.index, rec, name_raw, attr)
        # Duplex changes flip Offset's editability — emit dataChanged for
        # both cells.
        left = self.index(row, COL_DUPLEX)
        right = self.index(row, COL_STEP)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.DisplayRole])
        self.state.mark_dirty()
        return True

    def _set_tone_field(self, row: int, ch, col: int, value, mm) -> bool:
        """
        Two-stage tone editor: type column changes mode (and may auto-set
        a default value), value column updates the tone within the
        current type. Mirrors the upstream rxToneType / rxTone pair.
        """
        is_rx = col in (COL_RX_TYPE, COL_RX_VALUE)
        is_type = col in (COL_RX_TYPE, COL_TX_TYPE)
        chosen = str(value or "").strip()

        # Decide what tone string to write to the radio.
        if is_type:
            if chosen not in tonesmod.TONE_TYPE_LABELS:
                return False
            current_value = ch.rx_tone_label if is_rx else ch.tx_tone_label
            if chosen == tonesmod.TONE_TYPE_OFF:
                write_spec = "OFF"
            else:
                # Same type as before? Keep the existing value, otherwise
                # default to the first valid value for the new type.
                current_type = (tonesmod.tone_type_for_tmode(
                    ch.rx_tmode if is_rx else ch.tx_tmode))
                if chosen == current_type and current_value:
                    write_spec = current_value
                else:
                    write_spec = tonesmod.default_value_for_type(chosen)
        else:
            # Value column. Validate against the current type.
            current_type = (tonesmod.tone_type_for_tmode(
                ch.rx_tmode if is_rx else ch.tx_tmode))
            if current_type == tonesmod.TONE_TYPE_OFF:
                return False  # gated by flags() but be defensive
            valid = tonesmod.tone_values_for_type(current_type)
            if chosen and chosen not in valid:
                return False
            write_spec = chosen or "OFF"

        try:
            if is_rx:
                mm.patch_channel_tones(self.state.eeprom, ch.index,
                                        rx_tone=write_spec)
            else:
                mm.patch_channel_tones(self.state.eeprom, ch.index,
                                        tx_tone=write_spec)
        except (ValueError, AttributeError):
            return False

        # Refresh the in-memory snapshot from the patched bytes.
        rec = bytes(self.state.eeprom[mm.addr_channel(ch.index):
                                       mm.addr_channel(ch.index) + mm.CHANNEL_SIZE])
        name_raw = bytes(self.state.eeprom[mm.addr_channel_name(ch.index):
                                            mm.addr_channel_name(ch.index)
                                            + mm.CHANNEL_NAME_SIZE])
        attr = bytes(self.state.eeprom[mm.addr_ch_attr(ch.index):
                                        mm.addr_ch_attr(ch.index) + mm.CH_ATTR_SIZE])
        self.channels[row] = mm._decode_record(ch.index, rec, name_raw, attr)

        # Both type and value cells may need to redraw — emit for the row.
        left = self.index(row, COL_RX_TYPE if is_rx else COL_TX_TYPE)
        right = self.index(row, COL_RX_VALUE if is_rx else COL_TX_VALUE)
        self.dataChanged.emit(left, right,
                              [Qt.ItemDataRole.DisplayRole,
                               Qt.ItemDataRole.ForegroundRole])
        self.state.mark_dirty()
        return True

    def _scanlist_color(self, value: int) -> QColor:
        # Last label = "all/both" → yellow; mid labels → green; 0 → muted.
        # Adapts to F4HWN (26 labels, ALL=25) and K5 V1 (4 labels, SL1+SL2=3).
        mm = self.state.profile.memory_module if self.state.has_image else None
        labels = list(getattr(mm, "SCAN_LIST_LABELS", [])) if mm else []
        max_slot = len(labels) - 1 if labels else 25
        if value == max_slot and max_slot > 0:
            return QColor("#f9e2af")  # ALL / SL1+SL2 → yellow
        if 1 <= value < max_slot:
            return QColor("#a6e3a1")  # in a list → green
        return QColor("#7f849c")      # OFF / uninit → muted


class ToneTypeDelegate(QStyledItemDelegate):
    """
    First half of the 2-stage tone editor (matches the upstream K5/K1 tooling'
    rxToneType column). Four discoverable options: OFF, CTCSS, DCS-N,
    DCS-I. The Value column is filtered by whatever the user picked
    here.
    """

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(tonesmod.TONE_TYPE_LABELS)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        cur = index.data(Qt.ItemDataRole.EditRole) or tonesmod.TONE_TYPE_OFF
        i = editor.findText(str(cur))
        editor.setCurrentIndex(max(0, i))

    def setModelData(self, editor: QComboBox, model, index):
        model.setData(index, editor.currentText(),
                      Qt.ItemDataRole.EditRole)


class ToneValueDelegate(QStyledItemDelegate):
    """
    Second half of the 2-stage tone editor: shows ONLY the values valid
    for the type currently set on this row. CTCSS → 50 frequencies;
    DCS-N / DCS-I → 104 codes each. Greatly improves discoverability
    over the old single 260-entry combobox.
    """

    def __init__(self, parent, *, is_rx: bool):
        super().__init__(parent)
        self._is_rx = is_rx

    def createEditor(self, parent, option, index):
        # Look up the row's current tone type to filter the values.
        model = index.model()
        # Walk through proxy models (sorting/filtering) to reach the
        # underlying ChannelsModel — only it has `.channels`.
        src_idx = index
        m = model
        while hasattr(m, "mapToSource"):
            src_idx = m.mapToSource(src_idx)
            m = m.sourceModel()
        ch = m.channels[src_idx.row()]
        tmode = ch.rx_tmode if self._is_rx else ch.tx_tmode
        type_label = tonesmod.tone_type_for_tmode(tmode)

        combo = QComboBox(parent)
        values = tonesmod.tone_values_for_type(type_label)
        combo.addItems(values)
        if not values:
            combo.setEnabled(False)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        cur = index.data(Qt.ItemDataRole.EditRole)
        if cur:
            i = editor.findText(str(cur))
            if i >= 0:
                editor.setCurrentIndex(i)

    def setModelData(self, editor: QComboBox, model, index):
        if not editor.isEnabled():
            return
        model.setData(index, editor.currentText(),
                      Qt.ItemDataRole.EditRole)


class _ListDelegate(QStyledItemDelegate):
    """Generic combobox delegate — given a list of (label, value) pairs."""

    def __init__(self, parent, *, items_provider):
        super().__init__(parent)
        self._items_provider = items_provider     # () → list[(label, value)]

    def createEditor(self, parent, option, index):
        items = self._items_provider()
        combo = QComboBox(parent)
        for label, value in items:
            combo.addItem(label, value)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        cur = index.data(Qt.ItemDataRole.EditRole)
        for i in range(editor.count()):
            if editor.itemData(i) == cur:
                editor.setCurrentIndex(i)
                return
        editor.setCurrentIndex(0)

    def setModelData(self, editor: QComboBox, model, index):
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class ScanListDelegate(QStyledItemDelegate):
    """
    Delegate that pops up a combobox for the Scan List column. The list
    of options is built from the active profile's memory module so it
    adapts to F4HWN (OFF / L1..L24 / ALL = 26 entries) versus stock
    K5 V1 (OFF / SL1 / SL2 / SL1+SL2 = 4 entries).
    """

    def __init__(self, parent, *, profile_provider=None):
        super().__init__(parent)
        self._profile_provider = profile_provider

    def _labels(self) -> list[str]:
        if self._profile_provider is None:
            return ["OFF"] + [f"L{i}" for i in range(1, 25)] + ["ALL"]
        mm = self._profile_provider().memory_module
        return list(getattr(mm, "SCAN_LIST_LABELS", []))

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        for i, label in enumerate(self._labels()):
            combo.addItem(label, i)
        return combo

    def setEditorData(self, editor: QComboBox, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        for i in range(editor.count()):
            if editor.itemData(i) == value:
                editor.setCurrentIndex(i)
                return
        editor.setCurrentIndex(0)

    def setModelData(self, editor: QComboBox, model, index):
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class ChannelsView(QWidget):
    """Top-level channels page."""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._build_ui()
        self.state.eeprom_loaded.connect(self._on_eeprom_loaded)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        root.addWidget(PageHeader(
            "Channels",
            "Edit each channel's scan list inline. Apply your changes at the top right.",
        ))

        # ── Row 1: search + show empty toggle ───────────────────────────
        from PySide6.QtWidgets import QCheckBox
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name or frequency…")
        bar.addWidget(self.search, 1)
        self.show_empty_chk = QCheckBox("Show empty slots")
        self.show_empty_chk.setToolTip(
            "Show every channel slot, including unprogrammed ones. "
            "Useful when filling specific slot numbers (e.g. CH 17 for "
            "a personal channel)."
        )
        self.show_empty_chk.toggled.connect(self._on_show_empty_toggled)
        bar.addWidget(self.show_empty_chk)
        self.new_channel_btn = QPushButton("+ New channel")
        self.new_channel_btn.setObjectName("PrimaryBtn")
        self.new_channel_btn.setToolTip(
            "Create a new channel in the first empty slot (or a "
            "specific one). Quicker than the Show-empty + edit dance."
        )
        self.new_channel_btn.clicked.connect(self._on_new_channel)
        bar.addWidget(self.new_channel_btn)
        # CSV import/export — CHIRP-compatible columns so users can
        # round-trip channels with CHIRP, spreadsheets, or anything
        # else that handles the CHIRP CSV format.
        self.import_csv_btn = QPushButton("Import CSV…")
        self.import_csv_btn.setObjectName("SecondaryBtn")
        self.import_csv_btn.setToolTip(
            "Import a CHIRP-compatible CSV — Location/Name/Frequency/"
            "Mode required; Duplex/Offset/Power/Tone columns optional. "
            "If the CSV has 'LISTA N' in Comment, scan list assignment "
            "is recovered automatically."
        )
        self.import_csv_btn.clicked.connect(self._on_import_csv)
        bar.addWidget(self.import_csv_btn)
        self.export_csv_btn = QPushButton("Export CSV…")
        self.export_csv_btn.setObjectName("SecondaryBtn")
        self.export_csv_btn.setToolTip(
            "Save every configured channel to a CHIRP-compatible .csv. "
            "Open it in CHIRP, a spreadsheet, or re-import here later."
        )
        self.export_csv_btn.clicked.connect(self._on_export_csv)
        bar.addWidget(self.export_csv_btn)
        root.addLayout(bar)

        # ── Row 2: bulk-edit ─────────────────────────────────────────────
        # "Pick a field, pick a value, apply to selection." Mirrors the
        # spreadsheet-style bulk-edit ribbon power users expect, and
        # generalises the previous scanlist-only bulk flow.
        bulk_bar = QHBoxLayout()
        bulk_bar.setSpacing(8)
        bulk_label = QLabel("Bulk edit:")
        bulk_label.setStyleSheet("color:#7f849c;")
        bulk_bar.addWidget(bulk_label)
        self.bulk_field_combo = QComboBox()
        for label in ("Scan List", "Mode", "Duplex", "Power", "Step",
                      "RX Tone Type", "TX Tone Type"):
            self.bulk_field_combo.addItem(label)
        self.bulk_field_combo.currentIndexChanged.connect(
            self._on_bulk_field_changed
        )
        bulk_bar.addWidget(self.bulk_field_combo)
        self.bulk_combo = QComboBox()           # value combo (re-populated)
        bulk_bar.addWidget(self.bulk_combo, 1)
        self.apply_bulk_btn = QPushButton("Apply to selected")
        self.apply_bulk_btn.setObjectName("SecondaryBtn")
        self.apply_bulk_btn.clicked.connect(self._apply_bulk)
        bulk_bar.addWidget(self.apply_bulk_btn)
        root.addLayout(bulk_bar)

        # Initial population of the value combo (defaults to Scan List).
        self._populate_bulk_combo()

        # Table
        self.model = ChannelsModel(self.state)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)  # search every column

        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        # Force ascending order by channel number on first display so the
        # list reads 1 → 2 → 3 → … instead of inheriting any stale sort
        # indicator left over from a previous interaction.
        self.table.sortByColumn(COL_INDEX, Qt.SortOrder.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Single-click on a selected cell opens the editor (also F2,
        # Enter, or any key starts editing). Discoverability boost vs
        # the previous "DoubleClicked only" behaviour.
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        # Duplex / Power / Step combobox delegates. Items are sourced
        # from the active profile's memory module so a future radio with
        # different power levels gets the right list automatically.
        def _duplex_items():
            mm = self.state.profile.memory_module if self.state.has_image else None
            labels = list(getattr(mm, "DUPLEX_LABELS", ["", "+", "-"]))
            # Show "—" for the empty (simplex) entry so the combobox row
            # is visible — Qt collapses entries with empty label.
            return [(("—" if not l else l), l) for l in labels]
        self.table.setItemDelegateForColumn(
            COL_DUPLEX, _ListDelegate(self.table,
                                      items_provider=_duplex_items))

        def _power_items():
            mm = self.state.profile.memory_module if self.state.has_image else None
            lbls = list(getattr(mm, "POWER_LEVELS", []))
            return [(lbl, i) for i, lbl in enumerate(lbls)]
        self.table.setItemDelegateForColumn(
            COL_POWER, _ListDelegate(self.table,
                                     items_provider=_power_items))

        def _step_items():
            mm = self.state.profile.memory_module if self.state.has_image else None
            steps = list(getattr(mm, "STEPS_KHZ", []))
            return [(f"{s:g} kHz", i) for i, s in enumerate(steps)]
        self.table.setItemDelegateForColumn(
            COL_STEP, _ListDelegate(self.table,
                                    items_provider=_step_items))

        self.table.setItemDelegateForColumn(
            COL_SCAN,
            ScanListDelegate(
                self.table,
                profile_provider=lambda: self.state.profile,
            ),
        )
        self.table.setItemDelegateForColumn(
            COL_RX_TYPE, ToneTypeDelegate(self.table))
        self.table.setItemDelegateForColumn(
            COL_TX_TYPE, ToneTypeDelegate(self.table))
        self.table.setItemDelegateForColumn(
            COL_RX_VALUE, ToneValueDelegate(self.table, is_rx=True))
        self.table.setItemDelegateForColumn(
            COL_TX_VALUE, ToneValueDelegate(self.table, is_rx=False))
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_FREQ, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_MODE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_DUPLEX, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_OFFSET, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_POWER, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_STEP, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_SCAN, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_RX_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_RX_VALUE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TX_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TX_VALUE, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(COL_INDEX, 60)
        root.addWidget(self.table, 1)

        self.empty_hint = QLabel("Read the EEPROM (Dashboard → Read EEPROM) to "
                                 "populate this view.")
        self.empty_hint.setStyleSheet("color:#7f849c; padding:30px;")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_hint)

        self._on_eeprom_loaded()

    def _on_eeprom_loaded(self) -> None:
        has = self.state.has_image
        self.table.setVisible(has)
        self.search.setVisible(has)
        self.bulk_combo.setVisible(has)
        self.apply_bulk_btn.setVisible(has)
        self.empty_hint.setVisible(not has)
        # Repopulate the bulk-edit combo with the active profile's
        # scanlist labels (4 entries on stock, 26 on F4HWN).
        self._populate_bulk_combo()

    # ----------------------------------------------------------------
    # Bulk-edit ribbon
    # ----------------------------------------------------------------

    # Map the field-combo label to (column, item-builder).
    # The builder returns a list of (display label, value to store)
    # pairs scoped to the active profile.
    def _bulk_field_to_column(self, label: str) -> int:
        return {
            "Scan List":     COL_SCAN,
            "Mode":          COL_MODE,
            "Duplex":        COL_DUPLEX,
            "Power":         COL_POWER,
            "Step":          COL_STEP,
            "RX Tone Type":  COL_RX_TYPE,
            "TX Tone Type":  COL_TX_TYPE,
        }.get(label, COL_SCAN)

    def _bulk_value_options(self, label: str) -> list[tuple[str, object]]:
        mm = self.state.profile.memory_module if self.state.has_image else None
        if label == "Scan List":
            labels = list(getattr(mm, "SCAN_LIST_LABELS", [])) if mm else (
                ["OFF"] + [f"L{i}" for i in range(1, 25)] + ["ALL"]
            )
            return [(lab, i) for i, lab in enumerate(labels)]
        if label == "Mode":
            return [(m, m) for m in (getattr(mm, "MODE_TABLE", ["FM", "NFM", "AM"]))]
        if label == "Duplex":
            duplex = getattr(mm, "DUPLEX_LABELS", ["", "+", "-"])
            return [(("—" if not d else d), d) for d in duplex]
        if label == "Power":
            return [(lab, i) for i, lab in enumerate(getattr(mm, "POWER_LEVELS", []))]
        if label == "Step":
            return [(f"{s:g} kHz", i)
                    for i, s in enumerate(getattr(mm, "STEPS_KHZ", []))]
        if label in ("RX Tone Type", "TX Tone Type"):
            return [(t, t) for t in tonesmod.TONE_TYPE_LABELS]
        return []

    def _populate_bulk_combo(self) -> None:
        field_label = self.bulk_field_combo.currentText()
        options = self._bulk_value_options(field_label)
        self.bulk_combo.blockSignals(True)
        self.bulk_combo.clear()
        self.bulk_combo.addItem("Set selected to…", None)  # sentinel
        for label, value in options:
            self.bulk_combo.addItem(label, value)
        self.bulk_combo.blockSignals(False)

    def _on_bulk_field_changed(self, _: int) -> None:
        self._populate_bulk_combo()

    def _on_show_empty_toggled(self, checked: bool) -> None:
        self.model.show_empty = checked
        self.model.reload()

    def _on_import_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path as _Path
        if not self.state.has_image:
            QMessageBox.information(
                self, "Read EEPROM first",
                "Click Read EEPROM in the Dashboard first so the toolkit "
                "knows the radio's existing layout. The CSV will be applied "
                "on top of that image."
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a CHIRP-compatible CSV", "",
            "CSV files (*.csv);;All files (*.*)"
        )
        if not path:
            return
        ans = QMessageBox.question(
            self, "Import CSV",
            f"About to apply\n  {path}\n"
            f"on top of the current EEPROM image.\n\n"
            f"Channels in the CSV with 'LISTA N' in Comment will pick "
            f"up scan list N. Channels NOT in the CSV are left "
            f"untouched.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            from ...kradio import workflow as wf
            mm = self.state.profile.memory_module
            result = wf.import_channels_from_csv(
                self.state.eeprom, _Path(path),
                derive_scanlist_from_comment=True,
                memory_module=mm,
            )
        except Exception as e:                                 # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(e))
            return
        self.model.reload()
        self.state.mark_dirty()
        msg = (f"Imported {result['updated']} channels.\n"
               f"Skipped: {len(result['skipped'])}.\n"
               f"Cleared: {result['cleared']}.")
        if result['skipped']:
            msg += "\n\nFirst skip reason:\n  " + result['skipped'][0]
        QMessageBox.information(self, "Import complete", msg)

    def _on_export_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path as _Path
        if not self.state.has_image:
            QMessageBox.information(
                self, "Read EEPROM first",
                "Click Read EEPROM in the Dashboard first."
            )
            return
        suggested = "channels_export.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save channels to CSV", suggested,
            "CSV files (*.csv);;All files (*.*)"
        )
        if not path:
            return
        try:
            from ...kradio import workflow as wf
            mm = self.state.profile.memory_module
            n = wf.export_channels_to_csv(
                bytes(self.state.eeprom), _Path(path),
                memory_module=mm,
            )
        except Exception as e:                                 # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))
            return
        QMessageBox.information(
            self, "Export complete",
            f"Wrote {n} channels to:\n  {path}\n\n"
            f"You can re-import this file here, open it in CHIRP, or "
            f"edit it in a spreadsheet."
        )

    def _on_new_channel(self) -> None:
        """Open a small dialog to create a channel in an empty slot."""
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
            QSpinBox,
        )
        if not self.state.has_image:
            QMessageBox.information(
                self, "Read EEPROM first",
                "Click Read EEPROM in the Dashboard tab so the toolkit "
                "knows what's already on your radio. Then come back here "
                "to add channels."
            )
            return

        mm = self.state.profile.memory_module
        # Find the first empty slot to suggest as default.
        all_chans = mm.decode_all_channels(bytes(self.state.eeprom))
        first_empty = next((c.index for c in all_chans if c.is_empty), 0)

        dlg = QDialog(self)
        dlg.setWindowTitle("New channel")
        form = QFormLayout(dlg)

        slot_spin = QSpinBox()
        slot_spin.setRange(1, mm.NUM_CHANNELS)
        slot_spin.setValue(first_empty + 1)   # 1-based for the user
        form.addRow("Slot (1-based):", slot_spin)

        name_edit = QLineEdit()
        name_edit.setMaxLength(getattr(mm, "CHANNEL_NAME_MAX", 10))
        name_edit.setPlaceholderText("e.g. HAM-V")
        form.addRow("Name:", name_edit)

        freq_spin = QDoubleSpinBox()
        freq_spin.setDecimals(4)
        freq_spin.setRange(18.0, 1300.0)
        freq_spin.setSuffix(" MHz")
        freq_spin.setValue(145.5)
        form.addRow("Frequency:", freq_spin)

        mode_combo = QComboBox()
        for m in mm.MODE_TABLE:
            mode_combo.addItem(m)
        form.addRow("Mode:", mode_combo)

        scanlist_combo = QComboBox()
        for i, label in enumerate(getattr(mm, "SCAN_LIST_LABELS", ["OFF"])):
            scanlist_combo.addItem(label, i)
        form.addRow("Scan list:", scanlist_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        slot = slot_spin.value() - 1
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required",
                                "Channel name can't be empty.")
            return
        freq_hz = int(round(freq_spin.value() * 1_000_000))
        mode = mode_combo.currentText()
        scanlist = scanlist_combo.currentData()

        # Refuse to overwrite a non-empty slot without confirmation —
        # protects against fat-fingered slot numbers.
        if not all_chans[slot].is_empty:
            ans = QMessageBox.question(
                self,
                "Slot already in use",
                f"Slot {slot + 1} is currently '{all_chans[slot].name}' "
                f"({all_chans[slot].freq_mhz:.4f} MHz). Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        try:
            mm.patch_channel_in_image(
                self.state.eeprom,
                idx=slot,
                name=name,
                freq_hz=freq_hz,
                mode=mode,
                scanlist=scanlist,
            )
        except (ValueError, KeyError) as e:
            QMessageBox.critical(self, "Could not create channel",
                                 f"{type(e).__name__}: {e}")
            return

        self.model.reload()
        self.state.mark_dirty()

    def _apply_bulk(self) -> None:
        idx = self.bulk_combo.currentIndex()
        if idx <= 0:
            return
        new_value = self.bulk_combo.itemData(idx)
        column = self._bulk_field_to_column(self.bulk_field_combo.currentText())
        rows_proxy = self.table.selectionModel().selectedRows()
        for proxy_idx in rows_proxy:
            src_idx = self.proxy.mapToSource(proxy_idx)
            cell = self.model.index(src_idx.row(), column)
            self.model.setData(cell, new_value, Qt.ItemDataRole.EditRole)
