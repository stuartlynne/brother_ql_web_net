from __future__ import annotations

from importlib.resources import as_file, files
from tempfile import NamedTemporaryFile
from unittest import mock

from brother_ql.backends.generic import BrotherQLBackendGeneric
from brother_ql.labels import FormFactor
from brother_ql.raster import BrotherQLRaster
from brother_ql_web import labels
from brother_ql_web.configuration import Font
from PIL import Image, ImageChops, ImageFont

from tests import TestCase


class LabelParametersTestCase(TestCase):
    def test_kind(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            label_size="38",
        )
        self.assertEqual(FormFactor.ENDLESS, parameters.kind)
        parameters.label_size = "62x29"
        self.assertEqual(FormFactor.DIE_CUT, parameters.kind)

    def test_scale_margin(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_size=37,
            margin_top=10,
            margin_bottom=25,
            margin_left=33,
            margin_right=57,
        )
        self.assertEqual(3, parameters.margin_top_scaled)  # 3.7
        self.assertEqual(9, parameters.margin_bottom_scaled)  # 9.25
        self.assertEqual(12, parameters.margin_left_scaled)  # 12.21
        self.assertEqual(21, parameters.margin_right_scaled)  # 21.09

    def test_fill_color(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            label_size="62",
        )
        self.assertEqual((0, 0, 0), parameters.fill_color)
        parameters.label_size = "62red"
        self.assertEqual((255, 0, 0), parameters.fill_color)

    def test_font_path(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family=None,
            font_style=None,
        )
        parameters.configuration.label.default_font = Font(
            family="DejaVu Serif", style="Book"
        )

        # 1) Fallback to default.
        self.assertEqual(
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", parameters.font_path
        )

        # 2) Retrieve existing.
        parameters.font_family = "Roboto"
        parameters.font_style = "Medium"
        self.assertEqual(
            "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Medium.ttf",
            parameters.font_path,
        )

        # 3) Retrieve missing.
        parameters.font_family = "Custom family"
        parameters.font_style = "Regular"
        with self.assertRaisesRegex(
            expected_exception=LookupError,
            expected_regex=r"^Couldn't find the font & style$",
        ):
            parameters.font_path

    def test_width_height(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
        )

        # 1) Unknown label size.
        parameters.label_size = "1337"
        with self.assertRaisesRegex(
            expected_exception=LookupError, expected_regex=r"^Unknown label_size$"
        ):
            parameters.width_height

        # 2) Width > height. Handle standard and rotated.
        parameters.label_size = "62x29"
        self.assertEqual((696, 271), parameters.width_height)
        self.assertEqual(696, parameters.width)
        self.assertEqual(271, parameters.height)

        parameters.orientation = "rotated"
        self.assertEqual((271, 696), parameters.width_height)
        self.assertEqual(271, parameters.width)
        self.assertEqual(696, parameters.height)

        # 3) Height > width. Handle standard and rotated.
        parameters.label_size = "39x48"
        parameters.orientation = "standard"
        self.assertEqual((495, 425), parameters.width_height)
        self.assertEqual(495, parameters.width)
        self.assertEqual(425, parameters.height)

        parameters.orientation = "rotated"
        self.assertEqual((425, 495), parameters.width_height)
        self.assertEqual(425, parameters.width)
        self.assertEqual(495, parameters.height)

        # 4) Endless labels.
        parameters.label_size = "62"
        parameters.orientation = "standard"
        self.assertEqual((696, 0), parameters.width_height)
        self.assertEqual(696, parameters.width)
        self.assertEqual(0, parameters.height)

        parameters.orientation = "rotated"
        self.assertEqual((0, 696), parameters.width_height)
        self.assertEqual(0, parameters.width)
        self.assertEqual(696, parameters.height)

    def test_orientation_aliases(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            orientation="portrait",
        )
        self.assertEqual("rotated", parameters.orientation)

        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            orientation="landscape",
        )
        self.assertEqual("standard", parameters.orientation)


