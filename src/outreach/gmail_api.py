"""The real Gmail adapter: the official Gmail REST API with an OAuth desktop-app token.

Standard library only. This file is not covered by the automated tests (they use the in-memory fake); it is
checked by hand against a test account before any real outreach (see the Gmail section of docs/review-handoff-files.md).

Scope: `gmail.modify`, the narrowest single scope that covers every call made here: `drafts.create/update/send`,
`messages.list` (search Sent for the Message-ID), `messages.modify` and `labels.create/list` (the "Outreach"
label). `gmail.compose` alone cannot list or label messages, and `gmail.labels` cannot label a message.

Errors become the adapter errors in `outreach.gmail`: 401 and a rejected refresh token are auth errors, rate
and daily limits are quota errors, timeouts, dropped connections and 5xx answers are uncertain (Gmail may have
acted), any other refusal is a plain `GmailError`.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from outreach.config import Config
from outreach.errors import UserError
from outreach.gmail import (
    DraftContent,
    GmailAuthError,
    GmailError,
    GmailQuotaError,
    GmailUncertain,
    SentMessage,
    build_mime,
)

SCOPE = "https://www.googleapis.com/auth/gmail.modify"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com"
_QUOTA_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded", "quotaExceeded",
                  "limitExceeded", "sendLimitExceeded"}
_REAUTH = "run `outreach gmail-auth` to sign in to Gmail again"


def _load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except OSError:
        raise GmailAuthError(f"{what} not found at {path}; {_REAUTH}") from None
    except json.JSONDecodeError:
        raise GmailAuthError(f"{what} at {path} is not valid JSON; {_REAUTH}") from None
    if not isinstance(data, dict):
        raise GmailAuthError(f"{what} at {path} is not a JSON object; {_REAUTH}")
    return data


def _post_form(url: str, form: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code in (400, 401):
            raise GmailAuthError(f"Gmail authorization has expired or was revoked ({_oauth_error(body)}); "
                                 f"{_REAUTH}") from None
        raise GmailError(f"Google sign-in failed: HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GmailUncertain(f"cannot reach Google sign-in: {e}") from None
    if not isinstance(data, dict):
        raise GmailError("Google sign-in answered with something unexpected")
    return data


def _oauth_error(body: str) -> str:
    try:
        return str(json.loads(body).get("error", "unknown error"))
    except (json.JSONDecodeError, AttributeError):
        return "unknown error"


class RealGmail:
    def __init__(self, config: Config, timeout: float = 60.0) -> None:
        self._token_path = Path(config.gmail_token)
        self._timeout = timeout
        self._label_ids: dict[str, str] = {}

    # --- auth -------------------------------------------------------------------------------

    def _access_token(self) -> str:
        token = _load_json(self._token_path, "the Gmail token")
        if float(token.get("expires_at", 0)) > time.time() + 60 and token.get("access_token"):
            return str(token["access_token"])
        if not token.get("refresh_token"):
            raise GmailAuthError(f"the Gmail token has no refresh token; {_REAUTH}")
        fresh = _post_form(TOKEN_URL, {
            "grant_type": "refresh_token", "refresh_token": str(token["refresh_token"]),
            "client_id": str(token["client_id"]), "client_secret": str(token["client_secret"]),
        }, self._timeout)
        token.update(access_token=fresh["access_token"], expires_at=time.time() + float(fresh.get("expires_in", 3600)))
        _save_token(self._token_path, token)
        return str(token["access_token"])

    # --- HTTP -------------------------------------------------------------------------------

    def _call(self, method: str, path: str, *, query: dict[str, str] | None = None, body: bytes | None = None,
              content_type: str = "application/json") -> dict[str, Any]:
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        request = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self._access_token()}", "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise _http_error(e.code, e.read().decode(errors="replace")) from None
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError) as e:
            raise GmailUncertain(f"no answer from Gmail: {e}") from None
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise GmailError("Gmail answered with something unexpected")
        return data

    # --- the adapter ------------------------------------------------------------------------

    def create_draft(self, content: DraftContent) -> str:
        reply = self._call("POST", "/upload/gmail/v1/users/me/drafts", query={"uploadType": "media"},
                           body=build_mime(content), content_type="message/rfc822")
        return str(reply["id"])

    def update_draft(self, draft_id: str, content: DraftContent) -> None:
        self._call("PUT", f"/upload/gmail/v1/users/me/drafts/{urllib.parse.quote(draft_id, safe='')}",
                   query={"uploadType": "media"}, body=build_mime(content), content_type="message/rfc822")

    def send_draft(self, draft_id: str) -> SentMessage:
        reply = self._call("POST", "/gmail/v1/users/me/drafts/send", body=json.dumps({"id": draft_id}).encode())
        return SentMessage(str(reply["id"]))

    def find_sent(self, message_id: str) -> SentMessage | None:
        query = f"rfc822msgid:{message_id.strip('<>')} in:sent"
        reply = self._call("GET", "/gmail/v1/users/me/messages", query={"q": query, "maxResults": "1"})
        messages = reply.get("messages") or []
        return SentMessage(str(messages[0]["id"])) if messages else None

    def add_label(self, gmail_id: str, label: str) -> None:
        label_id = self._label_id(label)
        self._call("POST", f"/gmail/v1/users/me/messages/{urllib.parse.quote(gmail_id, safe='')}/modify",
                   body=json.dumps({"addLabelIds": [label_id]}).encode())

    def _label_id(self, name: str) -> str:
        if name not in self._label_ids:
            for existing in self._call("GET", "/gmail/v1/users/me/labels").get("labels", []):
                self._label_ids[str(existing["name"])] = str(existing["id"])
        if name not in self._label_ids:
            created = self._call("POST", "/gmail/v1/users/me/labels", body=json.dumps(
                {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}).encode())
            self._label_ids[name] = str(created["id"])
        return self._label_ids[name]

    def profile_address(self) -> str:
        return str(self._call("GET", "/gmail/v1/users/me/profile").get("emailAddress", ""))


def _http_error(status: int, body: str) -> GmailError:
    try:
        detail = json.loads(body).get("error", {})
        message = str(detail.get("message", "")) or f"HTTP {status}"
        reasons = {str(e.get("reason", "")) for e in detail.get("errors", []) if isinstance(e, dict)}
    except (json.JSONDecodeError, AttributeError):
        message, reasons = f"HTTP {status}", set()
    if status == 401 or "authError" in reasons or "insufficientPermissions" in reasons:
        return GmailAuthError(f"Gmail refused the saved authorization ({message}); {_REAUTH}")
    if status == 429 or reasons & _QUOTA_REASONS:
        return GmailQuotaError(f"Gmail limit reached ({message}); try again later")
    if status >= 500:
        return GmailUncertain(f"Gmail had a server problem ({message})")
    return GmailError(f"Gmail refused the request ({message})")


# --- the one-time sign-in (OAuth desktop flow with PKCE, loopback redirect) ---------------------


def _save_token(path: Path, token: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(token, f)
    path.chmod(0o600)


def authorize(config: Config, timeout: float = 300.0) -> str:
    """Sign in once in the browser and save the token. Returns the authorized Gmail address."""
    client_file = Path(config.gmail_credentials)
    client = _load_json(client_file, "the Google OAuth client file").get("installed")
    if not isinstance(client, dict) or not client.get("client_id") or not client.get("client_secret"):
        raise UserError(f"{client_file}: expected the JSON of a Google Cloud OAuth client of type 'Desktop app'")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            result.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Gmail is connected. You can close this tab and return to the terminal.")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = timeout
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    })
    print(f"Opening your browser to sign in to Gmail. If nothing opens, visit:\n{url}", flush=True)
    webbrowser.open(url)
    try:
        server.handle_request()
    finally:
        server.server_close()
    if not result or result.get("state") != state or "code" not in result:
        raise UserError(f"Gmail sign-in did not finish: {result.get('error', 'no answer from the browser')}")
    reply = _post_form(str(client.get("token_uri", TOKEN_URL)), {
        "grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect,
        "client_id": str(client["client_id"]), "client_secret": str(client["client_secret"]),
        "code_verifier": verifier,
    }, 60.0)
    if "refresh_token" not in reply:
        raise UserError("Google did not return a refresh token; remove this app at "
                        "https://myaccount.google.com/permissions and run the sign-in again")
    _save_token(Path(config.gmail_token), {
        "access_token": reply["access_token"], "refresh_token": reply["refresh_token"],
        "expires_at": time.time() + float(reply.get("expires_in", 3600)),
        "client_id": client["client_id"], "client_secret": client["client_secret"], "scope": SCOPE,
    })
    return RealGmail(config).profile_address()
