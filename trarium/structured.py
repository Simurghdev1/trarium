"""ترجمهٔ فایل‌های ساختاریافته بدون شکستن ساختار.

برای JSON فقط رشته‌های مقدار ترجمه می‌شوند (کلیدها دست‌نخورده می‌مانند)، در
CSV/TSV فقط سلول‌های متنی، در نوت‌بوک فقط سلول‌های markdown و در فایل‌های کد
فقط کامنت‌ها. خروجی همیشه با همان فرمت و قابل استفاده می‌ماند.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

JSON_EXTENSIONS = {".json", ".json5", ".jsonc", ".jsonld", ".geojson", ".webmanifest"}
JSONL_EXTENSIONS = {".jsonl", ".ndjson"}
CSV_EXTENSIONS = {".csv", ".tsv", ".psv", ".ssv"}
CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".java", ".kt", ".kts", ".scala", ".groovy", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".cxx", ".cs", ".go", ".rs", ".php", ".rb", ".swift", ".dart", ".sql", ".css",
    ".scss", ".sass", ".less", ".styl", ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".bat", ".cmd", ".pl", ".pm", ".lua", ".ex", ".exs", ".erl", ".hs", ".ml",
    ".fs", ".clj", ".cljs", ".r", ".jl", ".asm", ".nix", ".gradle", ".cmake",
}

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_COMMENT = re.compile(
    r"^(?P<indent>\s*)(?P<marker>#|//|/\*|\*|<!--|--|;)(?P<space>\s*)(?P<body>[^\n]*?)(?P<tail>\s*(?:\*/|-->)?)\s*$"
)


def batching(indexes: list[int], texts: list[str], max_chars: int = 3000) -> list[list[int]]:
    """ایندکس‌ها را به دسته‌های کوچک برای درخواست‌های API تقسیم می‌کند."""
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index in indexes:
        length = len(texts[index]) + 2
        if current and size + length > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        batches.append(current)
    return batches


def translate_segments(pipeline, segments: list[str]) -> list[str]:
    """قطعه‌ها را دسته‌ای ترجمه می‌کند و اگر مرزها حفظ نشد، تک‌تک ترجمه می‌کند."""
    results = list(segments)
    targets = [i for i, text in enumerate(segments) if text.strip() and _LETTER.search(text)]
    for batch in batching(targets, segments):
        joined = "\n\n".join(segments[i].strip() for i in batch)
        try:
            parts = [p.strip() for p in re.split(r"\n\s*\n", pipeline.translate_block(joined).strip()) if p.strip()]
        except Exception:
            parts = []
        if len(parts) == len(batch):
            for index, part in zip(batch, parts):
                results[index] = part
            continue
        for index in batch:
            try:
                results[index] = pipeline.translate_block(segments[index].strip())
            except Exception:
                results[index] = segments[index]
    return results


# ------------------------------------------------------------------------- json
def _json_strings(value, collected: list[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _json_strings(item, collected)
    elif isinstance(value, list):
        for item in value:
            _json_strings(item, collected)
    elif isinstance(value, str):
        collected.append(value)


def _json_rebuild(value, translated: list[str], cursor: list[int]):
    if isinstance(value, dict):
        return {key: _json_rebuild(item, translated, cursor) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_rebuild(item, translated, cursor) for item in value]
    if isinstance(value, str):
        result = translated[cursor[0]]
        cursor[0] += 1
        return result
    return value


def translate_json_text(text: str, pipeline) -> str:
    data = json.loads(text)
    collected: list[str] = []
    _json_strings(data, collected)
    translated = translate_segments(pipeline, collected)
    rebuilt = _json_rebuild(data, translated, [0])
    indent = 2 if "\n" in text else None
    return json.dumps(rebuilt, ensure_ascii=False, indent=indent) + "\n"


def translate_jsonl_text(text: str, pipeline) -> str:
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        lines.append(translate_json_text(line, pipeline).strip())
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------------- csv
def translate_csv_text(text: str, delimiter: str, pipeline) -> str:
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    flat = [cell for row in rows for cell in row]
    translated = translate_segments(pipeline, flat)
    position = 0
    output = []
    for row in rows:
        new_row = []
        for _cell in row:
            new_row.append(translated[position])
            position += 1
        output.append(new_row)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerows(output)
    return buffer.getvalue()


# --------------------------------------------------------------------- notebook
def translate_notebook_text(text: str, pipeline) -> str:
    notebook = json.loads(text)
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("ساختار نوت‌بوک قابل خواندن نبود")
    markdown_indexes = [
        index for index, cell in enumerate(cells)
        if cell.get("cell_type") in {"markdown", "raw"}
    ]
    sources = ["".join(cells[index].get("source") or []) for index in markdown_indexes]
    translated = translate_segments(pipeline, sources)
    for index, value in zip(markdown_indexes, translated):
        lines = value.splitlines(keepends=True)
        cells[index]["source"] = lines or [value]
    return json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"


# -------------------------------------------------------------------------- code
def translate_code_text(text: str, pipeline) -> str:
    """فقط متن کامنت‌ها ترجمه می‌شود؛ کد، رشته‌ها و کلیدها دست‌نخورده می‌مانند."""
    lines = text.splitlines()
    targets: list[int] = []
    bodies: list[str] = []
    for index, line in enumerate(lines):
        match = _COMMENT.match(line)
        if not match:
            continue
        body = match.group("body")
        if body and _LETTER.search(body):
            targets.append(index)
            bodies.append(body)
    translated = translate_segments(pipeline, bodies)
    for index, value in zip(targets, translated):
        match = _COMMENT.match(lines[index])
        lines[index] = (
            f"{match.group('indent')}{match.group('marker')}{match.group('space')}"
            f"{value}{match.group('tail')}"
        )
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# ----------------------------------------------------------------------- router
def translate_structured(source: Path, suffix: str, pipeline) -> str | None:
    """محتوای ترجمه‌شده را برمی‌گرداند؛ اگر این پسوند ساختاریافته نیست None."""
    text = source.read_text(encoding="utf-8-sig", errors="replace")
    if suffix in JSON_EXTENSIONS:
        return translate_json_text(text, pipeline)
    if suffix in JSONL_EXTENSIONS:
        return translate_jsonl_text(text, pipeline)
    if suffix in CSV_EXTENSIONS:
        delimiter = "\t" if suffix == ".tsv" else ("|" if suffix == ".psv" else (";" if suffix == ".ssv" else ","))
        return translate_csv_text(text, delimiter, pipeline)
    if suffix == ".ipynb":
        return translate_notebook_text(text, pipeline)
    if suffix in CODE_EXTENSIONS:
        return translate_code_text(text, pipeline)
    return None


STRUCTURED_EXTENSIONS = (
    JSON_EXTENSIONS | JSONL_EXTENSIONS | CSV_EXTENSIONS | CODE_EXTENSIONS | {".ipynb"}
)
