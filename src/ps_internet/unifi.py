"""Pause and resume existing UniFi QoS rules. This client never creates or deletes them.

Matching rules are listed from the gateway QoS API. Pause and resume are the
rule's enabled flag. A change is a PUT of the same rule with only enabled flipped.
"""

from __future__ import annotations

import httpx


class UnifiError(Exception):
    pass


class UnifiClient:
    def __init__(
        self,
        base_url: str,
        site_id: str,
        api_key: str,
        client: httpx.Client | None = None,
        network_site: str = "default",
    ):
        del site_id  # QoS uses the local Network site name, not the integration site id.
        root = base_url.rstrip("/")
        self.base = f"{root}/proxy/network/v2/api/site/{network_site}/qos-rules"
        self._owns_client = client is None
        self._client = client or httpx.Client(
            verify=False,
            timeout=40,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> UnifiClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def list_policies(self) -> list[dict]:
        response = self._client.get(self.base)
        self._raise(response, "list qos rules")
        body = response.json()
        if isinstance(body, list):
            return body
        return body.get("data") or []

    def matching(self, needle: str) -> list[dict]:
        prefix = needle.casefold().strip()
        if not prefix:
            return []
        found = []
        for rule in self.list_policies():
            name = str(rule.get("name") or "")
            if prefix and not name.casefold().startswith(prefix):
                continue
            found.append(
                {
                    "id": rule["_id"],
                    "name": name,
                    "description": _schedule(rule),
                    "enabled": bool(rule.get("enabled")),
                    "action": str(rule.get("objective") or ""),
                }
            )
        found.sort(key=lambda item: item["name"].casefold())
        return found

    def set_enabled(self, policy_id: str, enabled: bool, name_prefix: str = "") -> dict:
        current = self._rule(policy_id)
        name = str(current.get("name") or "")
        prefix = name_prefix.casefold().strip()
        if not prefix or not name.casefold().startswith(prefix):
            raise UnifiError(f"refusing to change {name or policy_id}")
        body = dict(current)
        body["enabled"] = bool(enabled)
        updated = self._client.put(f"{self.base}/{policy_id}", json=body)
        self._raise(updated, "pause or resume qos rule")
        return updated.json()

    def _rule(self, policy_id: str) -> dict:
        for rule in self.list_policies():
            if rule.get("_id") == policy_id:
                return rule
        raise UnifiError("qos rule not found")

    def _raise(self, response: httpx.Response, action: str) -> None:
        if response.status_code < 400:
            return
        raise UnifiError(f"{action} returned HTTP {response.status_code}")


def _schedule(rule: dict) -> str:
    schedule = rule.get("schedule") or {}
    if not isinstance(schedule, dict):
        return ""
    start = schedule.get("time_range_start") or ""
    end = schedule.get("time_range_end") or ""
    mode = schedule.get("mode") or ""
    if start and end:
        return f"{start}–{end}"
    return str(mode)
