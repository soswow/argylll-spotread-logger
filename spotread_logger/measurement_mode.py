from dataclasses import dataclass

from spotread_logger.models import MeasurementMode

MEASUREMENT_MODES: tuple[MeasurementMode, ...] = (
    "ambient",
    "reflectance",
    "emissive",
    "telephoto",
)


@dataclass(frozen=True)
class MeasurementModeConfig:
    spotread_arguments: tuple[str, ...]
    # Shown after calibration, once spotread is waiting for the first sample.
    ready_message: str


# Ambient already prints CCT and TM-30; -T would hide them.
# Emissive and telephoto print those metrics only when -T is set.
# Density (-d) is a reflectance figure.
MEASUREMENT_MODE_CONFIGS: dict[MeasurementMode, MeasurementModeConfig] = {
    "ambient": MeasurementModeConfig(
        spotread_arguments=("-a", "-H", "-s", "-v"),
        ready_message=(
            "Switch the sensor to ambient measurement, then press Enter to start logging."
        ),
    ),
    "reflectance": MeasurementModeConfig(
        spotread_arguments=("-H", "-s", "-v", "-d"),
        ready_message="Place the instrument on the sample, then press Enter to start logging.",
    ),
    "emissive": MeasurementModeConfig(
        spotread_arguments=("-e", "-H", "-s", "-v", "-T"),
        ready_message=(
            "Aim the open aperture at the light or display, then press Enter to start logging."
        ),
    ),
    "telephoto": MeasurementModeConfig(
        spotread_arguments=("-p", "-H", "-s", "-v", "-T"),
        ready_message="Aim the narrow aperture at the source, then press Enter to start logging.",
    ),
}


def parse_measurement_mode(value: str) -> MeasurementMode:
    for mode in MEASUREMENT_MODES:
        if mode == value:
            return mode
    names = ", ".join(MEASUREMENT_MODES)
    raise ValueError(f"--mode must be one of {names}")


def with_optional_skip_calibration(
    spotread_arguments: list[str],
    skip_calibration: bool,
) -> list[str]:
    """Keep -N with the flags. spotread treats a trailing word as a logfile name."""
    if not skip_calibration or "-N" in spotread_arguments:
        return spotread_arguments
    positional_index = next(
        (
            index
            for index, argument in enumerate(spotread_arguments)
            if not argument.startswith("-")
        ),
        None,
    )
    if positional_index is None:
        return [*spotread_arguments, "-N"]
    return [
        *spotread_arguments[:positional_index],
        "-N",
        *spotread_arguments[positional_index:],
    ]
