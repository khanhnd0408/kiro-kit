"""The map: jobs, focus, typing, switches, choices, hints, copy, theme, search, global options, deep links, reset, small windows.

Run through run.py, which builds the page and sets CREWCTL_MAP_URL and CREWCTL_MAP_SHOTS."""
import os
import sys
from playwright.sync_api import sync_playwright

URL = os.environ["CREWCTL_MAP_URL"]
errors, checks = [], []
def check(name, ok, detail=""):
    checks.append((name, ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL); page.wait_for_timeout(600)
    cmd = lambda: page.locator("#cmd").inner_text().replace("$ ", "").strip()
    orb = lambda label: page.locator(f'.node:not(.hidden) .orb[aria-label="{label}"]')
    page.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/1-root-light.png")
    check("root shows the three jobs", all(orb(n).count() == 1 for n in ("Crew", "Review", "Usage")))

    orb("Usage").click(); page.wait_for_timeout(550)
    check("breadcrumb follows", page.locator("#crumbs").inner_text().replace("\n", " ").strip() == "crewctl › Usage")
    orb("export").click(); page.wait_for_timeout(550)
    since = page.locator('.node:not(.hidden) input[id="in-c:usage export|since"]')
    since.fill("7d")
    page.locator('.node:not(.hidden) input[id="in-c:usage export|output"]').fill("my week.html")
    check("typing builds the command", cmd() == "crewctl usage export --since 7d --output 'my week.html'", cmd())
    check("impact badge shown", "WRITES ONE REPORT FILE" in page.locator("#impact").inner_text())
    page.locator('.node:not(.hidden)[data-id="c:usage export|since"] .hint-btn').click(); page.wait_for_timeout(200)
    pop = page.locator("#popover")
    check("? opens the hint", pop.is_visible() and "last N hours or days" in pop.inner_text(), pop.inner_text()[:80])
    page.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/2-export-hint-light.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(150)
    check("Esc closes the hint", not pop.is_visible())

    page.locator("#copy").click(); page.wait_for_timeout(200)
    copied = page.evaluate("navigator.clipboard.readText()")
    check("Copy puts the exact line on the clipboard", copied == "crewctl usage export --since 7d --output 'my week.html'", copied)

    page.locator('[data-theme-set="dark"]').click(); page.wait_for_timeout(300)
    check("dark theme applies", page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(13, 13, 13)")
    page.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/3-export-dark.png")

    page.locator("#q").click(); page.keyboard.type("move crewmates workspace"); page.wait_for_timeout(150)
    first = page.locator("#results button").first.inner_text()
    check("search finds workspace assign", "workspace assign" in first, first)
    page.keyboard.press("Enter"); page.wait_for_timeout(550)
    check("required value shows as missing", "<WORKSPACE>" in cmd() and "Still to fill in" in page.locator("#warn").inner_text(), cmd())
    page.locator('.node:not(.hidden)[data-id="c:workspace assign|all"] .toggle').click()
    page.locator('.node:not(.hidden) input[id="in-c:workspace assign|workspace_name"]').fill("ops")
    check("switch + required value", cmd() == "crewctl workspace assign ops --all", cmd())
    check("warning clears when filled", page.locator("#warn").inner_text().strip() == "")
    page.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/4-assign-dark.png")

    page.locator('.node:not(.hidden) .orb[aria-label="global options"]').click(); page.wait_for_timeout(550)
    page.locator('.node:not(.hidden) input[id="in-c:workspace assign|@globals|config_dir"]').fill("~/crew")
    page.locator('.node:not(.hidden)[data-id="c:workspace assign|@globals|verbose"] .toggle').click()
    check("global options go before the command", cmd() == "crewctl --config-dir '~/crew' --verbose workspace assign ops --all", cmd())
    page.keyboard.press("Escape"); page.wait_for_timeout(550)
    check("Esc goes back to the command", page.locator("#crumbs [aria-current]").inner_text() == "assign")

    page.locator('[data-theme-set="light"]').click()
    page.locator("#q").click(); page.keyboard.type("schedule cron timer"); page.wait_for_timeout(150); page.keyboard.press("Enter"); page.wait_for_timeout(550)
    page.locator('.node:not(.hidden)[data-id="c:usage schedule|scheduler"] [data-choice="systemd"]').click()
    page.locator('.node:not(.hidden)[data-id="c:usage schedule|report"] .toggle').click()
    check("choice + switch", cmd() == "crewctl --config-dir '~/crew' --verbose usage schedule systemd --report", cmd())
    page.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/5-schedule-light.png")

    url = page.url
    page2 = ctx.new_page(); page2.goto(url); page2.wait_for_timeout(600)
    check("a deep link restores the view and the values", page2.locator("#cmd").inner_text().replace("$ ", "").strip() == cmd())
    page.locator("#reset").click(); page.wait_for_timeout(550)
    check("Reset clears everything", cmd().startswith("crewctl") and "--" not in cmd() and page.locator("#crumbs").inner_text().strip() == "crewctl")

    narrow = browser.new_context(viewport={"width": 900, "height": 700}, color_scheme="dark").new_page()
    narrow.goto(URL + "#at=c:usage push"); narrow.wait_for_timeout(600)
    narrow.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/6-push-small-dark-auto.png")
    boxes = narrow.eval_on_selector_all(".node:not(.hidden)", "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })")
    vw, vh = 900, 700
    inside = all(b[0] >= -2 and b[2] <= vw + 2 for b in boxes)
    overlaps = sum(1 for i, a in enumerate(boxes) for b in boxes[i + 1:] if a[0] < b[2] - 4 and b[0] < a[2] - 4 and a[1] < b[3] - 4 and b[1] < a[3] - 4)
    check("small window: nodes stay on screen", inside, str(boxes))
    check("small window: no overlapping nodes", overlaps == 0, f"{overlaps} overlaps")
    browser.close()

check("no JavaScript errors", not errors, "; ".join(errors[:3]))
print(f"{sum(ok for _, ok in checks)}/{len(checks)} checks passed")

# extra checks for the fixes
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    pg.goto(URL + "#at=c:usage export&theme=light"); pg.wait_for_timeout(600)
    radius = pg.eval_on_selector('.node.focus .orb', "e => getComputedStyle(e).borderRadius")
    print(("PASS" if radius == "50%" else "FAIL") + " command node is round", radius)
    badge = pg.locator('.node.focus .badge'); bb = badge.bounding_box(); ob = pg.locator('.node.focus .orb').bounding_box()
    print(("PASS" if bb["x"] >= ob["x"] and bb["x"] + bb["width"] <= ob["x"] + ob["width"] else "FAIL") + " badge fits inside the circle", badge.inner_text())
    pg.locator('[data-id="c:usage export|since"] .hint-btn').click(); pg.wait_for_timeout(200)
    hb = pg.locator("#popover").bounding_box(); ib = pg.locator('[data-id="c:usage export|since"] input').bounding_box()
    apart = hb["y"] >= ib["y"] + ib["height"] or hb["y"] + hb["height"] <= ib["y"] or hb["x"] >= ib["x"] + ib["width"] or hb["x"] + hb["width"] <= ib["x"]
    print(("PASS" if apart else "FAIL") + " hint does not cover the input")
    pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/7-fixed-light.png")
    small = b.new_context(viewport={"width": 900, "height": 700}).new_page(); small.goto(URL + "#at=c:usage push"); small.wait_for_timeout(500)
    rb = small.locator("#reset").bounding_box()
    print(("PASS" if rb["x"] + rb["width"] <= 900 else "FAIL") + " header fits at 900px")
    b.close()
