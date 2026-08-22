from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import cast, Literal

from brother_ql import BrotherQLRaster, create_label
from brother_ql.labels import ALL_LABELS, FormFactor, Label
from brother_ql_web.configuration import Configuration, normalize_orientation
from brother_ql_web import utils
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
del logging

CONTINUOUS_FORM_FACTORS = (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS)
MIN_PRINTABLE_MARGIN_DOTS = 12
MIN_TRAILING_PRINTABLE_MARGIN_DOTS = 35
MIN_LANDSCAPE_RIGHT_PRINTABLE_MARGIN_DOTS = 38
MIN_PORTRAIT_RIGHT_PRINTABLE_MARGIN_DOTS = 70
MIN_LANDSCAPE_BOTTOM_PRINTABLE_MARGIN_DOTS = 42
DATE_STAMP_FONT_SCALE = 0.33
DATE_STAMP_MIN_FONT_SIZE = 10
DATE_STAMP_EDGE_MARGIN_DOTS = 1


@dataclass
class LabelParameters:
    configuration: Configuration

    font_family: str | None = None
    font_style: str | None = None
    text: str = ""
    image: bytes | None = None
    pdf: bytes | None = None
    font_size: int = 100
    label_size: str = "62"
    margin: int = 10
    threshold: int = 70
    align: str = "center"
    vertical_align: str = "auto"
    orientation: str = "standard"
    margin_top: int = 24
    margin_bottom: int = 45
    margin_left: int = 35
    margin_right: int = 35
    label_count: int = 1
    cut_mode: str = "each"
    date_stamp: bool = False
    date_stamp_horizontal: str = "right"
    date_stamp_vertical: str = "bottom"
    date_stamp_text: str = ""
    # TODO: Not yet taken into account. The number of dots in each direction has to be
    #       doubled. The generator/calculation methods have to be updated accordingly.
    high_quality: bool = False

    def __post_init__(self) -> None:
        self.orientation = normalize_orientation(self.orientation)

    @property
    def _label(self) -> Label:
        for label in ALL_LABELS:
            if label.identifier == self.label_size:
                return label
        raise LookupError("Unknown label_size")

    @property
    def kind(self) -> FormFactor:
        return self._label.form_factor

    def _scale_margin(self, margin: int) -> int:
        return int(self.font_size * margin / 100.0)

    @property
    def margin_top_scaled(self) -> int:
        return self._scale_margin(self.margin_top)

    @property
    def margin_bottom_scaled(self) -> int:
        return self._scale_margin(self.margin_bottom)

    @property
    def margin_left_scaled(self) -> int:
        return self._scale_margin(self.margin_left)

    @property
    def margin_right_scaled(self) -> int:
        return self._scale_margin(self.margin_right)

    @property
    def fill_color(self) -> tuple[int, int, int]:
        return (255, 0, 0) if "red" in self.label_size else (0, 0, 0)

    @property
    def font_path(self) -> str:
        try:
            if self.font_family is None or self.font_style is None:
                assert self.configuration.label.default_font is not None
                self.font_family = self.configuration.label.default_font.family
                self.font_style = self.configuration.label.default_font.style
            fonts = utils.collect_fonts(self.configuration)
            path = fonts[self.font_family][self.font_style]
        except KeyError:
            raise LookupError("Couldn't find the font & style")
        return path

    @property
    def width_height(self) -> tuple[float, float]:
        return self._orient_dimensions(self._label.dots_printable)

    @property
    def total_width_height(self) -> tuple[float, float]:
        return self._orient_dimensions(self._label.dots_total)

    def _orient_dimensions(self, dimensions: tuple[float, float]) -> tuple[float, float]:
        width, height = dimensions
        if height > width:
            width, height = height, width
        if self.orientation == "rotated":
            height, width = width, height
        return width, height

    @property
    def width(self) -> float:
        return self.width_height[0]

    @property
    def height(self) -> float:
        return self.width_height[1]

    @property
    def printable_insets(self) -> tuple[int, int, int, int]:
        if self.kind in CONTINUOUS_FORM_FACTORS:
            return 0, 0, 0, 0
        width, height = self.width_height
        total_width, total_height = self.total_width_height
        horizontal = max(int((total_width - width) / 2), 0)
        vertical = max(int((total_height - height) / 2), 0)
        return horizontal, vertical, horizontal, vertical

    @property
    def effective_margin_top(self) -> int:
        _, top, _, _ = self.printable_insets
        return _effective_printable_margin(self.margin_top_scaled, top)

    @property
    def effective_margin_bottom(self) -> int:
        _, _, _, bottom = self.printable_insets
        minimum = (
            MIN_LANDSCAPE_BOTTOM_PRINTABLE_MARGIN_DOTS
            if self.orientation == "standard"
            else MIN_PRINTABLE_MARGIN_DOTS
        )
        return _effective_printable_margin(self.margin_bottom_scaled, bottom, minimum)

    @property
    def effective_margin_left(self) -> int:
        left, _, _, _ = self.printable_insets
        return _effective_printable_margin(self.margin_left_scaled, left)

    @property
    def effective_margin_right(self) -> int:
        _, _, right, _ = self.printable_insets
        minimum = (
            MIN_PORTRAIT_RIGHT_PRINTABLE_MARGIN_DOTS
            if self.orientation == "rotated"
            else MIN_LANDSCAPE_RIGHT_PRINTABLE_MARGIN_DOTS
        )
        return _effective_printable_margin(
            self.margin_right_scaled, right, minimum
        )


