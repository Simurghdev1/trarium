"""Full feature run of Trarium main (v2.19) in GitHub Actions.
Keys come only from env and are never printed. Outputs are Trarium's own files, untouched."""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = REPO / "ci_out" / "main"; OUT.mkdir(parents=True, exist_ok=True)
INP = REPO / "ci_run" / "inputs"
report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}

def save():
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

def step(name):
    def deco(fn):
        t = time.time()
        try:
            res = fn()
            report[name] = {"ok": True, "seconds": round(time.time() - t, 1), **(res or {})}
        except Exception as exc:
            report[name] = {"ok": False, "seconds": round(time.time() - t, 1), "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        print(f"== {name}: {json.dumps(report[name], ensure_ascii=False)[:600]}", flush=True)
        save()
        return fn
    return deco

PATTERNS = r"sk-or-v1-[0-9a-f]{20,}|AIza[0-9A-Za-z_-]{30,}|AQ\.Ab8[0-9A-Za-z_-]{20,}|gsk_[0-9A-Za-z]{20,}|ghp_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{30,}|K8[0-9]{13,}"

@step("01_key_leak_scan")
def _():
    out = subprocess.run(["git", "grep", "-nIE", PATTERNS, "origin/main", "--", ".", ":!*.venv*"], cwd=REPO, capture_output=True, text=True).stdout
    secrets = [os.environ.get(k, "") for k in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "OCR_SPACE_KEY")]
    hist = 0
    for s in secrets:
        if s:
            r = subprocess.run(["git", "log", "--all", "--format=%h", "-S", s], cwd=REPO, capture_output=True, text=True).stdout.split()
            hist += len(r)
    return {"pattern_hits_on_main": len([l for l in out.splitlines() if l.strip()]), "our_keys_in_any_commit": hist}

from trarium.short_prompt import build_short_prompt
SHORT = build_short_prompt(["ادبی و شاعرانه ننویس"])
report["short_prompt"] = SHORT
CHAIN = [{"provider": "openrouter", "model": "google/gemini-2.5-flash-lite"},
         {"provider": "gemini", "model": "gemini-flash-lite-latest"},
         {"provider": "gemini", "model": "gemini-2.5-flash-lite"}]
BASE = {"provider_chain": CHAIN, "short_prompt": SHORT, "use_cache": True, "use_checkpoint": True,
        "workers": 3, "chunk_size": 8000, "failover": {"retries": 2}}

from trarium.jobs import get_manager
mgr = get_manager()
jobs: dict[str, str] = {}

def submit(kind, title, cfg, src: Path | None = None):
    data = src.read_bytes() if src else None
    jid = mgr.submit(kind, title, config={**BASE, **cfg}, source_bytes=data, source_name=src.name if src else "", target_lang="fa",
                     provider="openrouter", model="google/gemini-2.5-flash-lite")
    jobs[title] = jid
    return jid

