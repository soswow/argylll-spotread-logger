"""Local page that shows the latest spotread measurement."""

import asyncio
import socket
import threading
from contextlib import suppress
from pathlib import Path

from aiohttp import WSMsgType, web

from spotread_logger.models import SpotReading, StatusDensity

INDEX_PATH = Path(__file__).resolve().parent / "static" / "index.html"
DASHBOARD_HOST = "127.0.0.1"
WAITING_DETAIL = "Waiting for a measurement"

# Spotread's spectral units. Reflectance samples are percents.
SPECTRUM_AXIS: dict[str, tuple[str, str]] = {
    "ambient": ("Spectral irradiance", "mW/(m²·nm)"),
    "emissive": ("Spectral radiance", "mW/(m²·sr·nm)"),
    "telephoto": ("Spectral radiance", "mW/(m²·sr·nm)"),
    "reflectance": ("Reflectance", "%"),
}

Listener = tuple[web.WebSocketResponse, asyncio.Queue[dict[str, object]]]


def _whole_number(value: float | None) -> int | float | None:
    """Keep a whole step count as an integer so the page does not show 106.0."""
    if value is None:
        return None
    if value.is_integer():
        return int(value)
    return value


def _instrument_payload(reading: SpotReading) -> dict[str, str | None]:
    instrument = reading.instrument
    return {
        "instrument_type": instrument.instrument_type,
        "serial_number": instrument.serial_number,
        "firmware_version": instrument.firmware_version,
        "chip_id": instrument.chip_id,
        "version_string": instrument.version_string,
        "calibration_version": instrument.calibration_version,
        "production_number": instrument.production_number,
    }


def _density_channels(status: StatusDensity) -> dict[str, float | None]:
    return {
        "cyan": status.cyan,
        "magenta": status.magenta,
        "yellow": status.yellow,
        "visual": status.visual,
    }


def _cri_payload(reading: SpotReading) -> dict[str, float | None]:
    payload: dict[str, float | None] = {"ra": reading.cri_ra}
    for index in range(1, 15):
        key = f"r{index}"
        value = reading.cri.get(key)
        # The Ra line has its own R9. Use it when the table omitted that index.
        if key == "r9" and value is None:
            value = reading.cri_r9
        payload[key] = value
    return payload


def reading_payload(reading: SpotReading) -> dict[str, object]:
    """JSON fields for the page. The raw spotread text is not one of them."""
    quantity, unit = SPECTRUM_AXIS[reading.measurement_mode]
    return {
        "timestamp": reading.timestamp,
        "measurement_mode": reading.measurement_mode,
        "instrument": _instrument_payload(reading),
        "spectrum": {
            "start_nm": reading.spectrum_start_nm,
            "end_nm": reading.spectrum_end_nm,
            "steps": _whole_number(reading.spectrum_steps),
            "values": list(reading.spectrum_values),
            "quantity": quantity,
            "unit": unit,
        },
        "peak_value": reading.peak_value,
        "peak_wavelength_nm": reading.peak_wavelength_nm,
        "xyz": {"x": reading.xyz_x, "y": reading.xyz_y, "z": reading.xyz_z},
        "lab": {"l": reading.lab_l, "a": reading.lab_a, "b": reading.lab_b},
        "illuminant": {
            "lux": reading.ambient_lux,
            "cct_kelvin": reading.cct_kelvin,
            "duv": reading.duv,
            "suggested_ev_iso100": reading.suggested_ev_iso100,
            "closest_planckian_kelvin": reading.closest_planckian_kelvin,
            "closest_planckian_de2k": reading.closest_planckian_de2k,
            "closest_daylight_kelvin": reading.closest_daylight_kelvin,
            "closest_daylight_de2k": reading.closest_daylight_de2k,
        },
        "cri": _cri_payload(reading),
        "tlci_qa": reading.tlci_qa,
        "tm30": {
            "rf": reading.tm30_rf,
            "rg": reading.tm30_rg,
            "cct": reading.tm30_cct,
            "duv": reading.tm30_duv,
        },
        "density": {
            "iso_vis": reading.density_iso_vis,
            "type_1": reading.density_type_1,
            "type_2": reading.density_type_2,
            "status_a": _density_channels(reading.density_status_a),
            "status_m": _density_channels(reading.density_status_m),
            "status_t": _density_channels(reading.density_status_t),
            "status_e": _density_channels(reading.density_status_e),
        },
        "notes": list(reading.notes),
    }


