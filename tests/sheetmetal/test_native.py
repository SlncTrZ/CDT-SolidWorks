from __future__ import annotations

from cdt_solidworks.sheetmetal.native import SheetMetalNativeAdapter


class FakeApi:
    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if callable(member) else member


class FakeSession:
    def __init__(self) -> None:
        self.api = FakeApi()


class Feature:
    def __init__(self, raw):
        self.raw = raw

    def IsSuppressed2(self, config_opt, config_names):
        assert config_opt == 1
        assert config_names is None
        return self.raw


def test_flat_pattern_suppression_readback_handles_com_array_shape() -> None:
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)
    assert adapter._is_suppressed(Feature((True,))) is True
    assert adapter._is_suppressed(Feature((False,))) is False


def test_flat_pattern_suppression_readback_handles_scalar_shape() -> None:
    adapter = SheetMetalNativeAdapter(FakeSession(), path_policy=None)
    assert adapter._is_suppressed(Feature(True)) is True
    assert adapter._is_suppressed(Feature(False)) is False


def test_edge_flange_is_not_claimed_without_persistent_edge_identity() -> None:
    reason = SheetMetalNativeAdapter.unsupported_edge_flange_reason()
    assert "persistent edge-identity" in reason
    assert "not deterministic" in reason
