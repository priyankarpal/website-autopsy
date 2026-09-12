# Website Autopsy 🔬

Enter a website — a Playwright bot performs an automated **deep health audit** and emits a professional self-contained HTML report with screenshot evidence.

## What it discovers

- **Pages visited** — BFS crawl of same-origin pages (configurable depth/limit)
- **Dead buttons** — clicks every visible button; flags ones with zero navigation, DOM change, or network effect
- **Broken links** — verifies every discovered link (HEAD→GET), records HTTP status
- **JS errors** — captures `pageerror` + console errors per page
- **Slow interactions** — page loads >3s + click latency >1.2s
- **Forms tested** — missing submit, unlabeled fields, placeholder-only labels (safe fill, no destructive submit)
- **Hidden pages** — `robots.txt`, `sitemap.xml`, common admin paths (`/admin`, `/login`, `/.env`, …)
- **Deep checks (22)** — SEO (title/meta/canonical/headings/OG), accessibility (alt/names/lang/focus/viewport), performance (TTFB/weight/oversized assets), security headers + HTTPS/mixed-content/cookies, broken-asset forensics, link hygiene, tap targets, form autocomplete
- **Health score 0–100** + verdict (Excellent → Critical), severity-weighted

## Install

```bash
uv sync
uv run playwright install chromium  # only if browsers missing
```

## Use

```bash
uv run website-autospy https://example.com -o ./autopsy-report --max-pages 30 --max-depth 3
# open ./autopsy-report/report.html
```

Short command (same thing): `autopsy` instead of `website-autospy`:

```bash
uv run autopsy https://example.com
```

### Env for website (no URL arg needed)

```bash
WEBSITE=https://example.com uv run autopsy
```

Or put it in a `.env` file in the directory you run from (auto-loaded, no extra package needed):

```bash
echo "WEBSITE=https://example.com" > .env
uv run autopsy
```

`WEBSITE_URL` also works. Extras: `AUTOPSY_OUTPUT`, `AUTOPSY_MAX_PAGES`, `AUTOPSY_MAX_DEPTH`.

Options: `--max-pages`, `--max-depth`, `--headless/--headed`, `-o/--output`.

## Layout

- `src/website_autospy/engine.py` — crawl + all checks
- `src/website_autospy/models.py` — result types + weirdness score
- `src/website_autospy/report.py` — self-contained professional HTML report (score ring, executive summary, filterable findings, coverage ledger, screenshots inlined as base64)
- `src/website_autospy/__init__.py` — Click CLI
