"""تعمیر خودکار کتاب: پیدا کردن صفحات تکراری، سفید، خراب، جابه‌جا و کم‌کیفیت.

هیچ‌وقت فایل اصلی بازنویسی نمی‌شود. گزارش اول ساخته می‌شود، کاربر آن را
می‌بیند و تأیید می‌کند، و نسخهٔ اصلاح‌شده در یک فایل تازه ذخیره می‌شود.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SEVERITY_WEIGHT = {"critical": 22, "warning": 7, "info": 0}

_PAGE_NUMBER = re.compile(r"^\D{0,6}(\d{1,4})\D{0,6}$")


@dataclass
class PageIssue:
    page: int | None
    kind: str
    severity: str
    detail: str

    def to_dict(self) -> dict:
        return {"page": self.page, "kind": self.kind, "severity": self.severity,
                "detail": self.detail}


@dataclass
class RepairReport:
    source: str
    format: str
    pages: int = 0
    text_pages: int = 0
    scanned_pages: int = 0
    issues: list[PageIssue] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    blank_pages: list[int] = field(default_factory=list)
    corrupt_pages: list[int] = field(default_factory=list)
    out_of_order: list[tuple[int, int]] = field(default_factory=list)
    detected_order: list[int] = field(default_factory=list)
    quality_score: int = 100
    can_repair: bool = False
    notes: list[str] = field(default_factory=list)

    # -------------------------------------------------------------- helpers
    @property
    def removable_pages(self) -> list[int]:
        return sorted(set(self.duplicates))

    @property
    def has_problems(self) -> bool:
        return bool(self.issues)

    def summary_fa(self) -> str:
        if not self.issues:
            return "هیچ مشکل قابل توجهی پیدا نشد."
        parts = []
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} صفحهٔ تکراری")
        if self.blank_pages:
            parts.append(f"{len(self.blank_pages)} صفحهٔ سفید")
        if self.corrupt_pages:
            parts.append(f"{len(self.corrupt_pages)} صفحهٔ خراب")
        if self.out_of_order:
            parts.append(f"{len(self.out_of_order)} مورد جابه‌جایی")
        low = sum(1 for issue in self.issues if issue.kind == "low_quality")
        if low:
            parts.append(f"{low} صفحهٔ کم‌کیفیت/بدون متن")
        return " · ".join(parts) or "مشکل جزئی"

    def to_dict(self) -> dict:
        return {
            "source": self.source, "format": self.format, "pages": self.pages,
            "text_pages": self.text_pages, "scanned_pages": self.scanned_pages,
            "issues": [issue.to_dict() for issue in self.issues],
            "duplicates": self.duplicates, "blank_pages": self.blank_pages,
            "corrupt_pages": self.corrupt_pages,
            "out_of_order": [list(pair) for pair in self.out_of_order],
            "detected_order": self.detected_order,
            "quality_score": self.quality_score, "can_repair": self.can_repair,
            "notes": self.notes,
        }


@dataclass
class RepairResult:
    output_path: str
    pages_before: int
    pages_after: int
    removed: list[int] = field(default_factory=list)
    reordered: bool = False
    source_unchanged: bool = True
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"output_path": self.output_path, "pages_before": self.pages_before,
                "pages_after": self.pages_after, "removed": self.removed,
                "reordered": self.reordered, "source_unchanged": self.source_unchanged,
                "log": self.log}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _page_number(text: str) -> int | None:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None
    for candidate in (lines[-1], lines[0]):
        match = _PAGE_NUMBER.match(candidate)
        if match:
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if 0 < value < 10000:
                return value
    return None


def _finish(report: RepairReport) -> RepairReport:
    penalty = sum(SEVERITY_WEIGHT.get(issue.severity, 0) for issue in report.issues)
    report.quality_score = max(0, min(100, 100 - penalty))
    return report


def analyze_pdf(path: str | Path) -> RepairReport:
    import pymupdf

    path = Path(path)
    report = RepairReport(source=str(path), format="pdf", can_repair=True)
    document = pymupdf.open(str(path))
    report.pages = len(document)
    seen: dict[str, int] = {}
    numbers: list[tuple[int, int]] = []
    try:
        for index in range(report.pages):
            page_no = index + 1
            try:
                page = document[index]
                text = page.get_text("text") or ""
                images = page.get_images(full=True)
            except Exception as exc:
                report.corrupt_pages.append(page_no)
                report.issues.append(PageIssue(page_no, "corrupt", "critical",
                                               f"صفحه بازخوانی نشد: {exc}"))
                continue
            normalised = _normalise(text)
            if normalised:
                report.text_pages += 1
            if not normalised and not images:
                report.blank_pages.append(page_no)
                report.issues.append(PageIssue(page_no, "blank", "warning",
                                               "صفحه هیچ متن یا تصویری ندارد."))
                continue
            if not normalised and images:
                report.scanned_pages += 1
                report.issues.append(PageIssue(page_no, "low_quality", "info",
                                               "صفحه فقط تصویر دارد؛ برای ترجمه به OCR نیاز دارد."))
                continue
            if len(normalised) < 40 and images:
                report.issues.append(PageIssue(page_no, "low_quality", "warning",
                                               f"متن بسیار کم ({len(normalised)} کاراکتر) در صفحهٔ تصویری."))
            number = _page_number(text)
            if number is not None:
                numbers.append((index, number))
            if len(normalised) >= 40:
                digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()
                if digest in seen:
                    report.duplicates.append(page_no)
                    report.issues.append(PageIssue(
                        page_no, "duplicate", "warning",
                        f"محتوای این صفحه با صفحهٔ {seen[digest]} یکسان است."))
                else:
                    seen[digest] = page_no
    finally:
        document.close()

    if len(numbers) >= 4:
        ordered = [number for _, number in numbers]
        inversions = sum(1 for a, b in zip(ordered, ordered[1:]) if b < a)
        if inversions:
            report.out_of_order = [
                (numbers[i][0] + 1, numbers[i + 1][0] + 1)
                for i in range(len(ordered) - 1) if ordered[i + 1] < ordered[i]
            ]
            report.detected_order = [numbers[i][0] + 1 for i in sorted(
                range(len(numbers)), key=lambda i: (ordered[i], numbers[i][0]))]
            report.issues.append(PageIssue(
                None, "out_of_order", "warning",
                f"شمارهٔ چاپ‌شدهٔ صفحات {inversions} بار عقب می‌رود؛ احتمال جابه‌جایی وجود دارد."))
            report.notes.append("ترتیب پیشنهادی از شمارهٔ چاپ‌شدهٔ پایین/بالای صفحات ساخته شد.")
    if report.duplicates:
        report.notes.append("صفحات تکراری هنگام اصلاح حذف می‌شوند؛ نسخهٔ اصلی دست‌نخورده می‌ماند.")
    if report.corrupt_pages:
        report.notes.append("صفحات خراب قابل بازیابی نیستند و در نسخهٔ اصلاح‌شده از گزارش حذف می‌شوند.")
    return _finish(report)


def analyze_other(path: str | Path) -> RepairReport:
    """بررسی ساده برای EPUB/TXT/DOCX و بقیه: فصل‌های خالی و تکراری."""
    path = Path(path)
    suffix = path.suffix.lower().lstrip(".") or "file"
    report = RepairReport(source=str(path), format=suffix, can_repair=False)
    try:
        from trarium.extractors import get_extractor
        book = get_extractor(str(path), use_ocr=False).extract(str(path))
    except Exception as exc:
        report.issues.append(PageIssue(None, "corrupt", "critical", f"فایل خوانده نشد: {exc}"))
        return _finish(report)
    report.pages = len(book.chapters)
    seen: dict[str, int] = {}
    for index, chapter in enumerate(book.chapters, 1):
        content = _normalise(str(chapter.get("content") or ""))
        if not content:
            report.blank_pages.append(index)
            report.issues.append(PageIssue(index, "blank", "warning",
                                           "این بخش خالی است و متنی برای ترجمه ندارد."))
            continue
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()
        if digest in seen:
            report.duplicates.append(index)
            report.issues.append(PageIssue(index, "duplicate", "warning",
                                           f"این بخش با بخش {seen[digest]} یکسان است."))
        else:
            seen[digest] = index
        report.text_pages += 1
    report.notes.append("برای این فرمت نسخهٔ اصلاح‌شده ساخته نمی‌شود؛ گزارش فقط برای آگاهی قبل از ترجمه است.")
    return _finish(report)


def analyze_book(path: str | Path) -> RepairReport:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"فایل پیدا نشد: {path}")
    if path.suffix.lower() == ".pdf" or _looks_like_pdf(path):
        return analyze_pdf(path)
    return analyze_other(path)


def _looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def repair_pdf(source: str | Path, output_path: str | Path, report: RepairReport | None = None,
               *, drop_duplicates: bool = True, drop_blank: bool = False,
               fix_order: bool = False) -> RepairResult:
    """نسخهٔ اصلاح‌شده می‌سازد؛ فایل ورودی هرگز تغییر نمی‌کند."""
    import pymupdf

    source = Path(source)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("فایل خروجی نباید همان فایل اصلی باشد؛ نسخهٔ اصلی باید سالم بماند.")
    report = report or analyze_pdf(source)
    if report.format != "pdf":
        raise ValueError("تعمیر خودکار فقط برای PDF انجام می‌شود.")
    document = pymupdf.open(str(source))
    before = len(document)
    order = list(range(before))
    removed: list[int] = []
    log: list[str] = []
    drop = set()
    if drop_duplicates:
        drop |= {page - 1 for page in report.duplicates}
    if drop_blank:
        drop |= {page - 1 for page in report.blank_pages}
    if drop:
        order = [index for index in order if index not in drop]
        removed = sorted(index + 1 for index in drop)
        log.append(f"{len(removed)} صفحه حذف شد: {', '.join(str(p) for p in removed[:30])}")
    reordered = False
    if fix_order and report.detected_order:
        proposed = [page - 1 for page in report.detected_order if 0 <= page - 1 < before]
        if len(proposed) == before:
            order = [index for index in proposed if index not in drop]
            reordered = True
            log.append("ترتیب صفحات بر اساس شمارهٔ چاپ‌شده اصلاح شد.")
        else:
            log.append("شمارهٔ همهٔ صفحات پیدا نشد؛ ترتیب اصلی حفظ شد.")
    if not removed and not reordered:
        log.append("تغییری لازم نبود؛ یک کپی سالم از فایل اصلی ساخته شد.")
    if order != list(range(before)) or removed:
        document.select(order)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output), garbage=4, deflate=True, clean=True)
    document.close()
    return RepairResult(output_path=str(output), pages_before=before,
                        pages_after=len(order), removed=removed, reordered=reordered,
                        source_unchanged=True, log=log)


def quality_gate(path: str | Path) -> dict:
    """وضعیت کیفیت صفحه‌ها پیش از ترجمه."""
    try:
        report = analyze_book(path)
    except Exception as exc:
        return {"ok": False, "score": 0, "warnings": [str(exc)], "report": None}
    warnings = [f"صفحه {issue.page}: {issue.detail}" if issue.page else issue.detail
                for issue in report.issues if issue.severity != "info"]
    return {
        "ok": report.quality_score >= 55,
        "score": report.quality_score,
        "warnings": warnings,
        "report": report,
    }
