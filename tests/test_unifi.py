import json
import unittest

import httpx

from ps_internet.unifi import UnifiClient, UnifiError

MORNING = {
    "_id": "abc",
    "name": "student_uptime_morning",
    "enabled": True,
    "objective": "LIMIT",
    "index": 10003,
    "schedule": {"mode": "EVERY_DAY", "time_range_start": "07:00", "time_range_end": "09:00"},
    "source": {"matching_target": "NETWORK"},
    "destination": {"matching_target": "ANY"},
    "download_limit_kbps": 512000,
}


class UnifiTests(unittest.TestCase):
    def test_pause_puts_the_same_rule_with_only_enabled_flipped(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=[MORNING, {"_id": "nope", "name": "other_rule", "enabled": True}])
            seen["body"] = json.loads(request.content)
            seen["path"] = request.url.path
            return httpx.Response(200, json=seen["body"])

        client = UnifiClient(
            "https://gateway.example",
            "site",
            "secret",
            httpx.Client(transport=httpx.MockTransport(handler)),
        )
        matched = client.matching("student")
        self.assertEqual([item["name"] for item in matched], ["student_uptime_morning"])
        self.assertEqual(matched[0]["description"], "07:00–09:00")
        client.set_enabled("abc", False, name_prefix="student")
        body = seen["body"]
        self.assertFalse(body["enabled"])
        self.assertEqual(body["source"], MORNING["source"])
        self.assertEqual(body["download_limit_kbps"], 512000)
        self.assertTrue(seen["path"].endswith("/qos-rules/abc"))

    def test_refuses_a_rule_outside_the_prefix(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"_id": "abc", "name": "guest_limit", "enabled": True}])

        client = UnifiClient(
            "https://gateway.example",
            "site",
            "secret",
            httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with self.assertRaises(UnifiError):
            client.set_enabled("abc", False, name_prefix="student")

    def test_an_empty_prefix_matches_nothing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[MORNING])

        client = UnifiClient(
            "https://gateway.example",
            "site",
            "secret",
            httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(client.matching(""), [])


if __name__ == "__main__":
    unittest.main()
