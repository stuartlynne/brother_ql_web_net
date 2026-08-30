from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from queue import Queue
from time import sleep, time
from typing import Any

from brother_ql.labels import ALL_LABELS, FormFactor
from brother_ql_web.configuration import Configuration, PrinterConfiguration

STATUS_OID = ".1.3.6.1.4.1.11.2.4.3.1.2.0"
MEDIA_OID = ".1.3.6.1.2.1.43.8.2.1.12.1.1"
PAGE_COUNT_OID = ".1.3.6.1.2.1.43.10.2.1.4.1.1"
SYS_DESCR_OID = ".1.3.6.1.2.1.1.1.0"
HOSTNAME_OID = ".1.3.6.1.2.1.1.5.0"
MODEL_OID = ".1.3.6.1.2.1.25.3.2.1.3.1"
SERIAL_NUMBER_OID = ".1.3.6.1.2.1.43.5.1.1.17.1"
BUSY_STATUSES = {"PRINTING", "BUSY"}


def _form_factors(*names: str) -> tuple[FormFactor, ...]:
    return tuple(
        form_factor
        for name in names
        if (form_factor := getattr(FormFactor, name, None)) is not None
    )


CONTINUOUS_FORM_FACTORS = _form_factors("ENDLESS", "PTOUCH_ENDLESS")


@dataclass
class PrinterStatus:
    printer_id: str
    status: str = "UNKNOWN"
    media: str = ""
    page_count: str = ""
    hostname: str = ""
    sysdescr: str = ""
    media_label_size: str = ""
    error: str = ""
    updated_at: float = 0

    @property
    def ok(self) -> bool:
        return self.status.strip().upper() == "READY"

    @property
    def busy(self) -> bool:
        return self.status.strip().upper() in BUSY_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "printer_id": self.printer_id,
            "status": self.status,
            "media": self.media,
            "page_count": self.page_count,
            "hostname": self.hostname,
            "sysdescr": self.sysdescr,
            "media_label_size": self.media_label_size,
            "ok": self.ok,
            "busy": self.busy,
            "error": self.error,
            "updated_at": self.updated_at,
        }


