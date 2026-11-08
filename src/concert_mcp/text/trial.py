"""The pieces of a trial run (unit text-trial): what OpenClaw would send the model, and the report Ari reads.

Pure. `concert-mcp try-image` (flows/try_image.py) runs a screenshot through the real OCR, the real model and the
real tool in dry-run, so Ari can approve or deny the event before the bot ever sees the picture.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


def openclaw_user_message(caption: str, ocr: str) -> str:
    """The user turn OpenClaw builds from a photo: the image marker, the caption, then the OCR as its description."""
    return f"[Image]\nUser text:\n{caption}\nDescription:\n{ocr}"


def openai_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """MCP tool listings ({name, description, inputSchema}) as OpenAI chat-completions `tools`."""
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]},
        }
        for t in tools
    ]


def primary_model(openclaw: Mapping[str, Any]) -> tuple[str, str] | None:
    """(base URL, model id) of OpenClaw's primary model, e.g. "llama-server/qwen3.5-9b"; None when unset."""
    primary = openclaw.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
    provider, _, model = str(primary).partition("/")
    base = openclaw.get("models", {}).get("providers", {}).get(provider, {}).get("baseUrl")
    return (str(base).rstrip("/"), model) if base and model else None


def media_command(openclaw: Mapping[str, Any]) -> str | None:
    """OpenClaw's image-to-text command (tools.media.models[0].command); None when unset."""
    models = openclaw.get("tools", {}).get("media", {}).get("models", [])
    return str(models[0]["command"]) if models and models[0].get("command") else None


@dataclass(frozen=True)
class Trial:
    ocr: str
    tool: str | None
    args: dict[str, str] = field(default_factory=dict)
    model_text: str = ""  # what the model said when it called no tool
    result: str = ""  # the tool's dry-run result, what the bot would have replied
    elapsed_s: float = 0.0


def render_trial(t: Trial) -> str:
    """The report: the OCR, the model's call, the would-be event, then the question."""
    parts = ["== OCR (what the model read)", t.ocr.strip(), ""]
    if t.tool is None:
        parts += ["== Model called no tool. It said:", t.model_text.strip() or "(nothing)", ""]
    else:
        parts += [
            f"== Model called {t.tool} ({t.elapsed_s:.1f}s)",
            json.dumps(t.args, indent=2, ensure_ascii=False),
            "",
        ]
        parts += ["== Would add (dry run: nothing written, nothing sent)", t.result.strip(), ""]
    parts.append("Approve or deny this event.")
    return "\n".join(parts)
