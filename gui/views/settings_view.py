"""
Radio Settings view: a fully-populated form covering every named setting in
`kradio.settings.SETTINGS_REGISTRY`, organised into thematic sections.

Each editor reads its initial value through `kradio.settings.read_setting`
and writes back via `kradio.settings.apply_setting`, so the form is purely
registry-driven — adding a new setting in `kradio.settings` automatically
makes it appear in the GUI as long as it's listed in `SECTIONS` below.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...kradio import settings as setmod
from ...kradio.models import RadioProfile
from ..state import AppState
from ..widgets import Card, LcdPreview, PageHeader


# Logical grouping of registered settings into UI sections.
# Each list must reference keys that exist in SETTINGS_REGISTRY; missing
# keys are silently skipped so future driver versions can drop a setting
# without breaking the UI.
SECTIONS: dict[str, list[str]] = {
    "Audio & RF": [
        "squelch", "vox_switch", "vox_level", "mic_gain", "mic_bar",
        "set_rxa_fm", "set_rxa_am", "ste", "rp_ste", "AM_fix",
    ],
    "Display & Backlight": [
        "channel_display_mode", "backlight_min", "backlight_max",
        "backlight_time", "backlight_on_TX_RX",
        "set_contrast", "set_inv", "set_gui", "set_met",
    ],
    "TX & Operation": [
        "tx_vfo", "max_talk_time", "set_tot", "set_eot",
        "set_pwr", "set_ptt", "set_nfm", "set_lck",
        "dual_watch", "crossband",
    ],
    "Battery": [
        "battery_save", "battery_type", "battery_text",
    ],
    "Boot & Welcome": [
        "welcome_mode", "logo_line1", "logo_line2", "voice",
        "pwron_password",
    ],
    "Keys & Navigation": [
        "set_nav", "set_key", "set_menu_lock", "key_lock",
        "auto_keypad_lock", "button_beep",
        "key1_shortpress_action", "key1_longpress_action",
        "key2_shortpress_action", "key2_longpress_action",
        "keyM_longpress_action",
    ],
    "Scan & Alerts": [
        "scan_resume_mode", "noaa_autoscan", "alarm_mode",
        "roger_beep", "live_DTMF_decoder",
    ],
    "Timer": [
        "set_tmr", "set_off_tmr",
    ],
    "TX lock & Flags": [
        "int_flock", "int_350en", "int_scren",
    ],
    "VFO assignment": [
        "VFO_A_chn", "VFO_B_chn",
    ],
    "Scan priority": [
        "slPriorEnab", "slDef", "slPriorCh1", "slPriorCh2",
        "call_channel",
    ],
    "DTMF": [
        "dtmf_side_tone", "dtmf_decode_response",
        "dtmf_auto_reset_time", "dtmf_preload_time",
        "dtmf_first_code_persist_time", "dtmf_hash_persist_time",
        "dtmf_code_persist_time", "dtmf_code_interval_time",
        "dtmf_permit_remote_kill", "int_KILLED",
        "dtmf_up_code", "dtmf_down_code",
    ],
    # 48 entries on F4HWN, 20 on K5 V1 stock — registry-driven, the form
    # silently drops missing presets per profile.
    "FM Radio Presets": [f"fm_preset_{i:02d}" for i in range(1, 49)],
}


class SettingsView(QWidget):
    """Form view bound to the EEPROM image, registry-driven."""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._editors: dict[str, QWidget] = {}
        # For each editor we also track:
        #   _rows[key] = (label, editor)  — both the visible widgets so
        #                                    the search can hide them together.
        #   _row_section[key] = QGroupBox  — to recompute section visibility.
        #   _baseline[key] = str           — value as last read; used to
        #                                    compute the "N modified" count.
        self._rows: dict[str, tuple[QWidget, QWidget]] = {}
        self._row_section: dict[str, QGroupBox] = {}
        self._baseline: dict[str, str] = {}
        self._search_text: str = ""
        self.lcd_preview: LcdPreview | None = None
        self._syncing = False
        # Module the form was last built for (None until first build).
        self._active_module = None
        self._build_ui_chrome()
        self.state.eeprom_loaded.connect(self._reload)
        self._reload()

    # ------------------------------------------------------------------ UI

    def _build_ui_chrome(self) -> None:
        """Build the static parts of the page (header, placeholders).
        The form itself is rebuilt per-profile in _rebuild_form()."""
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(28, 24, 28, 24)
        self._root.setSpacing(14)

        self._root.addWidget(PageHeader(
            "Radio Settings",
            "Settings persisted on the radio's EEPROM. Edits are buffered "
            "locally; click Apply Changes (top right) to upload them.",
        ))

        # Top toolbar: live search + modified counter + reset button
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Filter settings…  (try 'backlight', 'tx', 'logo')"
        )
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_box, 1)

        self.modified_label = QLabel("")
        self.modified_label.setStyleSheet(
            "color: #f9e2af; font-size: 12px; font-weight: 600; "
            "padding: 0 8px;"
        )
        self.modified_label.setVisible(False)
        toolbar.addWidget(self.modified_label)

        self.btn_revert = QPushButton("Revert")
        self.btn_revert.setObjectName("SecondaryBtn")
        self.btn_revert.setToolTip(
            "Discard local edits and reload values from the current EEPROM image."
        )
        self.btn_revert.clicked.connect(self._on_revert_clicked)
        self.btn_revert.setVisible(False)
        toolbar.addWidget(self.btn_revert)

        self._root.addLayout(toolbar)

        self.empty = QLabel("Read the EEPROM first (Dashboard tab).")
        self.empty.setStyleSheet("color:#7f849c; padding:30px;")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._root.addWidget(self.empty)

        # Shown when the active profile has no settings module wired up
        # (e.g. a future profile that hasn't had its registry written yet).
        self.no_settings_card = Card()
        nsl = QVBoxLayout(self.no_settings_card)
        nsl.setContentsMargins(28, 24, 28, 24)
        nsl.setSpacing(8)
        nstitle = QLabel("Settings editor not available for this firmware")
        nstitle.setStyleSheet("font-size: 16px; font-weight: 700;")
        nsl.addWidget(nstitle)
        nstext = QLabel(
            "The active profile does not yet have a typed settings "
            "registry in this build. Read & channel-list editing still "
            "work normally; in the meantime, use CHIRP for settings on "
            "this firmware."
        )
        nstext.setWordWrap(True)
        nstext.setStyleSheet("color: #a6adc8; font-size: 13px;")
        nsl.addWidget(nstext)
        self._root.addWidget(self.no_settings_card)
        self.no_settings_card.setVisible(False)

        # Placeholder; replaced by _rebuild_form() the first time we
        # have an EEPROM image and a settings module to bind against.
        self.form_root: QWidget | None = None

    def _rebuild_form(self, mod) -> None:
        """
        Rebuild the section-grouped form using `mod.SETTINGS_REGISTRY` as
        the source of truth. Keys listed in SECTIONS but missing from the
        active registry are silently dropped, so smaller stock registries
        produce shorter forms automatically. Sections that end up with
        zero rendered fields are hidden.
        """
        if self.form_root is not None:
            self._root.removeWidget(self.form_root)
            self.form_root.deleteLater()
        self._editors = {}
        self._rows = {}
        self._row_section = {}
        self._baseline = {}
        self.lcd_preview = None

        self.form_root = QWidget()
        form_layout = QGridLayout(self.form_root)
        form_layout.setHorizontalSpacing(20)
        form_layout.setVerticalSpacing(14)

        # Track the "logical" cell so empty sections don't leave gaps.
        cell_idx = 0
        for section_name, keys in SECTIONS.items():
            box = QGroupBox(section_name.upper())
            box_layout = QFormLayout(box)
            box_layout.setSpacing(10)
            box_layout.setHorizontalSpacing(20)
            box_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                         | Qt.AlignmentFlag.AlignVCenter)
            box_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
            box_layout.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            rendered = 0
            for key in keys:
                spec = mod.SETTINGS_REGISTRY.get(key)
                if spec is None:
                    continue
                editor = self._make_editor(spec)
                if editor is None:
                    continue
                self._editors[key] = editor
                label = QLabel(_friendly_title(spec.name))
                label.setToolTip(spec.description)
                label.setMinimumWidth(160)
                label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                    QSizePolicy.Policy.Preferred)
                box_layout.addRow(label, editor)
                self._rows[key] = (label, editor)
                self._row_section[key] = box
                rendered += 1

            # Live LCD preview embedded in the "Boot & Welcome" section,
            # but only if the active registry actually exposes the logo
            # fields (F4HWN and K5 V1 stock both do).
            if section_name == "Boot & Welcome" and rendered:
                if ("logo_line1" in mod.SETTINGS_REGISTRY
                        and "logo_line2" in mod.SETTINGS_REGISTRY):
                    self.lcd_preview = LcdPreview()
                    preview_label = QLabel("LIVE PREVIEW")
                    preview_label.setStyleSheet(
                        "color:#7f849c; font-size:11px; font-weight:700; "
                        "letter-spacing:1.5px;"
                    )
                    box_layout.addRow(preview_label, self.lcd_preview)

            if rendered == 0:
                box.deleteLater()
                continue

            row, col = divmod(cell_idx, 2)
            form_layout.addWidget(box, row, col)
            cell_idx += 1

        self._root.addWidget(self.form_root, 1)
        self._active_module = mod

    def _make_editor(self, spec: setmod.SettingSpec) -> QWidget | None:
        if spec.kind in ("int", "u16le", "u32le"):
            lo, hi = spec.bounds  # type: ignore[misc]
            w = QSpinBox()
            # QSpinBox max is signed int32; clamp the bounds we render.
            w.setRange(max(lo, -(2**31)), min(hi, 2**31 - 1))
            w.valueChanged.connect(
                lambda v, k=spec.name: self._on_changed(k, str(v))
            )
            return w
        if spec.kind == "bool":
            w = QCheckBox()
            w.toggled.connect(
                lambda v, k=spec.name: self._on_changed(k, "on" if v else "off")
            )
            return w
        if spec.kind == "enum":
            w = QComboBox()
            w.addItems(list(spec.bounds))  # type: ignore[arg-type]
            w.currentTextChanged.connect(
                lambda v, k=spec.name: self._on_changed(k, v)
            )
            return w
        if spec.kind == "str":
            w = QLineEdit()
            # Use display_length when present (the radio shows fewer chars
            # than it stores) so the editor never lets the user type
            # something that gets visually truncated on the LCD.
            visible = spec.display_length or spec.length
            w.setMaxLength(visible)
            w.editingFinished.connect(
                lambda k=spec.name, ed=w: self._on_changed(k, ed.text())
            )
            return w
        if spec.kind == "fm_freq":
            # Free-form line edit: accepts "100.5", "100.5 MHz", or "OFF".
            w = QLineEdit()
            w.setPlaceholderText("e.g. 100.5 MHz   (OFF to clear)")
            w.editingFinished.connect(
                lambda k=spec.name, ed=w: self._on_changed(k, ed.text())
            )
            return w
        return None

    # ---------------------------------------------------------- Sync logic

    def _reload(self) -> None:
        has = self.state.has_image
        profile = self.state.profile if has else None
        mod = profile.settings_module if profile else None

        self.empty.setVisible(not has)
        self.no_settings_card.setVisible(has and mod is None)

        if not (has and mod is not None):
            if self.form_root is not None:
                self.form_root.setVisible(False)
            return

        # Rebuild the form when we cross profiles (different settings module).
        if mod is not self._active_module:
            self._rebuild_form(mod)
        if self.form_root is not None:
            self.form_root.setVisible(True)

        self._syncing = True
        try:
            for key, editor in self._editors.items():
                try:
                    value = mod.read_setting(bytes(self.state.eeprom), key)
                except Exception:
                    continue
                self._set_editor(editor, value)
                # Snapshot the post-read text — that's what _modified_count compares against.
                self._baseline[key] = self._editor_text_for_key(key)
            if self.lcd_preview is not None:
                try:
                    l1 = mod.read_setting(bytes(self.state.eeprom), "logo_line1")
                    l2 = mod.read_setting(bytes(self.state.eeprom), "logo_line2")
                    self.lcd_preview.set_lines(str(l1), str(l2))
                except Exception:
                    pass
        finally:
            self._syncing = False
        self._update_modified_indicator()
        self._apply_search_filter()

    def _set_editor(self, editor: QWidget, value) -> None:
        if isinstance(editor, QSpinBox):
            try:
                editor.setValue(int(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(editor, QCheckBox):
            editor.setChecked(bool(value))
        elif isinstance(editor, QComboBox):
            text = str(value)
            idx = editor.findText(text)
            if idx >= 0:
                editor.setCurrentIndex(idx)
            else:
                # Out-of-range raw value (e.g. uninitialised EEPROM).
                editor.setCurrentIndex(0)
        elif isinstance(editor, QLineEdit):
            editor.setText(str(value))

    def _on_changed(self, key: str, value: str) -> None:
        if self._syncing:
            return
        if not self.state.has_image:
            return
        mod = self._active_module
        if mod is None:
            return
        try:
            mod.apply_setting(self.state.eeprom, key, value)
        except (KeyError, ValueError):
            return
        self.state.mark_dirty()
        # Keep the LCD preview in sync as the user types.
        if key in ("logo_line1", "logo_line2") and self.lcd_preview is not None:
            l1 = self._editor_text("logo_line1")
            l2 = self._editor_text("logo_line2")
            self.lcd_preview.set_lines(l1, l2)
        self._update_modified_indicator()

    def _editor_text(self, key: str) -> str:
        ed = self._editors.get(key)
        if isinstance(ed, QLineEdit):
            return ed.text()
        return ""

    def _editor_text_for_key(self, key: str) -> str:
        """Return the editor's current value as a comparable string."""
        ed = self._editors.get(key)
        if isinstance(ed, QSpinBox):
            return str(ed.value())
        if isinstance(ed, QCheckBox):
            return "on" if ed.isChecked() else "off"
        if isinstance(ed, QComboBox):
            return ed.currentText()
        if isinstance(ed, QLineEdit):
            return ed.text()
        return ""

    # ---------------------------------------------------------- Search

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text
        self._apply_search_filter()

    def _apply_search_filter(self) -> None:
        q = self._search_text.strip().lower()
        if not self._rows:
            return
        sections_with_visible: dict[QGroupBox, int] = {}
        for key, (label, editor) in self._rows.items():
            visible = True
            if q:
                hay = (key + " " + _friendly_title(key)).lower()
                visible = q in hay
            label.setVisible(visible)
            editor.setVisible(visible)
            sec = self._row_section.get(key)
            if sec is not None:
                sections_with_visible[sec] = sections_with_visible.get(sec, 0) + (1 if visible else 0)
        # Hide section group boxes that have zero visible children.
        for sec, count in sections_with_visible.items():
            sec.setVisible(count > 0)

    # ---------------------------------------------------------- Modified counter

    def _modified_count(self) -> int:
        n = 0
        for key in self._editors:
            current = self._editor_text_for_key(key)
            base = self._baseline.get(key)
            if base is None:
                continue
            if current != base:
                n += 1
        return n

    def _update_modified_indicator(self) -> None:
        n = self._modified_count()
        if n == 0:
            self.modified_label.setVisible(False)
            self.btn_revert.setVisible(False)
        else:
            self.modified_label.setText(
                f"● {n} setting{'s' if n != 1 else ''} modified"
            )
            self.modified_label.setVisible(True)
            self.btn_revert.setVisible(True)

    def _on_revert_clicked(self) -> None:
        # Reload editor values from the current EEPROM (which still
        # reflects the pending edits — but the user wants to undo any
        # of those edits from the last read). For robustness, call
        # _reload() which is the same path used after Read EEPROM.
        # The simplest safe thing is to write each baseline value
        # back through apply_setting so the EEPROM image returns to
        # the post-read state, then re-sync the editors.
        if not self.state.has_image:
            return
        mod = self._active_module
        if mod is None:
            return
        for key, base in self._baseline.items():
            try:
                mod.apply_setting(self.state.eeprom, key, base)
            except (KeyError, ValueError):
                continue
        self._reload()


def _friendly_title(key: str) -> str:
    """`backlight_min` -> `Backlight min`. Acronyms stay uppercase."""
    upper_words = {"vox", "ste", "vfo", "tx", "rx", "fm", "am", "nfm",
                   "ptt", "dtmf", "noaa"}
    parts = key.split("_")
    out: list[str] = []
    for i, p in enumerate(parts):
        if p in upper_words:
            out.append(p.upper())
        elif p == "AM" or p == "DTMF":  # already uppercase tokens in some keys
            out.append(p)
        elif i == 0:
            out.append(p.capitalize())
        else:
            out.append(p)
    return " ".join(out)
