"""Shared UTF-8 persistence helpers for local text and JSONL files."""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
from typing import ContextManager, Iterable
import uuid


def atomic_write_text(path: Path | str, content: str) -> None:
    """Flush text to a unique sibling file before atomically replacing the target."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def append_jsonl(
    path: Path | str,
    record: dict,
    lock: ContextManager | None = None,
) -> None:
    """Append one JSON object under an optional caller-provided lock."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + '\n'
    with lock if lock is not None else nullcontext():
        with target.open('a', encoding='utf-8', newline='\n') as file:
            file.write(line)
            file.flush()


def read_jsonl(path: Path | str) -> list[dict]:
    """Read object records while ignoring malformed JSON and invalid UTF-8 lines."""
    target = Path(path)
    if not target.exists():
        return []
    records: list[dict] = []
    with target.open('rb') as file:
        for raw_line in file:
            try:
                record = json.loads(raw_line.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def replace_jsonl(path: Path | str, records: Iterable[dict]) -> None:
    """Atomically replace a JSONL file with the supplied object sequence."""
    content = ''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records)
    atomic_write_text(path, content)
