import argparse
import re
from pathlib import Path

import pytest

from spotread_logger.cli import build_parser, parse_interval
from spotread_logger.csv_log import CSV_COLUMNS, append_reading
from spotread_logger.measurement_mode import parse_measurement_mode, with_optional_skip_calibration
from spotread_logger.parse_reading import (
    extract_reading_block,
    parse_instrument_identity,
    parse_spot_reading,
)

BANNER = """Instrument Type:   ColorMunki
Serial Number:     1000001
Firmware version:  288
Chip ID:           00-EXAMPLE0000000
Version string:    'colormunki FW V1.32  Build Nr. 1303'
Calibration Ver.:  6
Production No.:    10001
"""

WARM_READING = """Spectrum from 380.000 to 730.000 nm in 106 steps
0.00184778, 0.00184778, 0.0765904
Peak value 0.076590 at (aprox.) 730.0 nm

 Result is XYZ: 1.920863 1.760226 0.750722, D50 Lab: 14.175013 5.478994 10.269623
 Ambient = 1.8 Lux, CCT = 3003K (Duv -0.0023)
 Suggested EV @ ISO100 for 1.8 Lux incident light = -0.5
 Closest Planckian temperature = 3071K (DE2K -4.2)
 Closest Daylight temperature  = 3064K (DE2K -6.0)
 Color Rendering Index (Ra) = 96.7 [ R9 = 81.7 ]
  R1  = 96.4  R2  = 97.6  R3  = 98.8  R4  = 97.2  R5  = 96.4  R6  = 97.5  R7  = 97.6
  R8  = 92.4  R9  = 81.7  R10 = 94.4  R11 = 97.2  R12 = 94.9  R13 = 96.6  R14 = 99.2
 Television Lighting Consistency Index 2012 (Qa) = 99.4
 IES TM-30-15 Rf = 97.44 Rg = 101.75 CCT = 3003 Duv = -0.002293
"""

BAD_READING = """Spectrum from 380.000 to 730.000 nm in 3 steps
-0.148688, 638.207, 0.186259
Peak value 638.207254 at (aprox.) 630.0 nm

 Result is XYZ: 6850.928595 3019.163362 0.000000, D50 Lab: 345.204811 514.004398 595.180709
 Ambient = 3019.2 Lux, CCT = 712K (Duv 0.0001)
 Suggested EV @ ISO100 for 3019.2 Lux incident light = 10.2
 (Bad Planckian temperature)
 Closest Daylight temperature  = 0K (DE2K nan)
 Color Rendering Index (Ra) = 49.9 [ R9 = -2014.3 ]
  R1  = 55.7  R2  = 95.2  R3  = 74.2  R4  = 35.8  R5  = 52.4  R6  = 75.4  R7  = 28.1
  R8  = -17.5  R9  = -2014.3  R10 = -2129.6  R11 = 69.1  R12 = 28.8  R13 = 71.1  R14 = 87.3
 Television Lighting Consistency Index 2012 (Qa) = nan
 IES TM-30-15 Rf = 6.93 Rg = -142.94 CCT = 712 Duv = 0.000082
"""

REFLECTANCE_READING = """Spectrum from 380.000 to 730.000 nm in 3 steps
24.3022, 25.1057, 23.2231
Peak value 25.105698 at (aprox.) 590.0 nm

 Result is XYZ: 23.921687 24.791885 20.453959, D50 Lab: 56.872082 0.075666 -0.006141
ISO Vis, Type 1, Type 2 Density: 0.605535 0.614326 0.609869
Status A CMYV Density: 0.609010 0.607342 0.604901 0.605535
Status M CMYV Density: 0.614504 0.606699 0.604631 0.605535
Status T CMYV Density: 0.604197 0.607080 0.606629 0.605535
Status E CMYV Density: 0.604196 0.607080 0.606906 0.605535
"""