@step("02_repair")
def _():
    from trarium.repair import analyze_book, repair_pdf, quality_gate
    res = {}
    d = OUT / "repair"; d.mkdir(exist_ok=True)
    for f in [INP / "Undine_Trarium_Test_6_Pages.pdf", INP / "Josep Moore - Mangas ENG.pdf"]:
        rep = analyze_book(f)
        rr = repair_pdf(f, d / f"{f.stem}_repaired.pdf", rep, drop_duplicates=True, fix_order=True)
        res[f.name] = {"summary": rep.summary_fa(), "issues": len(rep.issues), "pages_before": rr.pages_before,
                       "pages_after": rr.pages_after, "removed": rr.removed, "reordered": rr.reordered, "log": rr.log,
                       "quality_gate": quality_gate(f)}
    (d / "repair_report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"files": list(res)}

# --- YouTube subtitles: fetch English subs, translate with the subtitle handler
def fetch_youtube_srt(url: str, dest: Path) -> str:
    tries = []
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--write-auto-subs",
                            "--sub-langs", "en.*,en", "--sub-format", "vtt", "--convert-subs", "srt",
                            "-o", str(dest.parent / "yt.%(ext)s"), url], capture_output=True, text=True, timeout=180)
        tries.append(("yt-dlp", r.returncode, (r.stderr or "")[-300:]))
        cands = sorted(dest.parent.glob("yt*.srt"), key=lambda p: ("orig" in p.name, len(p.name)))
        if cands:
            shutil.copy(cands[0], dest); return "yt-dlp:" + cands[0].name
    except Exception as exc:
        tries.append(("yt-dlp", str(exc)[:200]))
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        vid = re.search(r"v=([\w-]{11})", url).group(1)
        t = YouTubeTranscriptApi().fetch(vid, languages=["en", "en-US", "en-GB"])
        def ts(x):
            ms = int(round(x * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
            return f"{h:02}:{m:02}:{s:02},{ms:03}"
        lines = []
        for i, sn in enumerate(t.snippets, 1):
            lines += [str(i), f"{ts(sn.start)} --> {ts(sn.start + sn.duration)}", sn.text.replace("\n", " "), ""]
        dest.write_text("\n".join(lines), encoding="utf-8"); return "youtube-transcript-api"
    except Exception as exc:
        tries.append(("transcript-api", f"{type(exc).__name__}: {str(exc)[:200]}"))
    fallback = INP / "youtube_5XLEjBlSaAY_en.srt"
    if fallback.exists():
        shutil.copy(fallback, dest); return "committed-fallback"
    raise RuntimeError(f"YouTube blocked: {tries}")

NOVEL_CANDIDATES = [
    "https://www.royalroad.com/fiction/21220/mother-of-learning/chapter/301778/1-good-morning-brother",
    "https://wanderinginn.com/2016/07/27/1-00/",
    "https://www.royalroad.com/fiction/25137/worth-the-candle/chapter/366248/taking-the-fall",
]
YT = OUT / "youtube"; YT.mkdir(exist_ok=True)
@step("03_youtube_fetch")
def _():
    how = fetch_youtube_srt("https://www.youtube.com/watch?v=5XLEjBlSaAY", YT / "5XLEjBlSaAY_en.srt")
    return {"method": how, "cues": (YT / "5XLEjBlSaAY_en.srt").read_text(encoding="utf-8").count(" --> ")}

@step("04_submit_jobs")
def _():
    if (YT / "5XLEjBlSaAY_en.srt").exists():
        submit("book", "youtube_5XLEjBlSaAY", {"output_format": "srt"}, YT / "5XLEjBlSaAY_en.srt")
    submit("pdf", "Undine_Trarium_Test_6_Pages", {}, INP / "Undine_Trarium_Test_6_Pages.pdf")
    submit("pdf", "Josep_Moore_Mangas", {"enable_ocr": True, "ocr_method": "openrouter",
           "ocr_api_key": os.environ.get("OPENROUTER_API_KEY", ""), "ocr_vision_model": "google/gemini-2.5-flash-lite"},
           INP / "Josep Moore - Mangas ENG.pdf")
    submit("book", "Omniscient_Readers_Viewpoint_2", {"output_format": "epub", "chunk_size": 12000, "workers": 4},
           INP / "_OceanofPDF.com_Omniscient_readers_viewpoint_novel_2_-_singNsong.epub")
    from trarium.novel.scraper import NovelScraper
    novel_url, tried = "", []
    for cand in NOVEL_CANDIDATES:
        try:
            ch = NovelScraper(use_browser=False, delay=0.3, max_chapters=1).scrape_chain(cand, 1)
            if ch and len(ch[0].text) > 500:
                novel_url = cand; break
            tried.append((cand, "empty"))
        except Exception as exc:
            tried.append((cand, str(exc)[:150]))
    report["novel_pick"] = {"url": novel_url, "tried": tried}
    submit("novel", "web_novel_5_chapters", {"url": novel_url, "chapter_count": 5, "output_format": "epub"})
    # manga enhance on 4 pages of the manga PDF
    import pymupdf
    z = OUT / "manga_pages.zip"
    with pymupdf.open(str(INP / "Josep Moore - Mangas ENG.pdf")) as d, zipfile.ZipFile(z, "w") as zf:
        for i in range(min(4, len(d))):
            zf.writestr(f"p{i+1:02}.png", d[i].get_pixmap(dpi=90).tobytes("png"))
    submit("manga", "manga_enhance_4_pages", {"manga": {"scale": "auto", "auto": True}}, z)
    return {"jobs": jobs}

@step("05_wait_jobs")
def _():
    deadline = time.time() + float(os.environ.get("JOB_TIMEOUT", 4 * 3600))
    while time.time() < deadline:
        st = {t: (mgr.store.get(j) or {}).get("status") for t, j in jobs.items()}
        if all(s in ("completed", "failed", "cancelled") for s in st.values()):
            break
        time.sleep(15)
        print("   ", json.dumps(st, ensure_ascii=False), flush=True)
    res = {}
    dl = OUT / "outputs"; dl.mkdir(exist_ok=True)
    for t, j in jobs.items():
        job = mgr.store.get(j) or {}
        info = {k: job.get(k) for k in ("status", "error", "stage", "total_units", "completed_units", "failed_units",
                                        "api_requests", "input_tokens", "output_tokens", "cost_usd", "result_name")}
        p = job.get("result_path")
        if p and Path(p).exists():
            shutil.copy(p, dl / Path(p).name); info["output_file"] = Path(p).name; info["bytes"] = Path(p).stat().st_size
        try:
            info["events"] = [e.get("message") for e in mgr.store.events(j)][-8:]
        except Exception:
            pass
        res[t] = info
    return {"jobs": res}

@step("06_library_playlist")
def _():
    from trarium.library import Library
    lib = Library(OUT / "library_root")
    added = []
    for f in sorted((OUT / "outputs").iterdir()) + [INP / "Undine_Trarium_Test_6_Pages.pdf"]:
        b = lib.add_book(f, f.name, category="تست اکشنز"); added.append(b["id"])
    again = lib.add_book(INP / "Undine_Trarium_Test_6_Pages.pdf", "Undine_Trarium_Test_6_Pages.pdf")
    pl = lib.create_playlist("ترجمه‌های فارسی"); 
    for bid in added: lib.add_to_playlist(pl["id"], bid)
    lib.set_status(added[0], "completed")
    lib.update_book(added[0], title="عنوان ویرایش‌شده")
    search = lib.list_books(search="Undine")
    plv = lib.playlist(pl["id"])
    lib.remove_from_playlist(pl["id"], added[-1])
    after_remove = lib.playlist(pl["id"])
    return {"added": len(added), "dedup_same_id": again["id"] in added, "search_undine": len(search),
            "playlist_count": (plv or {}).get("count"), "playlist_after_remove": (after_remove or {}).get("count"),
            "categories": lib.categories(), "stats": lib.stats(), "playlists": [p["name"] for p in lib.list_playlists()]}

try:
    mgr.shutdown(5)
except Exception:
    pass
report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()); save()
