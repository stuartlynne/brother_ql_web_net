from __future__ import annotations

from brother_ql_web.printers import label_sizes_for_model, media_to_label_size

from tests import TestCase


class MediaToLabelSizeTestCase(TestCase):
    def test_media_to_label_size(self) -> None:
        self.assertEqual(
            "62x100", media_to_label_size('62mm x 100mm / 2.4" x 3.9"')
        )
        self.assertEqual("62", media_to_label_size("62mm"))
        self.assertEqual("102x152", media_to_label_size("102mm x 153mm"))
        self.assertEqual("", media_to_label_size("No media"))


class LabelSizesForModelTestCase(TestCase):
    def test_label_sizes_for_model(self) -> None:
        ql710w = {label["id"] for label in label_sizes_for_model("QL-710W")}
        ql1060n = {label["id"] for label in label_sizes_for_model("QL-1060N")}

        self.assertIn("62x100", ql710w)
        self.assertNotIn("102x51", ql710w)
        self.assertIn("102x51", ql1060n)

        ql710w_by_id = {label["id"]: label for label in label_sizes_for_model("QL-710W")}
        self.assertEqual("die_cut", ql710w_by_id["62x100"]["form_factor"])
        self.assertFalse(ql710w_by_id["62x100"]["continuous"])
        self.assertEqual("endless", ql710w_by_id["62"]["form_factor"])
        self.assertTrue(ql710w_by_id["62"]["continuous"])
        self.assertEqual("ptouch_endless", ql710w_by_id["pt12"]["form_factor"])
        self.assertTrue(ql710w_by_id["pt12"]["continuous"])
