from __future__ import annotations

import logging
import json
from io import BytesIO
from pathlib import Path
from time import sleep, time
from typing import Any, cast

import bottle
from brother_ql import BrotherQLRaster

from brother_ql_web.configuration import Configuration
from brother_ql_web.labels import (
    LabelParameters,
    combine_preview_images,
    create_label_images,
    image_to_png_bytes,
    generate_label,
    print_label,
)
from brother_ql_web.printers import PrinterStatus, PrinterStatusCache, printer_payload, read_status
from brother_ql_web import utils
from brother_ql_web.utils import BACKEND_TYPE

logger = logging.getLogger(__name__)
del logging

CURRENT_DIRECTORY = Path(__file__).parent
PRINT_WAIT_TIMEOUT_SECONDS = 30.0
PRINT_WAIT_INTERVAL_SECONDS = 0.5


def get_config(key: str) -> object:
    return bottle.request.app.config[key]


@bottle.route("/")  # type: ignore[untyped-decorator]
def index() -> None:
    bottle.redirect("/labeldesigner")


@bottle.route("/static/<filename:path>")  # type: ignore[untyped-decorator]
def serve_static(filename: str) -> bottle.HTTPResponse:
    return bottle.static_file(filename, root=str(CURRENT_DIRECTORY / "static"))


@bottle.route("/labeldesigner")  # type: ignore[untyped-decorator]
@bottle.jinja2_view("labeldesigner.jinja2")  # type: ignore[untyped-decorator]
def labeldesigner() -> dict[str, Any]:
    fonts = cast(dict[str, dict[str, str]], get_config("brother_ql_web.fonts"))
    font_family_names = sorted(list(fonts.keys()))
    configuration = cast(Configuration, get_config("brother_ql_web.configuration"))
    status_cache = cast(
        PrinterStatusCache | None, get_config("brother_ql_web.status_cache")
    )
    printers = printer_payload(configuration=configuration, status_cache=status_cache)
    return {
        "font_family_names": font_family_names,
        "fonts": fonts,
        "label_sizes": get_config("brother_ql_web.label_sizes"),
        "printers": printers,
        "printers_json": json.dumps(printers),
        "website": configuration.website,
        "label": configuration.label,
        "default_orientation": configuration.label.default_orientation,
    }


def _save_to_bytes(upload: bottle.FileUpload | None) -> bytes | None:
    if upload is None:
        return None
    output = BytesIO()
    upload.save(output)
    output.seek(0)
    return output.getvalue()


def get_label_parameters(
    request: bottle.BaseRequest, should_be_file: bool = False
) -> LabelParameters:
    # As we have strings, *bottle* would try to generate Latin-1 bytes from it
    # before decoding it back to UTF-8. This seems to break some umlauts, thus
    # resulting in UnicodeEncodeErrors being raised when going back to UTF-8.
    # For now, we just state that we always receive clean UTF-8 data and thus
    # the recode operations just can be omitted. All external API users are
    # responsible for passing clean data.
    # References:
    #   * https://github.com/bottlepy/bottle/blob/99341ff3791b2e7e705d7373e71937e9018eb081/bottle.py#L2197-L2203  # noqa: E501
    #   * https://github.com/FriedrichFroebel/brother_ql_web/issues/9
    parameters = request.params
    parameters.recode_unicode = False
    d = parameters.decode()  # UTF-8 decoded form data

    try:
        font_family = d.get("font_family").rpartition("(")[0].strip()
        font_style = d.get("font_family").rpartition("(")[2].rstrip(")")
    except AttributeError:
        if should_be_file:
            font_family = ""
            font_style = ""
        else:
            raise ValueError(
                "Could not find valid font specifier. Please pass the `font_family` "
                "parameter with the family and style in the format `Roboto (Medium)`, "
                "where Roboto is the family name and Medium the corresponding font "
                "style."
            )
    configuration = cast(
        Configuration, request.app.config["brother_ql_web.configuration"]
    )
    selected_printer = configuration.printer_by_id(d.get("printer_id"))
    selected_configuration = Configuration(
        server=configuration.server,
        printer=selected_printer,
        label=configuration.label,
        website=configuration.website,
        printers=configuration.printers,
    )

    context = {
        "text": d.get("text", ""),
        "image": _save_to_bytes(request.files.get("image")),
        "pdf": _save_to_bytes(request.files.get("pdf")),
        "font_size": int(d.get("font_size", 100)),
        "font_family": font_family,
        "font_style": font_style,
        "label_size": d.get("label_size", "62"),
        "margin": int(d.get("margin", 10)),
        "threshold": int(d.get("threshold", 70)),
        "align": d.get("align", "center"),
        "vertical_align": d.get("vertical_align", "auto"),
        "orientation": d.get("orientation", "standard"),
        "margin_top": int(d.get("margin_top", 24)),
        "margin_bottom": int(d.get("margin_bottom", 45)),
        "margin_left": int(d.get("margin_left", 35)),
        "margin_right": int(d.get("margin_right", 35)),
        "label_count": int(d.get("label_count", 1)),
        "cut_mode": d.get("cut_mode", "each"),
        "high_quality": bool(d.get("high_quality", False)),  # TODO: Enable by default.
        "configuration": selected_configuration,
    }

    return LabelParameters(**context)


@bottle.get("/api/printers")  # type: ignore[untyped-decorator]
def get_printers() -> dict[str, Any]:
    configuration = cast(Configuration, get_config("brother_ql_web.configuration"))
    status_cache = cast(
        PrinterStatusCache | None, get_config("brother_ql_web.status_cache")
    )
    return {"printers": printer_payload(configuration, status_cache)}