EMISSIVE_READING = """Spectrum from 380.000 to 730.000 nm in 3 steps
0.00252686, 0.00247012, 6.880287
Peak value 6.880287 at (aprox.) 633.3 nm

 Result is XYZ: 90.679712 88.502684 54.000202, D50 Lab: 95.372201 9.820863 18.363457
                           CCT = 3799K (Duv -0.0011)
 Closest Planckian temperature = 3828K (DE2K -1.7)
 Closest Daylight temperature  = 3886K (DE2K -5.5)
 Color Rendering Index (Ra) = 59.2 [ R9 = -64.2 ]
  R1  = 54.1  R2  = 93.2  R3  = 45.7  R4  = 44.3  R5  = 67.2  R6  = 95.8  R7  = 58.8
  R8  = 14.6  R9  = -64.2  R10 = 91.8  R11 = 45.0  R12 = 69.2  R13 = 62.9  R14 = 66.8
 Television Lighting Consistency Index 2012 (Qa) = 46.0
 IES TM-30-15 Rf = 73.18 Rg = 120.17 CCT = 3799 Duv = -0.001053
"""

TELEPHOTO_READING = """Spectrum from 380.000 to 730.000 nm in 3 steps
0.00457959, 6.544148, 0.00264229
Peak value 6.544148 at (aprox.) 633.3 nm

 Result is XYZ: 88.172332 87.228811 58.923261, D50 Lab: 94.835267 7.577811 12.313118
"""

MEASURE_MENU = """
Place instrument on spot to be measured,
and hit [A-Z] to read white and setup FWA compensation (keyed to letter)
Hit ESC or Q to exit, instrument switch or any other key to take a reading:"""


def test_parses_the_instrument_banner() -> None:
    identity = parse_instrument_identity(BANNER)
    assert identity.instrument_type == "ColorMunki"
    assert identity.serial_number == "1000001"
    assert identity.firmware_version == "288"
    assert identity.chip_id == "00-EXAMPLE0000000"
    assert identity.version_string == "colormunki FW V1.32  Build Nr. 1303"
    assert identity.calibration_version == "6"
    assert identity.production_number == "10001"


def test_parses_a_normal_ambient_reading() -> None:
    identity = parse_instrument_identity(BANNER)
    reading = parse_spot_reading(WARM_READING, "2026-11-12T06:00:00.000Z", identity, "ambient")
    assert reading.spectrum_start_nm == 380
    assert reading.spectrum_end_nm == 730
    assert reading.spectrum_steps == 106
    assert reading.spectrum_values == [0.00184778, 0.00184778, 0.0765904]
    assert len(reading.notes) == 1
    assert reading.peak_value == 0.07659
    assert reading.peak_wavelength_nm == 730
    assert reading.xyz_y == 1.760226
    assert reading.lab_b == 10.269623
    assert reading.ambient_lux == 1.8
    assert reading.cct_kelvin == 3003
    assert reading.duv == -0.0023
    assert reading.suggested_ev_iso100 == -0.5
    assert reading.closest_planckian_kelvin == 3071
    assert reading.closest_planckian_de2k == -4.2
    assert reading.closest_daylight_kelvin == 3064
    assert reading.closest_daylight_de2k == -6
    assert reading.cri_ra == 96.7
    assert reading.cri["r1"] == 96.4
    assert reading.cri["r9"] == 81.7
    assert reading.cri["r14"] == 99.2
    assert reading.tlci_qa == 99.4
    assert reading.tm30_rf == 97.44
    assert reading.tm30_rg == 101.75
    assert reading.tm30_cct == 3003
    assert reading.tm30_duv == -0.002293


def test_keeps_failed_color_metrics_as_empty_notes() -> None:
    identity = parse_instrument_identity(BANNER)
    reading = parse_spot_reading(BAD_READING, "2026-11-12T06:01:00.000Z", identity, "ambient")
    assert reading.ambient_lux == 3019.2
    assert reading.closest_planckian_kelvin is None
    assert reading.closest_daylight_kelvin == 0
    assert reading.closest_daylight_de2k is None
    assert reading.cri["r9"] == -2014.3
    assert reading.tlci_qa is None
    assert reading.tm30_rg == -142.94
    assert "bad planckian temperature" in reading.notes
    assert "tlci qa is nan" in reading.notes


