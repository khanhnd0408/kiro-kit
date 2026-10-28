"""A long random session: after every action, one Esc must do exactly one thing (close the hint or search, or go back).

Run through run.py, which builds the page and sets CREWCTL_MAP_URL and CREWCTL_MAP_SHOTS."""
import os
import random, sys
from playwright.sync_api import sync_playwright
U = os.environ["CREWCTL_MAP_URL"]
seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
rnd = random.Random(seed)
problems, log = [], []
with sync_playwright() as p:
    b = p.chromium.launch(**({"channel": os.environ["CREWCTL_MAP_CHANNEL"]} if os.environ.get("CREWCTL_MAP_CHANNEL") else {}))
    pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U); pg.wait_for_timeout(500)
    state = lambda: pg.evaluate("""({at: state.focus, back: state.trail[state.trail.length - 1] || null, hint: !!document.querySelector('#popover.open'),
        search: !!document.querySelector('#results.open'), active: document.activeElement.tagName + '.' + (document.activeElement.className || '')})""")
    parent = lambda at: pg.evaluate(f"nodes[{at!r}].parent || null")

    def act():
        kind = rnd.choice(["child", "child", "child", "type", "toggle", "choice", "hint", "hintclose", "search", "chip", "crumb", "copy", "undo", "theme", "clickbg", "globals"])
        try:
            if kind == "child":
                orbs = pg.locator(".node:not(.hidden):not(.focus):not(.dim) .orb")
                if orbs.count(): orbs.nth(rnd.randrange(orbs.count())).click()
            elif kind == "globals":
                g = pg.locator('.node:not(.hidden) .orb[aria-label="global options"]')
                if g.count(): g.first.click()
            elif kind == "type":
                ins = pg.locator(".node:not(.hidden) .pill input")
                if ins.count(): el = ins.nth(rnd.randrange(ins.count())); el.click(); pg.keyboard.type(rnd.choice(["ops", "a, b", "x y", "7d"]))
            elif kind == "toggle":
                t = pg.locator(".node:not(.hidden) .toggle")
                if t.count(): t.nth(rnd.randrange(t.count())).click()
            elif kind == "choice":
                c = pg.locator(".node:not(.hidden) [data-choice]")
                if c.count(): c.nth(rnd.randrange(c.count())).click()
            elif kind == "hint":
                h = pg.locator(".node:not(.hidden) .hint-btn")
                if h.count(): h.nth(rnd.randrange(h.count())).click()
            elif kind == "hintclose":
                if pg.locator("#popover.open").count(): pg.mouse.click(30, 300)
            elif kind == "search":
                pg.locator("#q").click(); pg.keyboard.type(rnd.choice(["export", "assign", "mcp add", "plan"])); pg.wait_for_timeout(100)
                if rnd.random() < 0.6: pg.keyboard.press("Enter")
            elif kind == "chip":
                c = pg.locator("#cmd .chip")
                if c.count(): c.nth(rnd.randrange(c.count())).click()
            elif kind == "crumb":
                c = pg.locator("#crumbs [data-go]")
                if c.count() > 1: c.nth(rnd.randrange(c.count() - 1)).click()
            elif kind == "copy":
                if not pg.locator("#copy").is_disabled(): pg.locator("#copy").click()
            elif kind == "undo":
                pg.evaluate("document.activeElement.blur()"); pg.keyboard.press("Control+z")
            elif kind == "theme":
                pg.locator(f'[data-theme-set="{rnd.choice(["auto", "light", "dark"])}"]').click()
            elif kind == "clickbg":
                pg.mouse.click(rnd.randint(40, 1400), rnd.randint(120, 700))
        except Exception as e:
            kind += f" (skipped: {str(e)[:40]})"
        pg.wait_for_timeout(rnd.choice([0, 60, 200, 520]))
        return kind

    for step in range(int(sys.argv[2]) if len(sys.argv) > 2 else 120):
        kind = act(); log.append(kind)
        before = state()
        pg.keyboard.press("Escape"); pg.wait_for_timeout(80)
        after = state()
        if before["hint"]:
            ok = not after["hint"] and after["at"] == before["at"]
            expect = "close the hint"
        elif before["search"] or before["active"].startswith("INPUT.") and before["active"] == "INPUT." and pg.evaluate("document.activeElement === document.getElementById('q')"):
            ok = after["at"] == before["at"]
            expect = "close the search"
        else:
            want = before["back"] or parent(before["at"])
            ok = after["at"] == (want or before["at"])
            expect = f"go back to {want}"
        if not ok:
            problems.append((step, kind, before, after, expect, log[-4:]))
    b.close()
print(f"seed {seed}: {len(log)} actions, {len(problems)} times one Esc did not do its one job, {len(errors)} JS errors")
for step, kind, before, after, expect, recent in problems[:8]:
    print(f"  step {step}: after '{kind}' (recent: {recent}) at={before['at']} focus={before['active']} hint={before['hint']} search={before['search']}"
          f" -> expected to {expect}, got at={after['at']} focus={after['active']}")
for e in errors[:3]: print("  JS:", e)
