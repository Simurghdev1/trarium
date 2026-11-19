# Changelog

## 1.1.0

First public release of Trarium.

**Translation**
- Text, PDF, EPUB, DOCX/PPTX/XLSX, HTML, subtitles (SRT/WebVTT/SBV/LRC/ASS/MicroDVD), JSON/CSV, notebooks and code comments, each delivered in its own format.
- Layout-preserving PDF: the translation is written over the source text with a paper-coloured cover, real-font fitting and Persian shaping; scanned pages are read with OCR.space or a vision model.
- Manga/scan titles: overlapping OCR lines are merged, artwork lettering is left alone, and the Persian title is centred on the original at its own size.
- Web novels (follows real "Next chapter" links), whole websites (HTML structure kept, robots.txt respected) and YouTube subtitles (yt-dlp, auto-caption clean-up, original-language fallback).
- Translation memory, checkpoints, token-aware batching, placeholders for URLs/code, a character/term registry and provider failover (OpenRouter, Gemini, Groq, 9Router, any OpenAI-compatible API).

**Workflow**
- Persistent background jobs and a queue (pause / resume / cancel / delete), live monitor with tokens, cost and ETA, smart resume after a crash.
- Library and playlists, project templates (Manga, Manhwa, Manhua, Web Novel, Light Novel, Academic, Technical), automatic book repair, cross-format conversion, usage and analytics.
- Manga image enhancement (denoise, deblur, upscale, optional Real-ESRGAN / SwinIR).

**Security and deployment**
- SSRF guard on every website/novel fetch; server API keys are never sent to the browser and are only sent to the server's own base URL.
- Optional `TRARIUM_APP_PASSWORD` gate for public deployments.
- Dockerfile + `railway.json` ready for Railway.
