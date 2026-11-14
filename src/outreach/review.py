"""Review server: a local-only, server-rendered page over the Store.

Bound to 127.0.0.1. Everything shown comes from Jobright or Claude and is untrusted, so every value is
HTML-escaped and the page ships with a CSP that forbids scripts. Changes are POSTs that need the page's
random token, a local Host header and (when sent) a local Origin, which blocks other web pages and DNS
rebinding from driving it.

Routes: GET /; POST /emails/<id>/(edit|approve|reject|skip), /emails/resume (one or several ticked emails),
/groups/<id>/resume, /bulk-approve, /confirm-send.
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
:root{--fg:#1f1f1f;--muted:#6b6b6b;--line:#e4e1db;--bg:#f7f6f3;--card:#fff;--ok:#2f6b3c;--warn:#8a5a00;--bad:#a12a2a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:46rem;margin:0 auto;padding:2rem 1rem 5rem}
h1{font-size:1.35rem;font-weight:650;margin:0}
h2{font-size:1.1rem;font-weight:650;margin:0}
p{margin:.3rem 0}
a{color:inherit}
.muted,.meta{color:var(--muted)} .meta{font-size:.9rem}
.warn{color:var(--warn)} .bad{color:var(--bad);font-weight:600}
.notice,.err{border-radius:8px;padding:.6rem .8rem;margin:1rem 0}
.notice{background:#e8f2ea;color:#1e4a28} .err{background:#f8e8e8;color:#6e1c1c}
.bar{position:sticky;top:0;z-index:1;background:var(--bg);border-bottom:1px solid var(--line);
  padding:.6rem 0;margin:1rem 0 0;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;font-size:.9rem}
section.group{margin-top:2.5rem}
.head{display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem;margin:.8rem 0}
.who{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap}
.who strong{font-weight:600}
.status{margin-left:auto;font-size:.78rem;padding:.05rem .55rem;border-radius:999px;background:#efede8;
  color:var(--muted);white-space:nowrap}
.status.approved{background:#e5f1e7;color:var(--ok)}
.status.ready_to_send,.status.sending,.status.sent{background:#e7eef7;color:#2a5185}
.status.failed,.status.uncertain{background:#f8e8e8;color:var(--bad)}
.subject{font-weight:600;margin-top:.8rem}
pre.body{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:.4rem 0 0}
label{display:block;font-size:.82rem;color:var(--muted);margin:.8rem 0 .2rem}
input[type=text],textarea{width:100%;font:inherit;padding:.45rem .6rem;border:1px solid var(--line);
  border-radius:6px;background:#fff;color:inherit}
textarea{resize:vertical;min-height:13rem;line-height:1.5}
input[type=checkbox]{width:1rem;height:1rem;margin:0;align-self:center}
form{margin:0}
button{font:inherit;font-size:.88rem;padding:.3rem .8rem;border:1px solid #cdc9c1;border-radius:6px;
  background:#fff;color:var(--fg);cursor:pointer}
button:hover{background:#f1efea}
button.primary{background:var(--fg);border-color:var(--fg);color:#fff}
button.primary:hover{background:#3a3a3a}
.row{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
.row .end{margin-left:auto;display:flex;gap:.4rem}
.seg{display:inline-flex;border:1px solid #cdc9c1;border-radius:6px;overflow:hidden;vertical-align:middle}
.seg button{border:0;border-radius:0;border-left:1px solid #cdc9c1;padding:.15rem .55rem;font-size:.8rem}
.seg button:first-child{border-left:0}
.seg button[aria-pressed=true]{background:var(--fg);color:#fff}
.resume{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;font-size:.9rem;margin-top:.6rem}
ul.findings{margin:.5rem 0 0;padding-left:1.1rem;color:var(--warn);font-size:.9rem}
.flagged{font-size:.9rem;color:var(--warn)} .flagged ul{margin:.2rem 0 0;padding-left:1.1rem}
.confirm input[type=text]{width:4.5rem;display:inline-block}
.confirm label{display:inline-block;margin-right:.6rem}
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
        # Not `no-referrer`: under that policy browsers send `Origin: null` on the page's own form posts, which the
        # Origin check below would refuse. `same-origin` still sends nothing to any other site.
        self.send_header("Referrer-Policy", "same-origin")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def _page(self, status: int, title: str, message: str) -> None:
        self._send(status, f"<!doctype html><meta charset=utf-8><title>{_e(title)}</title><style>{_STYLE}</style>"
                           f"<main><p class=err>{_e(message)}</p>"
                           f"<p><a href=\"/\">Back to the review page</a></p></main>")

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
        if query.get("notice") == ["resume-n"]:
            notice = f"Resume switched for {_int(query.get('n', [''])[0])} email(s)."
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
        port = self.server.server_address[1]
        if origin is not None and origin not in (f"http://{self.server.authority}", f"http://localhost:{port}"):
            self._page(403, "Forbidden", "Cross-origin request refused.")
            return
        length = _int(self.headers.get("Content-Length") or "0")
        if length > MAX_FORM_BYTES:
            self._page(413, "Too large", "The form is too large.")
            return
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode(errors="replace"))
        form = {k: v[0] for k, v in fields.items()}
        ticked = fields.get("email", [])
        if not hmac.compare_digest(form.get("token", ""), self.server.token):
            self._page(403, "Forbidden", "Missing or wrong page token. Reload the review page.")
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            with closing(Store(self.server.db_path)) as store:
                with store.transaction():
                    location, to_sync = self._act(store, path, form, ticked)
                self._sync_drafts(store, to_sync)  # after the commit: no Gmail call holds the database lock
        except GmailError as e:
            self._page(502, "Gmail problem", f"{e} (your change is saved; the Gmail draft is brought up to date "
                                           "from it right before sending)")
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
        """Copy a saved change to the Gmail drafts, outside any transaction.

        The Store is the truth, so a Gmail failure raises but does not undo the change; each draft id that
        was created before the failure is already saved, and the sender refreshes every draft before sending.
        """
        if store.get_run(self.server.run_id)["dry_run"]:
            return  # dry run: no drafts exist
        if self.server.gmail is None:
            raise GmailError("Gmail is not connected")
        for email_id in email_ids:
            drafts.sync_draft(store, self.server.gmail, self.server.config, email_id)

    def _act(
        self, store: Store, path: str, form: dict[str, str], ticked: list[str],
    ) -> tuple[str | None, list[int]]:
        """Do the change in the open transaction. Returns where to go next and the emails whose draft needs syncing.

        `ticked` holds every `email` field of the form: the emails a resume switch applies to.
        """
        run_id, config = self.server.run_id, self.server.config
        if path == "/emails/resume":
            ids = []
            for value in dict.fromkeys(ticked):  # the same email ticked twice counts once
                row = store.get_email(_int(value))
                if row is None or row["run_id"] != run_id:
                    raise LookupError(f"Outreach Email {value} does not exist")
                ids.append(int(row["id"]))
            emails.switch_email_resume(store, config, ids, form.get("variant", ""))
            return f"/?notice=resume-n&n={len(ids)}#email-{ids[0]}", ids
        if m := re.fullmatch(r"/emails/(\d+)/(edit|approve|reject|skip)", path):
            email_id, action = int(m.group(1)), m.group(2)
            row = store.get_email(email_id)
            if row is None or row["run_id"] != run_id:
                raise LookupError(f"Outreach Email {email_id} does not exist")
            if action == "edit":
                emails.edit_email(store, config, email_id, form.get("subject", ""), form.get("body", ""))
                return f"/?notice=edited#email-{email_id}", [email_id]
            emails.decide(store, email_id, {"approve": "approved", "reject": "rejected", "skip": "skipped"}[action])
            return f"/?notice=decided#email-{email_id}", []
        if m := re.fullmatch(r"/groups/(\d+)/resume", path):
            group = store.get_group(int(m.group(1)))
            if group["run_id"] != run_id:
                raise LookupError(f"Company Group {group['id']} does not exist")
            emails.switch_resume(store, config, int(group["id"]), form.get("variant", ""))
            sync = [int(r["id"]) for r in store.run_emails_for_group(int(group["id"]))]
            return f"/?notice=resume#group-{group['id']}", sync
        if path == "/bulk-approve":
            approved, left = emails.bulk_approve(store, run_id)
            return f"/?notice=bulk&n={approved}&left={left}", []
        if path == "/confirm-send":
            for name in ("emails", "recipients"):
                if not form.get(name, "").strip().isdigit():
                    raise UserError(f"{name}: type the number shown on the page")
            emails.confirm_send(store, run_id, int(form["emails"]), int(form["recipients"]))
            self.server.confirmed = True
            return ("/?notice=dryrun" if store.get_run(run_id)["dry_run"] else "/?notice=confirmed"), []
        return None, []


def _int(text: str) -> int:
    return int(text) if text.isdigit() and len(text) < 9 else 0


# --- rendering ---------------------------------------------------------------------------------


def _form(action: str, token: str, inner: str, extra_class: str = "", form_id: str = "") -> str:
    ident = f' id="{_e(form_id)}"' if form_id else ""
    return (f'<form method="post" action="{_e(action)}" class="{extra_class}"{ident}>'
            f'<input type="hidden" name="token" value="{_e(token)}">{inner}</form>')


def _variant_buttons(config: Config, current: str | None) -> str:
    """One button per Resume Variant, the current one pressed: switching is a single click (no script needed)."""
    buttons = "".join(
        f'<button name="variant" value="{_e(v)}" aria-pressed="{"true" if v == current else "false"}">'
        f'{_e(v.replace("_", " "))}</button>'
        for v in config.resume_files
    )
    return f'<span class="seg">{buttons}</span>'


def _render_email(row: sqlite3.Row, config: Config, token: str, locked: bool) -> str:
    findings = json.loads(row["findings"])
    contact_groups = ", ".join(json.loads(row["jobright_groups"]))
    email_id = row["id"]
    tick = "" if locked else (f'<input type="checkbox" name="email" value="{email_id}" form="pick" '
                              f'aria-label="Select {_e(row["contact_name"])}">')
    about = [_e(row["category"]), f"Jobright group: {_e(contact_groups)}"]
    if row["connection"]:
        about.append(_e(row["connection"]))
    about.append(_safe_link(row["linkedin_url"], "LinkedIn"))
    parts = [
        f'<article class="card" id="email-{email_id}">',
        f'<div class="who">{tick}<strong>{_e(row["contact_name"])}</strong>'
        f'<span class="muted">{_e(row["contact_title"])}</span>'
        f'<span class="status {_e(row["status"])}">{_e(row["status"])}</span></div>',
        f'<p class="meta">{" &middot; ".join(about)}</p>',
        f'<p class="meta">To <strong>{_e(row["address"])}</strong> <span class="warn">&middot; Unverified Address: '
        "Jobright says it found this from public information, so it may be wrong</span></p>",
    ]
    file_name = _e(Path(row["resume_path"]).name)
    if locked:
        parts.append(f'<div class="resume">Resume: <strong>{_e(row["resume_variant"])}</strong>'
                     f'<span class="muted">{file_name}</span></div>')
    else:
        parts.append(_form("/emails/resume", token,
                           f'<input type="hidden" name="email" value="{email_id}">Resume '
                           f'{_variant_buttons(config, row["resume_variant"])}<span class="muted">{file_name}</span>',
                           "resume"))
    if row["block_reason"]:
        parts.append(f'<p class="bad">Blocked: {_e(row["block_reason"])}</p>')
    if findings:
        items = "".join(f"<li>{_e(f['message'])}</li>" for f in findings)
        parts.append(f'<ul class="findings">{items}</ul>')
    if locked:
        parts.append(f'<p class="subject">{_e(row["subject"])}</p><pre class="body">{_e(row["body"])}</pre>')
    else:
        edit = (f'<label for="subject-{email_id}">Subject</label>'
                f'<input type="text" id="subject-{email_id}" name="subject" value="{_e(row["subject"])}">'
                f'<label for="body-{email_id}">Body</label>'
                f'<textarea id="body-{email_id}" name="body" rows="10">{_e(row["body"])}</textarea>')
        parts.append(_form(f"/emails/{email_id}/edit", token, edit, form_id=f"edit-{email_id}"))
        decisions = "".join(
            _form(f"/emails/{email_id}/{action}", token, f'<button class="{cls}">{label}</button>')
            for action, label, cls in (("approve", "Approve", "primary"), ("reject", "Reject", ""),
                                       ("skip", "Skip", ""))
        )
        parts.append(f'<div class="row"><button form="edit-{email_id}">Save edits</button>'
                     f'<span class="end">{decisions}</span></div>')
    parts.append("</article>")
    return "".join(parts)


def _render_group_resume(group_rows: list[sqlite3.Row], config: Config, token: str, company: str) -> str:
    variants = {r["resume_variant"] for r in group_rows}
    current = next(iter(variants)) if len(variants) == 1 else None
    if all(r["status"] in emails.LOCKED for r in group_rows):
        shown = current or "mixed"
        return f'<div class="resume">Resume for everyone: <strong>{_e(shown)}</strong></div>'
    mixed = "" if current else '<span class="muted">mixed; one click sets all</span>'
    return _form(f"/groups/{group_rows[0]['group_id']}/resume", token,
                 f"Resume for everyone at {_e(company)} {_variant_buttons(config, current)}{mixed}", "resume")


def _safe_link(url: str, label: str | None = None) -> str:
    """The LinkedIn URL as a link only when it is plain https; otherwise as inert text."""
    if re.fullmatch(r"https://[^\s<>\"']+", url):
        return f'<a href="{_e(url)}" rel="noopener noreferrer" title="{_e(url)}">{_e(label or url)}</a>'
    return _e(url)


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
    return f'<div class="flagged"><p>Flagged for review, no Outreach Email:</p><ul>{items}</ul></div>'


def _drafts_note(group_rows: list[sqlite3.Row], dry: bool) -> str:
    if dry:
        return "none (dry run)"
    return f"{sum(1 for r in group_rows if r['draft_id'])} of {len(group_rows)}"


def render_page(store: Store, config: Config, run_id: int, token: str, notice: str = "") -> str:
    run = store.get_run(run_id)
    rows = store.run_emails(run_id)
    shown = emails.totals(store, run_id)
    dry = bool(run["dry_run"])
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no emails"
    out = [f"<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width\">"
           f"<title>Outreach review {_e(run['run_date'])}</title><style>{_STYLE}</style><main>"
           f"<h1>Outreach review, {_e(run['run_date'])}</h1>"
           f'<p class="muted">{len(rows)} Outreach Email(s): {_e(summary)}.</p>']
    if dry:
        out.append('<p class="warn"><strong>Dry run:</strong> no Gmail drafts exist and nothing can be sent.</p>')
    if notice:
        out.append(f'<p class="notice">{_e(notice)}</p>')
    if any(r["status"] not in emails.LOCKED for r in rows):
        out.append(_form("/emails/resume", token,
                         f"Set the resume of the ticked emails {_variant_buttons(config, None)}", "bar", "pick"))

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
        counts_line = (f"Outreach Emails: {len(group_rows)} &middot; Gmail drafts: {_drafts_note(group_rows, dry)}"
                       if group_rows else "no Outreach Emails for this Company Group")
        out.append(
            f'<section class="group" id="group-{group["id"]}"><div class="head"><h2>{_e(group["name"])}</h2>'
            f'<span class="muted">Rank {group["rank"]}</span></div>'
            f'<p class="meta">Roles: {roles}</p>'
            f'<p class="meta">Contacts: {_e(picked)} &middot; {counts_line}</p>'
        )
        if group_rows:
            out.append(_render_group_resume(group_rows, config, token, str(group["name"])))
        out.append(_render_flagged(flagged.get(group["id"], [])))
        out.extend(_render_email(r, config, token, r["status"] in emails.LOCKED) for r in group_rows)
        out.append("</section>")

    confirm = _form("/confirm-send", token, (
        f"<p><strong>{shown.emails} approved email(s) to {shown.recipients} recipient(s).</strong> "
        "Rejected, skipped and unreviewed emails are never sent.</p>"
        '<div class="row"><label>Number of emails <input type="text" name="emails" autocomplete="off"></label>'
        '<label>Number of recipients <input type="text" name="recipients" autocomplete="off"></label>'
        '<button class="primary">Confirm</button></div>'
    ), "confirm")
    waiting = [r for r in rows if r["status"] == "written"]
    blocked = sum(1 for r in waiting if r["block_reason"])
    bulk = _form("/bulk-approve", token, "<button>Bulk approve</button>")
    out.append(f'<section class="group"><h2>Approval</h2><div class="card">{bulk}'
               f'<p class="meta">{len(waiting)} waiting for review, {blocked} with a blocked resume. '
               "Bulk approve approves every email still waiting for review, including ones with findings "
               "(findings do not block Approval) but not ones with a blocked resume; "
               f"those stay until you switch the Resume Variant or fix the file.</p></div>"
               f'<div class="card">{confirm}</div></section></main>')
    return "".join(out)
