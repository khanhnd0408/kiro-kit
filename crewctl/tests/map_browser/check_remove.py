"""Removing and undoing: chips with x, Backspace, Ctrl/Cmd+Z, Clear, Reset, the bar following the path, Esc.

Run through run.py, which builds the page and sets CREWCTL_MAP_URL and CREWCTL_MAP_SHOTS."""
import os
from playwright.sync_api import sync_playwright
URL = os.environ["CREWCTL_MAP_URL"]
results = []
def report(name, ok, extra=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light").new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL + "#at=c:workspace assign"); pg.wait_for_timeout(600)
    cmd = lambda: pg.locator("#cmd").inner_text().split()
    line = lambda: " ".join(cmd())
    blur = lambda: pg.evaluate("document.activeElement && document.activeElement.blur()")
    pg.locator('[data-id="c:workspace assign|all"] .toggle').click()
    pg.locator('[data-id="c:workspace assign|yes"] .toggle').click()
    pg.locator('input[id="in-c:workspace assign|workspace_name"]').fill("ops")
    pg.locator('input[id="in-c:workspace assign|crewmates"]').fill("alpha, beta")
    blur()
    report("set up four options", line() == "crewctl workspace assign ops alpha beta --all --yes", line())

    pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    report("Backspace removes the option added last (the crewmates)", line() == "crewctl workspace assign ops --all --yes", line())
    report("a notice says what was removed, with Undo", pg.locator("#toast.open").is_visible() and "Removed CREWMATE" in pg.locator("#toastText").inner_text(),
           pg.locator("#toastText").inner_text())
    pg.locator("#toastAction").click(); pg.wait_for_timeout(150)
    report("Undo in the notice brings it back", line() == "crewctl workspace assign ops alpha beta --all --yes", line())

    def click_x(selector):                                   # the drawn circle on the chip's top-right corner
        box = pg.locator(selector).bounding_box(); pg.mouse.move(box["x"] + 5, box["y"] + 5); pg.wait_for_timeout(200)
        pg.mouse.click(box["x"] + box["width"] + 0.5, box["y"] + 0.5)
    click_x('#cmd .chip[data-key="yes"]'); pg.wait_for_timeout(150)
    report("the x on a chip removes just that option", line() == "crewctl workspace assign ops alpha beta --all", line())
    report("...and its switch turns off", pg.locator('[data-id="c:workspace assign|yes"] .toggle').get_attribute("aria-checked") == "false")
    click_x('#cmd .chip[data-key="crewmates"][data-item="0"]'); pg.wait_for_timeout(150)
    report("the x on one list item removes only that item", line() == "crewctl workspace assign ops beta --all", line())
    report("...and the input box shows what is left", pg.locator('input[id="in-c:workspace assign|crewmates"]').input_value() == "beta")

    pg.keyboard.press("Control+z"); pg.wait_for_timeout(100)
    report("Ctrl+Z undoes the last removal", line() == "crewctl workspace assign ops alpha beta --all", line())
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(100)
    report("Ctrl+Z again undoes the one before", line() == "crewctl workspace assign ops alpha beta --all --yes", line())
    pg.keyboard.press("Control+Shift+z"); pg.wait_for_timeout(100)
    report("Ctrl+Shift+Z redoes", line() == "crewctl workspace assign ops alpha beta --all", line())

    pg.locator('input[id="in-c:workspace assign|workspace_name"]').click()
    pg.locator('input[id="in-c:workspace assign|workspace_name"]').fill("")
    pg.wait_for_timeout(1300)                                     # a pause: clearing is its own step
    pg.keyboard.type("platform", delay=40); blur()
    report("typed word is in the line", line() == "crewctl workspace assign platform alpha beta --all", line())
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(100)
    report("typing a word is one undo step, not one per letter", line() == "crewctl workspace assign <WORKSPACE> alpha beta --all", line())
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(100)
    report("...and the step before it is the clearing", line() == "crewctl workspace assign ops alpha beta --all", line())

    pg.locator('input[id="in-c:workspace assign|workspace_name"]').fill("ops")
    field = pg.locator('[data-id="c:workspace assign|workspace_name"]')
    field.locator(".clear").click(); pg.wait_for_timeout(100)
    report("the x inside a filled input clears it", "<WORKSPACE>" in line() and pg.locator('input[id="in-c:workspace assign|workspace_name"]').input_value() == "", line())
    report("...and leaves the cursor in that box", pg.evaluate("document.activeElement.id") == "in-c:workspace assign|workspace_name")

    pg.locator('#cmd .chip.missing').click(); pg.wait_for_timeout(700)
    report("clicking a missing value jumps to its input", pg.evaluate("document.activeElement.id") == "in-c:workspace assign|workspace_name")
    pg.keyboard.type("ops"); blur()

    pg.locator("#clear").click(); pg.wait_for_timeout(150)
    report("Clear removes every option of this command", line() == "crewctl workspace assign <WORKSPACE>", line())
    report("...and disables itself", pg.locator("#clear").is_disabled())
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(100)
    report("...and Ctrl+Z brings them all back", line() == "crewctl workspace assign ops alpha beta --all", line())

    pg.keyboard.press("Escape"); pg.wait_for_timeout(550)
    report("Esc still goes back (to workspace)", pg.locator("#crumbs [aria-current]").inner_text() == "workspace")
    pg.locator('.node:not(.hidden) .orb[aria-label="assign"]').click(); pg.wait_for_timeout(550)
    report("...and keeps the options: they are there when you return", line() == "crewctl workspace assign ops alpha beta --all", line())

    # the bar follows where you are; choices stay with their command
    pg.locator('#crumbs [data-go="root"]').click(); pg.wait_for_timeout(550)
    report("at crewctl the bar shows only crewctl", line() == "crewctl …", line())
    report("...Copy is off there", pg.locator("#copy").is_disabled())
    report("...and it says the choices are kept", "kept" in pg.locator("#effects").inner_text(), pg.locator("#effects").inner_text())
    pg.locator('.node:not(.hidden) .orb[aria-label="Usage"]').click(); pg.wait_for_timeout(550)
    report("switching to Usage changes the bar to crewctl usage …", line() == "crewctl usage …", line())
    pg.locator('.node:not(.hidden) .orb[aria-label="export"]').click(); pg.wait_for_timeout(550)
    report("...and inside export it is export's own line, not assign's", line() == "crewctl usage export", line())
    pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    report("Backspace in export does not touch assign's options", line() == "crewctl usage export", line())
    pg.locator('#crumbs [data-go="root"]').click(); pg.wait_for_timeout(550)
    pg.keyboard.press("Backspace"); pg.wait_for_timeout(150)
    report("Backspace outside a command removes nothing and says why", "Open a command first" in pg.locator("#toastText").inner_text())
    pg.locator('.node:not(.hidden) .orb[aria-label="Crew"]').click(); pg.wait_for_timeout(550)
    pg.locator('.node:not(.hidden) .orb[aria-label="Crew records"]').click(); pg.wait_for_timeout(550)
    report("Crew records (register + workspace) has no shared word", line() == "crewctl …", line())
    pg.locator('.node:not(.hidden) .orb[aria-label="workspace"]').click(); pg.wait_for_timeout(550)
    report("workspace shows crewctl workspace …", line() == "crewctl workspace …", line())
    pg.locator('.node:not(.hidden) .orb[aria-label="assign"]').click(); pg.wait_for_timeout(550)
    report("back in assign, its choices are all still there", line() == "crewctl workspace assign ops alpha beta --all", line())

    pg.locator("#reset").click(); pg.wait_for_timeout(550)
    report("Reset clears everything", line() == "crewctl …", line())
    pg.keyboard.press("Control+z"); pg.wait_for_timeout(300)
    pg.locator('#q').click(); pg.keyboard.type("move crewmates workspace"); pg.keyboard.press("Enter"); pg.wait_for_timeout(600)
    report("Reset can be undone too (assign's choices come back)", line() == "crewctl workspace assign ops alpha beta --all", line())
    report("no JavaScript errors", not errors, "; ".join(errors))
    pg.goto(URL + "#at=c:workspace assign&v.all=1&v.workspace_name=ops&v.crewmates=alpha, beta&theme=dark"); pg.wait_for_timeout(700)
    pg.locator('#cmd .chip[data-key="all"]').hover(); pg.wait_for_timeout(200)
    pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/11-chips-dark.png")
    pg.keyboard.press("Backspace"); pg.wait_for_timeout(300)
    pg.screenshot(path=os.environ["CREWCTL_MAP_SHOTS"] + "/12-toast-dark.png")
    report("Backspace on a link removes the link's last value", " ".join(pg.locator("#cmd").inner_text().split()) == "crewctl workspace assign ops --all",
           pg.locator("#cmd").inner_text())
    report("a link's theme applies in the open tab", pg.evaluate("document.documentElement.dataset.theme") == "dark")
    before = pg.locator("#bar").bounding_box()["height"]
    pg.mouse.move(700, before and pg.locator("#bar").bounding_box()["y"] + 20); pg.wait_for_timeout(350)
    report("hovering the bar does not change its size (chips stay under the pointer)", abs(pg.locator("#bar").bounding_box()["height"] - before) < 1)
    gaps = pg.eval_on_selector_all("#cmd .chip", "els => els.map(e => e.getBoundingClientRect().width)")
    report("chips take only their text's space (the x floats)", all(w < 90 for w in gaps[:2]), str(gaps))
    report("the command text is one clean line (no x, no line breaks)", "\n" not in pg.locator("#cmd").inner_text() and "×" not in pg.locator("#cmd").inner_text(),
           repr(pg.locator("#cmd").inner_text()))
    b.close()
print(f"{sum(results)}/{len(results)} checks passed")

# Esc: one press goes back from anywhere (a hint closes first); Esc in search only closes the search
with sync_playwright() as p:
    b = p.chromium.launch()
    def fresh(at="c:workspace assign"):
        pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page(); pg.goto(URL + "#at=" + at); pg.wait_for_timeout(600); return pg
    current = lambda pg: pg.locator("#crumbs [aria-current]").inner_text()
    pg = fresh(); pg.locator('input[id="in-c:workspace assign|workspace_name"]').fill("ops"); pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("one Esc goes back even while typing in a box", current(pg) == "workspace", current(pg))
    pg = fresh(); pg.locator('[data-id="c:workspace assign|all"] .toggle').click(); pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("one Esc goes back after flipping a switch", current(pg) == "workspace", current(pg))
    pg = fresh(); pg.locator("#q").click(); pg.keyboard.type("export"); pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("Esc in the search box only closes the search", current(pg) == "assign" and not pg.locator("#results.open").count(), current(pg))
    pg = fresh(); pg.locator('[data-id="c:workspace assign|all"] .hint-btn').click(); pg.wait_for_timeout(100)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("with a hint open, Esc closes the hint first", current(pg) == "assign" and not pg.locator("#popover.open").count())
    b.close()
print(f"{sum(results)}/{len(results)} checks passed (with Esc)")

# Esc returns to the view you came from, even after a search or breadcrumb jump
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page(); pg.goto(URL); pg.wait_for_timeout(600)
    at = lambda: pg.evaluate("state.focus")
    pg.locator("#q").click(); pg.keyboard.type("move crewmates workspace"); pg.keyboard.press("Enter"); pg.wait_for_timeout(600)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("after a search jump, one Esc returns to where you were", at() == "root", at())
    pg.locator('.node:not(.hidden) .orb[aria-label="Usage"]').click(); pg.wait_for_timeout(550)
    pg.locator('.node:not(.hidden) .orb[aria-label="export"]').click(); pg.wait_for_timeout(550)
    pg.locator('#crumbs [data-go="root"]').click(); pg.wait_for_timeout(550)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("after a breadcrumb jump, one Esc returns to the command", at() == "c:usage export", at())
    pg.keyboard.press("Escape"); pg.wait_for_timeout(150)
    report("...and the next Esc retraces the way you came", at() == "g:Usage", at())
    b.close()
print(f"{sum(results)}/{len(results)} checks passed (with Esc history)")
