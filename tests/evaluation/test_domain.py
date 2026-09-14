import math
import unittest

from cdt_solidworks.evaluation.domain import (
    BoundingBox,
    EvaluationPostconditionError,
    EvaluationRefusal,
    EvaluationService,
    GeometrySanity,
    InterferenceSnapshot,
    MassProperties,
    MeasureSnapshot,
)


class FakeEvaluationAdapter:
    def __init__(self):
        self.mass = MassProperties(
            mass_kg=2.5,
            volume_m3=0.001,
            surface_area_m2=0.06,
            center_of_mass_m=(0.01, 0.02, 0.03),
            inertia_kg_m2=(0.1, 0.2, 0.3, 0.0, 0.0, 0.0),
        )
        self.bounds = BoundingBox(min_m=(0.0, 0.0, 0.0), max_m=(0.1, 0.2, 0.3), approximate=True)
        self.measurement = MeasureSnapshot(
            distance_m=0.025,
            angle_rad=None,
            radius_m=None,
            diameter_m=None,
        )
        self.interference_rows = (
            InterferenceSnapshot(
                identity="int-1",
                component_a="plate-1",
                component_b="bolt-1",
                volume_m3=1e-7,
            ),
        )
        self.sanity = GeometrySanity(
            solid_body_count=1,
            surface_body_count=0,
            component_count=0,
            feature_error_count=0,
        )

    def mass_properties(self, document_id, configuration):
        return self.mass

    def bounding_box(self, document_id, configuration):
        return self.bounds

    def measure(self, document_id, refs):
        self.measure_refs = refs
        return self.measurement

    def interferences(self, assembly_id, configuration):
        return self.interference_rows

    def geometry_sanity(self, document_id, configuration):
        return self.sanity


class EvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeEvaluationAdapter()
        self.service = EvaluationService(self.adapter)

    def test_mass_properties_require_finite_nonnegative_native_readback(self):
        result = self.service.mass_properties("part-1", "Default")
        self.assertEqual(2.5, result.mass_kg)
        self.assertEqual((0.01, 0.02, 0.03), result.center_of_mass_m)

    def test_nonfinite_mass_property_is_rejected(self):
        self.adapter.mass = MassProperties(
            mass_kg=math.nan,
            volume_m3=0.001,
            surface_area_m2=0.06,
            center_of_mass_m=(0.0, 0.0, 0.0),
            inertia_kg_m2=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        )
        with self.assertRaisesRegex(EvaluationPostconditionError, "invalid_mass_properties"):
            self.service.mass_properties("part-1")

    def test_bounding_box_orders_all_axes_and_preserves_approximate_flag(self):
        result = self.service.bounding_box("part-1")
        self.assertTrue(result.approximate)
        self.assertEqual((0.1, 0.2, 0.3), result.max_m)

    def test_inverted_bounding_box_is_rejected(self):
        self.adapter.bounds = BoundingBox(min_m=(0.2, 0.0, 0.0), max_m=(0.1, 0.2, 0.3), approximate=True)
        with self.assertRaisesRegex(EvaluationPostconditionError, "invalid_bounding_box"):
            self.service.bounding_box("part-1")

    def test_measure_requires_explicit_references_and_a_finite_result(self):
        result = self.service.measure("part-1", "plane:front", "plane:right")
        self.assertAlmostEqual(0.025, result.distance_m)
        self.assertEqual(("plane:front", "plane:right"), self.adapter.measure_refs)
        with self.assertRaisesRegex(EvaluationRefusal, "invalid_first_ref"):
            self.service.measure("part-1", "", "plane:right")

    def test_single_reference_radius_and_diameter_measurement_is_supported(self):
        self.adapter.measurement = MeasureSnapshot(
            distance_m=None,
            angle_rad=None,
            radius_m=0.005,
            diameter_m=0.010,
        )
        result = self.service.measure("part-1", "sketch:Sketch1:segment:0")
        self.assertEqual(("sketch:Sketch1:segment:0",), self.adapter.measure_refs)
        self.assertAlmostEqual(0.005, result.radius_m)
        self.assertAlmostEqual(0.010, result.diameter_m)

    def test_measurement_rejects_more_than_two_references(self):
        with self.assertRaisesRegex(EvaluationRefusal, "invalid_measurement_reference_count"):
            self.service.measure_refs("part-1", ("a", "b", "c"))

    def test_interference_results_require_distinct_component_identity_and_positive_volume(self):
        result = self.service.interferences("asm-1", "Default")
        self.assertEqual(1, len(result))
        self.assertGreater(result[0].volume_m3, 0.0)

    def test_feature_errors_fail_clean_geometry_gate(self):
        self.adapter.sanity = GeometrySanity(
            solid_body_count=1,
            surface_body_count=0,
            component_count=0,
            feature_error_count=1,
        )
        with self.assertRaisesRegex(EvaluationPostconditionError, "feature_errors_present"):
            self.service.require_clean_geometry("part-1")


if __name__ == "__main__":
    unittest.main()
