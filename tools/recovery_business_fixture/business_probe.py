"""Synthetic loopback-only HTTPS business probe; no browser or Release assertion.

Configuration is a local mode-0600 JSON file with base_url, ca_file, members
(two existing synthetic login_id/password pairs), and optionally expected_ratings.
This script never receives a production endpoint, prints credentials, changes
schema, reads a registry, or reconstructs old Redis rooms.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise RuntimeError("Redirects are disabled for the owned local fixture")


class Client:
    def __init__(self, base_url: str, context: ssl.SSLContext) -> None:
        self.base_url = base_url
        self.csrf: str | None = None
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def request(self, method: str, path: str, payload: dict | None = None,
                *, expected: int = 200, mutate: bool = False) -> dict:
        headers = {"Origin": self.base_url}
        if mutate:
            if self.csrf is None:
                raise RuntimeError("synthetic CSRF token missing")
            headers["X-CSRF-Token"] = self.csrf
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, body, headers, method=method)
        try:
            with self.opener.open(request, timeout=10) as response:
                if response.status != expected:
                    raise RuntimeError("unexpected HTTP status")
                result = json.load(response)
        except urllib.error.HTTPError as error:
            # Do not echo response body, supplied secrets, cookies, or raw URL.
            raise RuntimeError(f"HTTP status {error.code} for {method} synthetic route") from None
        return result


def run(config: dict) -> dict:
    if "SSLKEYLOGFILE" in os.environ:
        raise RuntimeError("TLS key logging is not permitted in this owned fixture")
    parts = urllib.parse.urlsplit(config["base_url"])
    if (parts.scheme != "https" or parts.hostname != "localhost" or
            parts.username or parts.password or parts.path or parts.query or parts.fragment or
            parts.port is None):
        raise RuntimeError("Only explicit localhost HTTPS synthetic endpoint is allowed")
    context = ssl.create_default_context(cafile=config["ca_file"])
    members = config["members"]
    if len(members) != 2 or any(set(member) != {"login_id", "password"} for member in members):
        raise RuntimeError("Two existing synthetic member credentials are required")
    clients = [Client(config["base_url"], context) for _ in members]
    started = time.monotonic()
    checks: list[str] = []
    clients[0].request("GET", "/health/ready")
    checks.append("production_provider_and_runner_readiness")
    for client, member in zip(clients, members, strict=True):
        login = client.request("POST", "/api/v1/sessions/member", member)
        if login["actor_type"] != "MEMBER":
            raise RuntimeError("Synthetic login did not return a Member")
        client.csrf = login["csrf_token"]
        current = client.request("GET", "/api/v1/session")
        if current["actor_type"] != "MEMBER" or current["room_id"] is not None:
            raise RuntimeError("New Redis session was not independent")
    checks.append("two_restored_members_login_with_fresh_secure_sessions")
    ranking = clients[0].request("GET", "/api/v1/rankings")
    ratings = {str(item["member_id"]): item["rating"] for item in ranking["items"]}
    if ranking["me"] is not None:
        ratings[str(ranking["me"]["member_id"])] = ranking["me"]["rating"]
    if any(ratings.get(str(k)) != v for k, v in config.get("expected_ratings", {}).items()):
        raise RuntimeError("Restored ranking/rating assertion failed")
    checks.append("persistent_ranking_and_rating_read")
    room = clients[0].request("POST", "/api/v1/rooms", {
        "request_id": str(uuid4()), "name": "Independent recovery fixture",
        "minimum_ready": 2, "vote_seconds": 15,
    }, expected=201, mutate=True)
    room_id = room["room_id"]
    room = clients[1].request("POST", f"/api/v1/rooms/{room_id}/joins", {
        "request_id": str(uuid4()), "expected_state_version": room["state_version"],
    }, expected=201, mutate=True)
    for client, team in zip(clients, ("BLACK", "WHITE"), strict=True):
        for suffix, values in (("team", {"team": team}), ("ready", {"ready": True})):
            room = client.request("PUT", f"/api/v1/rooms/{room_id}/participants/me/{suffix}", {
                "request_id": str(uuid4()), "expected_state_version": room["state_version"],
                **values,
            }, mutate=True)
    game = clients[0].request("POST", f"/api/v1/rooms/{room_id}/games", {
        "request_id": str(uuid4()), "expected_state_version": room["state_version"],
    }, expected=201, mutate=True)
    if game["game_status"] != "ACTIVE":
        raise RuntimeError("Synthetic new game did not become active")
    clients[1].request("GET", f"/api/v1/games/{game['game_id']}")
    checks.append("fresh_room_join_teams_ready_and_new_game_read")
    current_room = clients[0].request("GET", f"/api/v1/rooms/{room_id}/snapshot")
    waiting = clients[1].request("DELETE", f"/api/v1/rooms/{room_id}/participants/me", {
        "request_id": str(uuid4()), "expected_state_version": current_room["state_version"],
    }, mutate=True)
    if waiting["status"] != "WAITING" or waiting["last_game_id"] != game["game_id"]:
        raise RuntimeError("Explicit leave did not finish the new fixture game")
    completed = clients[0].request("GET", f"/api/v1/games/{game['game_id']}/result")
    if (completed["end_reason"] != "FORFEIT" or completed["winner"] != "BLACK" or
            completed["stats_eligible"] is not True or completed["my_rating"]["outcome"] != "WIN"):
        raise RuntimeError("New fixture forfeit/result assertion failed")
    checks.append("fresh_game_explicit_leave_forfeit_result_and_rating_read")
    return {"scope": "synthetic_loopback_https_backend_business",
            "service_rto": None, "release_acceptance": False,
            "probe_elapsed_seconds": round(time.monotonic() - started, 6),
            "new_completed_game_id": game["game_id"],
            "checks": checks,
            "excluded": ["FE/browser/WSS", "Harbor/Image/PVC/OCP", "incident_detection",
                         "manual_user_guidance", "real_RDS/S3/VPN", "historical_result_HTTP"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        raise RuntimeError("Synthetic credential configuration must be mode 0600")
    if args.output.exists():
        raise RuntimeError("Use a new output path for every measurement")
    with args.config.open() as stream:
        config = json.load(stream)
    result = run(config)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print("Synthetic HTTPS Backend business probe completed")


if __name__ == "__main__":
    main()