@bottle.get("/api/preview/text")  # type: ignore[untyped-decorator]
@bottle.post("/api/preview/text")  # type: ignore[untyped-decorator]
def get_preview_image() -> bytes:
    parameters = get_label_parameters(bottle.request)
    images = create_label_images(parameters=parameters)
    page_count = len(images)
    page_width, page_height = images[0].size
    image = combine_preview_images(images)
    bottle.response.set_header("X-Label-Pages", str(page_count))
    bottle.response.set_header("X-Label-Width", str(page_width))
    bottle.response.set_header("X-Label-Height", str(page_height))
    return_format = bottle.request.query.get("return_format", "png")
    if return_format == "base64":
        import base64

        bottle.response.set_header("Content-type", "text/plain")
        return base64.b64encode(image_to_png_bytes(image))
    else:
        bottle.response.set_header("Content-type", "image/png")
        return image_to_png_bytes(image)


@bottle.post("/api/print/text")  # type: ignore[untyped-decorator]
@bottle.get("/api/print/text")  # type: ignore[untyped-decorator]
def print_text() -> dict[str, bool | str]:
    """
    API to print some text

    returns: JSON
    """
    return_dict: dict[str, bool | str] = {"success": False}

    try:
        parameters = get_label_parameters(bottle.request)
    except (AttributeError, LookupError, ValueError) as e:
        return_dict["error"] = str(e)
        return return_dict

    if parameters.text is None:
        return_dict["error"] = "Please provide the text for the label"
        return return_dict

    qlr = generate_label(
        parameters=parameters,
        configuration=parameters.configuration,
        save_image_to="sample-out.png" if bottle.DEBUG else None,
    )

    return _print(parameters=parameters, qlr=qlr)


@bottle.post("/api/print/image")  # type: ignore[untyped-decorator]
def print_image() -> dict[str, bool | str]:
    """
    API to print an image

    returns: JSON
    """
    return_dict: dict[str, bool | str] = {"success": False}

    try:
        parameters = get_label_parameters(bottle.request, should_be_file=True)
    except (AttributeError, LookupError, ValueError) as e:
        return_dict["error"] = str(e)
        return return_dict

    if parameters.image is None or not parameters.image:
        return_dict["error"] = "Please provide the label image"
        return return_dict

    qlr = generate_label(
        parameters=parameters,
        configuration=parameters.configuration,
    )

    return _print(parameters=parameters, qlr=qlr)


def _print(parameters: LabelParameters, qlr: BrotherQLRaster) -> dict[str, bool | str]:
    return_dict: dict[str, bool | str] = {"success": False}

    if not bottle.DEBUG:
        preflight_error, waited = _wait_for_printer(parameters)
        if preflight_error:
            return_dict["message"] = preflight_error
            return return_dict
        try:
            print_label(
                parameters=parameters,
                qlr=qlr,
                configuration=parameters.configuration,
                backend_class=utils.get_backend_class(parameters.configuration),
            )
        except Exception as e:
            return_dict["message"] = str(e)
            logger.warning("Exception happened: %s", e)
            return return_dict

    return_dict["success"] = True
    return_dict["message"] = "Printing was sent." if not waited else "Printing was queued and sent."
    if bottle.DEBUG:
        return_dict["data"] = str(qlr.data)
    return return_dict


def _wait_for_printer(parameters: LabelParameters) -> tuple[str, bool]:
    printer = parameters.configuration.printer
    if not printer.snmp_enabled or not printer.printer.startswith("tcp://"):
        return "", False

    waited = False
    deadline = time() + PRINT_WAIT_TIMEOUT_SECONDS
    while True:
        status = _current_printer_status(parameters, fresh=waited)
        error = _preflight_printer(parameters, status)
        if not error:
            return "", waited
        if not status.busy:
            return error, waited
        if time() >= deadline:
            return f"Printer is still busy: {status.status or 'UNKNOWN'}", waited
        waited = True
        sleep(PRINT_WAIT_INTERVAL_SECONDS)


def _current_printer_status(
    parameters: LabelParameters, fresh: bool = False
) -> PrinterStatus:
    printer = parameters.configuration.printer
    status_cache = cast(
        PrinterStatusCache | None, get_config("brother_ql_web.status_cache")
    )
    status = (
        status_cache.get(printer)
        if status_cache is not None and not fresh
        else read_status(printer)
    )
    if not status.updated_at:
        status = read_status(printer)
    if status_cache and status.updated_at:
        status_cache.statuses[printer.identifier] = status
    return status


def _preflight_printer(parameters: LabelParameters, status: PrinterStatus) -> str:
    if not status.ok:
        if status.busy:
            return f"Printer is busy: {status.status}"
        detail = status.error or status.status or "UNKNOWN"
        return f"Printer is not ready: {detail}"

    if status.media_label_size and status.media_label_size != parameters.label_size:
        return (
            "Loaded media does not match selected label size: "
            f"{status.media or status.media_label_size} loaded, "
            f"{parameters.label_size} selected"
        )
    return ""


def main(
    configuration: Configuration,
    fonts: dict[str, dict[str, str]],
    label_sizes: list[tuple[str, str]],
    backend_class: BACKEND_TYPE,
) -> None:
    app = bottle.default_app()
    app.config["brother_ql_web.configuration"] = configuration
    app.config["brother_ql_web.fonts"] = fonts
    app.config["brother_ql_web.label_sizes"] = label_sizes
    app.config["brother_ql_web.backend_class"] = backend_class
    status_cache = PrinterStatusCache(configuration.printers)
    status_cache.start()
    app.config["brother_ql_web.status_cache"] = status_cache
    bottle.TEMPLATE_PATH.append(CURRENT_DIRECTORY / "views")
    debug = configuration.server.is_in_debug_mode
    app.run(host=configuration.server.host, port=configuration.server.port, debug=debug)
