## 2.18.1 — The pictures stay visible

- `trarium/pdf_images.py`: after translation every JPEG 2000 (`/JPXDecode`) image in
  the file is re-encoded in place — JPEG for photo-like pictures, lossless Flate for
  small, masked or transparent ones. Scanned books store their page texture, covers
  and engravings as JPEG 2000, and viewers without a JPEG 2000 decoder (pdf.js, which
  GitHub, Firefox and many phone readers use) then draw a blank page with only the
  translated text and the pale boxes behind it: it looked exactly as if the pictures
  had been deleted. Placement, size, aspect ratio and masks are untouched; a rendered
  page differs from the previous output by less than one colour step on average.
- New `viewer_safe_images=True` option in `translate_pdf_optimized`; the pipeline
  reports how many images it converted.
- 3 new tests (137 total), including a rendered-pixel comparison of the re-encoded page.

## 2.18.0 — Automatic Cover Detection

- `trarium/page_detector.py`: every page is classified on its own, before OCR and
  before translation, as `NORMAL_PAGE`, `BOOK_COVER`, `MANGA_COVER`, `TITLE_PAGE`,
  `ILLUSTRATION_PAGE`, `AD_PAGE` or `UNCERTAIN`.
- Cover, title, plate and advertising pages are excluded from OCR and from
  translation and stay pixel-identical to the source PDF; they are never redacted,
  re-rendered or moved.
- Evidence: paragraph structure and display typography from the text layer, ink
  bands / darkness / saturation from the rendered page, and an optional Gemini
  vision confirmation for ambiguous pages (capped per document).
- A page with a picture and story text is still translated; a picture-only page
  that OCR can read keeps its OCR; low confidence never removes a page.
- Configurable: `cover_detection`, `cover_threshold`, `manual_page_status`,
  `vision_api_key`; the Streamlit UI shows the detected pages in an editable table.
- Run reports gain `page_status.json` plus counts of translated pages, covers,
  title pages, illustration pages, advertising pages and suspicious pages.
- `trarium/subtitles.py` and `qa/run_youtube_test.py`: transcript translation that
  keeps every cue's timing (SRT, VTT, bilingual SRT).
- 37 new tests (134 total).

## 2.17.0 — Safe Translation Engine

- Hybrid PDF extraction: native text first; OCR only on pages without text.
- Safe optimization: conservative whitespace normalization and no aggressive literary rewriting.
- Translation Memory + normalized cache and in-book de-duplication.
- Persistent SQLite checkpoints for interrupted jobs and resume.
- Token-aware batching with adaptive batch sizing and natural block boundaries.
- Placeholder protection for URLs, emails, HTML/XML tags and inline code.
- Persistent character/term registry with per-chunk terminology injection.
- Dynamic provider failover instead of fixed round-robin.
- Pre-flight token estimation/connectivity probe helpers.
- PDF layout translation now reports cache/checkpoint/API/token metrics.
- Attribution page remains: «ترجمه شده توسط تراریوم».

# Trarium Changelog

## 2.16.4
- OpenRouter auto transport now prefers curl when available, with requests fallback.
- Removed localhost attribution headers from default OpenRouter requests; attribution is opt-in.
- Added real OpenRouter diagnostics: GET /models + tiny authenticated POST.
- Added direct Google Gemini REST provider with current Gemini 3.8/3.7/3.6/3.5 Flash model IDs.
- Added direct Groq provider with current GPT-OSS and Qwen 3.8 models.
- Added generic OpenAI-compatible provider for additional services.
- Streamlit provider selector updated accordingly.


## 2.16.3
- Added robust OpenRouter HTTP transport with WAF/security-policy classification.
- Automatic curl fallback on WAF-style 403 when available; no repeated retries for deterministic security blocks.
- OpenRouter errors now distinguish network/WAF blocks from API/guardrail/account 403s.
- Added OpenRouter connection diagnostic in the Streamlit UI.
- Refreshed current free OpenRouter model slugs (including Gemma 4 31B and Nemotron 3 Nano Omni).
- Removed stale free slug for Nemotron 3.5 Lightning, which is currently a paid model.
- OpenRouter OCR Vision now uses the same robust transport.
