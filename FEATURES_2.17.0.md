# Trarium 2.17.0 — Feature Map

## Core pipeline
- Native PDF text extraction first; OCR is used only for pages with no usable text layer.
- Safe Optimization: conservative whitespace normalization only; no aggressive story rewriting.
- Translation Memory / SQLite cache with normalized keys.
- Document-level duplicate suppression: identical blocks are translated once and reused.
- Persistent SQLite checkpoints: successful units are stored and reused after interruption.
- Token-aware batching at block boundaries with adaptive batch sizing.
- Placeholder protection for URLs, emails, HTML/XML tags and inline code.
- Persistent character/term registry and relevant-per-batch glossary injection.
- Dynamic provider failover; optional smart routing for short/simple dialogue vs. complex text.
- Pre-flight token estimation and authenticated connectivity probe.
- Provider usage metadata is captured when the provider returns token usage; otherwise Trarium falls back to a local estimate.

## Output
- Text, PDF, EPUB and HTML output paths remain available.
- PDF layout mode writes translated text only after a dry-run fit check, so a failed insert does not erase the source block.
- Every generated book output keeps the exact attribution text: «ترجمه شده توسط تراریوم».
- Sahel font remains bundled for Persian output.

## Provider layer
- Existing project providers are preserved: OpenRouter, Google Gemini, Groq, 9Router and generic OpenAI-compatible endpoints.
- OpenRouter WAF/security errors remain classified so deterministic 403 security blocks are not pointlessly retried.

## Compatibility
- Built as an upgrade on the 2.16.4 project structure.
- Python 3.10+.
- OCR engines remain optional.
