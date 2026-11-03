import json
import unittest
from unittest.mock import Mock

import requests

from leverage.client import ApiError, TmsClient
from leverage.config import ConfigurationError, Settings, timestamp
from tests.fakes import FakeSession, page, response


class ConfigurationTests(unittest.TestCase):
    def test_dashboard_credentials_are_rejected_without_revealing_them(self):
        for token in ("ApiToken https://analytics.cloud.memsource.com/embed/JWT", "Bearer secret\r\nother"):
            with self.assertRaises(ConfigurationError) as error:
                Settings(authorization=token)
            self.assertNotIn(token, str(error.exception))

    def test_only_official_regional_hosts_are_allowed(self):
        for url in ("http://cloud.memsource.com/web", "https://evil.test/web", "https://cloud.memsource.com@evil.test/web",
                    "https://cloud.memsource.com/web?token=hidden", "https://cloud.memsource.com:443/web"):
            with self.assertRaises(ConfigurationError):
                Settings(authorization="ApiToken fixture-secret", base_url=url)
        Settings(authorization="Bearer fixture-secret", base_url="https://us.cloud.memsource.com/web/")

    def test_timezones_and_half_open_period(self):
        config = Settings(authorization="Bearer fixture-secret", start="2026-01-01", end="2027-01-01")
        self.assertTrue(config.in_period("2026-01-01T01:00:00+01:00"))
        self.assertFalse(config.in_period("2027-01-01T00:00:00Z"))
        self.assertFalse(config.in_period("2025-12-31T23:59:59Z"))
        with self.assertRaises(ConfigurationError):
            timestamp("2026-01-01T01:00:00")
        with self.assertRaises(ConfigurationError):
            Settings(authorization="Bearer fixture-secret", start="2027-01-01", end="2026-01-01")

    def test_credentials_never_appear_in_repr_or_public_configuration(self):
        config = Settings(authorization="Bearer fixture-secret")
        self.assertNotIn("fixture-secret", repr(config))
        self.assertNotIn("fixture-secret", json.dumps(config.public()))

    def test_invalid_limits(self):
        for kwargs in ({"requests_per_second": 0}, {"requests_per_second": float("nan")}, {"retries": 11}, {"page_size": 51}, {"max_pages": 0}):
            with self.assertRaises(ConfigurationError):
                Settings(authorization="ApiToken fixture-secret", **kwargs)


