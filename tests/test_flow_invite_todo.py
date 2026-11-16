"""The invite to-do (bead beads-hmxw): the rule in text/records.py against literals, flows/invite_todo.py on the
fakes, and where it surfaces (concert_add, tickets_add and the `tickets` command, event_delete, the healthcheck)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from concert_mcp.config import Settings
from concert_mcp.flows import healthcheck as health
from concert_mcp.flows.concert_add import add_concert, add_plain
from concert_mcp.flows.event_delete import DeleteRequest, delete_event
from concert_mcp.flows.healthcheck import run_healthcheck
from concert_mcp.flows.invite_todo import drop_todo, todo_of, todo_step
from concert_mcp.flows.partiful import tickets_outcome
from concert_mcp.flows.records import CachedCalendar, record_for_event
from concert_mcp.flows.services import Services
from concert_mcp.result import Kind
from concert_mcp.text.health import Check
from concert_mcp.text.records import (
    ConcertRecord,
    TodoPlan,
    expressions,
    record_of_event,
    todo_due,
    todo_notes,
    todo_plan,
    todo_state,
    todo_title,
)
from conftest import TICKET_TEXT, make_services, run
from fakes import FakeCalendar, FakeContext, FakePartiful, FakeTasks, MemoryState, fake_html_link

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=LA)
PAGE = "https://partiful.com/e/pf1"
CAL = "https://www.google.com/calendar/event?eid=abc"
TICKETS = "https://www.ticketweb.com/event/zed-fox/123"
TASK_LINK = "https://tasks.google.com/task/task1"
CREATED_BY = "Created by github.com/AriSweedler/concert-mcp"
# A show 82 days after the fixtures' clock (2026-10-25): far enough out for a to-do.
FAR_TEXT = TICKET_TEXT.replace("Sat Nov 14 2026", "Fri Jan 15 2027")
FAR_START = "2027-01-15 20:30"
TODO_LINE = f"To-do: [invite people to Zed Fox]({TASK_LINK})"


def record(**over: Any) -> ConcertRecord:
    base: dict[str, Any] = dict(
        artist="Zed Fox",
        start="2027-01-15T20:30:00-08:00",
        location="The Quarry",
        lines=("Doors: 7:30 PM", f"Tickets: {TICKETS}", f"Partiful: {PAGE}"),
        calendar_id="cal-concerts",
        event_id="evt1",
        html_link=CAL + "&ctz=America/Los_Angeles",
    )
    base.update(over)
    return ConcertRecord(**base)


# ---------- text/records.py ----------


def test_the_rule_and_the_task_it_makes() -> None:
    assert todo_title("Zed Fox") == "invite people to Zed Fox"
    assert todo_notes(PAGE, CAL) == f"Partiful: {PAGE}\nCalendar: {CAL}\n\n{CREATED_BY}"
    assert todo_due("2027-01-15") == "2026-12-16T00:00:00.000Z"
    assert todo_due("2027-03-01") == "2027-01-30T00:00:00.000Z"
    # The calendar line is the one link form the package emits: the htmlLink cut to its eid.
    assert todo_plan(record(), now=NOW) == TodoPlan(
        title="invite people to Zed Fox",
        notes=f"Partiful: {PAGE}\nCalendar: {CAL}\n\n{CREATED_BY}",
        due="2026-12-16T00:00:00.000Z",
    )
    assert expressions(record(), now=NOW)["invite to-do"] == "can be made"


def test_no_task_without_the_page_the_link_or_the_time_and_none_twice() -> None:
    def state(r: ConcertRecord, now: datetime = NOW) -> str:
        assert (todo_plan(r, now=now) is None) is (todo_state(r, now=now) != "can be made")
        return todo_state(r, now=now)

    assert state(record(lines=("Doors: 7:30 PM", f"Tickets: {TICKETS}"))) == "needs the Partiful page"
    assert state(record(html_link=None)) == "needs the calendar link"
    assert state(record(todo={"id": "task1"})) == "made"
    # More than 30 days to the start: a to-do. Exactly 30 days, or less, or a show already played: none.
    start = datetime.fromisoformat(record().start)
    assert state(record(), start - timedelta(days=30, seconds=1)) == "can be made"
    assert state(record(), start - timedelta(days=30)) == "not needed (30 days or less to go)"
    assert state(record(), start - timedelta(days=2)) == "not needed (30 days or less to go)"
    assert state(record(), start + timedelta(days=2)) == "not needed (30 days or less to go)"
    # An all-day event has a day and no time: its day starts at midnight UTC for the rule.
    assert state(record(start="2026-11-25"), datetime(2026, 10, 25, 23, 59, tzinfo=ZoneInfo("UTC"))) == "can be made"
    assert state(record(start="2026-11-25"), datetime(2026, 10, 26, tzinfo=ZoneInfo("UTC"))).startswith("not needed")


def test_the_record_keeps_its_to_do() -> None:
    todo = {"id": "task1", "link": TASK_LINK, "task_list": "@default"}
    full = record(todo=todo)
    assert full.to_json()["todo"] == todo and ConcertRecord.from_json(full.to_json()) == full
    assert ConcertRecord.from_json({**full.to_json(), "todo": "nonsense"}).todo is None
    # A refresh from the calendar, which knows nothing of the task, keeps it.
    event = {"id": "evt1", "summary": "Zed Fox", "start": {"dateTime": full.start}, "description": "Doors: 7:30 PM"}
    assert record_of_event(event, "cal-concerts", now="later", known=full).todo == todo


# ---------- the flows ----------


def wired(svc: Services, tasks: FakeTasks | None) -> Services:
    """The services as build_services wires them: CachedCalendar over the calendar, a Partiful, Google Tasks."""
    svc.calendar = CachedCalendar(svc.calendar, svc.state, svc.settings.concerts_dir, svc.clock)
    svc.partiful = FakePartiful()
    svc.tasks = tasks
    return svc


def far_services(settings: Settings, tasks: FakeTasks | None, *, text: str = FAR_TEXT) -> Services:
    return wired(make_services(settings, NOW, route=None, text=text), tasks)


def concert(svc: Services, text: str, start: str = FAR_START) -> Any:
    return add_concert(
        artist="Zed Fox with special guest Zork.",
        start=start,
        venue="The Quarry, San Francisco",
        source_text=text,
        svc=svc,
    )


def saved(svc: Services, event_id: str) -> ConcertRecord:
    return record_for_event(event_id, svc.state, svc.settings.concerts_dir)[1]


def test_concert_add_with_the_ticket_link_makes_one_to_do(settings: Settings) -> None:
    tasks = FakeTasks()
    text = f"{FAR_TEXT}tickets: {TICKETS}\n"
    svc = far_services(settings, tasks, text=text)
    out = concert(svc, text)
    assert out.kind is Kind.OK and f"\n{TODO_LINE}\n[Open in Google Calendar](" in out.reply
    assert out.reply.count("To-do") == 1
    [(method, args)] = tasks.calls
    assert method == "insert_task" and args == {
        "task_list": "@default",
        "title": "invite people to Zed Fox",
        "notes": f"Partiful: {PAGE}\nCalendar: {fake_html_link('evt1', 'cal-concerts')}\n\n{CREATED_BY}",
        "due": "2026-12-16T00:00:00.000Z",
    }
    assert saved(svc, "evt1").todo == {"id": "task1", "link": TASK_LINK, "task_list": "@default"}
    # The same show again (a retry, the same ticket): no second task, and no To-do line.
    again = concert(svc, text)
    assert again.kind is Kind.DUPLICATE and "To-do" not in again.reply and len(tasks.calls) == 1
    assert tickets_outcome(TICKETS, "Zed Fox", "2027-01-15", svc).reply == (
        f"This show already has a Partiful: [Partiful]({PAGE})"
    )
    assert len(tasks.calls) == 1


def test_a_ticket_link_that_arrives_later_makes_the_to_do_once(settings: Settings) -> None:
    tasks = FakeTasks(link=False)
    svc = far_services(settings, tasks)
    out = concert(svc, FAR_TEXT)
    assert out.kind is Kind.OK and "To-do" not in out.reply and tasks.calls == []  # no Partiful page yet
    dry = tickets_outcome(TICKETS, "Zed Fox", "2027-01-15", svc, dry_run=True)
    assert dry.kind is Kind.DRY_RUN and "To-do" not in dry.reply and tasks.calls == []
    made = tickets_outcome(TICKETS, "Zed Fox", "2027-01-15", svc)
    assert made.kind is Kind.OK and made.reply.startswith("**Partiful made:** [Concert: Zed Fox at The Quarry 1/15]")
    # Google Tasks gave no link: the title alone.
    assert made.reply.endswith("\nTo-do: invite people to Zed Fox") and made.reply.count("To-do") == 1
    assert saved(svc, "evt1").todo == {"id": "task1", "link": None, "task_list": "@default"}
    assert tickets_outcome("", "Zed Fox", "2027-01-15", svc).reply.count("To-do") == 0 and len(tasks.calls) == 1


def test_the_tickets_command_makes_the_to_do(cli_env: Services, monkeypatch, capsys) -> None:
    tasks = FakeTasks()
    wired(cli_env, tasks)
    cli_env.context = FakeContext(text=FAR_TEXT)
    concert(cli_env, FAR_TEXT)
    code, out, _err = run(
        monkeypatch, capsys, ["tickets", "--link", TICKETS, "--artist", "Zed Fox", "--date", "2027-01-15", "--no-send"]
    )
    assert code == 0 and f"\n{TODO_LINE}\n" in out and len(tasks.calls) == 1


def test_no_to_do_for_a_near_show_a_plain_event_or_without_google_tasks(settings: Settings) -> None:
    tasks = FakeTasks()
    near = f"{TICKET_TEXT}tickets: {TICKETS}\n"  # 2026-11-14: 20 days after the clock
    svc = far_services(settings, tasks, text=near)
    out = concert(svc, near, start="2026-11-14 20:30")
    assert out.kind is Kind.OK and out.data["partiful"]["status"] == "created" and "To-do" not in out.reply
    plain = add_plain(title="[Dinner] Team dinner", start="2027-01-15 19:00", location="Somewhere", svc=svc)
    assert plain.kind is Kind.OK and "To-do" not in plain.reply
    assert tasks.calls == []

    far = f"{FAR_TEXT}tickets: {TICKETS}\n"
    bare = far_services(settings, None, text=far)
    assert concert(bare, far).kind is Kind.OK and todo_step("evt1", bare) is None
    # No record for the event, or one that cannot be read: no to-do, and nothing raised.
    svc = far_services(settings, tasks, text=far)
    assert todo_step("evt-unknown", svc) is None

    class BrokenState(MemoryState):
        def names(self, directory: Any) -> list[str]:
            raise OSError("disk gone")

    svc.state = BrokenState()
    assert todo_step("evt1", svc) is None and todo_of("evt1", svc) is None and tasks.calls == []


@pytest.mark.parametrize(
    ("tasks", "line"),
    [
        (FakeTasks(fail="insert_task"), "To-do not made: Google Tasks refused the request."),
        (FakeTasks(fail="insert_task", provider="gws-keychain"), "To-do not made: the login keychain is locked."),
        (FakeTasks(fail="insert_task", provider="mystery"), "To-do not made: Google Tasks failed."),
        (FakeTasks(crash="insert_task"), "To-do not made: RuntimeError."),
    ],
)
def test_a_failed_to_do_is_one_line_and_the_next_run_tries_again(
    settings: Settings, tasks: FakeTasks, line: str
) -> None:
    text = f"{FAR_TEXT}tickets: {TICKETS}\n"
    svc = far_services(settings, tasks, text=text)
    out = concert(svc, text)
    assert out.kind is Kind.OK and out.data["partiful"]["status"] == "created"
    assert f"\n{line}\n[Open in Google Calendar](" in out.reply and out.reply.count("To-do") == 1
    assert saved(svc, "evt1").todo is None
    # Google Tasks answers again: the next step that finds the pair complete makes it.
    tasks.fail = tasks.crash = None
    again = tickets_outcome("", "Zed Fox", "2027-01-15", svc)
    assert again.reply == f"This show already has a Partiful: [Partiful]({PAGE})\n{TODO_LINE}"
    assert saved(svc, "evt1").todo == {"id": "task1", "link": TASK_LINK, "task_list": "@default"}


def test_deleting_the_show_deletes_its_open_to_do_only(settings: Settings) -> None:
    text = f"{FAR_TEXT}tickets: {TICKETS}\n"

    def show_with_todo(tasks: FakeTasks) -> Services:
        svc = far_services(settings, tasks, text=text)
        assert TODO_LINE in concert(svc, text).reply
        return svc

    tasks = FakeTasks()
    svc = show_with_todo(tasks)
    gone = delete_event(DeleteRequest(title="Zed Fox", date="2027-01-15"), svc)
    assert gone.kind is Kind.OK and gone.reply.startswith("**Removed from Concerts**")
    assert tasks.deleted == [("@default", "task1")] and tasks.tasks["@default"] == []

    # A task already done is the owner's record of having invited people: it stays.
    tasks = FakeTasks()
    svc = show_with_todo(tasks)
    tasks.tasks["@default"][0]["status"] = "completed"
    assert delete_event(DeleteRequest(title="Zed Fox", date="2027-01-15"), svc).kind is Kind.OK
    assert tasks.deleted == []

    # Google Tasks failing does not fail the delete; a dry run touches neither.
    tasks = FakeTasks()
    svc = show_with_todo(tasks)
    dry = delete_event(DeleteRequest(title="Zed Fox", date="2027-01-15", dry_run=True), svc)
    assert dry.kind is Kind.DRY_RUN and [m for m, _a in tasks.calls] == ["insert_task"]
    tasks.fail = "get_task"
    assert delete_event(DeleteRequest(title="Zed Fox", date="2027-01-15"), svc).reply.startswith("**Removed from")
    assert (
        tasks.deleted == []
        and svc.calendar.list_events("cal-concerts", time_min=NOW, time_max=NOW + timedelta(days=365)) == []
    )

    # A plain event has no record and no to-do; nothing to drop without one or without Google Tasks.
    tasks = FakeTasks()
    svc = far_services(settings, tasks)
    add_plain(title="[Dinner] Team dinner", start="2027-01-15 19:00", location="Somewhere", svc=svc)
    assert delete_event(DeleteRequest(title="Team dinner", date="2027-01-15"), svc).kind is Kind.OK
    assert tasks.calls == []
    svc.tasks = None
    drop_todo({"id": "task1", "task_list": "@default"}, svc)


# ---------- the healthcheck ----------


def test_the_tasks_health_row(settings: Settings) -> None:
    svc = make_services(settings, NOW, route=None, text="")

    def row(**kw: Any) -> Check:
        only = kw.pop("only", ["tasks-auth"])
        return {c.name: c for c in run_healthcheck(settings, svc=svc, only=only, **kw).checks}["tasks-auth"]

    assert row() == Check("tasks-auth", "SKIP", "pass --probe")
    assert row(probe=True) == Check("tasks-auth", "SKIP", "no Google Tasks adapter")
    svc.tasks = FakeTasks()
    assert row(probe=True) == Check("tasks-auth", "PASS", "found My Tasks")
    assert svc.tasks.calls == [("get_task_list", {"task_list": "@default"})]  # a read; nothing is made
    svc.tasks = FakeTasks(fail="get_task_list")
    failed = row(probe=True)
    assert (failed.status, failed.evidence, failed.anchor, failed.fix) == (
        "FAIL",
        "gws tasks exited 1: fake get_task_list failure",
        "google-calendar",
        "gws auth login -s calendar,tasks",
    )
    svc.tasks = FakeTasks(fail="get_task_list", provider="gws-keychain")
    assert row(probe=True).fix == health.KEYFILE_FIX
    # One gws fault is one FAIL: with gws-auth failing, the Tasks row is a SKIP.
    svc.calendar = FakeCalendar(calendars=[], fail="list_calendars")
    assert row(probe=True, only=["gws-auth", "tasks-auth"]) == Check("tasks-auth", "SKIP", "gws-auth failed")


def test_a_task_list_with_no_title_is_named_by_the_setting(settings: Settings) -> None:
    class Untitled(FakeTasks):
        def get_task_list(self, task_list: str) -> dict[str, Any]:
            return {}

    svc = make_services(settings, NOW, route=None, text="")
    svc.tasks = Untitled()
    svc.settings = replace(settings, task_list="list-abc")
    report = run_healthcheck(svc.settings, probe=True, svc=svc, only=["tasks-auth"])
    assert report.checks == (Check("tasks-auth", "PASS", "found list-abc"),)
