"""Review server: a local-only, server-rendered page over the Store.

Bound to 127.0.0.1. Everything shown comes from Jobright or Claude and is untrusted, so every value is
HTML-escaped and the page ships with a CSP that forbids scripts. Changes are POSTs that need the page's
random token, a local Host header and (when sent) a local Origin, which blocks other web pages and DNS
rebinding from driving it.

Routes: GET /; POST /emails/<id>/(edit|approve|reject|skip), /groups/<id>/resume, /bulk-approve, /confirm-send.
"""
from __future__ import annotations

import hmac
import html
import json
import re
import secrets
import sqlite3
import urllib.parse
from collections.abc import Callable
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from outreach import drafts, emails
from outreach.config import Config
from outreach.errors import Conflict, UserError
from outreach.gmail import Gmail, GmailError
from outreach.store import Store

HOST = "127.0.0.1"
MAX_FORM_BYTES = 200_000
_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
_NOTICES = {
    "edited": "Edits saved.",
    "resume": "Resume Variant switched.",
    "decided": "Decision saved.",
    "confirmed": "Confirmed: the approved emails are marked ready to send.",
    "dryrun": "Dry run: totals confirmed; nothing was marked and nothing can be sent.",
}

_STYLE = """
body{font:15px/1.45 system-ui,sans-serif;max-width:60rem;margin:1.5rem auto;padding:0 1rem;color:#1b1b1b}
section{border:1px solid #bbb;border-radius:6px;padding:.8rem 1rem;margin:1rem 0}
article{border-top:1px solid #ddd;padding-top:.7rem;margin-top:.7rem}
textarea{width:100%;font:inherit} input[type=text]{width:100%;font:inherit}
.warn{color:#8a4b00}.bad{color:#a00000;font-weight:600}.muted{color:#666}
.notice{background:#eef6ee;border:1px solid #9c9;padding:.4rem .7rem}
.err{background:#fdeeee;border:1px solid #c99;padding:.4rem .7rem}
form{display:inline-block;margin:.2rem .3rem .2rem 0} .edit{display:block}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


class ReviewServer(HTTPServer):
    def __init__(self, db_path: Path, config: Config, run_id: int, port: int, gmail: Gmail | None = None) -> None:
        super().__init__((HOST, port), _Handler)
        self.gmail = gmail
        self.db_path = db_path
        self.config = config
        self.run_id = run_id
        self.token = secrets.token_urlsafe(24)
        self.confirmed = False

    @property
    def authority(self) -> str:
        return f"{HOST}:{self.server_address[1]}"

    def close(self) -> None:
        self.server_close()


def make_server(db_path: Path, config: Config, run_id: int, port: int = 0, gmail: Gmail | None = None) -> ReviewServer:
    return ReviewServer(db_path, config, run_id, port, gmail)


def serve(db_path: Path, config: Config, run_id: int, port: int = 0, gmail: Gmail | None = None,
          on_ready: Callable[[str], None] | None = None) -> bool:
    """Serve the review page until the final confirmation (or Ctrl-C). `on_ready` gets the page's URL.

    Returns whether the user confirmed.
    """
    server = make_server(db_path, config, run_id, port, gmail)
    url = f"http://{server.authority}/"
    print(f"review page ready: {url}  (Ctrl-C to stop)", flush=True)
    if on_ready:
        on_ready(url)
    server.timeout = 0.5
    try:
        while not server.confirmed:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nreview page stopped")
    finally:
        server.close()
    return server.confirmed


class _Handler(BaseHTTPRequestHandler):
    server: ReviewServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # keep the terminal quiet; nothing here is worth logging

    # --- plumbing ---

    def _send(self, status: int, body: str, location: str | None = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def _page(self, status: int, title: str, message: str) -> None:
        self._send(status, f"<!doctype html><meta charset=utf-8><title>{_e(title)}</title><style>{_STYLE}</style>"
                           f"<p class=err>{_e(message)}</p><p><a href=\"/\">Back to the review page</a></p>")

    def _local(self) -> bool:
        if self.headers.get("Host") not in (self.server.authority, f"localhost:{self.server.server_address[1]}"):
            self._page(403, "Forbidden", "Unexpected Host header.")
            return False
        return True

    # --- requests ---

    def do_GET(self) -> None:  # noqa: N802
        if not self._local():
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/":
            self._page(404, "Not found", "No such page.")
            return
        query = urllib.parse.parse_qs(parsed.query)
        notice = _NOTICES.get(query.get("notice", [""])[0], "")
        if query.get("notice") == ["bulk"]:
            n, left = (_int(query.get(k, [""])[0]) for k in ("n", "left"))
            notice = (f"Bulk approved {n} email(s); {left} left because of a blocked resume. "
                      "Switch the Resume Variant or fix the file, then approve them.")
        with closing(Store(self.server.db_path)) as store:
            body = render_page(store, self.server.config, self.server.run_id, self.server.token, notice)
        self._send(200, body)

    def do_POST(self) -> None:  # noqa: N802
        if not self._local():
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.server.authority}":
            self._page(403, "Forbidden", "Cross-origin request refused.")
            return
        length = _int(self.headers.get("Content-Length") or "0")
        if length > MAX_FORM_BYTES:
            self._page(413, "Too large", "The form is too large.")
            return
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace")).items()}
        if not hmac.compare_digest(form.get("token", ""), self.server.token):
            self._page(403, "Forbidden", "Missing or wrong page token. Reload the review page.")
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            with closing(Store(self.server.db_path)) as store, store.transaction():
                location = self._act(store, path, form)
        except GmailError as e:
            self._page(502, "Gmail problem", f"{e} (nothing was saved)")
        except Conflict as e:
            self._page(409, "Not allowed", str(e))
        except UserError as e:
            self._page(400, "Bad request", str(e))
        except LookupError as e:
            self._page(404, "Not found", str(e))
        except sqlite3.Error as e:
            self._page(500, "Database error", str(e))
        else:
            if location is None:
                self._page(404, "Not found", "No such action.")
            elif path == "/confirm-send":
                # The server stops right after this reply, so show the result now instead of redirecting.
                notice = _NOTICES[urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["notice"][0]]
                with closing(Store(self.server.db_path)) as store:
                    body = render_page(store, self.server.config, self.server.run_id, self.server.token, notice)
                self._send(200, body)
            else:
                self._send(303, "", location=location)

    def _sync_drafts(self, store: Store, email_ids: list[int]) -> None:
        """Copy a change to the Gmail drafts. A Gmail failure raises, which rolls the change back."""
        if store.get_run(self.server.run_id)["dry_run"]:
            return  # dry run: no drafts exist
        if self.server.gmail is None:
            raise GmailError("Gmail is not connected")
        for email_id in email_ids:
            drafts.sync_draft(store, self.server.gmail, self.server.config, email_id)

    def _act(self, store: Store, path: str, form: dict[str, str]) -> str | None:
        run_id, config = self.server.run_id, self.server.config
        if m := re.fullmatch(r"/emails/(\d+)/(edit|approve|reject|skip)", path):
            email_id, action = int(m.group(1)), m.group(2)
            row = store.get_email(email_id)
            if row is None or row["run_id"] != run_id:
                raise LookupError(f"Outreach Email {email_id} does not exist")
            if action == "edit":
                emails.edit_email(store, config, email_id, form.get("subject", ""), form.get("body", ""))
                self._sync_drafts(store, [email_id])
                return f"/?notice=edited#email-{email_id}"
            emails.decide(store, email_id, {"approve": "approved", "reject": "rejected", "skip": "skipped"}[action])
            return f"/?notice=decided#email-{email_id}"
        if m := re.fullmatch(r"/groups/(\d+)/resume", path):
            group = store.get_group(int(m.group(1)))
            if group["run_id"] != run_id:
                raise LookupError(f"Company Group {group['id']} does not exist")
            emails.switch_resume(store, config, int(group["id"]), form.get("variant", ""))
            self._sync_drafts(store, [int(r["id"]) for r in store.run_emails_for_group(int(group["id"]))])
            return f"/?notice=resume#group-{group['id']}"
        if path == "/bulk-approve":
            approved, left = emails.bulk_approve(store, run_id)
            return f"/?notice=bulk&n={approved}&left={left}"
        if path == "/confirm-send":
            for name in ("emails", "recipients"):
                if not form.get(name, "").strip().isdigit():
                    raise UserError(f"{name}: type the number shown on the page")
            emails.confirm_send(store, run_id, int(form["emails"]), int(form["recipients"]))
            self.server.confirmed = True
            return "/?notice=dryrun" if store.get_run(run_id)["dry_run"] else "/?notice=confirmed"
        return None


def _int(text: str) -> int:
    return int(text) if text.isdigit() and len(text) < 9 else 0


# --- rendering ---------------------------------------------------------------------------------


def _safe_link(url: str) -> str:
    """The LinkedIn URL as a link only when it is plain https; otherwise as inert text."""
    if re.fullmatch(r"https://[^\s<>\"']+", url):
        return f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(url)}</a>'
    return _e(url)


def _form(action: str, token: str, inner: str, extra_class: str = "") -> str:
    return (f'<form method="post" action="{_e(action)}" class="{extra_class}">'
            f'<input type="hidden" name="token" value="{_e(token)}">{inner}</form>')


def _render_email(row: sqlite3.Row, token: str, locked: bool) -> str:
    findings = json.loads(row["findings"])
    contact_groups = ", ".join(json.loads(row["jobright_groups"]))
    parts = [
        f'<article id="email-{row["id"]}">',
        f'<p><strong>{_e(row["contact_name"])}</strong> &middot; {_e(row["contact_title"])} &middot; '
        f'category {_e(row["category"])} &middot; Jobright group: {_e(contact_groups)}'
        + (f' &middot; {_e(row["connection"])}' if row["connection"] else "") + "<br>"
        f'LinkedIn: {_safe_link(row["linkedin_url"])}<br>'
        f'To: {_e(row["address"])} <span class="warn">(Unverified Address: Jobright says it found this '
        "from public information, so it may be wrong)</span> "
        f'&middot; Status: <strong>{_e(row["status"])}</strong></p>',
    ]
    if row["block_reason"]:
        parts.append(f'<p class="bad">Blocked: {_e(row["block_reason"])}</p>')
    if findings:
        items = "".join(f"<li>{_e(f['message'])}</li>" for f in findings)
        parts.append(f'<ul class="warn">{items}</ul>')
    if locked:
        parts.append(f"<p><strong>{_e(row['subject'])}</strong></p><pre>{_e(row['body'])}</pre>")
    else:
        edit = (f'<label>Subject <input type="text" name="subject" value="{_e(row["subject"])}"></label>'
                f'<label>Body <textarea name="body" rows="9">{_e(row["body"])}</textarea></label>'
                "<button>Save edits</button>")
        parts.append(_form(f"/emails/{row['id']}/edit", token, edit, "edit"))
        buttons = "".join(
            _form(f"/emails/{row['id']}/{action}", token, f"<button>{label}</button>")
            for action, label in (("approve", "Approve"), ("reject", "Reject"), ("skip", "Skip"))
        )
        parts.append(f"<div>{buttons}</div>")
    parts.append("</article>")
    return "".join(parts)


def _render_resume(group_rows: list[sqlite3.Row], config: Config, token: str) -> str:
    first = group_rows[0]
    variants = "".join(
        f'<option value="{_e(v)}"{" selected" if v == first["resume_variant"] else ""}>{_e(v)}</option>'
        for v in config.resume_files
    )
    locked = all(r["status"] in emails.LOCKED for r in group_rows)
    switch = "" if locked else _form(
        f"/groups/{first['group_id']}/resume", token,
        f'<label>Resume Variant <select name="variant">{variants}</select></label><button>Switch</button>',
    )
    return (f"<p>Resume: <strong>{_e(first['resume_variant'])}</strong> "
            f"({_e(Path(first['resume_path']).name)}) {switch}</p>")


def _flag_reason(contact: sqlite3.Row) -> str:
    if contact["status"] == "flagged":
        last = (contact["cooldown_last_emailed"] or "")[:10]
        return "emailed within the cooldown window" + (f" (last emailed {last})" if last else "")
    if contact["status"] == "bounced":
        return "address bounced before"
    return "same address as another Contact in this Run's emails"


def _render_flagged(contacts: list[sqlite3.Row]) -> str:
    """Contacts that get no email, with why, so nobody is silently missing from the page."""
    if not contacts:
        return ""
    items = "".join(
        f"<li>{_e(c['name'])} &middot; {_e(c['title'])} &middot; {_safe_link(c['linkedin_url'])}: "
        f"{_e(_flag_reason(c))}</li>"
        for c in contacts
    )
    return f'<div class="warn"><p>Flagged for review, no Outreach Email:</p><ul>{items}</ul></div>'


def _drafts_note(group_rows: list[sqlite3.Row], dry: bool) -> str:
    if dry:
        return "none (dry run)"
    return f"{sum(1 for r in group_rows if r['draft_id'])} of {len(group_rows)}"


def render_page(store: Store, config: Config, run_id: int, token: str, notice: str = "") -> str:
    run = store.get_run(run_id)
    rows = store.run_emails(run_id)
    shown = emails.totals(store, run_id)
    dry = bool(run["dry_run"])
    out = [f"<!doctype html><meta charset=utf-8><title>Outreach review {_e(run['run_date'])}</title>"
           f"<style>{_STYLE}</style><h1>Outreach review, {_e(run['run_date'])}</h1>"]
    if dry:
        out.append("<p class=\"warn\"><strong>Dry run:</strong> no Gmail drafts exist and nothing can be sent.</p>")
    if notice:
        out.append(f'<p class="notice">{_e(notice)}</p>')
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no emails"
    out.append(f"<p>{len(rows)} Outreach Email(s): {_e(summary)}.</p>")

    flagged: dict[int, list[sqlite3.Row]] = {}
    for contact in store.flagged_contacts(run_id):
        flagged.setdefault(contact["group_id"], []).append(contact)
    for group in store.shortlisted_groups(run_id):
        group_rows = [r for r in rows if r["group_id"] == group["id"]]
        roles = ", ".join(
            f"{_e(a['title'])} (Match Score {'closed' if a['match_score'] is None else a['match_score']})"
            for a in store.group_applications(group["id"])
        )
        picked = f"{group['contacts_picked']} of {group['contact_cap']}" if group["contact_cap"] is not None else "?"
        out.append(
            f'<section id="group-{group["id"]}"><h2>{_e(group["name"])} <small class="muted">Rank {group["rank"]}'
            f"</small></h2><p>Roles: {roles}</p><p>Contacts: {_e(picked)} &middot; "
            + (f"Outreach Emails: {len(group_rows)} &middot; Gmail drafts: {_drafts_note(group_rows, dry)}</p>"
               if group_rows else "no Outreach Emails for this Company Group</p>")
        )
        if group_rows:
            out.append(_render_resume(group_rows, config, token))
        out.append(_render_flagged(flagged.get(group["id"], [])))
        out.extend(_render_email(r, token, r["status"] in emails.LOCKED) for r in group_rows)
        out.append("</section>")

    confirm = _form("/confirm-send", token, (
        f"<p><strong>{shown.emails} approved email(s) to {shown.recipients} recipient(s).</strong> "
        "Rejected, skipped and unreviewed emails are never sent.</p>"
        '<label>Type the number of emails <input type="text" name="emails" size="4" autocomplete="off"></label> '
        '<label>and of recipients <input type="text" name="recipients" size="4" autocomplete="off"></label> '
        "<button>Confirm</button>"
    ))
    waiting = [r for r in rows if r["status"] == "written"]
    blocked = sum(1 for r in waiting if r["block_reason"])
    bulk = _form("/bulk-approve", token, "<button>Bulk approve</button>")
    out.append(f"<section><h2>Approval</h2>{bulk}"
               f'<p class="muted">{len(waiting)} waiting for review, {blocked} with a blocked resume. '
               "Bulk approve approves every email still waiting for review, including ones with findings "
               "(findings do not block Approval) but not ones with a blocked resume; "
               f"those stay until you switch the Resume Variant or fix the file.</p>{confirm}</section>")
    return "".join(out)
