import math
import re

from spotread_logger.models import InstrumentIdentity, MeasurementMode, SpotReading, StatusDensity

NUMBER = r"-?\d+(?:\.\d+)?"
NUMBER_OR_NAN = rf"{NUMBER}|nan"

_INSTRUMENT_TYPE = re.compile(r"Instrument Type:\s+(.+)")
_SERIAL_NUMBER = re.compile(r"Serial Number:\s+(.+)")
_FIRMWARE_VERSION = re.compile(r"Firmware version:\s+(.+)")
_CHIP_ID = re.compile(r"Chip ID:\s+(.+)")
_VERSION_STRING = re.compile(r"Version string:\s+'([^']*)'")
_CALIBRATION_VERSION = re.compile(r"Calibration Ver\.:\s+(.+)")
_PRODUCTION_NUMBER = re.compile(r"Production No\.:\s+(.+)")
_SPECTRUM_HEADER = re.compile(
    rf"Spectrum from\s+({NUMBER})\s+to\s+({NUMBER})\s+nm\s+in\s+(\d+)\s+steps"
)
_SPECTRUM_VALUES_START = re.compile(r"Spectrum from[^\n]*\n")
_PEAK = re.compile(rf"Peak value\s+({NUMBER})\s+at\s+\(aprox\.\)\s+({NUMBER})\s+nm")
_PEAK_LINE = re.compile(r"\n\s*Peak value\s+")
_TRISTIMULUS = re.compile(
    rf"Result is XYZ:\s+({NUMBER})\s+({NUMBER})\s+({NUMBER}),\s+"
    rf"D50 Lab:\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})"
)
_AMBIENT = re.compile(
    rf"Ambient =\s+({NUMBER})\s+Lux,\s+CCT =\s+({NUMBER})K\s+\(Duv\s+({NUMBER})\)"
)
# The K suffix keeps this off the TM-30 CCT, which has no unit.
_ILLUMINANT_CCT = re.compile(rf"CCT =\s+({NUMBER})K\s+\(Duv\s+({NUMBER})\)")
_ISO_DENSITY = re.compile(
    rf"ISO Vis, Type 1, Type 2 Density:\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})"
)
_EXPOSURE = re.compile(rf"Suggested EV @ ISO100 for\s+{NUMBER}\s+Lux incident light =\s+({NUMBER})")
_PLANCKIAN = re.compile(
    rf"Closest Planckian temperature =\s+({NUMBER})K\s+\(DE2K\s+({NUMBER_OR_NAN})\)",
    re.IGNORECASE,
)
_DAYLIGHT = re.compile(
    rf"Closest Daylight temperature\s+=\s+({NUMBER})K\s+\(DE2K\s+({NUMBER_OR_NAN})\)",
    re.IGNORECASE,
)
_CRI_RA = re.compile(
    rf"Color Rendering Index \(Ra\) =\s+({NUMBER_OR_NAN})\s+\[\s*R9\s*=\s*({NUMBER_OR_NAN})\s*\]",
    re.IGNORECASE,
)
_CRI_VALUE = re.compile(rf"R(\d+)\s*=\s*({NUMBER_OR_NAN})", re.IGNORECASE)
_TLCI = re.compile(
    rf"Television Lighting Consistency Index 2012 \(Qa\) =\s+({NUMBER_OR_NAN})",
    re.IGNORECASE,
)
_TM30 = re.compile(
    rf"IES TM-30-15 Rf =\s+({NUMBER_OR_NAN})\s+Rg =\s+({NUMBER_OR_NAN})\s+"
    rf"CCT =\s+({NUMBER_OR_NAN})\s+Duv =\s+({NUMBER_OR_NAN})",
    re.IGNORECASE,
)
_MENU = re.compile(
    r"\n(?:Place instrument on spot to be measured,|and hit \[A-Z\] to read white)",
    re.IGNORECASE,
)
_BAD_PLANCKIAN = re.compile(r"Bad Planckian temperature", re.IGNORECASE)
_NAN_DAYLIGHT = re.compile(r"DE2K nan", re.IGNORECASE)


def parse_number(text: str | None) -> float | None:
    """spotread prints "nan" for indexes it could not compute."""
    if text is None:
        return None
    trimmed = text.strip()
    if trimmed == "" or trimmed.lower() == "nan":
        return None
    try:
        value = float(trimmed)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def _match_group(text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1)


def _group(match: re.Match[str] | None, index: int) -> str | None:
    if match is None:
        return None
    return match.group(index)


def parse_instrument_identity(text: str) -> InstrumentIdentity:
    """Pull the one-time instrument banner. Later readings reuse it."""
    return InstrumentIdentity(
        instrument_type=_match_group(text, _INSTRUMENT_TYPE),
        serial_number=_match_group(text, _SERIAL_NUMBER),
        firmware_version=_match_group(text, _FIRMWARE_VERSION),
        chip_id=_match_group(text, _CHIP_ID),
        version_string=_match_group(text, _VERSION_STRING),
        calibration_version=_match_group(text, _CALIBRATION_VERSION),
        production_number=_match_group(text, _PRODUCTION_NUMBER),
    )


