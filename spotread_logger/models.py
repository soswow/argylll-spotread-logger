from dataclasses import dataclass, field
from typing import Literal

MeasurementMode = Literal["ambient", "reflectance", "emissive", "telephoto"]


@dataclass
class InstrumentIdentity:
    """Identity block printed once when spotread connects."""

    instrument_type: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    chip_id: str | None = None
    version_string: str | None = None
    calibration_version: str | None = None
    production_number: str | None = None


@dataclass
class StatusDensity:
    """Cyan, magenta, yellow, and visual density from one Status line."""

    cyan: float | None = None
    magenta: float | None = None
    yellow: float | None = None
    visual: float | None = None


@dataclass
class SpotReading:
    """One spot reading, including the raw text block it was parsed from."""

    timestamp: str
    measurement_mode: MeasurementMode
    instrument: InstrumentIdentity
    spectrum_start_nm: float | None = None
    spectrum_end_nm: float | None = None
    spectrum_steps: float | None = None
    spectrum_values: list[float] = field(default_factory=list)
    peak_value: float | None = None
    peak_wavelength_nm: float | None = None
    xyz_x: float | None = None
    xyz_y: float | None = None
    xyz_z: float | None = None
    lab_l: float | None = None
    lab_a: float | None = None
    lab_b: float | None = None
    density_iso_vis: float | None = None
    density_type_1: float | None = None
    density_type_2: float | None = None
    density_status_a: StatusDensity = field(default_factory=StatusDensity)
    density_status_m: StatusDensity = field(default_factory=StatusDensity)
    density_status_t: StatusDensity = field(default_factory=StatusDensity)
    density_status_e: StatusDensity = field(default_factory=StatusDensity)
    ambient_lux: float | None = None
    cct_kelvin: float | None = None
    duv: float | None = None
    suggested_ev_iso100: float | None = None
    closest_planckian_kelvin: float | None = None
    closest_planckian_de2k: float | None = None
    closest_daylight_kelvin: float | None = None
    closest_daylight_de2k: float | None = None
    cri_ra: float | None = None
    cri_r9: float | None = None
    cri: dict[str, float | None] = field(default_factory=dict)
    tlci_qa: float | None = None
    tm30_rf: float | None = None
    tm30_rg: float | None = None
    tm30_cct: float | None = None
    tm30_duv: float | None = None
    notes: list[str] = field(default_factory=list)
    raw_reading: str = ""
