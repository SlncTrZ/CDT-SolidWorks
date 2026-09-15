from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.sketch.models import LineSegment, PlaneKind


_MODULE_PATH = Path(__file__).parents[2] / "src/cdt_solidworks/integration/sketch.py"
_SPEC = importlib.util.spec_from_file_location("agent1_integration_sketch", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)
IntegratedSketchService = module.IntegratedSketchService


class StubService:
    pass


def integrated(root: Path):
    return IntegratedSketchService(
        service=StubService(),
        path_policy=DocumentPathPolicy((root,)),
    )


def test_rectangle_primitive_expands_to_four_connected_lines():
    with tempfile.TemporaryDirectory() as tmp:
        service = integrated(Path(tmp))
        definition = service._definition(
            name="Rect",
            plane="front",
            entities=[{"type": "rectangle", "corner1_mm": [0, 0], "corner2_mm": [20, 10]}],
            constraints=None,
            dimensions=None,
        )
        assert len(definition.entities) == 4
        assert all(isinstance(item, LineSegment) for item in definition.entities)
        assert definition.entities[0].start == definition.entities[-1].end
        assert definition.entities[0].end == definition.entities[1].start
        assert definition.entities[1].end == definition.entities[2].start
        assert definition.entities[2].end == definition.entities[3].start


def test_reference_plane_identity_is_explicit_and_not_localized_name_guessing():
    with tempfile.TemporaryDirectory() as tmp:
        service = integrated(Path(tmp))
        definition = service._definition(
            name="OnCustomPlane",
            plane="reference:Plane-Offset-25",
            entities=[{"type": "line", "start_mm": [0, 0], "end_mm": [10, 0]}],
            constraints=None,
            dimensions=None,
        )
        assert definition.plane.kind is PlaneKind.REFERENCE
        assert definition.plane.reference_id == "Plane-Offset-25"