class DetermineImageDimensionsTestCase(TestCase):
    def test_determine_image_dimensions(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="Roboto",
            font_style="Medium",
        )
        image_font = ImageFont.truetype(parameters.font_path, parameters.font_size)
        text = "Test text"

        # 1) Fixed size labels.
        parameters.label_size = "62x29"
        parameters.orientation = "standard"
        result = labels._determine_image_dimensions(
            text=text, image_font=image_font, parameters=parameters
        )
        self.assertEqual((696, 271, 391, 72), result)

        parameters.orientation = "rotated"
        result = labels._determine_image_dimensions(
            text=text, image_font=image_font, parameters=parameters
        )
        self.assertEqual((271, 696, 391, 72), result)

        # 2) Endless labels.
        parameters.label_size = "62"
        parameters.orientation = "standard"
        result = labels._determine_image_dimensions(
            text=text, image_font=image_font, parameters=parameters
        )
        self.assertEqual((696, 141, 391, 72), result)

        parameters.orientation = "rotated"
        result = labels._determine_image_dimensions(
            text=text, image_font=image_font, parameters=parameters
        )
        self.assertEqual((461, 696, 391, 72), result)


class DetermineTextOffsetsTestCase(TestCase):
    def test_determine_text_offsets(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="Roboto",
            font_style="Medium",
        )

        # 1) Die cut/fixed size label.
        parameters.label_size = "62x29"
        parameters.orientation = "standard"
        result = labels._determine_text_offsets(
            height=271, width=696, text_height=72, text_width=391, parameters=parameters
        )
        self.assertEqual((152, 84), result)

        parameters.orientation = "rotated"
        result = labels._determine_text_offsets(
            width=271, height=696, text_height=72, text_width=391, parameters=parameters
        )
        self.assertEqual((0, 304), result)

        # 2) Endless label.
        parameters.label_size = "62"
        parameters.orientation = "standard"
        result = labels._determine_text_offsets(
            height=141, width=696, text_height=72, text_width=391, parameters=parameters
        )
        self.assertEqual((152, 24), result)

        parameters.orientation = "rotated"
        result = labels._determine_text_offsets(
            height=696, width=461, text_height=72, text_width=391, parameters=parameters
        )
        self.assertEqual((35, 301), result)

    def test_determine_text_offsets__alignment(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            label_size="62x29",
            orientation="standard",
            margin_top=24,
            margin_bottom=45,
            margin_left=35,
            margin_right=35,
        )

        cases = [
            ("left", "top", (17, 12)),
            ("center", "center", (152, 84)),
            ("right", "bottom", (267, 157)),
        ]
        for horizontal, vertical, expected in cases:
            with self.subTest(horizontal=horizontal, vertical=vertical):
                parameters.align = horizontal
                parameters.vertical_align = vertical
                result = labels._determine_text_offsets(
                    height=271,
                    width=696,
                    text_height=72,
                    text_width=391,
                    parameters=parameters,
                )
                self.assertEqual(expected, result)

    def test_determine_text_offsets__endless_variable_axis(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            label_size="62",
            margin_top=24,
            margin_bottom=80,
            margin_left=35,
            margin_right=90,
        )

        parameters.orientation = "standard"
        parameters.vertical_align = "bottom"
        result = labels._determine_text_offsets(
            height=176,
            width=696,
            text_height=72,
            text_width=391,
            parameters=parameters,
        )
        self.assertEqual((152, 24), result)

        parameters.orientation = "rotated"
        parameters.align = "right"
        result = labels._determine_text_offsets(
            height=696,
            width=516,
            text_height=72,
            text_width=391,
            parameters=parameters,
        )
        self.assertEqual((35, 544), result)


