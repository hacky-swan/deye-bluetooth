"""Bleak I/O layer for the Deye logger BLE local protocol.

This is the only module that touches hardware. It owns the GATT connection,
enables notifications, sends AT commands, and awaits the matching notification.
All byte construction / parsing is delegated to the pure `protocol` module so
the wire logic can be tested without a radio.

One BLE central at a time: the phone app and HA cannot both connect.
"""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

WRITE_CHAR = "0000fec7-0000-1000-8000-00805f9b34fb"
# The 0x0922 profile observed upstream replies on FED8. AP_* loggers with the
# HF-LPx70-style FEE7 profile use FEC8 (notify) or FED6 (indicate) for
# module->app UART data. Pick the first characteristic actually exposed.
NOTIFY_CHARS = (
    "0000fed8-0000-1000-8000-00805f9b34fb",
    "0000fec8-0000-1000-8000-00805f9b34fb",
    "0000fed6-0000-1000-8000-00805f9b34fb",
)

DEFAULT_TIMEOUT = 10.0  # seconds to await a notification reply
CONNECT_ATTEMPTS = 3    # establish_connection retries transient proxy failures
# A hung disconnect on a flaky proxy link must never hold the caller (and thus
# the coordinator's BLE lock) open — bound it, then drop the client regardless.
DISCONNECT_TIMEOUT = 10.0


class DeyeBleError(Exception):
    """Connection lost, timed out, or the logger rejected a command."""


