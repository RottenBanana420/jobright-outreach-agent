"""The real Gmail adapter's error mapping and token file, against a throwaway local HTTP server (no Google)."""
from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from outreach import gmail_api
from outreach.config import Config
from outreach.gmail import GmailAuthError, GmailError, GmailUncertain


@contextmanager
def server(status: int, body: str) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body.encode())

        do_GET = do_POST = do_PUT = _answer

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def write_token(path: Path, **over: object) -> None:
    token: dict[str, object] = {
        "access_token": "old", "refresh_token": "r", "expires_at": 0, "client_id": "c", "client_secret": "s",
    }
    token.update(over)
    path.write_text(json.dumps(token))


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> gmail_api.RealGmail:
    config = replace(Config(), gmail_token=str(tmp_path / "token.json"))
    write_token(Path(config.gmail_token))
    return gmail_api.RealGmail(config, timeout=5)


@pytest.mark.parametrize("body, auth", [
    ('{"error": "invalid_grant"}', True),
    ('{"error": "invalid_request", "error_description": "Missing required parameter"}', False),
    ("not json", False),
])
def test_only_an_invalid_grant_answer_means_the_authorization_expired(
    real: gmail_api.RealGmail, monkeypatch: pytest.MonkeyPatch, body: str, auth: bool,
) -> None:
    with server(400, body) as url:
        monkeypatch.setattr(gmail_api, "TOKEN_URL", url)
        with pytest.raises(GmailError) as raised:
            real._access_token()

    assert isinstance(raised.value, GmailAuthError) is auth
    assert ("outreach gmail-auth" in str(raised.value)) is auth


def test_a_token_answer_that_is_not_json_is_a_gmail_error_not_a_crash(
    real: gmail_api.RealGmail, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with server(200, "<html>captive portal</html>") as url:
        monkeypatch.setattr(gmail_api, "TOKEN_URL", url)
        with pytest.raises(GmailError):
            real._access_token()


def test_a_token_answer_without_an_access_token_is_a_gmail_error_not_a_crash(
    real: gmail_api.RealGmail, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with server(200, '{"expires_in": 3600}') as url:
        monkeypatch.setattr(gmail_api, "TOKEN_URL", url)
        with pytest.raises(GmailError):
            real._access_token()


def test_a_token_file_missing_the_client_id_asks_to_sign_in_again(real: gmail_api.RealGmail) -> None:
    Path(real._token_path).write_text(json.dumps({"refresh_token": "r", "expires_at": 0}))

    with pytest.raises(GmailAuthError, match="outreach gmail-auth"):
        real._access_token()


def test_a_token_file_with_a_bad_expiry_asks_to_sign_in_again(real: gmail_api.RealGmail) -> None:
    write_token(Path(real._token_path), expires_at="soon")

    with pytest.raises(GmailAuthError, match="outreach gmail-auth"):
        real._access_token()


def test_a_gmail_answer_that_is_not_json_is_uncertain_so_a_send_is_looked_up_in_sent(
    real: gmail_api.RealGmail, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_token(Path(real._token_path), access_token="fresh", expires_at=time.time() + 3600)
    with server(200, "<html>proxy error</html>") as url:
        monkeypatch.setattr(gmail_api, "API", url)
        with pytest.raises(GmailUncertain):
            real.send_draft("draft1")


def test_a_send_answer_without_a_message_id_is_uncertain(
    real: gmail_api.RealGmail, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_token(Path(real._token_path), access_token="fresh", expires_at=time.time() + 3600)
    with server(200, "{}") as url:
        monkeypatch.setattr(gmail_api, "API", url)
        with pytest.raises(GmailUncertain):
            real.send_draft("draft1")


def test_a_failed_token_write_leaves_the_old_token_intact(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    gmail_api._save_token(path, {"refresh_token": "keep-me"})

    with pytest.raises(TypeError):
        gmail_api._save_token(path, {"refresh_token": object()})  # json.dump fails part way

    assert json.loads(path.read_text()) == {"refresh_token": "keep-me"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token.json"]  # no temp file left behind


def test_the_token_file_is_private_from_the_first_byte(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "token.json"
    old_umask = os.umask(0)
    try:
        gmail_api._save_token(path, {"refresh_token": "x"})
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