class CreateLabelImageTestCase(TestCase):
    def _get_content_bbox(self, image: Image.Image) -> tuple[int, int, int, int]:
        bbox = ImageChops.difference(
            image, Image.new("RGB", image.size, "white")
        ).getbbox()
        assert bbox is not None
        return bbox

    def _expected_physical_margin(
        self, requested: int, inset: int, printable_minimum: int
    ) -> int:
        if inset == 0:
            return requested
        return max(requested, inset + printable_minimum)

    def test_create_label_image(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="Roboto",
            font_style="Medium",
            text="Hello World!",
            label_size="62",
        )
        image = labels.create_label_image(parameters)
        self.addCleanup(image.close)
        reference = files("tests") / "data" / "hello_world.png"
        with as_file(reference) as path:
            with Image.open(path) as target_image:
                self.assertEqual(target_image.mode, image.mode)
                self.assertEqual(target_image.size, image.size)
                difference = ImageChops.difference(target_image, image)
                for index, pixel in enumerate(difference.get_flattened_data()):
                    self.assertEqual((0, 0, 0), pixel, index)

    def test_create_label_image__right_bottom_margins(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            text="AlignTest",
            label_size="62x100",
            align="right",
            vertical_align="bottom",
            margin_right=35,
            margin_bottom=45,
        )

        for orientation in ("standard", "rotated"):
            with self.subTest(orientation=orientation):
                parameters.orientation = orientation
                image = labels.create_label_image(parameters)
                self.addCleanup(image.close)
                bbox = self._get_content_bbox(image)
                _, _, right_inset, bottom_inset = parameters.printable_insets
                self.assertEqual(
                    self._expected_physical_margin(
                        35,
                        right_inset,
                        labels.MIN_PORTRAIT_RIGHT_PRINTABLE_MARGIN_DOTS
                        if orientation == "rotated"
                        else labels.MIN_LANDSCAPE_RIGHT_PRINTABLE_MARGIN_DOTS,
                    ),
                    image.size[0] - bbox[2] + right_inset,
                )
                self.assertEqual(
                    self._expected_physical_margin(
                        45,
                        bottom_inset,
                        labels.MIN_LANDSCAPE_BOTTOM_PRINTABLE_MARGIN_DOTS
                        if orientation == "standard"
                        else labels.MIN_PRINTABLE_MARGIN_DOTS,
                    ),
                    image.size[1] - bbox[3] + bottom_inset,
                )

    def test_create_label_image__date_stamp(self) -> None:
        cases = [
            ("left", "top", lambda image, bbox: (bbox[0], bbox[1])),
            (
                "right",
                "bottom",
                lambda image, bbox: (image.size[0] - bbox[2], image.size[1] - bbox[3]),
            ),
        ]
        for horizontal, vertical, margins in cases:
            with self.subTest(horizontal=horizontal, vertical=vertical):
                parameters = labels.LabelParameters(
                    configuration=self.example_configuration,
                    font_family="DejaVu Serif",
                    font_style="Book",
                    text=" ",
                    font_size=70,
                    label_size="62x100",
                    date_stamp=True,
                    date_stamp_horizontal=horizontal,
                    date_stamp_vertical=vertical,
                    date_stamp_text="2026-08-22",
                )
                image = labels.create_label_image(parameters)
                self.addCleanup(image.close)
                bbox = self._get_content_bbox(image)
                horizontal_margin, vertical_margin = margins(image, bbox)
                self.assertLessEqual(horizontal_margin, 4)
                self.assertLessEqual(vertical_margin, 4)

    def test_create_label_images__die_cut_overflow_pages(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            font_size=70,
            text=("This is a long line that should wrap and overflow. " * 8),
            label_size="62x29",
        )

        images = labels.create_label_images(parameters)
        self.addCleanup(lambda: [image.close() for image in images])
        self.assertGreater(len(images), 1)
        self.assertTrue(all(image.size == (696, 271) for image in images))

    def test_create_label_images__continuous_expands(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            font_size=70,
            text=("This is a long line that should wrap and expand. " * 8),
            label_size="62",
        )

        images = labels.create_label_images(parameters)
        self.addCleanup(lambda: [image.close() for image in images])
        self.assertEqual(1, len(images))
        self.assertEqual(696, images[0].size[0])
        self.assertGreater(images[0].size[1], 271)

    def test_create_preview_image__stacks_overflow_pages(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            font_size=70,
            text=("This is a long line that should wrap and overflow. " * 8),
            label_size="62x29",
        )

        images = labels.create_label_images(parameters)
        expected_height = sum(image.size[1] for image in images) + 20 * (
            len(images) - 1
        )
        for image in images:
            image.close()
        preview = labels.create_preview_image(parameters)
        self.addCleanup(preview.close)
        self.assertEqual(696, preview.size[0])
        self.assertEqual(expected_height, preview.size[1])

    def test_create_label_image__multiline_text(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            text="Hello World!\r\n\nLorem ipsum",
            label_size="62",
        )
        image = labels.create_label_image(parameters)
        self.addCleanup(image.close)
        reference = files("tests") / "data" / "multiline.png"
        with as_file(reference) as path:
            with Image.open(path) as target_image:
                self.assertEqual(target_image.mode, image.mode)
                self.assertEqual(target_image.size, image.size)
                difference = ImageChops.difference(target_image, image)
                for index, pixel in enumerate(difference.get_flattened_data()):
                    self.assertEqual((0, 0, 0), pixel, index)


class ImageToPngBytesTestCase(TestCase):
    def test_image_to_png_bytes(self) -> None:
        reference = files("tests") / "data" / "hello_world.png"
        with as_file(reference) as path:
            with Image.open(path) as image:
                actual = labels.image_to_png_bytes(image)
            expected = path.read_bytes()
        self.assertEqual(expected, actual)