def _effective_printable_margin(
    requested: int, inset: int, minimum: int = MIN_PRINTABLE_MARGIN_DOTS
) -> int:
    if inset == 0:
        return requested
    return max(requested - inset, minimum)


def _determine_image_dimensions(
    text: str, image_font: ImageFont.FreeTypeFont, parameters: LabelParameters
) -> tuple[float, float, float, float]:
    image = Image.new("L", (20, 20), "white")
    draw = ImageDraw.Draw(image)

    left, top, right, bottom = draw.multiline_textbbox(
        xy=(0, 0), text=text, font=image_font
    )
    text_width, text_height = (right - left, bottom - top)
    width, height = parameters.width_height
    if parameters.orientation == "standard":
        if parameters.kind in CONTINUOUS_FORM_FACTORS:
            height = (
                text_height
                + parameters.margin_top_scaled
                + parameters.margin_bottom_scaled
            )
    elif parameters.orientation == "rotated":
        if parameters.kind in CONTINUOUS_FORM_FACTORS:
            width = (
                text_width
                + parameters.margin_left_scaled
                + parameters.margin_right_scaled
            )
    return width, height, text_width, text_height


def _determine_text_offsets(
    height: float,
    width: float,
    text_height: float,
    text_width: float,
    parameters: LabelParameters,
) -> tuple[float, float]:
    def horizontal_offset() -> float:
        if parameters.align == "left":
            return min(parameters.effective_margin_left, max(width - text_width, 0))
        if parameters.align == "right":
            return max(width - text_width - parameters.effective_margin_right, 0)
        return max((width - text_width) // 2, 0)

    def vertical_offset(default: str) -> float:
        selected = parameters.vertical_align
        if selected == "auto":
            selected = default
        if selected == "top":
            return min(parameters.effective_margin_top, max(height - text_height, 0))
        if selected == "bottom":
            return max(height - text_height - parameters.effective_margin_bottom, 0)
        offset = (height - text_height) // 2
        offset += (
            parameters.effective_margin_top - parameters.effective_margin_bottom
        ) // 2
        return max(offset, 0)

    if parameters.orientation == "standard":
        default_vertical_align = (
            "center"
            if parameters.kind in (FormFactor.DIE_CUT, FormFactor.ROUND_DIE_CUT)
            else "top"
        )
        vertical_offset_value = (
            parameters.effective_margin_top
            if parameters.kind in CONTINUOUS_FORM_FACTORS
            else vertical_offset(default_vertical_align)
        )
        horizontal_offset_value = horizontal_offset()
    elif parameters.orientation == "rotated":
        vertical_offset_value = vertical_offset("center")
        horizontal_offset_value = horizontal_offset()
    return horizontal_offset_value, vertical_offset_value


def _text_bbox(
    text: str, image_font: ImageFont.FreeTypeFont
) -> tuple[int, int, int, int]:
    return ImageDraw.Draw(Image.new("L", (20, 20), "white")).multiline_textbbox(
        xy=(0, 0), text=text, font=image_font
    )


def _normalize_text(text: str) -> list[str]:
    # Workaround for a bug in multiline_textsize()
    # when there are empty lines in the text:
    lines = []
    for line in text.splitlines(keepends=False):
        if line == "":
            line = " "
        lines.append(line)
    return lines or [" "]


def _wrap_line(line: str, image_font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = line.split(" ")
    if len(words) == 1:
        return _split_oversized_word(line, image_font, max_width)

    wrapped = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        left, _, right, _ = _text_bbox(candidate, image_font)
        if right - left <= max_width:
            current = candidate
            continue
        if current:
            wrapped.append(current)
        wrapped.extend(_split_oversized_word(word, image_font, max_width)[:-1])
        current = _split_oversized_word(word, image_font, max_width)[-1]
    if current:
        wrapped.append(current)
    return wrapped or [" "]


def _split_oversized_word(
    word: str, image_font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    parts = []
    current = ""
    for char in word:
        candidate = f"{current}{char}"
        left, _, right, _ = _text_bbox(candidate, image_font)
        if current and right - left > max_width:
            parts.append(current)
            current = char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts or [" "]


def _prepare_text_pages(
    parameters: LabelParameters, image_font: ImageFont.FreeTypeFont
) -> list[str]:
    lines = _normalize_text(parameters.text)
    width, height = parameters.width_height
    should_wrap = parameters.kind not in CONTINUOUS_FORM_FACTORS or (
        parameters.orientation == "standard"
    )
    max_width = max(
        int(width - parameters.margin_left_scaled - parameters.margin_right_scaled),
        1,
    )
    if should_wrap:
        wrapped_lines = [
            wrapped
            for line in lines
            for wrapped in _wrap_line(line, image_font, max_width)
        ]
    else:
        wrapped_lines = lines

    if parameters.kind in CONTINUOUS_FORM_FACTORS:
        return ["\n".join(wrapped_lines)]

    max_height = max(
        int(height - parameters.margin_top_scaled - parameters.margin_bottom_scaled),
        1,
    )
    pages: list[list[str]] = []
    current: list[str] = []
    for line in wrapped_lines:
        candidate = current + [line]
        left, top, right, bottom = _text_bbox("\n".join(candidate), image_font)
        if current and bottom - top > max_height:
            pages.append(current)
            current = [line]
        else:
            current = candidate
    if current:
        pages.append(current)
    return ["\n".join(page) for page in pages] or [" "]


def _render_text_image(
    text: str, image_font: ImageFont.FreeTypeFont, parameters: LabelParameters
) -> Image.Image:
    text_left, text_top, _, _ = _text_bbox(text, image_font)

    width, height, text_width, text_height = _determine_image_dimensions(
        text=text, image_font=image_font, parameters=parameters
    )
    offset = _determine_text_offsets(
        width=width,
        height=height,
        text_width=text_width,
        text_height=text_height,
        parameters=parameters,
    )

    image = Image.new("RGB", (int(width), int(height)), "white")
    draw = ImageDraw.Draw(image)
    align = cast(Literal["left", "center", "right"], parameters.align)
    draw_offset = (offset[0] - text_left, offset[1] - text_top)
    draw.multiline_text(
        draw_offset, text, parameters.fill_color, font=image_font, align=align
    )
    _draw_date_stamp(image, parameters)
    return image


def _draw_date_stamp(image: Image.Image, parameters: LabelParameters) -> None:
    if not parameters.date_stamp:
        return

    stamp_text = parameters.date_stamp_text or date.today().isoformat()
    stamp_font_size = max(
        DATE_STAMP_MIN_FONT_SIZE, int(parameters.font_size * DATE_STAMP_FONT_SCALE)
    )
    stamp_font = ImageFont.truetype(parameters.font_path, stamp_font_size)
    text_left, text_top, text_right, text_bottom = _text_bbox(stamp_text, stamp_font)
    text_width = text_right - text_left
    text_height = text_bottom - text_top

    if parameters.date_stamp_horizontal == "left":
        x = DATE_STAMP_EDGE_MARGIN_DOTS
    else:
        x = max(image.size[0] - text_width - DATE_STAMP_EDGE_MARGIN_DOTS, 0)

    if parameters.date_stamp_vertical == "top":
        y = DATE_STAMP_EDGE_MARGIN_DOTS
    else:
        y = max(image.size[1] - text_height - DATE_STAMP_EDGE_MARGIN_DOTS, 0)

    draw = ImageDraw.Draw(image)
    draw.text(
        (x - text_left, y - text_top),
        stamp_text,
        parameters.fill_color,
        font=stamp_font,
    )


def create_label_images(parameters: LabelParameters) -> list[Image.Image]:
    if parameters.image:
        return [Image.open(BytesIO(parameters.image))]

    image_font = ImageFont.truetype(parameters.font_path, parameters.font_size)
    return [
        _render_text_image(text=page, image_font=image_font, parameters=parameters)
        for page in _prepare_text_pages(parameters, image_font)
    ]


def create_label_image(parameters: LabelParameters) -> Image.Image:
    return create_label_images(parameters)[0]


def combine_preview_images(images: list[Image.Image]) -> Image.Image:
    if len(images) == 1:
        return images[0]
    gap = 20
    width = max(image.size[0] for image in images)
    height = sum(image.size[1] for image in images) + gap * (len(images) - 1)
    preview = Image.new("RGB", (width, height), (230, 230, 230))
    y = 0
    for image in images:
        x = (width - image.size[0]) // 2
        preview.paste(image, (x, y))
        y += image.size[1] + gap
        image.close()
    return preview


def create_preview_image(parameters: LabelParameters) -> Image.Image:
    return combine_preview_images(create_label_images(parameters))


def image_to_png_bytes(image: Image.Image) -> bytes:
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    return image_buffer.read()


def generate_label(
    parameters: LabelParameters,
    configuration: Configuration,
    save_image_to: str | None = None,
) -> BrotherQLRaster:
    images = create_label_images(parameters)
    if save_image_to:
        images[0].save(save_image_to)

    red: bool = "red" in parameters.label_size
    rotate: int | str = 0
    if parameters.kind == FormFactor.ENDLESS:
        rotate = 0 if parameters.orientation == "standard" else 90
    elif parameters.kind in (FormFactor.DIE_CUT, FormFactor.ROUND_DIE_CUT):
        rotate = "auto"

    if parameters.high_quality:
        logger.warning("High quality mode is not implemented for now.")

    qlr = BrotherQLRaster(configuration.printer.model)
    label_images = images * parameters.label_count
    create_label(
        qlr,
        label_images,
        parameters.label_size,
        red=red,
        threshold=parameters.threshold,
        cut=True,
        rotate=rotate,
        dpi_600=False,
    )
    pages_per_label = len(images)
    if pages_per_label > 1:
        _set_cut_every(qlr, pages_per_label)
    elif parameters.cut_mode == "last" and parameters.label_count > 1:
        _set_cut_every(qlr, parameters.label_count)

    return qlr


def _set_cut_every(qlr: BrotherQLRaster, count: int) -> None:
    qlr.data = qlr.data.replace(
        b"\x1b\x69\x41\x01", b"\x1b\x69\x41" + bytes([count & 0xFF])
    )


def print_label(
    parameters: LabelParameters,
    qlr: BrotherQLRaster,
    configuration: Configuration,
    backend_class: type,
) -> None:
    backend = backend_class(configuration.printer.printer)
    logger.info("Printing %d label(s) ...", parameters.label_count)
    backend.write(qlr.data)
    backend.dispose()
    del backend
