"""Website Autopsy — Playwright health-check bot. CLI entry."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .engine import run_autopsy
from .report import write_report

console = Console()


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency): KEY=val lines, no override."""
    try:
        p = Path(path)
        if not p.is_file():
            return
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


_load_dotenv()


@click.command()
@click.argument("url", required=False, envvar=["WEBSITE_URL", "WEBSITE"])
@click.option("-o", "--output", default="autopsy-report", show_default=True,
              envvar="AUTOPSY_OUTPUT", help="Output directory for report + screenshots")
@click.option("--max-pages", default=30, show_default=True,
              envvar="AUTOPSY_MAX_PAGES", help="Max pages to crawl")
@click.option("--max-depth", default=3, show_default=True,
              envvar="AUTOPSY_MAX_DEPTH", help="Max crawl depth from start URL")
@click.option("--headless/--headed", default=True, show_default=True)
def main(url: str | None, output: str, max_pages: int, max_depth: int, headless: bool):
    """Run a forensic health check on URL and emit a dramatic HTML report.

    URL can be omitted if the WEBSITE (or WEBSITE_URL) env var / .env is set.
    """
    if not url:
        console.print("[bold red]No website given.[/bold red] Pass a URL or set WEBSITE env, e.g.:")
        console.print('  [green]WEBSITE=https://example.com autopsy[/green]  (or put WEBSITE=... in .env)')
        raise SystemExit(2)
    out = Path(output)
    msgs: list[str] = []

    def progress(m: str):
        console.print(f"[dim]{m}[/dim]")

    console.print(f"[bold red]🔬 WEBSITE AUTOPSY[/bold red] — examining [cyan]{url}[/cyan]")
    try:
        result = asyncio.run(run_autopsy(url, out, max_pages=max_pages,
                                         max_depth=max_depth, headless=headless,
                                         progress=progress))
    except Exception as e:
        console.print(f"[bold red]Autopsy failed:[/bold red] {e}")
        raise SystemExit(1)

    rp = write_report(result, out)

    t = Table(title="Autopsy findings")
    t.add_column("Metric"); t.add_column("Count", justify="right")
    t.add_row("Pages visited", str(len(result.pages)))
    t.add_row("Dead buttons", str(len(result.dead_buttons)))
    t.add_row("Broken links", str(len(result.broken_links)))
    t.add_row("JS errors", str(len(result.js_errors)))
    t.add_row("Slow / perf", str(len(result.slow) + len(result.perf_issues)))
    t.add_row("SEO", str(len(result.seo_issues)))
    t.add_row("Accessibility", str(len(result.a11y_issues)))
    t.add_row("Security", str(len(result.security_issues)))
    t.add_row("Broken resources", str(len(result.resource_issues)))
    t.add_row("Forms tested", str(result.forms_tested))
    t.add_row("Hidden pages", str(len(result.hidden_pages)))
    console.print(t)
    verdict, sub = result.verdict()
    console.print(f"\n[bold]UX weirdness score: {result.weirdness_score()}/100[/bold]")
    console.print(f"[bold yellow]{verdict}[/bold yellow] — {sub}")
    console.print(f"\n[green]Report:[/green] {rp.resolve()}")


if __name__ == "__main__":
    main()