class ClientTests(unittest.TestCase):
    def client(self, handler, **kwargs):
        self.session = FakeSession(handler)
        self.sleep = Mock()
        return TmsClient(Settings(authorization="ApiToken fixture-secret", **kwargs), self.session, self.sleep, lambda: 0)

    def test_retries_retry_after_and_server_errors(self):
        responses = iter([response(429, {}, {"Retry-After": "2"}), response(503, {}), response(200, {"ok": True})])
        client = self.client(lambda p, q: next(responses))
        self.assertEqual({"ok": True}, client.get("/api2/v1/projects"))
        self.assertEqual(3, len(self.session.calls))
        self.assertIn(unittest.mock.call(2.0), self.sleep.call_args_list)
        self.assertTrue(all(c[1]["allow_redirects"] is False for c in self.session.calls))
        self.assertTrue(all(c[1]["headers"]["Authorization"] == "ApiToken fixture-secret" for c in self.session.calls))

    def test_concurrency_backoff_and_budget(self):
        client = self.client(lambda p, q: response(429, {}, {"Ratelimit-Limit": "10"}), retries=1)
        with self.assertRaises(ApiError):
            client.get("/api2/v1/projects")
        self.assertEqual(2, len(self.session.calls))
        self.assertTrue(any(call.args and call.args[0] >= 60 for call in self.sleep.call_args_list))

    def test_excessive_retry_after_is_actionable(self):
        client = self.client(lambda p, q: response(429, {}, {"Retry-After": "3600"}))
        with self.assertRaisesRegex(ApiError, "repeat retrieval later"):
            client.get("/api2/v1/projects")
        self.assertEqual(1, len(self.session.calls))

    def test_permanent_errors_are_not_retried_or_echoed(self):
        for status in (401, 403, 404, 302, 400, 202):
            client = self.client(lambda p, q: response(status, {"error": "fixture-secret https://private.invalid"}))
            with self.assertRaises(ApiError) as error:
                client.get("/api2/v1/projects")
            self.assertEqual(1, len(self.session.calls))
            self.assertNotIn("fixture-secret", str(error.exception))
            self.assertNotIn("private.invalid", str(error.exception))

    def test_transport_errors_are_sanitized(self):
        def failure(p, q):
            raise requests.Timeout("credential fixture-secret")
        client = self.client(failure, retries=1)
        with self.assertRaises(ApiError) as error:
            client.get("/api2/v1/projects")
        self.assertNotIn("fixture-secret", str(error.exception))
        self.assertEqual(2, len(self.session.calls))

    def test_invalid_json(self):
        client = self.client(lambda p, q: response(200, content=b"not-json fixture-secret"))
        with self.assertRaises(ApiError) as error:
            client.get("/api2/v1/projects")
        self.assertEqual("INVALID_JSON", error.exception.code)

    def test_unsafe_paths_and_no_mutation_surface(self):
        client = self.client(lambda p, q: response(200, {}))
        for path in ("https://evil.invalid", "/api2/v1/../auth", "/api2/v1/projects?token=x", "/api2/v1/projects/%2e%2e"):
            with self.assertRaises(ApiError):
                client.get(path)
        self.assertFalse(any(hasattr(client, method) for method in ("post", "put", "patch", "delete")))
        self.assertEqual([], self.session.calls)

    def test_pagination_exhausts_all_pages_and_archives_them(self):
        rows = [{"uid": str(i)} for i in range(103)]
        client = self.client(lambda p, q: response(200, page(rows, q)))
        self.assertEqual(rows, list(client.pages("/api2/v1/projects")))
        self.assertEqual([0, 1, 2], [c[1]["params"]["pageNumber"] for c in self.session.calls])
        self.assertEqual(3, len(client.snapshots))

    def test_changed_or_truncated_pages_are_failures(self):
        def changed(p, q):
            return response(200, {"content": [{"uid": str(q["pageNumber"])}], "totalElements": 2 + q["pageNumber"], "totalPages": 2})
        client = self.client(changed)
        with self.assertRaises(ApiError) as error:
            list(client.pages("/api2/v1/projects"))
        self.assertEqual("PAGE_CHANGED", error.exception.code)
        client = self.client(lambda p, q: response(200, {"content": [], "totalElements": 1, "totalPages": 1}))
        with self.assertRaises(ApiError) as error:
            list(client.pages("/api2/v1/projects"))
        self.assertEqual("PAGE_TRUNCATED", error.exception.code)

    def test_duplicate_and_malformed_pages_fail(self):
        for body in ({"content": [{"uid": "a"}, {"uid": "a"}], "totalElements": 2, "totalPages": 1},
                     {"content": ["bad"], "totalElements": 1, "totalPages": 1}, {"content": []}):
            client = self.client(lambda p, q: response(200, body))
            with self.assertRaises(ApiError):
                list(client.pages("/api2/v1/projects"))

    def test_max_pages_is_bounded(self):
        client = self.client(lambda p, q: response(200, {"content": [{"uid": "a"}], "totalElements": 2, "totalPages": 2}), max_pages=1)
        with self.assertRaises(ApiError) as error:
            list(client.pages("/api2/v1/projects"))
        self.assertEqual("PAGE_LIMIT", error.exception.code)

    def test_snapshot_redacts_nested_tokens_and_unexpected_echoes(self):
        client = self.client(lambda p, q: response(200, {}))
        client.snapshot("test", "a", {"csrfToken": "secret", "nested": {"authorization": "secret"}, "name": "fixture-secret"}, "v3")
        text = json.dumps(list(client.snapshots.values()))
        self.assertNotIn("fixture-secret", text)
        self.assertNotIn('"secret"', text)


if __name__ == "__main__":
    unittest.main()
