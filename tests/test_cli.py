"""Unit tests for the CLI's profile-detection helpers (no radio)."""
from quansheng_toolkit.cli import _resolve_profile_for_image
from quansheng_toolkit.kradio.models import (
    F4HWN_FUSION_5X, UVK1_STOCK, UVK5_STOCK,
)


class TestResolveProfileForImage:
    def test_auto_8kb_returns_generic_family_label(self):
        # K1 stock (7.03.x) and K5(8) stock (7.00.x) share an identical
        # EEPROM layout — auto-detect cannot tell them apart from bytes.
        # The label override must clarify this honestly.
        img = b"\xff" * 0x2000
        profile, label = _resolve_profile_for_image(img, "auto")
        assert profile is UVK1_STOCK   # underlying modules are identical
        assert label is not None
        assert "K1" in label and "K5" in label

    def test_auto_picks_f4hwn_for_45kb(self):
        img = b"\xff" * 0xB190
        profile, label = _resolve_profile_for_image(img, "auto")
        assert profile is F4HWN_FUSION_5X
        assert label is None  # F4HWN's own name is fine to display

    def test_explicit_f4hwn_overrides_size(self):
        # Even with an 8KB image, explicit --profile f4hwn wins.
        img = b"\xff" * 0x2000
        profile, label = _resolve_profile_for_image(img, "f4hwn")
        assert profile is F4HWN_FUSION_5X
        assert label is None

    def test_explicit_k1stock_returns_uvk1_with_no_override(self):
        img = b"\xff" * 0x2000
        profile, label = _resolve_profile_for_image(img, "k1stock")
        assert profile is UVK1_STOCK
        assert label is None  # Use the profile's own name

    def test_explicit_k5stock_returns_uvk5_with_no_override(self):
        img = b"\xff" * 0x2000
        profile, label = _resolve_profile_for_image(img, "k5stock")
        assert profile is UVK5_STOCK
        assert label is None

    def test_legacy_k5v1_alias_routes_to_k5stock(self):
        # Old `--profile k5v1` callers used to silently get UVK1_STOCK
        # back, which printed a misleading "UV-K1 stock" label. The
        # explicit choice now honours the user's intent and returns
        # UVK5_STOCK.
        img = b"\xff" * 0x2000
        profile, label = _resolve_profile_for_image(img, "k5v1")
        assert profile is UVK5_STOCK
        assert label is None

    def test_generic_stock_alias_returns_family_label(self):
        img = b"\xff" * 0x2000
        profile, label = _resolve_profile_for_image(img, "stock")
        assert profile is UVK1_STOCK
        assert label is not None and "K1" in label and "K5" in label

    def test_explicit_overrides_size_for_uvk5(self):
        img = b"\xff" * 0xB190  # F4HWN-sized but explicitly K5
        profile, label = _resolve_profile_for_image(img, "k5stock")
        assert profile is UVK5_STOCK
