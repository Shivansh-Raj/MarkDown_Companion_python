"""Writing voice outputs to Markdown files: new files (never overwriting) or appended to an existing file safely."""
from __future__ import annotations

from pathlib import Path

from ..ai.create import write_new_file
from ..services.synchronization import ToggleStatus, read_snapshot
from .templates import Section, append_from_sections, append_to_content, combined_from_sections, standalone_from_section
from .transcript import suggest_file_name


def write_new(directory: str | Path, meta, doc_title: str, sections: list[Section], name: str, separate: bool) -> list[Path]:
    """One combined document, or one file per section. Existing files are never overwritten (-1, -2 ... suffixes)."""
    if separate:
        return [write_new_file(directory, suggest_file_name(meta.title, meta.created_at_ms, s.suffix),
                               standalone_from_section(meta, s).rstrip("\n") + "\n") for s in sections]
    body = combined_from_sections(meta, doc_title, sections)
    return [write_new_file(directory, name or suggest_file_name(meta.title, meta.created_at_ms, doc_title), body.rstrip("\n") + "\n")]


def append_to_file(sync, path: str | Path, meta, sections: list[Section]) -> None:
    """Add the sections at the end of `path`. The write is hash-checked: if the file changed after we read it, nothing is
    written. Raises RuntimeError with a readable message on any problem; existing content, checkboxes and line endings stay."""
    try:
        snap = read_snapshot(str(path))
    except OSError as e:
        raise RuntimeError(f"Cannot read {Path(path).name}: {e}") from None
    if not snap.valid_utf8:
        raise RuntimeError(f"{Path(path).name} is not valid UTF-8, so nothing was added to it.")
    new = append_to_content(snap.text, append_from_sections(meta, sections))
    result = sync.save_content(str(path), snap.digest, new)
    if result.status == ToggleStatus.OK:
        return
    if result.status == ToggleStatus.CONFLICT:
        raise RuntimeError(f"{Path(path).name} changed while exporting, so nothing was added. Try again.")
    raise RuntimeError(result.message or f"Could not add to {Path(path).name}.")
