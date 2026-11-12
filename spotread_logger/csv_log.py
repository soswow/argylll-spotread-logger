import csv
import io
from pathlib import Path

from spotread_logger.models import SpotReading, StatusDensity

CRI_INDEXES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)


def _status_columns(status: str) -> list[str]:
    return [
        f"density_status_{status}_c",
        f"density_status_{status}_m",
        f"density_status_{status}_y",
        f"density_status_{status}_v",
    ]


# Stable column order. Spectrum samples are one JSON array so the row stays one record.
CSV_COLUMNS = [
    "timestamp",
    "measurement_mode",
    "instrument_type",
    "serial_number",
    "firmware_version",
    "chip_id",
    "version_string",
    "calibration_version",
    "production_number",
    "spectrum_start_nm",
    "spectrum_end_nm",
    "spectrum_steps",
    "spectrum_values",
    "peak_value",
    "peak_wavelength_nm",
    "xyz_x",
    "xyz_y",
    "xyz_z",
    "lab_l",
    "lab_a",
    "lab_b",
    "density_iso_vis",
    "density_type_1",
    "density_type_2",
    *_status_columns("a"),
    *_status_columns("m"),
    *_status_columns("t"),
    *_status_columns("e"),
    "ambient_lux",
    "cct_k",
    "duv",
    "suggested_ev_iso100",
    "closest_planckian_k",
    "closest_planckian_de2k",
    "closest_daylight_k",
    "closest_daylight_de2k",
    "cri_ra",
    "cri_r9_summary",
    *[f"cri_r{index}" for index in CRI_INDEXES],
    "tlci_qa",
    "tm30_rf",
    "tm30_rg",
    "tm30_cct",
    "tm30_duv",
    "notes",
]


def format_number(value: float) -> str:
    """Decimal text without an exponent, so small spectrum samples stay readable."""
    if value == 0:
        return "0"
    text = format(value, ".12f").rstrip("0").rstrip(".")
    if text in {"", "-", "-0"}:
        return "0"
    return text


def _format_cell(value: str | float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format_number(value)
    return value


def _format_spectrum(values: list[float]) -> str:
    return "[" + ",".join(format_number(value) for value in values) + "]"


def _status_cells(status: StatusDensity) -> list[float | None]:
    return [status.cyan, status.magenta, status.yellow, status.visual]


def _reading_cells(reading: SpotReading) -> list[str]:
    instrument = reading.instrument
    cri_cells = [reading.cri.get(f"r{index}") for index in CRI_INDEXES]
    values: list[str | float | None] = [
        reading.timestamp,
        reading.measurement_mode,
        instrument.instrument_type,
        instrument.serial_number,
        instrument.firmware_version,
        instrument.chip_id,
        instrument.version_string,
        instrument.calibration_version,
        instrument.production_number,
        reading.spectrum_start_nm,
        reading.spectrum_end_nm,
        reading.spectrum_steps,
        _format_spectrum(reading.spectrum_values),
        reading.peak_value,
        reading.peak_wavelength_nm,
        reading.xyz_x,
        reading.xyz_y,
        reading.xyz_z,
        reading.lab_l,
        reading.lab_a,
        reading.lab_b,
        reading.density_iso_vis,
        reading.density_type_1,
        reading.density_type_2,
        *_status_cells(reading.density_status_a),
        *_status_cells(reading.density_status_m),
        *_status_cells(reading.density_status_t),
        *_status_cells(reading.density_status_e),
        reading.ambient_lux,
        reading.cct_kelvin,
        reading.duv,
        reading.suggested_ev_iso100,
        reading.closest_planckian_kelvin,
        reading.closest_planckian_de2k,
        reading.closest_daylight_kelvin,
        reading.closest_daylight_de2k,
        reading.cri_ra,
        reading.cri_r9,
        *cri_cells,
        reading.tlci_qa,
        reading.tm30_rf,
        reading.tm30_rg,
        reading.tm30_cct,
        reading.tm30_duv,
        "; ".join(reading.notes),
    ]
    return [_format_cell(value) for value in values]


def _csv_line(cells: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(cells)
    return buffer.getvalue()


def append_reading(file_path: str | Path, reading: SpotReading) -> None:
    """Append one reading. The header is written only when the file is missing or empty."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    has_content = path.exists() and path.stat().st_size > 0
    lines: list[str] = []
    if not has_content:
        lines.append(_csv_line(CSV_COLUMNS))
    lines.append(_csv_line(_reading_cells(reading)))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(lines))
