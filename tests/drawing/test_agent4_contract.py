from __future__ import annotations

import pytest

from cdt_solidworks.drawing.domain import (
    BomSnapshot,
    DimensionSnapshot,
    DrawingPostconditionError,
    DrawingRefusal,
    DrawingService,
    RebuildReport,
    ViewSnapshot,
)


class _Adapter:
    def __init__(self) -> None:
        self.views = {
            "view-1": ViewSnapshot(
                identity="view-1",
                sheet_name="Sheet1",
                view_kind="front",
                source_model_path=r"C:\\models\\part.SLDPRT",
                source_configuration="Default",
                dangling=False,
            )
        }
        self.dimensions: dict[str, DimensionSnapshot] = {}
        self.boms: dict[str, BomSnapshot] = {}
        self.dimension_mutations = 0
        self.bom_mutations = 0
        self.rebuild = RebuildReport(ok=True)

    def read_view(self, drawing_id: str, view_id: str):
        return self.views.get(view_id)

    def add_dimension(self, drawing_id: str, view_id: str, source_ref: str, x_mm: float, y_mm: float):
        self.dimension_mutations += 1
        snapshot = DimensionSnapshot("dim-1", view_id, source_ref, "10.00", False)
        self.dimensions[snapshot.identity] = snapshot
        return snapshot.identity

    def read_dimension(self, drawing_id: str, dimension_id: str):
        return self.dimensions.get(dimension_id)

    def list_dimensions(self, drawing_id: str):
        return tuple(self.dimensions.values())

    def supports_bom(self, drawing_id: str) -> bool:
        return True

    def create_bom(self, drawing_id: str, view_id: str, source_configuration: str | None):
        self.bom_mutations += 1
        snapshot = BomSnapshot(
            identity="bom-1",
            view_id=view_id,
            source_configuration=source_configuration,
            row_count=2,
            column_count=4,
            rows=(("ITEM NO.", "QTY.", "PART NUMBER", "DESCRIPTION"), ("1", "2", "P-100", "PIN")),
        )
        self.boms[snapshot.identity] = snapshot
        return snapshot.identity

    def read_bom(self, drawing_id: str, bom_id: str):
        return self.boms.get(bom_id)

    def list_boms(self, drawing_id: str):
        return tuple(self.boms.values())

    def list_views(self, drawing_id: str):
        return tuple(self.views.values())

    def rebuild_drawing(self, drawing_id: str):
        return self.rebuild


def _service() -> tuple[DrawingService, _Adapter]:
    adapter = _Adapter()
    return DrawingService(adapter), adapter


def test_dimension_rejects_source_or_configuration_mismatch_before_mutation() -> None:
    service, adapter = _service()
    ref = "swref1.opaque.reference"

    with pytest.raises(DrawingRefusal, match="dimension_source_identity_mismatch"):
        service.add_dimension(
            "drawing.SLDDRW",
            "view-1",
            ref,
            source_model_path=r"C:\\models\\other.SLDPRT",
            source_configuration="Default",
            x_mm=40.0,
            y_mm=30.0,
        )
    with pytest.raises(DrawingRefusal, match="dimension_source_configuration_mismatch"):
        service.add_dimension(
            "drawing.SLDDRW",
            "view-1",
            ref,
            source_model_path=r"C:\\models\\part.SLDPRT",
            source_configuration="Alt",
            x_mm=40.0,
            y_mm=30.0,
        )

    assert adapter.dimension_mutations == 0


def test_dimension_requires_opaque_topology_reference_and_readback() -> None:
    service, adapter = _service()
    with pytest.raises(DrawingRefusal, match="invalid_topology_reference"):
        service.add_dimension(
            "drawing.SLDDRW",
            "view-1",
            "Edge1",
            source_model_path=r"C:\\models\\part.SLDPRT",
            source_configuration="Default",
            x_mm=40.0,
            y_mm=30.0,
        )
    assert adapter.dimension_mutations == 0

    result = service.add_dimension(
        "drawing.SLDDRW",
        "view-1",
        "swref1.opaque.reference",
        source_model_path=r"C:\\models\\part.SLDPRT",
        source_configuration="Default",
        x_mm=40.0,
        y_mm=30.0,
    )
    assert result.identity == "dim-1"
    assert service.list_dimensions("drawing.SLDDRW", "view-1") == (result,)


def test_bom_configuration_mismatch_rejects_before_mutation_and_rows_are_readable() -> None:
    service, adapter = _service()
    with pytest.raises(DrawingRefusal, match="bom_configuration_mismatch"):
        service.create_bom("drawing.SLDDRW", "view-1", "Alt")
    assert adapter.bom_mutations == 0

    result = service.create_bom("drawing.SLDDRW", "view-1", "Default")
    assert result.rows[1] == ("1", "2", "P-100", "PIN")
    assert service.read_bom("drawing.SLDDRW", result.identity) == result


def test_bom_missing_property_cell_is_preserved_without_inventing_a_value() -> None:
    service, adapter = _service()
    adapter.boms["bom-missing-property"] = BomSnapshot(
        identity="bom-missing-property",
        view_id="view-1",
        source_configuration="Default",
        row_count=2,
        column_count=4,
        rows=(
            ("ITEM NO.", "QTY.", "PART NUMBER", "DESCRIPTION"),
            ("1", "1", "P-200", ""),
        ),
        component_ids=((), ("COMP-1",)),
    )

    result = service.read_bom("drawing.SLDDRW", "bom-missing-property")
    assert result.rows[1][3] == ""
    assert result.component_ids[1] == ("COMP-1",)


def test_update_fails_closed_on_dangling_dimension_and_reports_clean_state() -> None:
    service, adapter = _service()
    adapter.dimensions["dim-1"] = DimensionSnapshot(
        "dim-1", "view-1", "swref1.opaque.reference", "10.00", True
    )
    with pytest.raises(DrawingPostconditionError, match="dangling_dimension"):
        service.update_drawing(
            "drawing.SLDDRW",
            source_model_path=r"C:\\models\\part.SLDPRT",
            source_configuration="Default",
        )

    adapter.dimensions["dim-1"] = DimensionSnapshot(
        "dim-1", "view-1", "swref1.opaque.reference", "10.00", False
    )
    adapter.boms["bom-1"] = BomSnapshot(
        "bom-1", "view-1", "Default", 2, 2, (("ITEM", "QTY"), ("1", "2"))
    )
    report = service.update_drawing(
        "drawing.SLDDRW",
        source_model_path=r"C:\\models\\part.SLDPRT",
        source_configuration="Default",
    )
    assert report.view_count == 1
    assert report.dimension_count == 1
    assert report.bom_count == 1
    assert report.current is True
