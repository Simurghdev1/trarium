"""ترجمه‌گر بدون کلید برای وقتی که کاربر کلید ترجمه ندارد.

دو موتور رایگان، به‌ترتیب:

1. نقطهٔ پایانی عمومی Google Translate (همان که افزونه‌های مرورگر استفاده
   می‌کنند)؛ کیفیتش از حافظهٔ ترجمه بهتر است و سقف نویسهٔ بالاتری دارد.
2. MyMemory، که سهمیهٔ رایگان روزانه دارد و چند صد درخواست را رد می‌کند؛ اگر
   «سهمیه تمام شد» بدهد تا پایان اجرا کنار گذاشته می‌شود.

نکات مهم پیاده‌سازی:

* پرامپت دسته‌ای موتور (`[TRARIUM_BLOCK_n]`) اینجا خودش تجزیه می‌شود؛ به API
  فقط متن خام هر بلاک می‌رود، نه دستورهای انگلیسی موتور.
* توکن‌های `__TRARIUM_PH_####__` هرگز به API فرستاده نمی‌شوند (تکه‌تکه دورشان
  ترجمه می‌شود) تا شماره صفحه/سال/نام خراب نشود.
* در خطای شبکه/۴۲۹، عقب‌نشینی نمایی و سپس شکستن تکه به دو نیم؛ اگر بازهم نشد
  متن اصلی برگردانده می‌شود تا بلاک «ترجمه‌نشده» بماند، نه خراب.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from trarium.translators.base import BaseTranslator

GOOGLE_URL = "https://clients5.google.com/translate_a/t"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Trarium/2.19"
MARKER_RE = re.compile(r"\[TRARIUM_BLOCK_(\d+)\]", re.I)
PLACEHOLDER_RE = re.compile(r"(__TRARIUM_PH_\d+__)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:؛])\s+")
# Scanned headings are often letter-spaced ("C H A P T E R VI").  Sent to the
# API as-is, "VI" came back as the wrong numeral; joined back it reads correctly.
SPACED_CAPS_RE = re.compile(r"(?:\b[A-Z]\s){2,}[A-Z]\b")
# Old books use -eth verbs the engines leave untranslated (a heading came back
# as "TELLETH از یک عروسی").  Fixing the handful of common forms before the
# request is safer than stripping "eth" from every word (death, teeth, earth).
ARCHAIC = {
    "telleth": "tells", "sayeth": "says", "saith": "says", "speaketh": "speaks",
    "cometh": "comes", "maketh": "makes", "taketh": "takes", "giveth": "gives",
    "seeth": "sees", "knoweth": "knows", "doeth": "does", "goeth": "goes",
    "bringeth": "brings", "asketh": "asks", "calleth": "calls", "thinketh": "thinks",
    "findeth": "finds", "keepeth": "keeps", "putteth": "puts", "showeth": "shows",
    "beareth": "bears", "becometh": "becomes", "looketh": "looks", "dwelleth": "dwells",
}
ARCHAIC_RE = re.compile(r"\b(" + "|".join(ARCHAIC) + r")\b", re.I)
def _caps_with_l(word: str) -> bool:
    """True for words like ``ClTY``: caps around stray lowercase l glyphs."""
    letters = [c for c in word if c.isalpha()]
    if len(letters) < 3:
        return False
    lowercase = [c for c in letters if c.islower()]
    if not lowercase or any(c != "l" for c in lowercase):
        return False
    return len(letters) - len(lowercase) > len(lowercase)


QUOTA_MARKERS = (
    "MYMEMORY WARNING",
    "QUERY LENGTH LIMIT",
    "INVALID LANGUAGE PAIR",
    "PLEASE SELECT TWO DISTINCT LANGUAGES",
)
PROMPT_NOISE_RE = re.compile(
    r"^(Translate (every marked block|this literary text)|Output only (translated blocks|the translation)|"
    r"Keep \[TRARIUM_BLOCK_n\]|Preserve (every )?__TRARIUM_PH|Do not summarize|Style:|Target language:)",
    re.I,
)


class FreeWebTranslator(BaseTranslator):
    """ترجمهٔ واقعی، بدون کلید: Google عمومی و بعد MyMemory."""

    provider = "freeweb"
    model = "google-web+mymemory"

    def __init__(self, source_lang: str = "en", timeout: float = 30.0,
                 min_interval: float = 0.25, max_chars: int = 1800):
        self.source_lang = source_lang
        self.timeout = timeout
        self.min_interval = max(0.0, min_interval)
        self.max_chars = max(200, min(4000, max_chars))
        self.last_error = ""
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._memo: dict[tuple[str, str], str] = {}
        self._dead: set[str] = set()

    # ------------------------------------------------------------------ API
    def get_token_limit(self) -> int:
        return 4000

    def translate(self, text: str, target_lang: str = "fa") -> str:
        text = str(text or "")
        blocks = list(MARKER_RE.finditer(text))
        if blocks:
            pieces = []
            for i, match in enumerate(blocks):
                start = match.end()
                end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
                body = text[start:end].strip()
                pieces.append(f"[TRARIUM_BLOCK_{match.group(1)}]\n{self._translate_text(body, target_lang)}")
            return "\n\n".join(pieces)
        return self._translate_text(self._strip_prompt(text), target_lang)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _strip_prompt(text: str) -> str:
        lines = [line for line in text.splitlines() if not PROMPT_NOISE_RE.match(line.strip())]
        return "\n".join(lines).strip()

    def _translate_text(self, text: str, target_lang: str) -> str:
        """متن را دور توکن‌های محافظ‌شده تکه‌تکه ترجمه می‌کند."""
        if not text.strip():
            return text
        out: list[str] = []
        for part in PLACEHOLDER_RE.split(text):
            if not part:
                continue
            if PLACEHOLDER_RE.fullmatch(part) or not part.strip():
                out.append(part)
                continue
            if self._looks_like_listing(part):
                translated = [self._translate_plain(line, target_lang)
                              for line in part.splitlines() if line.strip()]
                out.append("\n".join(translated) if translated else part)
            else:
                joined = re.sub(r"\s*\n\s*", " ", part).strip()
                out.append(self._translate_plain(joined, target_lang))
        return "".join(out)

    @staticmethod
    def _looks_like_listing(text: str) -> bool:
        """فهرست/سرفصل (خطوط کوتاه) باید خط‌به‌خط ترجمه شود، نه یک‌جا."""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        return sum(len(ln) for ln in lines) / len(lines) < 45

    @staticmethod
    def _despace_caps(text: str) -> str:
        return SPACED_CAPS_RE.sub(lambda m: re.sub(r"\s+", "", m.group(0)), text)

    @staticmethod
    def _normalize_print_artifacts(text: str) -> str:
        """Undo scan artefacts that make a translation look broken.

        Printed page numbers arrive letter-spaced (``1 2`` for twelve) and
        chapter numbers as roman numerals at the start of a line; both were
        rendered as stray Latin fragments inside the Persian text.
        """
        text = re.sub(r"(?<=\d)[ \t]+(?=\d)", "", text)
        # OCR reads capital I as lowercase l in caps-only words ("ClTY",
        # "RlNGSTETTEN").  The engine then leaves them untranslated because they
        # are not real words; restoring the capital I fixes the whole word.
        text = re.sub(r"\b[A-Z][A-Za-z]{2,}\b",
                      lambda m: m.group(0).replace("l", "I") if _caps_with_l(m.group(0)) else m.group(0),
                      text)
        roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
                 "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
                 "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18,
                 "XIX": 19, "XX": 20}

        def convert(match: re.Match) -> str:
            value = roman.get(match.group(1).upper())
            return f"{value}." if value else match.group(0)

        return re.sub(r"^\s*([IVXLCDM]{1,5})\s*\.", convert, text, flags=re.M)

    @staticmethod
    def _modernize_archaic(text: str) -> str:
        def replace(match: re.Match) -> str:
            word = match.group(0)
            modern = ARCHAIC[word.lower()]
            if word.isupper():
                return modern.upper()
            return modern.capitalize() if word[:1].isupper() else modern
        return ARCHAIC_RE.sub(replace, text)

    def _translate_plain(self, text: str, target_lang: str) -> str:
        text = self._normalize_print_artifacts(self._modernize_archaic(self._despace_caps(text)))
        key = (text, target_lang)
        if key in self._memo:
            return self._memo[key]
        value = self._translate_chunked(text, target_lang)
        if value and value.strip():
            self._memo[key] = value
        return value or text

    def _translate_chunked(self, text: str, target_lang: str) -> str:
        results = []
        for chunk in self._split_chunks(text):
            piece = self._request(chunk, target_lang)
            if piece is None:
                return text  # بلاک ترجمه‌نشده بماند، بهتر از خرابی
            results.append(piece)
        return self._fix_leftover_latin(text, " ".join(results).strip(), target_lang)

    def _fix_leftover_latin(self, source: str, value: str, target_lang: str) -> str:
        """Translate the Latin words an engine leaves untouched mid-sentence.

        "چگونه آنها در CITY آمدند" is what complete translation should not look
        like.  Only words that already existed in the source are retried, one at
        a time, so nothing is invented and a cached word costs one request.
        """
        if not value:
            return value
        source_words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’]{2,}", source)}
        for word in sorted(set(re.findall(r"[A-Za-z][A-Za-z'’]{2,}", value))):
            if word.lower() not in source_words:
                continue
            piece = self._request(word, target_lang)
            if not piece or re.search(r"[A-Za-z]{3,}", piece):
                continue  # موتور همین واژه را برنگرداند؛ متن اصلی می‌ماند
            value = re.sub(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", piece, value)
        return value

    def _split_chunks(self, text: str) -> list[str]:
        if len(text) <= self.max_chars:
            return [text]
        chunks, current = [], ""
        for sentence in SENTENCE_SPLIT_RE.split(text):
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= self.max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            while len(sentence) > self.max_chars:  # جملهٔ بی‌نقطهٔ طولانی
                cut = sentence.rfind(" ", 0, self.max_chars)
                cut = cut if cut > 40 else self.max_chars
                chunks.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            current = sentence
        if current:
            chunks.append(current)
        return [c for c in chunks if c.strip()]

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    # ------------------------------------------------------------- engines
    def _request(self, text: str, target_lang: str) -> str | None:
        """اول Google؛ اگر نبود MyMemory. شکست هر دو یعنی بلاک ترجمه‌نشده."""
        for engine in ("google", "mymemory"):
            if engine in self._dead:
                continue
            value = self._google_request(text, target_lang) if engine == "google" \
                else self._mymemory_request(text, target_lang)
            if value:
                return value
        return None

    def _google_request(self, text: str, target_lang: str) -> str | None:
        params = urllib.parse.urlencode({"client": "dict-chrome-ex", "sl": self.source_lang,
                                         "tl": target_lang, "q": text})
        for attempt in range(3):
            self._throttle()
            request = urllib.request.Request(f"{GOOGLE_URL}?{params}",
                                             headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", "replace")
                data = json.loads(raw)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self.last_error = f"google: {exc}"
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 403}:
                    self._dead.add("google")
                    return None
                time.sleep(min(6.0, 1.0 * (2 ** attempt)))
                continue
            value = self._flatten_google(data)
            if value:
                return html.unescape(value)
            self.last_error = "google: پاسخ خالی"
            time.sleep(min(6.0, 1.0 * (2 ** attempt)))
        return None

    @staticmethod
    def _flatten_google(payload) -> str:
        """google returns ``["x"]``, ``[["x"]]`` or one list per sentence."""
        if isinstance(payload, str):
            return payload.strip()
        if not isinstance(payload, list):
            return ""
        pieces = []
        for item in payload:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, list) and item:
                first = item[0]
                if isinstance(first, str):
                    pieces.append(first)
                elif isinstance(first, list) and first and isinstance(first[0], str):
                    pieces.append(first[0])
        return "".join(pieces).strip()

    def _mymemory_request(self, text: str, target_lang: str) -> str | None:
        params = urllib.parse.urlencode({"q": text.replace("\n", " "),
                                         "langpair": f"{self.source_lang}|{target_lang}"})
        for attempt in range(2):
            self._throttle()
            request = urllib.request.Request(f"{MYMEMORY_URL}?{params}",
                                             headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self.last_error = f"mymemory: {exc}"
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    self._dead.add("mymemory")  # سهمیهٔ روزانهٔ همین IP
                    return None
                time.sleep(min(6.0, 1.0 * (2 ** attempt)))
                continue
            data = payload.get("responseData") or {}
            value = html.unescape(str(data.get("translatedText") or "")).strip()
            upper = value.upper()
            if any(marker in upper for marker in QUOTA_MARKERS) or payload.get("quotaFinished"):
                # سهمیهٔ این IP تمام شده؛ تلاش‌های بعدی فقط وقت تلف می‌کند.
                self._dead.add("mymemory")
                self.last_error = value[:200]
                return None
            if value and int(payload.get("responseStatus") or 200) < 400:
                return value
            self.last_error = f"mymemory: {value or payload.get('responseStatus')}"
            time.sleep(min(4.0, 1.0 * (2 ** attempt)))
        return None
