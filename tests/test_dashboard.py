import asyncio
import json
import socket
from dataclasses import replace

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from spotread_logger.cli import measurement_failure
from spotread_logger.dashboard import (
    WAITING_DETAIL,
    Dashboard,
    DashboardState,
    create_app,
    reading_payload,
)
from spotread_logger.parse_reading import parse_instrument_identity, parse_spot_reading
from tests.test_parse_reading import (
    BANNER,
    EMISSIVE_READING,
    REFLECTANCE_READING,
    WARM_READING,
)


def _reading(block: str, mode: str, timestamp: str = "2026-11-12T06:00:00.000Z"):
    return parse_spot_reading(
        block,
        timestamp,
        parse_instrument_identity(BANNER),
        mode,  # type: ignore[arg-type]
    )


def test_payload_keeps_measured_values_and_leaves_out_the_raw_text() -> None:
    ambient = reading_payload(_reading(WARM_READING, "ambient"))
    encoded = json.dumps(ambient)
    assert "raw_reading" not in encoded
    assert "Spectrum from" not in encoded
    assert ambient["measurement_mode"] == "ambient"
    assert ambient["instrument"]["instrument_type"] == "ColorMunki"
    assert ambient["spectrum"]["start_nm"] == 380
    assert ambient["spectrum"]["end_nm"] == 730
    assert ambient["spectrum"]["steps"] == 106
    assert ambient["spectrum"]["values"] == [0.00184778, 0.00184778, 0.0765904]
    assert ambient["spectrum"]["quantity"] == "Spectral irradiance"
    assert ambient["spectrum"]["unit"] == "mW/(m²·nm)"
    assert ambient["illuminant"]["lux"] == 1.8
    assert ambient["illuminant"]["cct_kelvin"] == 3003
    assert ambient["cri"]["ra"] == 96.7
    assert ambient["cri"]["r9"] == 81.7
    assert ambient["tm30"]["rf"] == 97.44
    assert ambient["density"]["iso_vis"] is None
    assert ambient["notes"]

    emissive = reading_payload(_reading(EMISSIVE_READING, "emissive"))
    assert emissive["illuminant"]["lux"] is None
    assert emissive["illuminant"]["cct_kelvin"] == 3799
    assert emissive["spectrum"]["unit"] == "mW/(m²·sr·nm)"

    reflectance = reading_payload(_reading(REFLECTANCE_READING, "reflectance"))
    assert reflectance["spectrum"]["quantity"] == "Reflectance"
    assert reflectance["spectrum"]["unit"] == "%"
    assert reflectance["density"]["status_e"]["yellow"] == 0.606906
    assert reflectance["illuminant"]["cct_kelvin"] is None


async def _latest_reading_over_websocket() -> None:
    state = DashboardState("readings.csv")
    client = TestClient(TestServer(create_app(state)))
    await client.start_server()
    try:
        page = await client.get("/")
        assert page.status == 200
        assert "no-store" in page.headers["Cache-Control"]
        body = await page.text()
        assert 'id="plot"' in body
        assert "/ws" in body

        denied = await client.get("/ws", headers={"Origin": "https://example.org"})
        assert denied.status == 403
        await denied.text()

        socket = await client.ws_connect("/ws")
        snapshot = await socket.receive_json()
        assert snapshot == {
            "type": "snapshot",
            "detail": WAITING_DETAIL,
            "error": None,
            "output": "readings.csv",
            "reading": None,
        }
        state.publish_error("spotread returned no measurement")
        waiting_failure = await socket.receive_json()
        assert waiting_failure["error"] == "spotread returned no measurement"
        first = _reading(WARM_READING, "ambient")
        second = replace(first, ambient_lux=42.5, timestamp="2026-11-12T06:05:00.000Z")
        state.publish_reading(first)
        state.publish_reading(second)
        first_message = await socket.receive_json()
        second_message = await socket.receive_json()
        assert first_message["type"] == "reading"
        assert first_message["reading"]["illuminant"]["lux"] == 1.8
        assert second_message["reading"]["illuminant"]["lux"] == 42.5
        assert "readings" not in second_message

        again = await client.ws_connect("/ws")
        replay = await again.receive_json()
        assert replay["type"] == "snapshot"
        assert replay["reading"]["timestamp"] == "2026-11-12T06:05:00.000Z"
        assert replay["reading"]["illuminant"]["lux"] == 42.5
        assert "readings" not in replay

        state.publish_error("spotread aborted a reading; retrying")
        state.publish_status("next reading in 30s")
        kept = await socket.receive_json()
        assert kept["detail"] == "next reading in 30s"
        assert kept["error"] is None
        kept_again = await again.receive_json()
        assert kept_again["detail"] == "next reading in 30s"
        assert kept_again["error"] is None
        held = await client.ws_connect("/ws")
        held_snapshot = await held.receive_json()
        assert held_snapshot["reading"]["illuminant"]["lux"] == 42.5
        assert held_snapshot["error"] is None
        await held.close()
        await socket.close()
        await again.close()
    finally:
        await client.close()


def test_page_and_socket_follow_the_latest_reading() -> None:
    asyncio.run(_latest_reading_over_websocket())


async def _published_from_the_session_side(dashboard: Dashboard) -> None:
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        address = f"http://127.0.0.1:{dashboard.bound_port}/ws"
        async with session.ws_connect(address) as socket:
            snapshot = await asyncio.wait_for(socket.receive_json(), timeout=3)
            assert snapshot["reading"] is None
            assert snapshot["output"] == "live.csv"
            dashboard.publish_reading(_reading(WARM_READING, "ambient"))
            dashboard.publish_status("next reading in 10s")
            reading_message = await asyncio.wait_for(socket.receive_json(), timeout=3)
            status_message = await asyncio.wait_for(socket.receive_json(), timeout=3)
            assert reading_message["reading"]["peak_wavelength_nm"] == 730
            assert status_message["detail"] == "next reading in 10s"


def test_session_thread_can_publish_the_latest_reading() -> None:
    dashboard = Dashboard(0, "live.csv")
    try:
        dashboard.start()
        assert dashboard.bound_port != 0
        asyncio.run(_published_from_the_session_side(dashboard))
    finally:
        dashboard.close()


def test_a_taken_port_is_reported_before_the_server_runs() -> None:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    dashboard = Dashboard(holder.getsockname()[1], "readings.csv")
    try:
        with pytest.raises(OSError, match="could not listen"):
            dashboard.start()
    finally:
        dashboard.close()
        holder.close()


def test_a_reply_without_a_sample_is_a_failure_and_a_real_reading_is_not() -> None:
    blank = _reading("  \n", "reflectance")
    assert measurement_failure(blank) == "spotread returned no measurement"
    told = _reading("Measurement failed: instrument not in contact", "reflectance")
    assert measurement_failure(told) == (
        "spotread returned no measurement: Measurement failed: instrument not in contact"
    )
    noted = replace(blank, notes=["spectrum value count 0 does not match reported steps 106"])
    assert measurement_failure(noted) == (
        "spotread returned no measurement: "
        "spectrum value count 0 does not match reported steps 106"
    )
    long_reading = _reading("x" * 200, "reflectance")
    failure = measurement_failure(long_reading)
    assert failure is not None
    assert failure.endswith("...")
    assert len(failure) == len("spotread returned no measurement: ") + 160
    assert measurement_failure(_reading(WARM_READING, "ambient")) is None