def _parse_spectrum_values(block: str) -> list[float]:
    header_match = _SPECTRUM_VALUES_START.search(block)
    peak_match = _PEAK_LINE.search(block)
    if header_match is None or peak_match is None:
        return []
    values_text = block[header_match.end() : peak_match.start()]
    values: list[float] = []
    for part in values_text.split(","):
        trimmed = part.strip()
        if trimmed == "":
            continue
        try:
            value = float(trimmed)
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _parse_status_density(block: str, status: str) -> StatusDensity:
    """Status A/M/T/E lines are cyan, magenta, yellow, visual in that order."""
    match = re.search(
        rf"Status {status} CMYV Density:\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})",
        block,
    )
    if match is None:
        return StatusDensity()
    return StatusDensity(
        cyan=parse_number(match.group(1)),
        magenta=parse_number(match.group(2)),
        yellow=parse_number(match.group(3)),
        visual=parse_number(match.group(4)),
    )


def _parse_cri(block: str) -> dict[str, float | None]:
    cri: dict[str, float | None] = {}
    for match in _CRI_VALUE.finditer(block):
        cri[f"r{match.group(1)}"] = parse_number(match.group(2))
    return cri


def extract_reading_block(text: str) -> str:
    """Drop the command menu spotread prints after a sample.

    The session waits for that menu because reflectance, and a light reading
    without -T, never print a TM-30 line.
    """
    menu_match = _MENU.search(text)
    if menu_match is None:
        return text.strip()
    return text[: menu_match.start()].strip()


def parse_spot_reading(
    block: str,
    timestamp: str,
    instrument: InstrumentIdentity,
    measurement_mode: MeasurementMode,
) -> SpotReading:
    """Parse one measurement block.

    Ambient, emissive, and telephoto share the spectrum and XYZ lines. Density
    is reflectance. CCT on its own line is a light reading with -T. Missing or
    "nan" figures stay null and are named in notes.
    """
    notes: list[str] = []
    spectrum_header = _SPECTRUM_HEADER.search(block)
    peak = _PEAK.search(block)
    tristimulus = _TRISTIMULUS.search(block)
    ambient = _AMBIENT.search(block)
    illuminant = None if ambient is not None else _ILLUMINANT_CCT.search(block)
    iso_density = _ISO_DENSITY.search(block)
    exposure = _EXPOSURE.search(block)
    planckian = _PLANCKIAN.search(block)
    daylight = _DAYLIGHT.search(block)
    ra = _CRI_RA.search(block)
    tlci = _TLCI.search(block)
    tm30 = _TM30.search(block)

    if _BAD_PLANCKIAN.search(block):
        notes.append("bad planckian temperature")
    if _NAN_DAYLIGHT.search(block):
        notes.append("daylight delta-e is nan")
    if tlci is not None and tlci.group(1).lower() == "nan":
        notes.append("tlci qa is nan")

    spectrum_values = _parse_spectrum_values(block)
    spectrum_steps = parse_number(_group(spectrum_header, 3))
    if spectrum_steps is not None and len(spectrum_values) != spectrum_steps:
        notes.append(
            f"spectrum value count {len(spectrum_values)} does not match "
            f"reported steps {int(spectrum_steps)}"
        )

    cct_text = _group(ambient, 2) if ambient is not None else _group(illuminant, 1)
    duv_text = _group(ambient, 3) if ambient is not None else _group(illuminant, 2)

    return SpotReading(
        timestamp=timestamp,
        measurement_mode=measurement_mode,
        instrument=instrument,
        spectrum_start_nm=parse_number(_group(spectrum_header, 1)),
        spectrum_end_nm=parse_number(_group(spectrum_header, 2)),
        spectrum_steps=spectrum_steps,
        spectrum_values=spectrum_values,
        peak_value=parse_number(_group(peak, 1)),
        peak_wavelength_nm=parse_number(_group(peak, 2)),
        xyz_x=parse_number(_group(tristimulus, 1)),
        xyz_y=parse_number(_group(tristimulus, 2)),
        xyz_z=parse_number(_group(tristimulus, 3)),
        lab_l=parse_number(_group(tristimulus, 4)),
        lab_a=parse_number(_group(tristimulus, 5)),
        lab_b=parse_number(_group(tristimulus, 6)),
        density_iso_vis=parse_number(_group(iso_density, 1)),
        density_type_1=parse_number(_group(iso_density, 2)),
        density_type_2=parse_number(_group(iso_density, 3)),
        density_status_a=_parse_status_density(block, "A"),
        density_status_m=_parse_status_density(block, "M"),
        density_status_t=_parse_status_density(block, "T"),
        density_status_e=_parse_status_density(block, "E"),
        ambient_lux=parse_number(_group(ambient, 1)),
        cct_kelvin=parse_number(cct_text),
        duv=parse_number(duv_text),
        suggested_ev_iso100=parse_number(_group(exposure, 1)),
        closest_planckian_kelvin=parse_number(_group(planckian, 1)),
        closest_planckian_de2k=parse_number(_group(planckian, 2)),
        closest_daylight_kelvin=parse_number(_group(daylight, 1)),
        closest_daylight_de2k=parse_number(_group(daylight, 2)),
        cri_ra=parse_number(_group(ra, 1)),
        cri_r9=parse_number(_group(ra, 2)),
        cri=_parse_cri(block),
        tlci_qa=parse_number(_group(tlci, 1)),
        tm30_rf=parse_number(_group(tm30, 1)),
        tm30_rg=parse_number(_group(tm30, 2)),
        tm30_cct=parse_number(_group(tm30, 3)),
        tm30_duv=parse_number(_group(tm30, 4)),
        notes=notes,
        raw_reading=block.strip(),
    )
