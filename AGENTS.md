# Hermes Agent — Trarium Project Instructions

## What is Trarium?
Trarium is a Persian/Farsi book translation tool with:
- PDF translation (scanned + text) with format preservation
- EPUB/TXT translation
- Novel web scraper (GenericAdapter, Syosetu, Wuxia)
- Multiple OCR engines (Tesseract, PaddleOCR, EasyOCR, OpenRouter Vision)
- Multiple providers (OpenRouter, 9Router, Gemini, Groq, OpenAI-compatible)
- Persistent background jobs, a translation queue and live monitoring
- Smart provider failover with local quotas, cost caps and history
- Manga/webtoon image enhancement (optional, originals untouched)
- Structure-preserving website translation (robots.txt aware)
- Local usage/billing ledger for LLM and OCR

## Capabilities
1. **PDF Translation**: Upload scanned or text PDF → OCR → Translate → Output PDF with same layout
2. **EPUB/TXT Translation**: Upload ebook → Translate chapters → Output translated file
3. **Novel Scraping**: Provide URL → Scrape chapters → Clean → Translate → Export EPUB/PDF/TXT
4. **Hybrid Mode**: Preserve PDF format while translating text blocks

## How to Run

### Streamlit Web UI
```bash
streamlit run streamlit_app.py
```

### CLI
```bash
# Translate a PDF
python -m trarium.cli translate input.pdf -o output.pdf --provider openrouter --api-key YOUR_KEY

# Translate an EPUB
python -m trarium.cli translate book.epub -o book_fa.epub --provider openrouter --api-key YOUR_KEY

# Scrape a novel
python -m trarium.cli novel https://example.com/novel/1/ -o output/
```

## Translation Pipeline
1. User uploads file (PDF/EPUB/TXT)
2. System detects format and analyzes content
3. For PDF: runs OCR if needed (Tesseract → PaddleOCR → EasyOCR → OpenRouter Vision)
4. Translates text blocks with literary rules (no em-dash, proper ZWNJ, quotes)
5. Outputs translated file in same or different format

## Literary Translation Rules
- No em-dashes (— or –) — use commas, periods, or parentheses
- Proper half-spaces (ZWNJs): می‌شود، کتاب‌ها، رفته‌است
- Dialogues in Persian quotes: «...»
- Complete translation without summarization
- Preserve author's tone and style

## Provider Configuration
- **OpenRouter**: Free models (Nemotron, GPT-OSS) — requires API key
- **9Router**: Local router (localhost:20128) — smart fallback to free/cheap providers
- **Gemini**: Google models — requires API key (may be geo-blocked in Iran)
- **Custom Model**: Any OpenAI-compatible model can be specified

## Background jobs, queue and metering

- `trarium/jobs/` owns durable jobs (`~/.trarium/jobs.sqlite3`), the dispatcher thread and handlers.
  UI code must go through `get_manager()`; never run a long translation on the Streamlit thread when the
  background toggle is on.
- Job handlers are idempotent at unit level: completed units and Checkpoint/cache entries are reused on resume.
- `trarium/usage.py` records every metered call. Wrap translators with `UsageTrackingTranslator`;
  never estimate a provider's official quota, keep local estimates separately labelled.
- `trarium/failover.py` classifies errors; permanent/auth/network errors must not loop on the same endpoint.
- `trarium/manga/` and `trarium/websites.py` must never overwrite an input file.
- `TRARIUM_HOME` overrides `~/.trarium` (tests set it, plus `HOME`).

## Project Repositories
- **Main repo (ship all code and docs here):** https://github.com/subaruvibe1/trarium — branch `main`.
- **Files repo (test inputs, sample files and test outputs):** https://github.com/subaruvibe1/trarium-files — branch `main`. Put translated outputs, QA logs and sample inputs here and link them when reporting a test.
- Both repos are private, so `raw.githubusercontent.com` returns 404 without a token. Check published content with `curl -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github.raw" https://api.github.com/repos/subaruvibe1/trarium/contents/README.md` instead.

## Reporting Test Outputs
- Whenever you test Trarium with a file, always hand the user a download link to the produced output (the translated PDF/TXT/EPUB/DOCX, the QA log, and the input file when it is a sample), in this project's `trarium-files` repository or as an attached artifact. "Tests passed" is not enough; the user must be able to open the result.

## Important Notes
- OpenRouter and 9Router use OpenAI-compatible API format
- For Iranian users: Google Gemini may be blocked, use OpenRouter or 9Router
- OCR engines are optional (install as needed)
- Font: Sahel.ttf for Persian PDF output
