# Website Autopsy

Point it at a website and it crawls the pages, clicks the buttons, checks links, forms, SEO, accessibility, performance and security headers, then writes a single HTML report you can open in a browser or send to someone.

You run it locally with `uv`. The report lands in a folder (by default `autopsy-report/report.html`) with screenshots next to it.

## What it checks

- Pages visited — same-origin crawl, breadth-first, with page and depth limits you control
- Dead buttons — clicks visible controls and notes the ones that do nothing
- Broken links — verifies each discovered link and records the status
- JS errors — collects page errors and console errors per page
- Slow pages and clicks — page loads over 3s, click latency over 1.2s
- Forms — missing submit controls, unlabeled fields, placeholder-only labels (fills safely, does not submit anything destructive)
- Exposed paths — `robots.txt`, `sitemap.xml` plus common admin/debug probes
- Deep checks (30+) — titles and meta, headings, alt text and control names, language and viewport, transfer weight and slow assets, HTTPS and security headers, failed sub-resources, link hygiene, tap targets, autocomplete
- A health score from 0 to 100 with a severity-weighted breakdown

## Prerequisites

You need these before anything else:

- Python 3.12 or newer (`python3 --version` to check)
- `uv` 0.12 or newer (`uv --version` to check, install from https://docs.astral.sh/uv/ if missing)
- Git, to clone the repo
- About 300 MB free for the Playwright Chromium download on first run

Works on macOS and Linux. Windows works through WSL; native Windows works too but the examples below assume a Unix shell.

## Setup

Clone the repo and move into it (replace the URL with your fork if you have one):

```bash
git clone https://github.com/<you>/website-autospy
cd website-autospy
```

Install dependencies:

```bash
uv sync
```

You should see `uv` create `.venv/` and finish with no errors. If you want to confirm the install worked:

```bash
uv run autopsy --help
```

That prints the available options. If it prints a usage screen, you are set.

### Browsers

The crawl uses Playwright with Chromium. If you already have Playwright browsers installed, skip this. Otherwise run it once:

```bash
uv run playwright install chromium
```

If that step fails with a missing system dependency error, install the OS packages Playwright asks for and run the command again. You only need to do this once per machine.

## Your first audit

Pick a site you are allowed to scan and run:

```bash
uv run autopsy https://example.com -o ./autopsy-report
```

When it finishes you get a small table in the terminal and a report on disk. Open it:

```bash
# macOS
open ./autopsy-report/report.html
# Linux
xdg-open ./autopsy-report/report.html
# or just open the file in your browser manually
```

Start with a small crawl while you are learning the flags:

```bash
uv run autopsy https://example.com --max-pages 10 --max-depth 2 -o ./autopsy-report
```

## How to run it

There are two command names and they do the same thing. Use whichever you remember:

```bash
uv run website-autospy https://example.com -o ./autopsy-report
uv run autopsy https://example.com -o ./autopsy-report
```

If you do not want to pass the URL every time, set it once:

```bash
WEBSITE=https://example.com uv run autopsy
```

`WEBSITE_URL` works as well. You can also put it in a `.env` file in the folder you run from (it is picked up automatically, no extra setup):

```bash
echo "WEBSITE=https://example.com" > .env
uv run autopsy
```

Headed mode opens a visible browser window, useful when a crawl behaves oddly:

```bash
uv run autopsy https://example.com --headed
```

## Options and environment

Flags:

| Flag | Default | What it does |
| --- | --- | --- |
| `-o, --output` | `autopsy-report` | Folder for `report.html` plus `screenshots/` |
| `--max-pages` | `30` | Stop after this many pages |
| `--max-depth` | `3` | How many links deep to go from the start page |
| `--headless / --headed` | headless | Headless is faster; headed shows the browser |

Environment variables (handy for scripts and CI):

| Variable | Same as | Example |
| --- | --- | --- |
| `WEBSITE` or `WEBSITE_URL` | URL argument | `WEBSITE=https://example.com uv run autopsy` |
| `AUTOPSY_OUTPUT` | `-o` | `AUTOPSY_OUTPUT=./out uv run autopsy https://example.com` |
| `AUTOPSY_MAX_PAGES` | `--max-pages` | `AUTOPSY_MAX_PAGES=10 uv run autopsy https://example.com` |
| `AUTOPSY_MAX_DEPTH` | `--max-depth` | `AUTOPSY_MAX_DEPTH=2 uv run autopsy https://example.com` |

Command-line flags always win over environment variables.

## What you get

After a run, the output folder looks like this:

```text
autopsy-report/
  report.html      # open this in a browser
  screenshots/     # evidence images, inlined into the HTML as well
```

The report has a summary with the health score, findings grouped by category with severity filters and search, a pages table sorted slowest-first, a coverage list of every check that ran, and an exposed-paths section. Use Print in the browser to save it as PDF.

## Project layout

- `src/website_autospy/engine.py` — crawl and all checks
- `src/website_autospy/models.py` — result types, scoring, check registry
- `src/website_autospy/report.py` — HTML report generator (single self-contained file, no CDN)
- `src/website_autospy/__init__.py` — command-line entry point
- `tests/` — browser-free unit tests

## Tests

Run the full suite:

```bash
uv run pytest -q
```

Run one file while you are working:

```bash
uv run pytest tests/test_registry.py -q
```

All tests run without a browser. A real crawl is the only thing that needs Chromium.

## Troubleshooting

**`autopsy: command not found` or `No module named website_autospy`**
You skipped the install or ran outside `uv`. Run `uv sync` in the repo root, then prefix commands with `uv run`.

**Playwright says the browser is missing (`Executable doesn't exist`)**
Run `uv run playwright install chromium` once. On Linux you may also need the system libraries Playwright lists — install those, then retry.

**`No website given`**
You ran `autopsy` with no URL and no env set. Either pass a URL (`uv run autopsy https://example.com`) or set `WEBSITE` in your shell or `.env`.

**Permission denied writing the output folder**
You pointed `-o` somewhere you cannot write to. Pick a folder you own (`-o ./autopsy-report`) or fix the directory permissions, then rerun.

**Nothing happens / crawl is slow**
Lower the scope first (`--max-pages 10 --max-depth 2`), then raise it. Large JavaScript-heavy sites take a while; headed mode helps you see where it stalls.

**Report looks empty or pages show no status**
The target may be blocking bots or very slow. Try one page manually in a browser, then rerun with `--headed` and a smaller page limit to see what the crawler sees.

**Python version errors during `uv sync`**
Check `python3 --version` is 3.12+. If your system Python is older, install a newer Python and let `uv` pick it up, then run `uv sync` again.
