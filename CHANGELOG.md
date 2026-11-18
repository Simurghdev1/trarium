## 2.18.0 — Persistent Jobs & Provider Control

- Persistent job manager with SQLite state: Pause, Resume, Cancel, Delete and crash recovery.
- Translation queue with priorities, reordering, concurrency limit and per-project errors.
- Web-novel chapter ranges with an independent status for every chapter.
- Live monitor ("Show Details"): real token counts, requests, cost, stage, page/unit, ETA from actual speed.
- Smart provider failover: provider fallback, model fallback and smart routing, with local quota and cost caps.
- Permanent errors are not retried; uncertain network requests are not blindly resent.
- Usage & billing tab for LLM and OCR, with daily/monthly charts, per-model and per-project breakdown,
  local limits, warnings and a failover history. API keys stay masked.
- Optional manga enhancement: Auto/1x/2x/4x, denoise, deblur, color, contrast, deskew, Pillow/OpenCV,
  optional Real-ESRGAN/SwinIR. Originals are never modified; a failed step falls back to the original page.
- Website translation: structure-preserving HTML, multi-page crawl, robots.txt, deduplication,
  resumable per-page cache and HTML/TXT/EPUB output.
- OCR pages and requests are now metered separately from LLM tokens.
- New `numpy` dependency; OpenCV remains optional (`.[image-opencv]`).

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
