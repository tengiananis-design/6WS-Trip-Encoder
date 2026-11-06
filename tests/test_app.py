import os
import unittest
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

from app import DashboardAuthMiddleware


class DashboardAuthTests(unittest.IsolatedAsyncioTestCase):
    def make_request(self, path="/", authorization=None):
        headers = []
        if authorization:
            headers.append((b"authorization", authorization.encode("ascii")))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
        }
        return Request(scope)

    async def call_middleware(self, path="/", authorization=None):
        middleware = DashboardAuthMiddleware(lambda scope, receive, send: None)
        called = {"next": False}

        async def call_next(request):
            called["next"] = True
            return Response("ok", status_code=200)

        response = await middleware.dispatch(
            self.make_request(path, authorization), call_next
        )
        return response, called["next"]

    async def test_missing_credentials_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            response, continued = await self.call_middleware()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(continued)

    async def test_health_check_does_not_require_login(self):
        with patch.dict(os.environ, {}, clear=True):
            response, continued = await self.call_middleware("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(continued)

    async def test_valid_basic_credentials_are_accepted(self):
        with patch.dict(os.environ, {"APP_USERNAME": "tester", "APP_PASSWORD": "secret"}, clear=True):
            response, continued = await self.call_middleware(
                authorization="Basic dGVzdGVyOnNlY3JldA=="
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(continued)

    async def test_invalid_basic_credentials_are_rejected(self):
        with patch.dict(os.environ, {"APP_USERNAME": "tester", "APP_PASSWORD": "secret"}, clear=True):
            response, continued = await self.call_middleware(
                authorization="Basic dGVzdGVyOndyb25n"
            )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(continued)


if __name__ == "__main__":
    unittest.main()
