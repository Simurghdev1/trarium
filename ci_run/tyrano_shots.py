"""CI: play a TyranoScript game in headless Chromium and screenshot it (title, then clicks through text and choices)."""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

url, out, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
    page = b.new_page(viewport={"width": 1280, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    time.sleep(6)
    page.screenshot(path=str(out / "shot_01.png"))
    page.mouse.click(260, 260)          # دکمهٔ شروع در صفحهٔ عنوان
    time.sleep(3)
    for i in range(2, n + 1):
        glinks = page.locator(".glink_button")
        if glinks.count():
            page.screenshot(path=str(out / f"shot_{i:02d}.png"))
            glinks.first.click()
        else:
            time.sleep(2.5)                 # تا متن کامل تایپ شود
            page.screenshot(path=str(out / f"shot_{i:02d}.png"))
            page.mouse.click(640, 620)
        time.sleep(1.2)
    (out / "page_errors.txt").write_text("\n".join(errors), encoding="utf-8")
    b.close()
