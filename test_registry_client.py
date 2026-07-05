from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

import httpx

from doctor import app as doctor_app
from server.common.registry.client import RegistryClient


def _client(**kwargs):
    defaults = {
        "service_name": "doctor",
        "version": "0.1.0",
        "host": "localhost",
        "port": 8020,
        "capabilities": ["diagnostics", "health_report"],
        "metadata": {},
    }
    defaults.update(kwargs)
    return RegistryClient(**defaults)


class RegistryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_uses_official_header_and_expected_payload(self):
        captured: list[tuple[httpx.Request, dict | None]] = []

        async def core(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content) if request.content else None
            captured.append((request, payload))
            return httpx.Response(200, json={"status": "healthy"})

        client = _client(
            core_url="http://core.test",
            api_key="secret",
            host="doctor.internal",
            transport=httpx.MockTransport(core),
        )
        try:
            registered = await client.register()
        finally:
            await client.stop()

        request, payload = captured[0]
        self.assertTrue(registered)
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer secret",
        )
        self.assertNotIn("X-API-Key", request.headers)
        self.assertEqual(
            payload,
            {
                "service_name": "doctor",
                "host": "doctor.internal",
                "port": 8020,
                "version": "0.1.0",
                "status": "healthy",
                "capabilities": ["diagnostics", "health_report"],
                "metadata": {},
            },
        )

    async def test_start_without_core_does_not_crash(self):
        async def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Core unavailable", request=request)

        client = _client(
            core_url="http://core.test",
            transport=httpx.MockTransport(unavailable),
        )
        try:
            await client.start()
            await asyncio.sleep(0.01)
            self.assertFalse(client._registered)
            self.assertIsNotNone(client._task)
        finally:
            await client.stop()

    async def test_heartbeat_is_sent_periodically(self):
        paths: list[str] = []

        async def core(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"status": "healthy"})

        client = _client(
            core_url="http://core.test",
            heartbeat_interval=0.01,
            transport=httpx.MockTransport(core),
        )
        try:
            await client.start()
            await asyncio.sleep(0.035)
        finally:
            await client.stop()

        self.assertEqual(paths[0], "/registry/register")
        self.assertIn("/registry/heartbeat", paths[1:])

    async def test_environment_configuration(self):
        env = {
            "NERON_CORE_URL": "http://core.internal:8010/",
            "NERON_API_KEY": "key",
            "NERON_SERVICE_HOST": "doctor.internal",
            "NERON_SERVICE_PORT": "9020",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            client = _client()

        self.assertEqual(client.settings.core_url, "http://core.internal:8010")
        self.assertEqual(client.settings.api_key, "key")
        self.assertEqual(client.service.host, "doctor.internal")
        self.assertEqual(client.service.port, 9020)
        await client.stop()

    async def test_doctor_lifespan_starts_and_stops_registry_client(self):
        fake_client = mock.Mock()
        fake_client.start = mock.AsyncMock()
        fake_client.stop = mock.AsyncMock()

        with mock.patch.object(doctor_app, "RegistryClient", return_value=fake_client):
            async with doctor_app.lifespan(doctor_app.app):
                fake_client.start.assert_awaited_once()
                self.assertIs(
                    doctor_app.app.state.registry_client,
                    fake_client,
                )

        fake_client.stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
