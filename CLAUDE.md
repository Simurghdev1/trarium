# Claude Code Project Instructions — Trarium

## Overview
Trarium is a Persian/Farsi book translation tool. You can help by:
1. Fixing bugs in the Python codebase
2. Adding new features (OCR engines, providers, output formats)
3. Improving translation quality
4. Testing and building releases

## Quick Start
```bash
# Install dependencies
pip install -r requirements.txt

# Run the web UI
streamlit run streamlit_app.py

# Run tests
python -m pytest tests/ -v
```

## Architecture
- `trarium/` — Main package
  - `extractors/` — PDF/EPUB/TXT parsing
  - `processors/` — Text chunking and processing
  - `translators/` — API providers (OpenRouter, 9Router, Gemini)
  - `novel/` — Web scraper for novels
  - `output/` — PDF/EPUB/TXT export
- `streamlit_app.py` — Web UI
- `cli.py` — Command line interface

## Translation Flow
1. Parse input file → Extract text blocks
2. For PDF: OCR scanned pages if needed
3. Chunk text for API limits
4. Translate chunks with literary rules
5. Assemble and export output file

## Literary Rules (IMPORTANT)
When modifying translation prompts or post-processing:
- No em-dashes (— or –) — replace with commas/periods
- Proper half-spaces (ZWNJ): می‌شود، کتاب‌ها
- Dialogues in Persian quotes: «...»
- Complete translation, no summarization

## Provider Setup
- **OpenRouter**: Free tier available, requires API key
- **9Router**: Local (localhost:20128), smart fallback
- **Custom**: Any OpenAI-compatible endpoint

## Common Tasks
- Add new OCR engine: Create `trarium/extractors/ocr_xxx.py`
- Add new provider: Create `trarium/translators/xxx.py` and update `__init__.py`
- Add new output format: Update `trarium/output/__init__.py`
- Fix PDF layout: Edit `trarium/pdf_optimized.py`

## Testing
- Test OCR with sample PDFs
- Test translation with short text
- Verify output files open correctly
- Check Persian font rendering in PDFs
