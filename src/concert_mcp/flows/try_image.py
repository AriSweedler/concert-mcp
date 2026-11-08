"""`concert-mcp try-image` (unit flows-trial): a screenshot through the real OCR, model and tool, in dry-run.

The same path the bot takes, minus Telegram: OpenClaw's OCR command reads the image, OpenClaw's model gets the user
turn OpenClaw would build plus the server's instructions and the chat tools, and the tool it picks runs with
dry_run on and sending off. Ari reads the report and approves or denies the event. The model sees the tools as
plain function definitions, not OpenClaw's compact tool-search surface, so a trial is close to the bot, not equal.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from concert_mcp.adapters.chat_model_http import ChatModelClient
from concert_mcp.result import ProviderError
from concert_mcp.text.trial import Trial, openai_tools, openclaw_user_message

OCR_TIMEOUT_S = 60
OCR_MAX_CHARS = "4000"  # OpenClaw's tools.media.image.maxChars


def ocr_image(command: str, image: Path) -> str:
    """Run an image-to-text command as OpenClaw does (`<command> <path> <maxChars>`); its stdout is the text."""
    try:
        done = subprocess.run(
            [command, str(image), OCR_MAX_CHARS], capture_output=True, text=True, timeout=OCR_TIMEOUT_S, check=True
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise ProviderError("ocr", f"{command}: {type(e).__name__}: {e}", f"OCR failed: {command}") from e
    return done.stdout


def run_trial(
    *,
    ocr: str,
    caption: str,
    system: str,
    tools: Sequence[dict[str, Any]],
    model: ChatModelClient,
    call_tool: Callable[[str, dict[str, str]], str],
) -> Trial:
    """Ask the model, run the tool it picked, and return what happened."""
    t0 = time.monotonic()
    name, args, text = model.first_tool_call(
        system=system, user=openclaw_user_message(caption, ocr), tools=openai_tools(tools)
    )
    elapsed = time.monotonic() - t0
    if name is None:
        return Trial(ocr=ocr, tool=None, model_text=text, elapsed_s=elapsed)
    clean = {k: "" if v is None else str(v) for k, v in args.items()}
    return Trial(ocr=ocr, tool=name, args=clean, result=call_tool(name, clean), elapsed_s=elapsed)
