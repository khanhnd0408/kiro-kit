# Browser checks for `crewctl map`

The unit tests (`tests/test_map.py`) check the page's data and that its JavaScript builds the same command lines as
`crewctl guide`. These scripts check the page the way a person uses it, in a real browser, with Playwright:

| Script | What it checks |
|---|---|
| `check_map.py` | Jobs, focusing nodes, typing, switches, choices, hints, Copy, theme, search, global options, links, Reset, a small window |
| `check_glass.py` | The floating glass bar: blur, growing and shrinking with the command, nothing hidden behind it, options never split |
| `check_remove.py` | Chips with ×, Backspace, Ctrl/Cmd+Z and redo, Clear, Reset, the bar following where you are, Esc |
| `check_esc.py` | A long random session: after every action, one Esc does exactly one thing |

```bash
pip install playwright && playwright install chromium     # once
python3 tests/map_browser/run.py                           # from the crewctl folder
CREWCTL_MAP_CHANNEL=chrome python3 tests/map_browser/run.py   # with the installed Google Chrome instead
```

`run.py` builds a fresh page with `crewctl map`, runs every script against it, and prints where the screenshots are.
They are not part of `unittest discover` because they need a browser.
