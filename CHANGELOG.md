# Changelog

## 1.3.0 — 2026-11-22

**Media: dubbing and subtitle maker**
- New «🎙️ دوبله» tab: YouTube link (yt-dlp, ≤720p) or uploaded audio/video → any target language. Live mode streams the audio to Gemini Live (`gemini-3.5-live-translate-preview`, ephemeral token) and lays the translated speech over the video; fallback key 2 → native-audio live model → normal mode. Normal mode: Gemini timestamped transcription (faster-whisper fallback) → Trarium translators → Gemini TTS (edge-tts fallback) → atempo time-fit → ducked mix → MP4 + audio + SRT.
- New «📝 زیرنویس» tab: speech → original-language subtitles, optional translation and bilingual mode; SRT/VTT files and/or burned-in subtitles (libass, Vazirmatn/Sahel, correct Persian shaping and RTL, size/position/margin/box).
- `trarium/media/` package with a multi-key Gemini client (key 1 until quota, then key 2), unit tests in `tests/test_media.py`. Vazirmatn Bold (OFL) bundled.
- `tests/test_deploy_gate.py` now expects 13 tabs.

## 1.2 (README) — games / visual novels

**Games / visual novels**
- GameMaker Studio 1/2 (data.win, game.unx, game.ios, game.droid, APK `assets/game.droid`): player text in STRG translated by append-and-repoint, Persian glyphs added to bitmap fonts (PNG texture pages). GameMaker 8 exe route (experimental).
- RPG Maker XP/VX/VX Ace via a pure-Python Ruby Marshal reader/writer.
- Generic fallback for unknown engines: text files found by content, one short AI classification call, placeholders locked, `.mo` rebuilt, unknown binaries never modified.
- The game tab accepts any file type, including ISO images (7z or pycdlib) and nested archives; output is always a patch zip.
- Upload limit raised to 2 GB (unpacked limit 6 GB, env-configurable); uploads are streamed to disk and inputs hard-linked instead of copied.

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
