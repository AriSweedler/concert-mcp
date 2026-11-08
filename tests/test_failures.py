"""text/failures.py and flows/failures.py: a failed screenshot turn in OpenClaw's transcript is saved once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from concert_mcp.config import Settings
from concert_mcp.flows.failures import (
    GRACE_MS,
    MAX_KEPT,
    NOT_EXPLAINED,
    ORIN_FILE,
    collect,
    explain,
    rotate,
    saved_failures,
)
from concert_mcp.result import ProviderError
from concert_mcp.text.failures import find_failures

T0 = 1_794_080_325_000  # a Saturday, 11:38 PST
CAPTION = "This is not a concert, but a movie."
OCR = "Godzilla Minus Zero (2026)\nR, 2 hr 15 min"


def shot(eid: str, image: str, caption: str = CAPTION) -> dict[str, Any]:
    """A screenshot turn as OpenClaw writes it."""
    return {
        "type": "message",
        "id": eid,
        "message": {
            "role": "user",
            "content": f"[Image]\nUser text:\n{caption}\nDescription:\n{OCR}",
            "__openclaw": {
                "transport": {"channel": "telegram", "conversationRef": "conv_x", "threadId": "56", "messageId": "57"},
                "media": [{"path": image, "kind": "image", "contentType": "image/jpeg"}],
            },
        },
    }


def requeued() -> dict[str, Any]:
    return {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "[Earlier ...]"}]}}


def assistant(stop: str, error: str = "") -> dict[str, Any]:
    return {
        "type": "message",
        "message": {"role": "assistant", "content": [], "stopReason": stop, "errorMessage": error},
    }


def test_a_connection_error_is_a_failure() -> None:
    rows = [
        ("s1", T0, shot("e1", "/i.jpg")),
        ("s1", T0 + 2000, requeued()),
        ("s1", T0 + 63_000, assistant("error", "Connection error.")),
    ]
    (f,) = find_failures(rows, now_ms=T0 + 70_000, grace_ms=GRACE_MS)
    assert (f.event_id, f.caption, f.ocr, f.image, f.error) == ("e1", CAPTION, OCR, "/i.jpg", "Connection error.")
    assert f.chat == "telegram:conv_x:56:57"


def test_an_answered_turn_is_not_a_failure() -> None:
    rows = [
        ("s1", T0, shot("e1", "/i.jpg")),
        ("s1", T0 + 90_000, assistant("toolUse")),
        ("s1", T0 + 99_000, assistant("stop")),
    ]
    assert find_failures(rows, now_ms=T0 + 10**8, grace_ms=GRACE_MS) == []


def test_silence_is_a_failure_only_after_the_grace() -> None:
    rows = [("s1", T0, shot("e1", "/i.jpg"))]
    assert find_failures(rows, now_ms=T0 + GRACE_MS - 1, grace_ms=GRACE_MS) == []
    (f,) = find_failures(rows, now_ms=T0 + GRACE_MS + 1, grace_ms=GRACE_MS)
    assert f.error == "no reply"


def test_collect_saves_each_failure_once_under_its_epoch(settings: Settings, tmp_path: Path) -> None:
    image = tmp_path / "staged.jpg"
    image.write_bytes(b"\xff\xd8jpeg")
    rows = [("s1", T0, shot("e1abcdef99", str(image))), ("s1", T0 + 63_000, assistant("error", "Connection error."))]
    (folder,) = collect(settings, lambda since: rows, T0 + 70_000)
    assert folder.name == str(T0 // 1000)
    assert (folder / "image.jpg").read_bytes() == b"\xff\xd8jpeg"
    assert (folder / "prompt.txt").read_text() == CAPTION
    assert (folder / "text.txt").read_text() == OCR
    assert (folder / "request.txt").read_text().startswith("[Image]\nUser text:\n")
    assert json.loads((folder / "meta.json").read_text())["error"] == "Connection error."
    assert "concert-mcp try-image" in (folder / "replay.sh").read_text()
    assert (folder / ORIN_FILE).read_text().startswith(NOT_EXPLAINED)
    assert collect(settings, lambda since: rows, T0 + 80_000) == []
    assert saved_failures(settings) == [folder]


def test_rotate_keeps_the_newest(settings: Settings) -> None:
    root = settings.state_dir / "failures"
    for i in range(MAX_KEPT + 3):
        (root / str(1_700_000_000 + i)).mkdir(parents=True)
    gone = rotate(settings)
    assert [p.name for p in gone] == [str(1_700_000_000 + i) for i in range(3)]
    assert len(saved_failures(settings)) == MAX_KEPT


def test_explain_records_the_answer_or_why_not(settings: Settings, tmp_path: Path) -> None:
    rows = [("s1", T0, shot("e1", "/missing.jpg")), ("s1", T0 + 63_000, assistant("error", "Connection error."))]
    (folder,) = collect(settings, lambda since: rows, T0 + 70_000)

    def down(system: str, user: str) -> str:
        raise ProviderError("model", "EHOSTUNREACH", "unreachable")

    assert not explain(folder, down)
    assert "unreachable (EHOSTUNREACH)" in (folder / ORIN_FILE).read_text()
    asked: list[str] = []
    assert explain(folder, lambda system, user: asked.append(user) or "It was a movie; call movie_add.")
    assert "Connection error." in asked[0] and CAPTION in asked[0]
    assert (folder / ORIN_FILE).read_text() == "It was a movie; call movie_add.\n"
    assert explain(folder, down)  # an answered folder is never asked again
