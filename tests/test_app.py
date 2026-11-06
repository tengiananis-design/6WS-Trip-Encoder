import os
import unittest
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

from app import DashboardAuthMiddleware, normalize_manual_driver_name, sync_review_edits_to_analysis, unique_pdf_filename


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

    def test_duplicate_sanitized_pdf_names_never_overwrite(self):
        seen = set()
        names = [
            unique_pdf_filename("trip.pdf", 1, seen),
            unique_pdf_filename("trip.pdf", 2, seen),
            unique_pdf_filename("trip_2.pdf", 3, seen),
            unique_pdf_filename("../trip.pdf", 4, seen),
        ]
        self.assertEqual(names, ["trip.pdf", "trip_2.pdf", "trip_2_2.pdf", "trip_3.pdf"])
        self.assertEqual(len({name.lower() for name in names}), len(names))

    def test_upload_filename_is_bounded_for_preview_route(self):
        filename = unique_pdf_filename(("A" * 250) + ".pdf", 1, set())
        self.assertLessEqual(len(filename), 124)

    def test_review_edits_sync_to_driver_and_source_analysis(self):
        result = {
            "trips": [{
                "source_filename": "ticket.pdf",
                "trip_date": "2026-11-01",
                "origin": "SOC 6",
                "segments": [{"origin": "SOC 6", "destination": "Old Hub"}]
            }],
            "drivers": [{"trips": [{
                "source_filename": "ticket.pdf",
                "trip_date": "2026-11-01",
                "origin": "SOC 6",
                "segments": [{"origin": "SOC 6", "destination": "Old Hub"}]
            }]}],
            "document_analyses": [{
                "source_filename": "ticket.pdf",
                "trips": [{
                    "trip_date": "2026-11-01",
                    "origin": "SOC 6",
                    "segments": [{"origin": "SOC 6", "destination": "Old Hub"}]
                }]
            }]
        }
        result["trips"][0]["destination"] = "Corrected Hub"
        result["trips"][0]["segments"][0]["destination"] = "Corrected Hub"
        sync_review_edits_to_analysis(result, {
            "trips": {"destination", "segments"}
        })
        self.assertEqual(result["drivers"][0]["trips"][0]["destination"], "Corrected Hub")
        self.assertEqual(result["document_analyses"][0]["trips"][0]["destination"], "Corrected Hub")
        self.assertEqual(
            result["document_analyses"][0]["trips"][0]["segments"][0]["destination"],
            "Corrected Hub"
        )

    def test_manual_driver_name_whitespace_is_normalized(self):
        self.assertEqual(normalize_manual_driver_name("  Amado   B   Canar  "), "Amado B Canar")

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