@dataclass
class PrinterStatusCache:
    printers: list[PrinterConfiguration]
    interval: float = 2.0
    discovery_enabled: bool = False
    discovery_interval: float = 10.0
    statuses: dict[str, PrinterStatus] = field(default_factory=dict)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _last_discovery: float = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        for printer in self.printers:
            self.statuses[printer.identifier] = PrinterStatus(
                printer_id=printer.identifier
            )
        self._thread = threading.Thread(
            target=self._run, name="PrinterStatusCache", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def get(self, printer: PrinterConfiguration) -> PrinterStatus:
        return self.statuses.get(
            printer.identifier, PrinterStatus(printer_id=printer.identifier)
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            self._refresh_discovered_printers()
            for printer in self.printers:
                if self._stop.is_set():
                    return
                self.statuses[printer.identifier] = read_status(printer)
            sleep(self.interval)

    def _refresh_discovered_printers(self) -> None:
        if not self.discovery_enabled:
            return
        if time() - self._last_discovery < self.discovery_interval:
            return
        self._last_discovery = time()
        discovered = discover_network_printers()
        known = {printer.identifier: printer for printer in self.printers}
        for printer in discovered:
            if printer.identifier in known:
                known[printer.identifier].printer = printer.printer
                known[printer.identifier].name = printer.name
                known[printer.identifier].model = printer.model
                continue
            self.printers.append(printer)
            self.statuses[printer.identifier] = PrinterStatus(
                printer_id=printer.identifier
            )


def read_status(printer: PrinterConfiguration) -> PrinterStatus:
    status = PrinterStatus(printer_id=printer.identifier, updated_at=time())
    if not printer.snmp_enabled or not printer.printer.startswith("tcp://"):
        status.status = "UNAVAILABLE"
        status.error = "SNMP status is only available for tcp:// printers"
        return status

    host = _host_from_tcp_printer(printer.printer)
    if not host:
        status.status = "UNAVAILABLE"
        status.error = "Could not determine printer host"
        return status

    try:
        from easysnmp import Session

        session = Session(
            hostname=host,
            community="public",
            version=1,
            timeout=0.2,
            retries=0,
            use_sprint_value=False,
            use_numeric=False,
            use_long_names=True,
        )
        values = session.get(
            [STATUS_OID, MEDIA_OID, PAGE_COUNT_OID, HOSTNAME_OID, SYS_DESCR_OID]
        )
        status.status = _safe_snmp_value(values[0].value).strip()
        status.media = _safe_snmp_value(values[1].value).strip()
        status.page_count = _safe_snmp_value(values[2].value).strip()
        status.hostname = _safe_snmp_value(values[3].value).strip()
        status.sysdescr = _safe_snmp_value(values[4].value).strip()
        status.media_label_size = media_to_label_size(status.media)
    except Exception as e:
        status.status = "UNAVAILABLE"
        status.error = str(e)
    return status


def discover_network_printers() -> list[PrinterConfiguration]:
    try:
        from qlmux.discovery import DiscoveryThread
    except Exception:
        return []

    discovered: dict[str, PrinterConfiguration] = {}
    stop_event = threading.Event()
    change_event = threading.Event()
    discovery_queue: Queue[tuple[str, object, object, object, object]] = Queue()
    threads = [
        DiscoveryThread(
            name="brother_ql_web_net_discovery_v1",
            av="v1",
            snmpDiscoveredQueue=discovery_queue,
            stopEvent=stop_event,
            changeEvent=change_event,
        ),
        DiscoveryThread(
            name="brother_ql_web_net_discovery_v2c",
            av="v2c",
            snmpDiscoveredQueue=discovery_queue,
            stopEvent=stop_event,
            changeEvent=change_event,
        ),
    ]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(timeout=7.0)
    stop_event.set()

    while not discovery_queue.empty():
        host, hostname, sysdescr, mac_address, serial_number = discovery_queue.get()
        sysdescr_text = _safe_snmp_value(sysdescr)
        if "Brother" not in sysdescr_text:
            continue
        info = read_printer_info(host)
        model = _model_from_text(info.get("model", "") or sysdescr_text)
        if not model:
            continue
        identifier = (
            _safe_snmp_value(serial_number)
            or info.get("serial_number", "")
            or _safe_snmp_value(mac_address)
            or host
        )
        name = _safe_snmp_value(hostname) or identifier
        discovered[identifier] = PrinterConfiguration(
            id=identifier,
            name=f"{model} - {name}",
            model=model,
            printer=f"tcp://{host}:9100",
        )
    return list(discovered.values())


def read_printer_info(host: str) -> dict[str, str]:
    try:
        from easysnmp import Session

        session = Session(
            hostname=host,
            community="public",
            version=1,
            timeout=0.2,
            retries=0,
            use_sprint_value=False,
            use_numeric=False,
            use_long_names=True,
        )
        values = session.get([MODEL_OID, SERIAL_NUMBER_OID])
        return {
            "model": _safe_snmp_value(values[0].value).strip(),
            "serial_number": _safe_snmp_value(values[1].value).strip(),
        }
    except Exception:
        return {}


def _model_from_text(value: str) -> str:
    match = re.search(r"\bQL[-\s]?(\d{3,4}[A-Z]*)\b", value, flags=re.IGNORECASE)
    if not match:
        return ""
    return f"QL-{match.group(1).upper()}"


def _safe_snmp_value(value: object) -> str:
    try:
        return str(value).replace("\x00", "")
    except UnicodeEncodeError:
        return str(value).encode("ascii", "ignore").decode("ascii")


def _host_from_tcp_printer(printer: str) -> str:
    address = printer.removeprefix("tcp://")
    host, _, _port = address.partition(":")
    return host


def label_sizes_for_model(model: str) -> list[dict[str, str | bool]]:
    sizes = []
    for label in ALL_LABELS:
        restricted = getattr(label, "restricted_to_models", [])
        if restricted and model not in restricted:
            continue
        form_factor = label.form_factor
        continuous = form_factor in CONTINUOUS_FORM_FACTORS
        sizes.append(
            {
                "id": label.identifier,
                "name": label.name,
                "form_factor": form_factor.name.lower(),
                "continuous": continuous,
            }
        )
    return sizes


def media_to_label_size(media: str) -> str:
    match = re.search(r"(\d+)\s*mm(?:\s*x\s*(\d+)\s*mm)?", media)
    if not match:
        return ""
    width, length = match.groups()
    if not length:
        return width
    candidate = f"{width}x{length}"
    label_ids = {label.identifier for label in ALL_LABELS}
    if candidate in label_ids:
        return candidate

    # Some Brother status strings round the printed description differently
    # from brother_ql's historical label identifiers.
    for label in ALL_LABELS:
        if label.identifier.startswith(f"{width}x"):
            label_length = re.search(r"x(\d+)$", label.identifier)
            if label_length and abs(int(label_length.group(1)) - int(length)) <= 1:
                return label.identifier
    return width


def printer_payload(
    configuration: Configuration, status_cache: PrinterStatusCache | None = None
) -> list[dict[str, Any]]:
    payload = []
    for printer in configuration.printers:
        status = (
            status_cache.get(printer)
            if status_cache is not None
            else PrinterStatus(printer_id=printer.identifier)
        )
        payload.append(
            {
                "id": printer.identifier,
                "name": printer.display_name,
                "model": printer.model,
                "printer": printer.printer,
                "label_sizes": label_sizes_for_model(printer.model),
                "status": status.to_dict(),
            }
        )
    return payload
