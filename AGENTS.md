# Hermes Agent — Trarium Project Instructions

## What is Trarium?
Trarium is a Persian/Farsi book translation tool with:
- PDF translation (scanned + text) with format preservation
- EPUB/TXT translation
- Novel web scraper (GenericAdapter, Syosetu, Wuxia)
- Multiple OCR engines (Tesseract, PaddleOCR, EasyOCR, OpenRouter Vision)
- Multiple providers (OpenRouter, 9Router, Gemini)

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

## Important Notes
- OpenRouter and 9Router use OpenAI-compatible API format
- For Iranian users: Google Gemini may be blocked, use OpenRouter or 9Router
- OCR engines are optional (install as needed)
- Font: Sahel.ttf for Persian PDF output
