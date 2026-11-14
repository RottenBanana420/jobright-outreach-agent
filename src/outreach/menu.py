"""The arrow-key menu that `outreach` opens with no command (issue #22).

`build_menu` reads the Store, the config and the files and returns a `Menu`: the status lines, the items that
apply today and the item the pointer starts on. It never prompts, so it is tested directly. `ask_questionary`
is the thin layer that shows a `Menu` as a Questionary select prompt; `menu_loop` shows the menu, runs the
chosen action and shows it again with the new status until the user quits.

User-facing text says "undelivered", never "bounced"; the code keeps `bounce`.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.config import Config
from outreach.gmail_api import signed_in_at
from outreach.resume import resume_path
from outreach.store import Store

TOKEN_DAYS = 7  # a Gmail sign-in lasts 7 days while the Google consent screen is in "Testing"
RENEW_FROM_DAY = 6

START, PRACTICE, REVIEW, SEND, STOP = "start", "practice", "review", "send", "stop"
SETUP, GMAIL, SETTINGS, QUIT = "setup", "gmail", "settings", "quit"


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    argv: tuple[str, ...] = ()  # the `outreach` command the item runs; empty for Setup and Quit


@dataclass(frozen=True)
class Menu:
    title: str  # "Outreach · Fri 13 Nov · today: ..."
    notes: tuple[str, ...]  # further status lines: Gmail sign-in, undelivered emails
    daily: tuple[Item, ...]  # above the separator
    upkeep: tuple[Item, ...]  # below it
    default: str  # the key of the item the pointer starts on

    def keys(self) -> list[str]:
        return [i.key for i in (*self.daily, *self.upkeep)]

    def item(self, key: str) -> Item:
        return next(i for i in (*self.daily, *self.upkeep) if i.key == key)


Ask = Callable[[Menu], "str | None"]


def _emails(n: int) -> str:
    return f"{n} email" if n == 1 else f"{n} emails"


def missing_setup(config: Config, config_file: Path) -> list[str]:
    """What `scripts/setup-wizard.sh` has yet to set up, in the user's words. Empty when setup is complete."""
    missing = []
    if not config_file.exists():
        missing.append(config_file.name)
    if not Path(config.gmail_credentials).exists():
        missing.append(Path(config.gmail_credentials).name)
    if not _signed_in(config):
        missing.append("Gmail sign-in")
    if not all(resume_path(config, v).exists() for v in config.resume_files):
        missing.append("resume files")
    return missing


def _signed_in(config: Config) -> bool:
    return Path(config.gmail_token).exists()


def _gmail_day(config: Config, today: datetime) -> int | None:
    """Which day of the 7-day sign-in today is (1 = the day of the sign-in), or None when there is no countdown."""
    if config.gmail_publishing_status == "production":
        return None  # the sign-in doesn't expire, so there is nothing to count
    at = signed_in_at(Path(config.gmail_token))
    if at is None:
        return None
    return (today.date() - at.astimezone(today.tzinfo).date()).days + 1


def _gmail_note(day: int | None) -> str:
    if day is None:
        return "Gmail: signed in"
    if day > TOKEN_DAYS:
        return "Gmail: the sign-in has run out; renew it"
    if day == TOKEN_DAYS:
        return f"Gmail: day {day} of {TOKEN_DAYS}; renew the sign-in today"
    if day >= RENEW_FROM_DAY:
        return f"Gmail: day {day} of {TOKEN_DAYS}; renew the sign-in soon"
    return f"Gmail: signed in, day {day} of {TOKEN_DAYS}"


