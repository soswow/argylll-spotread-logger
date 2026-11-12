import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pexpect

from spotread_logger.models import InstrumentIdentity, MeasurementMode, SpotReading
from spotread_logger.parse_reading import (
    extract_reading_block,
    parse_instrument_identity,
    parse_spot_reading,
)

# -N can skip calibration, so the first prompt is whichever of these spotread prints.
CALIBRATION_PROMPT = r"(?i)hit any key to continue,\s*\n?\s*or hit Esc or Q to abort:"
MEASURE_PROMPT = r"(?i)any other key to take a reading:"
RETRY_PROMPT = r"(?i)any other key to retry:"


@dataclass
class SpotreadSessionOptions:
    command: str
    arguments: list[str]
    measurement_mode: MeasurementMode
    ready_message: str
    interval_seconds: float
    prompt_for_setup: bool
    reading_timeout_seconds: float
    on_status: Callable[[str], None]


class _StopController:
    """First Ctrl-C asks spotread to quit. A second one kills the process."""

    def __init__(self, on_status: Callable[[str], None]) -> None:
        self.requested = False
        self.child: pexpect.spawn | None = None
        self._on_status = on_status

    def request(self, _signum: int, _frame: object) -> None:
        child = self.child
        if child is None:
            return
        if self.requested:
            child.kill(signal.SIGKILL)
            return
        self.requested = True
        self._on_status("stopping spotread")
        try:
            child.send("q")
        except OSError:
            child.kill(signal.SIGKILL)


def _describe_instrument(instrument: InstrumentIdentity) -> str:
    parts = [
        instrument.instrument_type,
        None if instrument.serial_number is None else f"serial {instrument.serial_number}",
        None if instrument.firmware_version is None else f"firmware {instrument.firmware_version}",
    ]
    present = [part for part in parts if part is not None]
    if len(present) == 0:
        return "connected to instrument"
    return f"connected: {', '.join(present)}"


def _confirm_setup(options: SpotreadSessionOptions, message: str) -> None:
    if not options.prompt_for_setup or not sys.stdin.isatty():
        options.on_status(message)
        return
    input(f"{message}\n")


def _sleep_until_stop(seconds: float, controller: _StopController) -> None:
    deadline = time.monotonic() + seconds
    while not controller.requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _expect(child: pexpect.spawn, patterns: list[str], timeout_seconds: float) -> tuple[int, str]:
    try:
        index = child.expect(patterns, timeout=timeout_seconds)
    except pexpect.TIMEOUT as error:
        raise TimeoutError(f"timed out after {timeout_seconds}s waiting for spotread") from error
    except pexpect.EOF as error:
        raise RuntimeError("spotread closed before the reading finished") from error
    # Carriage returns are stripped so spotread's wrapping matches the parser.
    before = str(child.before or "").replace("\r", "")
    return index, before


def run_spotread_session(
    options: SpotreadSessionOptions,
    on_reading: Callable[[SpotReading], None],
) -> None:
    """Drive spotread on a pseudo-terminal.

    spotread reads single keypresses, so a pipe is not enough. A wide terminal
    keeps a 106-sample spectrum on one line for the parser.
    """
    environment = os.environ.copy()
    environment.setdefault("TERM", "xterm-256color")
    try:
        child = pexpect.spawn(
            options.command,
            options.arguments,
            timeout=options.reading_timeout_seconds,
            encoding="utf-8",
            codec_errors="replace",
            dimensions=(40, 4000),
            env=environment,
        )
    except pexpect.ExceptionPexpect as error:
        raise RuntimeError(str(error)) from error

    # pexpect otherwise waits briefly before every key.
    child.delaybeforesend = 0
    controller = _StopController(options.on_status)
    controller.child = child
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, controller.request)
    signal.signal(signal.SIGTERM, controller.request)

    try:
        setup_index, setup_before = _expect(
            child,
            [CALIBRATION_PROMPT, MEASURE_PROMPT],
            options.reading_timeout_seconds,
        )
        instrument = parse_instrument_identity(setup_before)
        options.on_status(_describe_instrument(instrument))
        if setup_index == 0:
            _confirm_setup(
                options,
                "Set the instrument to the calibration position, then press Enter.",
            )
            child.send("\r")
            _expect(child, [MEASURE_PROMPT], options.reading_timeout_seconds)
        else:
            options.on_status("spotread went straight to measuring")
        _confirm_setup(options, options.ready_message)

        while not controller.requested:
            child.send(" ")
            outcome_index, outcome_before = _expect(
                child,
                [MEASURE_PROMPT, RETRY_PROMPT],
                options.reading_timeout_seconds,
            )
            if outcome_index == 1:
                options.on_status("spotread aborted a reading; retrying")
                child.send(" ")
                continue
            timestamp = (
                datetime.now(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            reading = parse_spot_reading(
                extract_reading_block(outcome_before),
                timestamp,
                instrument,
                options.measurement_mode,
            )
            on_reading(reading)
            if controller.requested:
                break
            # min, and 0, mean the next key goes out as soon as this sample is logged.
            if options.interval_seconds > 0:
                options.on_status(f"next reading in {round(options.interval_seconds)}s")
                _sleep_until_stop(options.interval_seconds, controller)
    except RuntimeError:
        if not controller.requested:
            raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if child.isalive() and not controller.requested:
            try:
                child.send("q")
            except OSError:
                pass
            time.sleep(0.3)
        if child.isalive():
            child.terminate(force=True)
