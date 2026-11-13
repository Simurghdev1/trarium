# QA harness

These tests exercise the real code paths without a third-party API key:

- `mock_provider.py` is a local HTTP server that speaks the OpenAI chat-completions
  protocol, counts tokens with `tiktoken`, and can simulate transient failures
  (HTTP 500), rate limits, empty answers and OpenRouter WAF `403` responses.
- `qa_suite.py` drives the real translators, the translation pipeline, the PDF
  layout writer, the text/PDF/EPUB exporters and the vision-OCR path.

Run locally:

```bash
pip install -r requirements.txt pytest requests tiktoken
python -m pytest qa/qa_suite.py -q
```

Point the suite at another checkout:

```bash
TRARIUM_TARGET=/path/to/checkout QA_TMP=/tmp/qa python -m pytest qa/qa_suite.py -q
```

Measurements are written to `qa/qa_results_<label>.json` and printed as
`QA_SUMMARY_JSON:` in the log, so CI keeps a machine-readable record.

Notes:

- `TRARIUM_OPENROUTER_HTTP=requests` keeps the transport deterministic.
- `openrouter_models.json` is a snapshot of the public OpenRouter catalog used to
  verify that the model slugs shipped in the UI still exist. Set
  `QA_STRICT_SLUGS=1` to make a drift a hard failure (CI keeps it informational).