def build_menu(
    db_path: Path, config: Config, *, config_file: Path, data_dir: Path, now: datetime,
    is_alive: Callable[[int], bool],
) -> Menu:
    """Today's status, the items that apply and the likely next step, from the Store, config and files."""
    zone = ZoneInfo(config.timezone)
    now = now.astimezone(zone)
    run_dir = data_dir / now.date().isoformat()
    live = interrupted = waiting = sent = undelivered = 0
    dry_run = False
    run_found = has_emails = reviewed = False
    if db_path.exists():
        with closing(Store(db_path)) as store:
            live = sum(1 for r in store.runs_with_processes() if is_alive(r["owner_pid"]))
            midnight = datetime.combine(now.date(), time(), zone).astimezone(UTC)
            undelivered = store.bounces_recorded_since(midnight.isoformat(timespec="seconds"))
            run = store.find_run(run_dir)
            if run is not None:
                run_found = True
                dry_run = bool(run["dry_run"])
                emails = store.run_emails(run["id"])
                interrupted = len(store.unfinished_emails(run["id"]))
                # A practice run's confirmed emails stay `approved` (nothing is marked to send), so once its review
                # page has closed during the Run, nothing is waiting any more.
                reviewed = store.run_reached(run["id"], "reviewed")
                closed = dry_run and reviewed
                waiting = 0 if closed else sum(1 for e in emails if e["status"] in ("written", "approved"))
                sent = sum(1 for e in emails if e["status"] == "sent")
                has_emails = bool(emails)

    if live:
        status = "a Run is in progress in another window"
    elif not run_found:
        status = "no Run yet"
    elif interrupted:
        status = f"send interrupted: {_emails(interrupted)} to finish"
    elif waiting:
        status = f"waiting for your review: {_emails(waiting)}" + (" (practice run)" if dry_run else "")
    elif not has_emails and reviewed:
        status = "done, no applications to follow up"  # `shortlist` found nothing, so the Run ended
    elif not has_emails:
        status = "started, not finished; Start picks up where it left off"
    elif sent:
        status = f"all sent: {_emails(sent)}"
    else:
        status = "done, nothing sent" + (" (practice run)" if dry_run else "")

    missing = missing_setup(config, config_file)
    notes: list[str] = []
    gmail_day: int | None = None
    if _signed_in(config):
        gmail_day = _gmail_day(config, now)
        notes.append(_gmail_note(gmail_day))
    if undelivered:
        those = "that address" if undelivered == 1 else "those addresses"
        notes.append(f"{_emails(undelivered)} undelivered; {those} won't be used again")

    daily = [
        Item(START, "Start today's Run", ("start",)),
        Item(PRACTICE, "Practice run (nothing is sent)", ("start", "--dry-run")),
    ]
    if waiting and not live and not interrupted:
        daily.append(Item(REVIEW, "Reopen the review page", ("review", str(run_dir))))
    if interrupted and not live:
        daily.append(Item(SEND, "Finish an interrupted send", ("send", str(run_dir))))
    if live:
        daily.append(Item(STOP, "Stop the Run", ("stop",)))
    upkeep = []
    if missing:
        upkeep.append(Item(SETUP, f"Finish setup (missing: {', '.join(missing)})"))
    upkeep += [
        Item(GMAIL, "Renew Gmail sign-in", ("gmail-auth",)),
        Item(SETTINGS, "Show settings", ("config",)),
        Item(QUIT, "Quit"),
    ]

    if missing:
        default = SETUP
    elif live:
        default = QUIT  # Start would be refused, and one Enter must not stop a Run in another window
    elif gmail_day is not None and gmail_day >= RENEW_FROM_DAY:
        default = GMAIL
    elif interrupted:
        default = SEND
    elif waiting:
        default = REVIEW
    elif not run_found or (not has_emails and not reviewed):
        default = START
    else:
        default = QUIT  # today's Run is done
    title = f"Outreach · {now:%a} {now.day} {now:%b} · today: {status}"
    return Menu(title, tuple(notes), tuple(daily), tuple(upkeep), default)


def menu_loop(build: Callable[[], Menu], ask: Ask, act: Callable[[Item], object]) -> int:
    """Show the menu, run the chosen action, then show it again with the new status, until Quit."""
    while True:
        menu = build()
        choice = ask(menu)
        if choice is None or choice == QUIT:
            return 0
        try:
            act(menu.item(choice))
        except KeyboardInterrupt:
            print()  # Ctrl-C ends the action, not the menu
        print()


def ask_questionary(menu: Menu) -> str | None:
    """Show the menu as a Questionary select prompt. Returns the chosen key, or None for Ctrl-C."""
    import questionary
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

    numbered = [*menu.daily, *menu.upkeep]
    choices: list[questionary.Choice | questionary.Separator] = []
    for i, item in enumerate(numbered, 1):
        if i == len(menu.daily) + 1:
            choices.append(questionary.Separator("  ─────"))
        choices.append(questionary.Choice(item.label, value=item.key, shortcut_key=str(i) if i < 10 else None))
    # The notes go after the instruction, so the key hint stays on the title line.
    instruction = "\n".join(["(↑↓ or number, Enter; q quits)", *(f"  {n}" for n in menu.notes)])
    question = questionary.select(
        menu.title, choices=choices, default=next(c for c in choices if getattr(c, "value", None) == menu.default),
        use_shortcuts=True, use_jk_keys=False, instruction=instruction,
    )
    quit_key = KeyBindings()

    @quit_key.add("q", eager=True)
    def _(event: object) -> None:
        event.app.exit(result=QUIT)  # type: ignore[attr-defined]

    app = question.application
    app.key_bindings = merge_key_bindings([app.key_bindings or KeyBindings(), quit_key])
    answer = question.ask()
    return None if answer is None else str(answer)
