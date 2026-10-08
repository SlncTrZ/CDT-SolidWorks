import unittest

from cdt_solidworks.mbd.domain import (
    MbdPostconditionError,
    MbdRefusal,
    MbdService,
    MbdSnapshot,
    PmiAnnotation,
    PmiKind,
)


class FakeMbdAdapter:
    def __init__(self):
        self.supported = True
        self.snapshot = MbdSnapshot(
            document_id="part-1",
            configuration="Default",
            annotations=(
                PmiAnnotation(
                    identity="gtol-1",
                    kind=PmiKind.GTOL,
                    text="POSITION 0.1 A B C",
                    dangling=False,
                ),
                PmiAnnotation(
                    identity="datum-a",
                    kind=PmiKind.DATUM,
                    text="A",
                    dangling=False,
                ),
            ),
        )

    def supports_query(self, document_id):
        return self.supported

    def query_pmi(self, document_id, configuration):
        return self.snapshot


class MbdServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeMbdAdapter()
        self.service = MbdService(self.adapter)

    def test_query_returns_typed_pmi_without_mutation(self):
        result = self.service.query_pmi("part-1", "Default")
        self.assertEqual(2, len(result.annotations))
        self.assertEqual(PmiKind.GTOL, result.annotations[0].kind)

    def test_unavailable_dimxpert_is_typed_refusal(self):
        self.adapter.supported = False
        with self.assertRaisesRegex(MbdRefusal, "unsupported_capability"):
            self.service.query_pmi("part-1")

    def test_document_identity_mismatch_is_rejected(self):
        self.adapter.snapshot = MbdSnapshot(
            document_id="other",
            configuration=None,
            annotations=(),
        )
        with self.assertRaisesRegex(MbdPostconditionError, "document_identity_mismatch"):
            self.service.query_pmi("part-1")

    def test_dangling_pmi_is_not_promoted_as_valid_readback(self):
        self.adapter.snapshot = MbdSnapshot(
            document_id="part-1",
            configuration=None,
            annotations=(
                PmiAnnotation(
                    identity="gtol-1",
                    kind=PmiKind.GTOL,
                    text="POSITION 0.1 A",
                    dangling=True,
                ),
            ),
        )
        with self.assertRaisesRegex(MbdPostconditionError, "dangling_pmi"):
            self.service.query_pmi("part-1")

    def test_blank_annotation_identity_is_rejected(self):
        self.adapter.snapshot = MbdSnapshot(
            document_id="part-1",
            configuration=None,
            annotations=(
                PmiAnnotation(identity="", kind=PmiKind.DATUM, text="A", dangling=False),
            ),
        )
        with self.assertRaisesRegex(MbdPostconditionError, "invalid_pmi_identity"):
            self.service.query_pmi("part-1")


if __name__ == "__main__":
    unittest.main()