def test_parses_a_reflectance_reading_with_density() -> None:
    identity = parse_instrument_identity(BANNER)
    reading = parse_spot_reading(
        REFLECTANCE_READING,
        "2026-11-12T06:02:00.000Z",
        identity,
        "reflectance",
    )
    assert reading.measurement_mode == "reflectance"
    assert len(reading.notes) == 0
    assert reading.lab_l == 56.872082
    assert reading.lab_a == 0.075666
    assert reading.lab_b == -0.006141
    assert reading.ambient_lux is None
    assert reading.cct_kelvin is None
    assert reading.density_iso_vis == 0.605535
    assert reading.density_type_1 == 0.614326
    assert reading.density_type_2 == 0.609869
    assert reading.density_status_a.cyan == 0.60901
    assert reading.density_status_a.visual == 0.605535
    assert reading.density_status_e.yellow == 0.606906
    assert reading.spectrum_values == [24.3022, 25.1057, 23.2231]


def test_parses_an_emissive_reading_whose_cct_is_on_its_own_line() -> None:
    identity = parse_instrument_identity(BANNER)
    reading = parse_spot_reading(EMISSIVE_READING, "2026-11-12T06:03:00.000Z", identity, "emissive")
    assert reading.measurement_mode == "emissive"
    assert reading.ambient_lux is None
    assert reading.suggested_ev_iso100 is None
    assert reading.density_iso_vis is None
    assert reading.cct_kelvin == 3799
    assert reading.duv == -0.0011
    assert reading.closest_planckian_kelvin == 3828
    assert reading.closest_planckian_de2k == -1.7
    assert reading.cri_ra == 59.2
    assert reading.cri["r9"] == -64.2
    assert reading.tlci_qa == 46
    assert reading.tm30_rf == 73.18
    assert reading.tm30_cct == 3799
    assert reading.tm30_duv == -0.001053


def test_parses_a_telephoto_reading_that_stops_at_lab() -> None:
    identity = parse_instrument_identity(BANNER)
    reading = parse_spot_reading(
        TELEPHOTO_READING,
        "2026-11-12T06:04:00.000Z",
        identity,
        "telephoto",
    )
    assert reading.measurement_mode == "telephoto"
    assert len(reading.notes) == 0
    assert reading.xyz_y == 87.228811
    assert reading.lab_l == 94.835267
    assert reading.cct_kelvin is None
    assert reading.density_iso_vis is None
    assert reading.tm30_rf is None


def test_drops_the_measure_menu_that_follows_a_sample() -> None:
    block = extract_reading_block(f"{TELEPHOTO_READING}{MEASURE_MENU}")
    assert re.search(r"D50 Lab: 94.835267 7.577811 12.313118\Z", block)
    assert "Place instrument" not in block


def test_interval_min_reads_again_immediately() -> None:
    assert parse_interval("min") == 0
    assert parse_interval("0") == 0
    assert parse_interval("30") == 30
    with pytest.raises(argparse.ArgumentTypeError, match="seconds >= 0, or min"):
        parse_interval("fast")
    args = build_parser().parse_args(["--interval", "min"])
    assert args.interval == 0


def test_rejects_an_unknown_mode_and_inserts_skip_calibration_ahead_of_a_logfile() -> None:
    assert parse_measurement_mode("emissive") == "emissive"
    with pytest.raises(ValueError, match="--mode must be one of"):
        parse_measurement_mode("flash")
    assert with_optional_skip_calibration(["-e", "-H", "-s", "-v", "-T"], False) == [
        "-e",
        "-H",
        "-s",
        "-v",
        "-T",
    ]
    assert with_optional_skip_calibration(["-e", "-H"], True) == ["-e", "-H", "-N"]
    assert with_optional_skip_calibration(["-e", "-N"], True) == ["-e", "-N"]
    assert with_optional_skip_calibration(["-e", "capture.txt"], True) == [
        "-e",
        "-N",
        "capture.txt",
    ]


def test_appends_a_header_once_without_the_raw_spotread_text(tmp_path: Path) -> None:
    file_path = tmp_path / "readings.csv"
    identity = parse_instrument_identity(BANNER)
    reading = parse_spot_reading(WARM_READING, "2026-11-12T06:00:00.000Z", identity, "ambient")
    append_reading(file_path, reading)
    append_reading(file_path, reading)
    csv_text = file_path.read_text(encoding="utf-8")
    assert csv_text.startswith(f"{','.join(CSV_COLUMNS)}\n")
    assert "raw_reading" not in csv_text
    assert "Spectrum from" not in csv_text
    assert csv_text.count("2026-11-12T06:00:00.000Z") == 2
