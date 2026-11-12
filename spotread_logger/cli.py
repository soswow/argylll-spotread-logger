import argparse
import math
import sys

from spotread_logger.csv_log import append_reading
from spotread_logger.measurement_mode import (
    MEASUREMENT_MODE_CONFIGS,
    parse_measurement_mode,
    with_optional_skip_calibration,
)
from spotread_logger.models import SpotReading
from spotread_logger.session import SpotreadSessionOptions, run_spotread_session


def _describe_reading(reading: SpotReading) -> str:
    parts: list[str] = []
    if reading.ambient_lux is not None:
        parts.append(f"{reading.ambient_lux} lux")
    if reading.lab_l is not None and reading.ambient_lux is None:
        parts.append(f"Lab {reading.lab_l} {reading.lab_a} {reading.lab_b}")
    if reading.cct_kelvin is not None:
        parts.append(f"{reading.cct_kelvin} K")
    if reading.density_iso_vis is not None:
        parts.append(f"density {reading.density_iso_vis}")
    if len(parts) == 0:
        return "reading logged"
    return "  ".join(parts)


def parse_interval(value: str) -> float:
    """Seconds to wait, or min to take the next reading as soon as the last one is logged."""
    if value == "min":
        return 0.0
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--interval must be seconds >= 0, or min") from error
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("--interval must be seconds >= 0, or min")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m spotread_logger",
        description="Periodically take spotread readings and append them to a CSV file.",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=parse_interval,
        default=30,
        help="seconds to wait between readings, or min to read again immediately",
    )
    parser.add_argument("-o", "--output", default="readings.csv", help="CSV file to append")
    parser.add_argument(
        "--mode",
        default="ambient",
        help="ambient, reflectance, emissive, or telephoto",
    )
    parser.add_argument("--spotread", default="spotread", help="spotread executable")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="seconds to wait for connect, calibration, or one reading",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="send the calibration and first measure keys without asking",
    )
    parser.add_argument(
        "--no-calibration",
        action="store_true",
        help="pass -N so spotread can skip calibration when it still has one",
    )
    parser.add_argument(
        "spotread_args",
        nargs=argparse.REMAINDER,
        help="arguments passed to spotread, replacing the mode defaults",
    )
    return parser


def run(argv: list[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be a number of seconds > 0")

    measurement_mode = parse_measurement_mode(args.mode)
    mode_config = MEASUREMENT_MODE_CONFIGS[measurement_mode]
    spotread_arguments = list(args.spotread_args)
    if spotread_arguments[:1] == ["--"]:
        spotread_arguments = spotread_arguments[1:]
    if len(spotread_arguments) > 0:
        base_arguments = spotread_arguments
    else:
        base_arguments = list(mode_config.spotread_arguments)
    spotread_args = with_optional_skip_calibration(base_arguments, args.no_calibration)

    print(f"writing {args.output}")
    print(f"mode {measurement_mode}")
    print(f"running {args.spotread} {' '.join(spotread_args)}")

    def on_reading(reading: SpotReading) -> None:
        append_reading(args.output, reading)
        print(f"{reading.timestamp}  {_describe_reading(reading)}  -> {args.output}")

    run_spotread_session(
        SpotreadSessionOptions(
            command=args.spotread,
            arguments=spotread_args,
            measurement_mode=measurement_mode,
            ready_message=mode_config.ready_message,
            interval_seconds=args.interval,
            prompt_for_setup=not args.no_prompt,
            reading_timeout_seconds=args.timeout,
            on_status=print,
        ),
        on_reading,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run(sys.argv[1:] if argv is None else argv)
    except (TimeoutError, RuntimeError, ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0