class DashboardState:
    """Latest reading and the browsers waiting for the next one."""

    def __init__(self, output_path: str) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._output_path = output_path
        self._detail = WAITING_DETAIL
        self._error: str | None = None
        self._reading: dict[str, object] | None = None
        self.listeners: set[Listener] = set()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_reading(self, reading: SpotReading) -> None:
        payload = reading_payload(reading)
        with self._lock:
            self._reading = payload
            self._error = None
        self._submit({"type": "reading", "reading": payload, "error": None})

    def publish_status(self, detail: str) -> None:
        with self._lock:
            self._detail = detail
            error = self._error
        self._submit({"type": "status", "detail": detail, "error": error})

    def publish_error(self, detail: str) -> None:
        """Report a failed sample only while the page is still waiting.

        A later failure leaves the last reading in place. The status line stays
        on the countdown, and the age keeps moving, until the next sample.
        """
        with self._lock:
            if self._reading is not None or self._error == detail:
                return
            self._error = detail
            status_detail = self._detail
        self._submit({"type": "status", "detail": status_detail, "error": detail})

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "type": "snapshot",
                "detail": self._detail,
                "error": self._error,
                "output": self._output_path,
                "reading": self._reading,
            }

    def _submit(self, message: dict[str, object]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._broadcast, message)
        except RuntimeError:
            return

    def _broadcast(self, message: dict[str, object]) -> None:
        for listener_socket, queue in tuple(self.listeners):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A slow page reconnects and receives a fresh snapshot.
                asyncio.create_task(
                    listener_socket.close(code=1013, message=b"Client too slow")
                )


STATE_KEY: web.AppKey[DashboardState] = web.AppKey("state")


def _close_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    with suppress(OSError):
        sock.close()


async def _close_listeners(state: DashboardState) -> None:
    sockets = [listener_socket for listener_socket, _queue in tuple(state.listeners)]
    if len(sockets) == 0:
        return
    await asyncio.gather(
        *(
            listener_socket.close(code=1001, message=b"Server shutting down")
            for listener_socket in sockets
        ),
        return_exceptions=True,
    )


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(INDEX_PATH, headers={"Cache-Control": "no-store"})


async def websocket(request: web.Request) -> web.WebSocketResponse:
    origin = request.headers.get("Origin")
    expected_origin = f"{request.scheme}://{request.host}"
    if origin is not None and origin != expected_origin:
        raise web.HTTPForbidden(text="Origin does not match this server")
    socket_response = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
    await socket_response.prepare(request)
    state = request.app[STATE_KEY]
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=8)
    listener = (socket_response, queue)
    state.listeners.add(listener)
    queue.put_nowait(state.snapshot())

    async def write_messages() -> None:
        try:
            while True:
                await socket_response.send_json(await queue.get())
        except (ConnectionError, RuntimeError):
            await socket_response.close()

    writer = asyncio.create_task(write_messages())
    try:
        async for incoming in socket_response:
            if incoming.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR}:
                break
    finally:
        state.listeners.discard(listener)
        writer.cancel()
        with suppress(asyncio.CancelledError):
            await writer
    return socket_response


def create_app(state: DashboardState) -> web.Application:
    app = web.Application()
    app[STATE_KEY] = state
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket)

    async def remember_loop(_app: web.Application) -> None:
        state.attach_loop(asyncio.get_running_loop())

    app.on_startup.append(remember_loop)
    return app


class Dashboard:
    """Page server for one logging session.

    The spotread session stays on the main thread so it can read the terminal
    and handle Ctrl-C. This server runs beside it and is updated from those callbacks.
    """

    def __init__(self, port: int, output_path: str) -> None:
        self._port = port
        self.bound_port = port
        self._state = DashboardState(output_path)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._socket: socket.socket | None = None
        self._started = threading.Event()
        self._start_error: BaseException | None = None
        self._closed = False

    def publish_reading(self, reading: SpotReading) -> None:
        self._state.publish_reading(reading)

    def publish_status(self, detail: str) -> None:
        self._state.publish_status(detail)

    def publish_error(self, detail: str) -> None:
        self._state.publish_error(detail)

    def start(self) -> None:
        """Listen before returning, so a taken port is reported before measuring."""
        if self._thread is not None:
            raise RuntimeError("dashboard is already started")
        self._thread = threading.Thread(
            target=self._run,
            name="spotread-dashboard",
            daemon=True,
        )
        self._thread.start()
        self._started.wait()
        if self._start_error is not None:
            self._thread.join(timeout=5)
            raise self._start_error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        stop = self._stop
        if loop is not None and stop is not None:
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._state.attach_loop(loop)
        self._stop = asyncio.Event()
        try:
            loop.run_until_complete(self._serve())
        except Exception as error:
            if self._start_error is None:
                self._start_error = error
        finally:
            if not self._started.is_set():
                if self._start_error is None:
                    self._start_error = RuntimeError("dashboard stopped before listening")
                self._started.set()
            loop.close()
            self._loop = None

    async def _serve(self) -> None:
        listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listening.bind((DASHBOARD_HOST, self._port))
            listening.listen(128)
        except OSError as error:
            listening.close()
            self._start_error = OSError(
                f"could not listen on {DASHBOARD_HOST}:{self._port}: {error.strerror}"
            )
            self._started.set()
            return
        self.bound_port = int(listening.getsockname()[1])
        self._socket = listening
        runner = web.AppRunner(create_app(self._state), access_log=None)
        try:
            await runner.setup()
            await web.SockSite(runner, listening).start()
        except Exception as error:
            self._start_error = error
            with suppress(Exception):
                await runner.cleanup()
            _close_socket(self._socket)
            self._socket = None
            self._started.set()
            return
        # SockSite owns the listening socket once start succeeds.
        self._socket = None
        self._started.set()
        try:
            assert self._stop is not None
            await self._stop.wait()
            await _close_listeners(self._state)
        finally:
            await runner.cleanup()