class DeyeBleTransport:
    """A single AT-command request/response session over GATT.

    Usage:
        async with DeyeBleTransport(ble_device) as t:
            await t.handshake()
            regs = await t.read(0x008F, 1)
            await t.write(0x008F, 100)
    """

    def __init__(self, ble_device, timeout: float = DEFAULT_TIMEOUT):
        self._device = ble_device
        # For establish_connection logging; BLEDevice exposes name/address.
        self._name = getattr(ble_device, "name", None) or getattr(
            ble_device, "address", "deye"
        )
        self._timeout = timeout
        self._disconnect_timeout = DISCONNECT_TIMEOUT
        self._client: BleakClient | None = None
        self._reply: asyncio.Future[str] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notify_char = NOTIFY_CHARS[0]
        self._write_with_response = True

    async def __aenter__(self) -> "DeyeBleTransport":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        # Use bleak-retry-connector — the resilient path HA expects. It routes
        # through the ESP32 proxies, retries transient failures, and handles the
        # reconnect churn that raw BleakClient.connect() does not.
        try:
            self._client = await establish_connection(
                BleakClient,
                self._device,
                self._name,
                max_attempts=CONNECT_ATTEMPTS,
            )
        except Exception as e:  # noqa: BLE001 — connect can raise many bleak errors
            raise DeyeBleError(f"connect failed: {e}") from e
        # Select the GATT transport profile from the characteristics that are
        # actually present. The original logger profile uses FED8 for replies;
        # AP_* loggers based on the HF-LPx70 BLE profile expose FEC8/FED6
        # instead. FEC7 is the UART write channel in both profiles, but on the
        # standard FEE7 profile it is Write Without Response.
        services = getattr(self._client, "services", None)
        if services is not None and hasattr(services, "get_characteristic"):
            write_char = services.get_characteristic(WRITE_CHAR)
            if write_char is None:
                available = self._gatt_summary(services)
                await self.disconnect()
                raise DeyeBleError(
                    f"write characteristic {WRITE_CHAR} not found; GATT: {available}"
                )

            props = {str(p).lower() for p in getattr(write_char, "properties", [])}
            self._write_with_response = "write" in props

            selected = None
            for uuid in NOTIFY_CHARS:
                char = services.get_characteristic(uuid)
                if char is None:
                    continue
                char_props = {str(p).lower() for p in getattr(char, "properties", [])}
                if "notify" in char_props or "indicate" in char_props:
                    selected = uuid
                    break

            if selected is None:
                available = self._gatt_summary(services)
                await self.disconnect()
                raise DeyeBleError(
                    f"no supported reply characteristic found; GATT: {available}"
                )
            self._notify_char = selected
            _LOGGER.info(
                "Deye BLE GATT profile: write=%s response=%s reply=%s",
                WRITE_CHAR,
                self._write_with_response,
                self._notify_char,
            )

        # start_notify can raise a raw bleak error. The GATT connection is ALREADY
        # open at this point, so on failure we MUST release it: the logger accepts
        # a single central and stops advertising while held, so a leaked link
        # wedges the inverter (and eats a proxy slot) until the ESP32 proxy is
        # restarted — an integration reload cannot undo it. Re-raise as
        # DeyeBleError so the coordinator's failure grace rides it out.
        try:
            await self._client.start_notify(self._notify_char, self._on_notify)
        except Exception as e:  # noqa: BLE001 — notify can raise many bleak errors
            await self.disconnect()
            raise DeyeBleError(
                f"start_notify failed on {self._notify_char}: {e}"
            ) from e

    async def disconnect(self) -> None:
        """Tear down the session, but never block on it.

        stop_notify and disconnect are best-effort: a flaky proxy link can leave
        either hanging, and if disconnect stalls it would hold the coordinator's
        BLE lock forever, wedging every subsequent poll and write until HA
        restarts. Bound both with a timeout and always drop the client. Each call
        is also shielded, so a poll cancelled mid-teardown still lets the release
        finish in the background instead of abandoning a half-open link.
        """
        client, self._client = self._client, None
        if client is None:
            return
        for coro, what in (
            (client.stop_notify(self._notify_char), "stop_notify"),
            (client.disconnect(), "disconnect"),
        ):
            try:
                await asyncio.wait_for(asyncio.shield(coro), self._disconnect_timeout)
            except Exception:  # noqa: BLE001 — teardown is best-effort, never raises
                _LOGGER.debug("BLE %s did not complete cleanly", what, exc_info=True)

    @staticmethod
    def _gatt_summary(services) -> str:
        """Compact characteristic/property list for unsupported logger profiles."""
        rows = []
        try:
            for service in services:
                for char in service.characteristics:
                    props = ",".join(str(p) for p in getattr(char, "properties", []))
                    rows.append(f"{char.uuid}[{props}]")
        except Exception:  # noqa: BLE001 — diagnostics must never mask the real error
            return "<unavailable>"
        return "; ".join(rows) or "<empty>"

    def _on_notify(self, _char, data: bytearray) -> None:
        text = bytes(data).decode("ascii", errors="replace").strip()
        if self._reply is not None and not self._reply.done():
            self._reply.set_result(text)

    async def _command(self, payload: bytes) -> str:
        if self._client is None or self._loop is None:
            raise DeyeBleError("not connected")
        self._reply = self._loop.create_future()
        await self._client.write_gatt_char(
            WRITE_CHAR, payload, response=self._write_with_response
        )
        try:
            return await asyncio.wait_for(self._reply, self._timeout)
        except asyncio.TimeoutError as e:
            raise DeyeBleError(f"no reply to {payload!r}") from e
        finally:
            self._reply = None

    async def handshake(self) -> None:
        reply = await self._command(b"AT+DTYPE\n")
        if not p.is_handshake_ack(reply):
            raise DeyeBleError(f"unexpected handshake reply: {reply!r}")

    async def read(self, address: int, count: int) -> list[int]:
        reply = await self._command(p.wrap_read(p.build_read(address, count)))
        try:
            return p.parse_read(reply)
        except p.ProtocolError as e:
            raise DeyeBleError(str(e)) from e

    async def write_block(self, address: int, values: list[int]) -> None:
        """Write a contiguous register range in ONE frame (see build_write_block).

        Used by the clock commit, where three separate single-register frames
        corrupt the year byte on this hardware.
        """
        request = p.build_write_block(address, values)
        reply = await self._command(p.wrap_write(request))
        try:
            acked = p.parse_write_ack(reply, request)
        except p.ProtocolError as e:
            raise DeyeBleError(str(e)) from e
        if not acked:
            raise DeyeBleError(
                f"block write to 0x{address:04X} not acked: {reply!r}"
            )

    async def write(self, address: int, value: int) -> None:
        """Write a register and verify the ack echoes the address + quantity."""
        request = p.build_write(address, value)
        reply = await self._command(p.wrap_write(request))
        try:
            acked = p.parse_write_ack(reply, request)
        except p.ProtocolError as e:
            raise DeyeBleError(str(e)) from e
        if not acked:
            raise DeyeBleError(f"write to 0x{address:04X} not acked: {reply!r}")
