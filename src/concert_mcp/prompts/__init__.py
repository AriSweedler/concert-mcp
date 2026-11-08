"""Every prompt the package sends a model, one plaintext file each, loaded by name."""

from __future__ import annotations

from importlib.resources import files


def load_prompt(name: str) -> str:
    """The text of prompts/<name>.txt (a "/" in name is a subfolder), without its final newline."""
    return files(__package__).joinpath(f"{name}.txt").read_text(encoding="utf-8").removesuffix("\n")
