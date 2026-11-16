"""The invite to-do: a Google Task that reminds the owner to invite people to a show (unit flows-records).

One more expression of a concert, by rule and with no model: once the show has a calendar event and a Partiful
page, and starts more than thirty days from now, one task `invite people to <artist>` is made, due thirty days
before the show, and kept in the concert's record so it is made once (`todo_plan` in `text/records.py` decides).
Whichever step completes the pair runs it: `concert_add` when the ticket link came with the ticket, `tickets_add`
(and `/tickets`) when it arrives later. A failed call is one line in the reply and no task id in the record, so the
next run that finds the pair complete tries again. Deleting the show's event deletes its to-do unless it is done.
Nothing here raises.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from concert_mcp.flows.records import record_for_event, remember_todo
from concert_mcp.flows.services import Services
from concert_mcp.log import kv, log_info, log_warn
from concert_mcp.result import ProviderError
from concert_mcp.text.records import ConcertRecord, todo_plan
from concert_mcp.text.reply import TODO_WHY, todo_failed_note, todo_note

OPEN_STATUS = "needsAction"  # the Tasks API's status of a task not yet completed


def _record(event_id: str, svc: Services) -> ConcertRecord | None:
    """The show's record, or None (no record, or one that cannot be read). Never raises."""
    try:
        known = record_for_event(event_id, svc.state, svc.settings.concerts_dir)
    except Exception as e:  # noqa: BLE001 - an unreadable cache means no to-do this time, never a failed add
        log_warn(f"concert record not read | error='{type(e).__name__}: {str(e)[:200]}'")
        return None
    return known[1] if known else None


def todo_step(event_id: str, svc: Services) -> str | None:
    """Make the show's invite to-do when its record says one is due; the reply's line on it.

    None when no to-do is due (not a recorded concert, no Partiful page, 30 days away or less, made before) or when
    no Tasks API is wired; else `To-do: ...` or `To-do not made: <why>.`. Never raises: the event has landed.
    """
    api = svc.tasks
    record = _record(event_id, svc) if api is not None else None
    plan = todo_plan(record, now=svc.clock.now()) if record is not None else None
    if api is None or plan is None:
        return None
    task_list = svc.settings.task_list
    try:
        task = api.insert_task(task_list, title=plan.title, notes=plan.notes, due=plan.due)
    except ProviderError as e:
        log_warn("invite to-do not made | " + kv(event=event_id, error=str(e.error)[:200]))
        return todo_failed_note(TODO_WHY.get(e.provider, "Google Tasks failed"))
    except Exception as e:  # noqa: BLE001 - the add or the invite matters more than the reminder
        log_warn("invite to-do not made | " + kv(event=event_id, error=f"{type(e).__name__}: {str(e)[:200]}"))
        return todo_failed_note(type(e).__name__)
    link = str(task.get("webViewLink") or "") or None
    remember_todo(event_id, {"id": str(task["id"]), "link": link, "task_list": task_list}, svc)
    log_info("invite to-do made | " + kv(event=event_id, due=plan.due))
    return todo_note(plan.title, link)


def todo_of(event_id: str, svc: Services) -> Mapping[str, Any] | None:
    """The to-do the show's record holds, read before its event is deleted (the delete forgets the record)."""
    record = _record(event_id, svc)
    return record.todo if record is not None else None


def drop_todo(todo: Mapping[str, Any] | None, svc: Services) -> None:
    """Delete a deleted show's to-do unless it is done: a completed task is the owner's record of having invited
    people, and stays. Never raises: the event is gone whatever Google Tasks says."""
    api = svc.tasks
    if not todo or api is None:
        return
    task_list = str(todo.get("task_list") or svc.settings.task_list)
    task_id = str(todo.get("id") or "")
    try:
        if api.get_task(task_list, task_id).get("status") != OPEN_STATUS:
            return
        api.delete_task(task_list, task_id)
    except Exception as e:  # noqa: BLE001
        log_warn("invite to-do not deleted | " + kv(task=task_id, error=f"{type(e).__name__}: {str(e)[:200]}"))
        return
    log_info("invite to-do deleted | " + kv(task=task_id))
