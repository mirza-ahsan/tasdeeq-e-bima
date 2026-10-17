#!/usr/bin/env python3
"""Capture the product screenshots used by the README and the deck.

    uv run --with playwright python scripts/screenshots.py [--url http://localhost:3000]

Needs the app running (API + web) and a Chromium; pass --chromium to point at one, or
let Playwright use its own. Writes docs/screenshots/*.png. Every screen is the real app
driving the scripted demo claims, so the images show what the demo actually does.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
VIEW = {"width": 1440, "height": 900}


async def run_demo(page, label: str) -> None:
    """Start a demo from the landing page and let autoplay finish it."""
    await page.get_by_role("button", name=label).click()
    await page.get_by_text("What moved the estimate").wait_for(timeout=60_000)
    await page.wait_for_timeout(1200)   # let the bars finish growing


async def main(url: str, chromium: str | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chromium, args=["--no-sandbox"])

        async def page(scheme: str = "light"):
            ctx = await browser.new_context(viewport=VIEW, color_scheme=scheme,
                                            device_scale_factor=1)
            return await ctx.new_page()

        landing = await page()
        await landing.goto(url)
        await landing.wait_for_timeout(5000)            # the specimen replays its path
        await landing.screenshot(path=OUT / "landing.png")

        check = await page()
        await check.goto(url)
        await check.get_by_role("button", name="Risky example").click()
        await check.get_by_role("heading", name="This claim needs pre-authorisation").wait_for()
        await check.get_by_role("button", name="Pause demo").click()
        await check.get_by_role("button", name="No, not obtained").click()
        await check.get_by_role("status").filter(has_text="raised the estimate by").wait_for()
        await check.wait_for_timeout(600)
        await check.screenshot(path=OUT / "check.png")

        risky = await page()
        await risky.goto(url)
        await run_demo(risky, "Risky example")
        await risky.screenshot(path=OUT / "result-risky.png")

        clean = await page("dark")
        await clean.goto(url)
        await run_demo(clean, "Clean example")
        await clean.screenshot(path=OUT / "result-clean-dark.png")

        await browser.close()
    for f in sorted(OUT.glob("*.png")):
        print(f"  {f.relative_to(OUT.parent.parent)}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:3000")
    ap.add_argument("--chromium", default=shutil.which("chromium"))
    args = ap.parse_args()
    asyncio.run(main(args.url, args.chromium))