class GenerateLabelTestCase(TestCase):
    @mock.patch("brother_ql.raster.logger.warning")
    @mock.patch("brother_ql.conversion.logger.warning")
    def test_generate_label(self, _: mock.Mock, __: mock.Mock) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="Roboto",
            font_style="Medium",
            text="Hello World!",
        )

        # 1) Save image.
        with NamedTemporaryFile(suffix=".png") as save_to:
            result = labels.generate_label(
                parameters=parameters,
                configuration=parameters.configuration,
                save_image_to=save_to.name,
            )
            reference = files("tests") / "data" / "hello_world.png"
            with as_file(reference) as path:
                save_to.seek(0)
                self.assertEqual(path.read_bytes(), save_to.read())
        self.assertTrue(result.data)

        # 2) Endless label with standard orientation.
        parameters.label_size = "62"
        parameters.orientation = "standard"
        result = labels.generate_label(
            parameters=parameters, configuration=parameters.configuration
        )
        reference = (
            files("tests") / "data" / "hello_world__label_size_62__standard.data"
        )
        with as_file(reference) as path:
            self.assertEqual(path.read_bytes(), result.data)

        # 3) Endless label with rotated orientation.
        parameters.label_size = "62"
        parameters.orientation = "rotated"
        result = labels.generate_label(
            parameters=parameters, configuration=parameters.configuration
        )
        reference = files("tests") / "data" / "hello_world__label_size_62__rotated.data"
        with as_file(reference) as path:
            self.assertEqual(path.read_bytes(), result.data)

        # 4) Die cut label.
        for orientation in ["standard", "rotated"]:
            with self.subTest(orientation=orientation):
                parameters.label_size = "62x29"
                parameters.orientation = orientation
                result = labels.generate_label(
                    parameters=parameters,
                    configuration=parameters.configuration,
                )
                reference = (
                    files("tests")
                    / "data"
                    / f"hello_world__label_size_62x29__{orientation}.data"
                )
                with as_file(reference) as path:
                    self.assertEqual(path.read_bytes(), result.data)

        # 5) Red mode.
        parameters.label_size = "62red"
        parameters.orientation = "standard"
        parameters.label_count = 1
        parameters.configuration.printer.model = "QL-800"
        result = labels.generate_label(
            parameters=parameters, configuration=parameters.configuration
        )
        reference = (
            files("tests") / "data" / "hello_world__label_size_62red__standard.data"
        )
        with as_file(reference) as path:
            self.assertEqual(path.read_bytes(), result.data)

    def test_generate_label__cut_after_last_copy(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            text="Hello World!",
            label_size="62x29",
            label_count=3,
            cut_mode="last",
        )
        parameters.configuration.printer.model = "QL-710W"

        result = labels.generate_label(
            parameters=parameters, configuration=parameters.configuration
        )
        self.assertNotIn(b"\x1b\x69\x41\x01", result.data)
        self.assertEqual(3, result.data.count(b"\x1b\x69\x41\x03"))
        self.assertEqual(2, result.data.count(b"\x0c"))
        self.assertTrue(result.data.endswith(b"\x1a"))

    def test_generate_label__cut_after_overflow_pages(self) -> None:
        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
            font_family="DejaVu Serif",
            font_style="Book",
            font_size=70,
            text=("This is a long line that should wrap and overflow. " * 8),
            label_size="62x29",
            label_count=2,
            cut_mode="each",
        )
        parameters.configuration.printer.model = "QL-710W"
        page_count = len(labels.create_label_images(parameters))

        result = labels.generate_label(
            parameters=parameters, configuration=parameters.configuration
        )
        self.assertGreater(page_count, 1)
        self.assertNotIn(b"\x1b\x69\x41\x01", result.data)
        self.assertEqual(
            page_count * parameters.label_count,
            result.data.count(b"\x1b\x69\x41" + bytes([page_count])),
        )


class PrintLabelTestCase(TestCase):
    def test_print_label(self) -> None:
        class Backend(BrotherQLBackendGeneric):
            def __init__(self, device_specifier: str) -> None:
                pass

        parameters = labels.LabelParameters(
            configuration=self.example_configuration,
        )
        qlr = BrotherQLRaster()
        qlr.data = b"My dummy data"

        parameters.label_count = 5
        with (
            mock.patch.object(labels.logger, "info") as info_mock,
            mock.patch.object(Backend, "write") as write_mock,
        ):
            labels.print_label(
                parameters=parameters,
                qlr=qlr,
                configuration=parameters.configuration,
                backend_class=Backend,
            )
        info_mock.assert_called_once_with("Printing %d label(s) ...", 5)
        write_mock.assert_called_once_with(b"My dummy data")
