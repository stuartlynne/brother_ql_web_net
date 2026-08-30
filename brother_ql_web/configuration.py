from __future__ import annotations

import json
from dataclasses import dataclass, field as dataclass_field
from typing import Any

ORIENTATION_ALIASES = {
    "landscape": "standard",
    "portrait": "rotated",
}


def normalize_orientation(value: str) -> str:
    return ORIENTATION_ALIASES.get(value, value)


@dataclass
class Configuration:
    server: ServerConfiguration
    printer: PrinterConfiguration | None
    label: LabelConfiguration
    website: WebsiteConfiguration
    printers: list[PrinterConfiguration] = dataclass_field(default_factory=list)
    discovery: DiscoveryConfiguration = dataclass_field(
        default_factory=lambda: DiscoveryConfiguration()
    )

    @classmethod
    def from_json(cls, json_file: str) -> Configuration:
        with open(json_file) as fd:
            parsed: dict[str, Any] = json.load(fd)
        server = ServerConfiguration(**parsed.pop("server", {}))
        discovery = DiscoveryConfiguration(**parsed.pop("discovery", {}))

        printer_data = parsed.pop("printer", None)
        printers_data = parsed.pop("printers", [])
        if printer_data:
            printer = PrinterConfiguration(**printer_data)
        elif printers_data:
            printer = PrinterConfiguration(**printers_data[0])
        else:
            printer = None
        printers = [PrinterConfiguration(**item) for item in printers_data]
        if not printers and printer is not None:
            printers = [printer]

        label = LabelConfiguration(**parsed.pop("label", {}))
        website = WebsiteConfiguration(**parsed.pop("website", {}))
        if parsed:
            raise ValueError(f"Unknown configuration values: {parsed}")
        return cls(
            server=server,
            printer=printer,
            label=label,
            website=website,
            printers=printers,
            discovery=discovery,
        )

    def to_json(self) -> str:
        data = {
            "server": self.server,
        }
        if self.printer is not None:
            data["printer"] = _to_jsonable(self.printer)
        data["label"] = self.label
        data["website"] = self.website
        if self.discovery != DiscoveryConfiguration():
            data["discovery"] = self.discovery
        if self.printers and self.printers != [self.printer]:
            data["printers"] = [_to_jsonable(printer) for printer in self.printers]
        return json.dumps(data, indent=2, default=lambda o: o.__dict__)

    def printer_by_id(self, printer_id: str | None) -> PrinterConfiguration | None:
        if not printer_id:
            return self.printer
        for printer in self.printers:
            if printer.identifier == printer_id:
                return printer
        raise LookupError("Unknown printer")


@dataclass
class ServerConfiguration:
    port: int = 8013
    host: str = ""
    log_level: str = "WARNING"
    additional_font_folder: str = ""

    @property
    def is_in_debug_mode(self) -> bool:
        return self.log_level == "DEBUG"


@dataclass
class DiscoveryConfiguration:
    enabled: bool = True
    interval: float = 10.0


@dataclass
class PrinterConfiguration:
    model: str
    printer: str
    id: str = ""
    name: str = ""
    snmp_enabled: bool = True

    @property
    def identifier(self) -> str:
        return self.id or self.printer

    @property
    def display_name(self) -> str:
        return self.name or f"{self.model} ({self.printer})"


@dataclass(frozen=True)
class Font:
    family: str
    style: str


@dataclass
class LabelConfiguration:
    default_size: str = "62"
    default_orientation: str = "standard"
    default_font_size: int = 70
    default_fonts: list[Font] = dataclass_field(default_factory=list)
    default_font: Font | None = None

    def __post_init__(self) -> None:
        self.default_orientation = normalize_orientation(self.default_orientation)
        self.default_fonts = [
            font if isinstance(font, Font) else Font(**font)
            for font in self.default_fonts
        ]


@dataclass
class WebsiteConfiguration:
    html_title: str = "Label Designer"
    page_title: str = "Brother QL Label Designer"
    page_headline: str = "Design your label and print it…"


def _to_jsonable(printer: PrinterConfiguration) -> dict[str, str | bool]:
    data: dict[str, str | bool] = {
        "model": printer.model,
        "printer": printer.printer,
    }
    if printer.id:
        data["id"] = printer.id
    if printer.name:
        data["name"] = printer.name
    if not printer.snmp_enabled:
        data["snmp_enabled"] = printer.snmp_enabled
    return data
