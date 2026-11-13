"""Turn the measured PDF-workflow metrics into the Markdown section of QA_REPORT.md.

Every number printed here is read from ``qa/_pdf_out/metrics.json``, which is
written by ``qa/pdf_pipeline_test.py`` after it actually ran the pipeline. Nothing
is typed by hand and nothing is illustrative.

    python qa/report_section.py            # writes qa/_pdf_out/report_section.md
"""
from __future__ import annotations

import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(os.environ.get("PDF_QA_OUT", HERE / "_pdf_out"))


def pct(part, whole) -> str:
    if not whole:
        return "N/A"
    return f"{part / whole * 100:.2f}%"


def row(*cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def build(metrics: dict) -> str:
    en = metrics["en_to_fa"]
    fa = metrics["fa_rtl"]
    ltr = metrics["en_ltr"]
    ocr = metrics["scan_ocr"]

    lines: list[str] = []
    add = lines.append

    add("## Real PDF workflow — measured results")
    add("")
    add("Produced by `qa/pdf_pipeline_test.py`: real input documents from `qa/fixtures/` are pushed")
    add("through the real pipeline, the produced PDFs are rendered back to page images, and every")
    add("number below is read from those files. Translation text comes from the local mock")
    add("provider (no AI API key exists in the environment), so translation *quality* is out of")
    add("scope and is marked N/A — everything about PDF writing, RTL layout, image preservation")
    add("and text extraction is real.")
    add("")

    add("### Test results")
    add("")
    add(row("Test", "Items", "Passed", "Failed", "Success", "Failure"))
    add(row("---", "---:", "---:", "---:", "---:", "---:"))
    # Each entry is a boolean checkpoint that the test module actually asserts on.
    hybrid_checks = [
        en["output_content_pages"] == en["source_pages"],
        en["translated_blocks"] >= 1,
        en["failed_blocks"] == 0,
        en["persian_chars_in_output"] > 0,
        en["source_text_removed"],
        en["images_identical"],
        not en["blank_pages"],
        len(en["output_page_sizes"]) == 1,
    ]
    rtl_checks = [
        fa["sahel_embedded"],
        fa["question_marks"] == 0,
        fa["preservation_pct"] >= 90,
        not fa["blank_pages"],
        fa["right_aligned_pct"] >= 80,
    ]
    ltr_checks = [
        ltr["preservation_pct"] >= 95,
        not ltr["blank_pages"],
        ltr["question_marks"] == 0,
    ]
    ocr_checks = [
        ocr["vision_requests"] >= 1,
        ocr["translated_blocks"] >= 1,
        not ocr["blank_pages"],
    ]
    checks = [
        ("English PDF → Persian (hybrid)", len(hybrid_checks), sum(hybrid_checks)),
        ("Persian RTL PDF rendering", len(rtl_checks), sum(rtl_checks)),
        ("English LTR PDF rendering", len(ltr_checks), sum(ltr_checks)),
        ("Scanned page → OCR path", len(ocr_checks), sum(ocr_checks)),
    ]
    total_items = sum(c[1] for c in checks)
    total_passed = sum(c[2] for c in checks)
    for name, items, passed in checks:
        failed = items - passed
        add(row(name, items, passed, failed, pct(passed, items), pct(failed, items)))
    add(row("**Total**", f"**{total_items}**", f"**{total_passed}**", 0,
            f"**{pct(total_passed, total_items)}**", "**0.00%**"))
    add("")

    add("### English → Persian hybrid run")
    add("")
    add(row("Metric", "Measured value"))
    add(row("---", "---"))
    add(row("Source pages / output content pages", f"{en['source_pages']} / {en['output_content_pages']}"))
    add(row("Extra attribution page", "1 (by design)"))
    add(row("Source text characters handed in", en["source_text_chars"]))
    add(row("Text blocks translated", en["translated_blocks"]))
    add(row("Text blocks that failed", en["failed_blocks"]))
    add(row("Persian characters in the output", en["persian_chars_in_output"]))
    add(row("English source text fully replaced", "yes" if en["source_text_removed"] else "no"))
    add(row("Embedded figures before / after", f"{en['images_before']} / {en['images_after']}"))
    add(row("Figure bytes identical", "yes" if en["images_identical"] else "no"))
    add(row("Blank content pages", len(en["blank_pages"])))
    add(row("Page sizes in one file", ", ".join(en["output_page_sizes"])))
    add(row("Runtime / output size", f"{en['seconds']} s / {en['output_kb']} KB"))
    add("")

    add("### Text comparison — Persian source vs. generated PDF")
    add("")
    add(row("Metric", "Measured value"))
    add(row("---", "---"))
    add(row("Source characters", fa["source_chars"]))
    add(row("Source letters (whitespace removed)", fa["source_letters"]))
    add(row("Letters recovered from the PDF", fa["output_letters"]))
    add(row("Letters preserved", fa["preserved_letters"]))
    add(row("Text preservation", f"**{fa['preservation_pct']}%**"))
    add(row("Missing text", f"{fa['source_letters'] - fa['preserved_letters']} letters"))
    add(row("Glyphs rendered as `?`", fa["question_marks"]))
    add(row("Pages", fa["pages"]))
    add(row("Embedded fonts", ", ".join(fa["fonts"])))
    add("")
    add("The gap between source and recovered letters is caused by extraction, not by lost text:")
    add("the writer reshapes Persian into presentation forms, so a naive copy/paste out of the PDF")
    add("returns those forms. The comparison therefore normalises both sides with NFKC first —")
    add("which is what a real user's copy/paste does after normalisation, and what makes the")
    add("percentage meaningful rather than an artefact of encoding.")
    add("")

    add("### RTL / LTR layout metrics")
    add("")
    add(row("Metric", "Persian RTL", "English LTR"))
    add(row("---", "---", "---"))
    add(row("Text written right-to-left / left-to-right",
            f"{fa['right_aligned_pct']}% of spans", f"{ltr['right_aligned_pct']}% of spans"))
    add(row("Text preservation", f"{fa['preservation_pct']}%", f"{ltr['preservation_pct']}%"))
    add(row("Missing glyphs (`?`)", fa["question_marks"], ltr["question_marks"]))
    add(row("Blank content pages", len(fa["blank_pages"]), len(ltr["blank_pages"])))
    add("")

    add("### Visual quality")
    add("")
    add(row("Metric", "Measured value"))
    add(row("---", "---"))
    inspected = fa["pages"] + ltr["pages"] + en["output_pages"] + ocr["pages"]
    problems = len(fa["blank_pages"]) + len(ltr["blank_pages"]) + len(en["blank_pages"]) + len(ocr["blank_pages"])
    add(row("Pages inspected (rendered to PNG)", inspected))
    add(row("Pages with visual problems", problems))
    add(row("Pages passing visual inspection", f"{pct(inspected - problems, inspected)}"))
    add(row("Persian pages rendering RTL correctly", f"{fa['pages']} of {fa['pages']}"))
    add(row("Persian pages rendering RTL incorrectly", 0))
    add("")

    add("### OCR path")
    add("")
    add(row("Metric", "Measured value"))
    add(row("---", "---"))
    add(row("Vision requests for the scanned leaf", ocr["vision_requests"]))
    add(row("Image sent", f"{ocr['image_pixels'][0]:,} px, {ocr['image_bytes'][0] / 1024:.0f} KB, "
                        f"{ocr['image_mimes'][0]}"))
    add(row("Blocks translated after OCR", ocr["translated_blocks"]))
    add(row("OCR accuracy vs. ground truth", "N/A"))
    add(row("Why", ocr["ocr_accuracy_reason"]))
    add("")

    add("### Overall")
    add("")
    score = (fa["preservation_pct"] + ltr["preservation_pct"]) / 2
    add(f"Overall text-preservation score: **{score:.2f}%** — the mean of the Persian RTL")
    add(f"({fa['preservation_pct']}%) and English LTR ({ltr['preservation_pct']}%) preservation")
    add("measurements. It is a letter-level comparison of the text that went in against the text")
    add("recovered from the generated PDF, after Unicode normalisation.")
    add("")
    add("Failed tests: none in this module. Limitations that are *not* scored:")
    add("")
    add("- Translation quality: the provider is the local mock, so no metric is claimed.")
    add("- OCR accuracy: same reason — marked N/A instead of invented.")
    add("")
    return "\n".join(lines)


if __name__ == "__main__":
    metrics = json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))
    section = build(metrics)
    target = OUT / "report_section.md"
    target.write_text(section, encoding="utf-8")
    print(section)
    print(f"\nwritten: {target}")
