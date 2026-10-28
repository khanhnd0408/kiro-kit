"""The floating glass bar: blur, growing and shrinking with the command, nothing hidden behind it, no split options.

Run through run.py, which builds the page and sets CREWCTL_MAP_URL and CREWCTL_MAP_SHOTS."""
import os
from playwright.sync_api import sync_playwright
URL = os.environ["CREWCTL_MAP_URL"]
results = []
def report(name, ok, extra=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        pg = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme).new_page()
        errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL + "#at=c:workspace create"); pg.wait_for_timeout(700)
        bar = pg.locator("#bar")
        style = pg.eval_on_selector("#bar", "e => { const s = getComputedStyle(e); return [s.position, s.backdropFilter || s.webkitBackdropFilter, s.borderRadius]; }")
        report(f"{scheme}: floating glass", style[0] == "fixed" and "blur" in style[1] and style[2] == "22px", str(style))
        h1 = bar.bounding_box()["height"]
        pg.locator('input[id="in-c:workspace create|name"]').fill("customer-analytics-platform-production-eu-west-1")
        pg.locator('input[id="in-c:workspace create|dir"]').fill("workspace-customer-analytics-platform-production-eu-west-1-shared-reports")
        pg.locator('input[id="in-c:workspace create|copy_from"]').fill("customer-analytics-platform-staging-eu-west-1")
        pg.locator('[data-id="c:workspace create|yes"] .toggle').click()
        pg.wait_for_timeout(450)
        h2 = bar.bounding_box()["height"]
        box = pg.eval_on_selector("#cmd", "e => [e.scrollWidth, e.clientWidth, e.scrollHeight, e.clientHeight]")
        report(f"{scheme}: long command wraps, no sideways scroll", box[0] <= box[1] + 1, str(box))
        report(f"{scheme}: the panel grows", h2 > h1 + 15, f"{h1:.0f}px -> {h2:.0f}px")
        bb = bar.bounding_box()
        nodes = pg.eval_on_selector_all(".node:not(.hidden)", "els => els.map(e => e.getBoundingClientRect().bottom)")
        report(f"{scheme}: no node behind the panel", max(nodes) <= bb["y"] - 4, f"lowest node {max(nodes):.0f}, panel top {bb['y']:.0f}")
        pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + f"/9-glass-long-{scheme}.png")
        pg.locator('input[id="in-c:workspace create|dir"]').fill(""); pg.locator('input[id="in-c:workspace create|copy_from"]').fill("")
        pg.wait_for_timeout(450)
        report(f"{scheme}: the panel shrinks back", bar.bounding_box()["height"] < h2 - 15)
        pg.goto(URL + "#at=c:usage export&v.since=7d"); pg.wait_for_timeout(600)
        pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + f"/10-glass-short-{scheme}.png")
        report(f"{scheme}: no JavaScript errors", not errors, "; ".join(errors))
    b.close()

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light").new_page()
    pg.goto(URL + "#at=c:workspace create"); pg.wait_for_timeout(600)
    pg.locator('input[id="in-c:workspace create|name"]').fill("customer-analytics-platform-production-eu-west-1")
    pg.locator('input[id="in-c:workspace create|dir"]').fill("workspace-customer-analytics-platform-production-eu-west-1-shared-reports")
    pg.locator('input[id="in-c:workspace create|copy_from"]').fill("customer-analytics-platform-staging-eu-west-1")
    pg.wait_for_timeout(400)
    split = pg.eval_on_selector_all("#cmd span.o", "els => els.filter(e => e.getClientRects().length > 1).map(e => e.textContent)")
    report("options never split across lines", not split, str(split))
    pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/9-glass-long-light.png")
    pg.goto(URL + "#at=c:usage export&v.since=7d"); pg.wait_for_timeout(600)
    report("a link pasted into the open tab is followed", pg.locator("#cmd").inner_text().strip() == "crewctl usage export --since 7d",
           pg.locator("#cmd").inner_text())
    pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/10-glass-short-light.png")
    b.close()

print(f"{sum(results)}/{len(results)} checks passed")
