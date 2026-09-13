"""Site-audit HTML report (self-contained, no CDN)."""
from __future__ import annotations

import base64
import html
from pathlib import Path

from .models import AutopsyResult

# kind -> (Category label, marker, blurb) — marker kept for shape compat, unused in UI
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "security": ("Security", "", "Headers, transport, cookies and exposed paths"),
    "seo": ("SEO", "", "Crawlability, metadata and heading structure"),
    "a11y": ("Accessibility", "", "Names, labels, language and keyboard support"),
    "perf": ("Performance", "", "Transfer weight, TTFB and oversized assets"),
    "slow": ("Performance", "", "Transfer weight, TTFB and oversized assets"),
    "resource": ("Stability", "", "Failed requests and broken sub-resources"),
    "js_error": ("Stability", "", "Failed requests and broken sub-resources"),
    "broken_link": ("Links", "", "Integrity and hygiene of every discovered link"),
    "content": ("Content and UX", "", "Images, tap targets and layout stability"),
    "dead_button": ("Interactivity", "", "Click-tested controls and latency budgets"),
    "form": ("Forms", "", "Structure, labels, autocomplete and safe handling"),
}

SECTION_ORDER = ["security", "seo", "a11y", "perf", "resource", "broken_link", "content", "dead_button", "form", "slow", "js_error"]
SECTION_OF = {
    "security": "security", "seo": "seo", "a11y": "a11y",
    "perf": "performance", "slow": "performance",
    "resource": "stability", "js_error": "stability",
    "broken_link": "links", "content": "content",
    "dead_button": "interactivity", "form": "forms",
}

SECTIONS: dict[str, tuple[str, str, str]] = {
    "security": ("Security", "Headers, HTTPS, cookies and exposed paths"),
    "seo": ("SEO", "Titles, meta, canonical, headings and social tags"),
    "a11y": ("Accessibility", "Alt text, names, lang, focus and mobile viewport"),
    "performance": ("Performance", "Load budgets, TTFB, weight and heavy assets"),
    "stability": ("Stability", "JS runtime errors and failed sub-resources"),
    "links": ("Links", "Broken-link verification and link hygiene"),
    "content": ("Content and UX", "Broken images, tap targets and layout shift"),
    "interactivity": ("Interactivity", "Dead-button tests and click latency"),
    "forms": ("Forms", "Submit controls, labels and autocomplete"),
}

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

CSS = """
:root{--bg:#fafafa;--card:#ffffff;--ink:#1a1a1a;--mut:#6b7280;--line:#e5e7eb;
--accent:#1d4ed8;--crit:#b42318;--high:#c2410c;--med:#a16207;--low:#047857}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
/* masthead */
.masthead{background:var(--card);border-bottom:1px solid var(--line)}
.masthead-in{max-width:960px;margin:0 auto;padding:14px 20px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.brand{font-weight:650;font-size:15px;letter-spacing:-.1px}
.brand small{font-weight:400;color:var(--mut)}
.masthead .url{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;color:var(--mut);word-break:break-all}
.masthead nav{margin-left:auto;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.masthead nav a{color:var(--mut);font-size:13px}
.masthead nav a:hover{color:var(--ink);text-decoration:none}
.btn{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font-size:13px;font-weight:500;cursor:pointer}
.btn:hover{background:#f3f4f6}.btn.ghost{background:transparent}
/* layout */
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
.hero{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:24px;display:grid;grid-template-columns:170px 1fr;gap:24px}
@media(max-width:720px){.hero{grid-template-columns:1fr}.kpi{border-left:0;border-top:1px solid var(--line)}.kpi:first-child{border-top:0}.cat{border-left:0;border-top:1px solid var(--line)}}
.ring-wrap{text-align:left}
.ring{width:150px;height:150px}
.ring .num{font-size:34px;font-weight:650;font-variant-numeric:tabular-nums}.ring .of{font-size:12px;color:var(--mut)}
.verdict{margin:10px 0 0;font-size:14px;font-weight:600}
.verdict-sub{margin:4px 0 0;color:var(--mut);font-size:13.5px}
.hero h1{margin:0 0 4px;font-size:22px;letter-spacing:-.2px;font-weight:650}
.hero .lede{color:var(--mut);margin:0 0 12px;font-size:14.5px}
.meta-row{display:flex;gap:14px;flex-wrap:wrap;color:var(--mut);font-size:13px;margin-bottom:10px}
.meta-row b{color:var(--ink);font-weight:600}
.sevbar{display:flex;height:6px;border-radius:3px;overflow:hidden;background:#eef0f3;margin:10px 0 6px}
.sevbar i{display:block;height:100%}
.sev-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12.5px;color:var(--mut)}
.dotk{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px}
/* summary numbers */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0;margin:16px 0;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card)}
.kpi{padding:12px 14px;border-left:1px solid var(--line)}
.kpi:first-child{border-left:0}
.kpi .n{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}.kpi .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px;margin-top:2px}
.kpi small{color:var(--mut);font-size:12px}
/* panels */
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:20px;margin-top:16px}
.panel h2{margin:0 0 2px;font-size:17px;font-weight:650}.panel .sub{color:var(--mut);font-size:13.5px;margin:0 0 12px}
.catgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:0;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.cat{padding:12px 14px;border-left:1px solid var(--line);border-top:1px solid var(--line)}
.cat b{font-size:14px;font-weight:600}.cat p{margin:2px 0 8px;color:var(--mut);font-size:12.5px}
.cat .counts{display:flex;gap:6px;flex-wrap:wrap}
.pill{font-size:11.5px;font-weight:500;padding:2px 8px;border-radius:4px;background:#f3f4f6;color:#374151;border:1px solid var(--line)}
.sumlist{margin:6px 0 0;padding-left:20px}.sumlist li{margin:5px 0}
/* toolbar */
.toolbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0 4px}
.chip{border:1px solid var(--line);background:#fff;border-radius:6px;padding:5px 12px;font-size:13px;font-weight:500;cursor:pointer;color:#374151}
.chip.active{background:var(--ink);color:#fff;border-color:var(--ink)}
.search{margin-left:auto;border:1px solid var(--line);border-radius:6px;padding:7px 10px;font-size:13.5px;min-width:200px;background:#fff}
/* findings */
.sec-t{margin:24px 0 2px;font-size:16px;font-weight:650;display:flex;align-items:baseline;gap:8px}
.sec-t .cnt{font-size:12px;color:var(--mut);font-weight:500}
.sec-d{color:var(--mut);font-size:13px;margin:0 0 8px}
.find{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--med);border-radius:6px;padding:14px 16px;margin:8px 0}
.find.critical{border-left-color:var(--crit)}.find.high{border-left-color:var(--high)}.find.medium{border-left-color:var(--med)}.find.low{border-left-color:var(--low)}
.find-head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.find-head h3{margin:0;font-size:14.5px;font-weight:600;flex:1;min-width:200px}
.badge{font-size:11px;font-weight:600;letter-spacing:.4px;text-transform:uppercase;padding:2px 8px;border-radius:4px;white-space:nowrap;border:1px solid var(--line);color:#374151;background:#f9fafb}
.b-critical{color:var(--crit);border-color:#fecdca;background:#fffbfa}
.b-high{color:var(--high);border-color:#fed7aa;background:#fffaf5}
.b-medium{color:var(--med);border-color:#fde68a;background:#fffdf0}
.b-low{color:var(--low);border-color:#a7f3d0;background:#f6fef9}
.b-cat{background:#fff;color:var(--mut)}
.find .where{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;color:var(--mut);word-break:break-all;margin:6px 0}
.find p.desc{margin:6px 0;color:#374151;font-size:14px}
.fix{background:#fafafa;border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:13.5px;margin:8px 0}
.fix b{font-weight:600}
.checkline{font-size:12px;color:var(--mut);font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.find img.ev{max-width:100%;border:1px solid var(--line);border-radius:6px;margin-top:10px;cursor:zoom-in;max-height:340px;object-fit:contain;background:#fafafa}
details.evbox{margin-top:8px;font-size:12.5px}details.evbox summary{cursor:pointer;color:var(--accent);font-weight:500}
details.evbox pre{background:#f9fafb;color:#1f2937;border:1px solid var(--line);padding:12px;border-radius:6px;overflow:auto;font-size:12px}
/* tables */
table.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);padding:8px 10px;border-bottom:1px solid var(--line);font-weight:600}
.tbl td{padding:8px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}
.tbl tr:last-child td{border-bottom:0}
.tbl tr:hover td{background:#fafafa}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;word-break:break-all}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.oktag{color:var(--low);font-weight:600}.badt{color:var(--crit);font-weight:600}.warnt{color:var(--med);font-weight:600}
.pass{color:var(--low);font-weight:600;font-size:12.5px}
.fail{color:var(--crit);font-weight:600;font-size:12.5px}
/* lightbox */
#lb{position:fixed;inset:0;background:rgba(0,0,0,.8);display:none;align-items:center;justify-content:center;z-index:99;padding:20px}
#lb img{max-width:94vw;max-height:90vh;border-radius:6px;background:#fff}
#lb.show{display:flex}
footer{color:var(--mut);font-size:12.5px;padding:24px 0 0}
.empty{background:#fafafa;border:1px solid var(--line);border-radius:6px;padding:14px;color:var(--mut);font-size:14px}
hr.sep{border:0;border-top:1px solid var(--line);margin:16px 0 0}
@media print{.masthead nav,.toolbar,.btn,#lb{display:none}.wrap{padding:0}.panel,.hero{break-inside:avoid}}
"""

JS = """
function setFilter(sev,btn){document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));btn.classList.add('active');
document.querySelectorAll('.find').forEach(f=>{f.style.display=(sev==='all'||f.dataset.sev===sev)?'':'none';});applySearch();}
function applySearch(){const q=(document.getElementById('q').value||'').toLowerCase();
document.querySelectorAll('.find').forEach(f=>{if(f.style.display==='none')return;
const t=f.innerText.toLowerCase();f.style.display=t.includes(q)?'':'none';});}
function lb(src){const d=document.getElementById('lb');d.innerHTML='<img src="'+src+'">';d.classList.add('show');}
document.addEventListener('click',e=>{if(e.target.id==='lb')e.target.classList.remove('show');});
document.addEventListener('keydown',e=>{if(e.key==='Escape')document.getElementById('lb').classList.remove('show');});
"""


def _img_tag(out_dir: Path, rel: str | None, thumb: bool = True) -> str:
    if not rel:
        return ""
    p = out_dir / rel
    if not p.exists():
        return ""
    try:
        data = base64.b64encode(p.read_bytes()).decode()
        src = f"data:image/png;base64,{data}"
        return f"<img class='ev' src='{src}' loading='lazy' onclick=\"lb('{src}')\" alt='evidence screenshot'/>"
    except Exception:
        return ""


def _ring(score: int) -> str:
    color = "#047857" if score >= 80 else "#a16207" if score >= 55 else "#c2410c" if score >= 30 else "#b42318"
    r = 62
    circ = 2 * 3.14159 * r
    off = circ * (1 - score / 100)
    return f"""<svg class="ring" viewBox="0 0 160 160">
<circle cx="80" cy="80" r="{r}" fill="none" stroke="#eef0f3" stroke-width="10"/>
<circle cx="80" cy="80" r="{r}" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="butt"
 stroke-dasharray="{circ:.1f}" stroke-dashoffset="{off:.1f}" transform="rotate(-90 80 80)"/>
<text x="80" y="80" text-anchor="middle" class="num" fill="#1a1a1a">{score}</text>
<text x="80" y="98" text-anchor="middle" class="of" fill="#6b7280">/ 100</text></svg>"""


def _verdict(score: int, weird: int, verdict: str, sub: str) -> str:
    return f"<p class='verdict'>{html.escape(verdict)}</p><p class='verdict-sub'>Health {score}/100 · Risk {weird}/100 — {html.escape(sub)}</p>"


def _summary(result: AutopsyResult) -> str:
    """Plain-language summary derived from the measurements."""
    bullets: list[str] = []
    counts = result.severity_counts()
    total = sum(counts.values())
    if total == 0:
        return "<ul class='sumlist'><li><b>No issues found.</b> All checks passed on the crawled pages.</li></ul>"
    by_kind: dict[str, int] = {}
    for i in result.issues:
        by_kind[i.kind] = by_kind.get(i.kind, 0) + 1
    top = sorted(by_kind.items(), key=lambda kv: -kv[1])[:3]
    names = {"security": "security", "seo": "SEO", "a11y": "accessibility", "perf": "performance",
             "slow": "load performance", "resource": "broken sub-resources", "js_error": "JS errors",
             "broken_link": "broken links", "content": "content/UX", "dead_button": "dead controls", "form": "forms"}
    topline = ", ".join(f"<b>{n}</b> ({names.get(k, k)})" for k, n in top)
    bullets.append(f"<li>Most common: {topline}.</li>")
    if result.pages:
        worst = max(result.pages, key=lambda p: p.load_ms)
        avg = sum(p.load_ms for p in result.pages) / len(result.pages)
        bullets.append(f"<li>Slowest page is <span class='mono'>{html.escape(worst.url)}</span> at <b>{worst.load_ms} ms</b> (average {avg:.0f} ms across {len(result.pages)} pages, budget 3000 ms).</li>")
    sec = [i for i in result.issues if i.kind == "security"]
    if sec:
        crit = sum(1 for i in sec if i.severity in ("critical", "high"))
        bullets.append(f"<li>Security: <b>{len(sec)} finding(s)</b>{f', {crit} high/critical' if crit else ''}.</li>")
    dead = len(result.dead_buttons)
    if dead:
        bullets.append(f"<li><b>{dead} control(s) do nothing when clicked.</b> Consider removing or fixing them.</li>")
    if result.hidden_pages:
        bullets.append(f"<li><b>{len(result.hidden_pages)} exposed path(s)</b> returned HTTP 2xx. Check each one is intentional.</li>")
    return "<ul class='sumlist'>" + "".join(bullets) + "</ul>"


def write_report(result: AutopsyResult, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    esc = html.escape
    weird = result.weirdness_score()
    health = result.health_score()
    verdict, sub = result.verdict()
    counts = result.severity_counts()
    total = sum(counts.values())

    grouped: dict[str, list] = {k: [] for k in SECTIONS}
    for i in result.issues:
        sec = SECTION_OF.get(i.kind, "content")
        grouped.setdefault(sec, []).append(i)
    for lst in grouped.values():
        lst.sort(key=lambda i: (SEV_RANK.get(i.severity, 2), i.title))

    per_page: dict[str, int] = {}
    for i in result.issues:
        per_page[i.page] = per_page.get(i.page, 0) + 1

    cat_cards = ""
    for sec_key, (sec_title, sec_desc) in SECTIONS.items():
        items = grouped.get(sec_key, [])
        if not items and sec_key not in ("security", "seo", "performance"):
            pass
        c = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for i in items:
            if i.severity in c:
                c[i.severity] += 1
        pills = "".join(
            f"<span class='pill'>{v} {s}</span>"
            for s, v in c.items() if v
        ) or "<span class='pill'>0 issues</span>"
        cat_cards += f"<div class='cat'><b>{sec_title}</b><p>{sec_desc}</p><div class='counts'>{pills}</div></div>"

    def finding_card(i) -> str:
        cat = CATEGORIES.get(i.kind, ("General", "", ""))[0]
        ev = ""
        if i.evidence:
            rows = "".join(f"{esc(str(k))}: {esc(str(v))[:220]}" for k, v in list(i.evidence.items())[:8])
            ev = f"<details class='evbox'><summary>Evidence ({len(i.evidence)} fields)</summary><pre>{rows}</pre></details>"
        fix = f"<div class='fix'><b>Fix: </b>{esc(i.recommendation)}</div>" if i.recommendation else ""
        check = f"<div class='checkline'>check: {esc(i.check or i.kind)} · kind: {esc(i.kind)}</div>" if (i.check or i.kind) else ""
        return f"""<div class="find {esc(i.severity)}" data-sev="{esc(i.severity)}">
<div class="find-head"><h3>{esc(i.title)}</h3>
<span class="badge b-{esc(i.severity)}">{esc(i.severity)}</span><span class="badge b-cat">{esc(cat)}</span></div>
<div class="where">{esc(i.page)}</div>
<p class="desc">{esc(i.detail)}</p>{fix}{check}{ev}{_img_tag(out_dir, i.screenshot)}</div>"""

    sections_html = ""
    for sec_key, (sec_title, sec_desc) in SECTIONS.items():
        items = grouped.get(sec_key, [])
        sections_html += f"<h3 class='sec-t' id='sec-{sec_key}'>{sec_title} <span class='cnt'>{len(items)}</span></h3><p class='sec-d'>{sec_desc}</p>"
        sections_html += "".join(finding_card(i) for i in items) if items else \
            "<div class='empty'>No issues found in this category.</div>"

    if result.pages:
        avg_load = sum(p.load_ms for p in result.pages) / len(result.pages)
        tot_kb = sum(p.transfer_kb or 0 for p in result.pages)
    else:
        avg_load, tot_kb = 0, 0
    rows = ""
    for p in sorted(result.pages, key=lambda p: -p.load_ms):
        n_iss = per_page.get(p.url, 0)
        load_cls = "badt" if p.load_ms > 3000 else "warnt" if p.load_ms > 1500 else "oktag"
        rows += (f"<tr><td class='mono'>{esc(p.url)}<br><span style='color:#6b7280'>{esc((p.title or '')[:70])}</span></td>"
                 f"<td class='num'>{p.status if p.status is not None else '—'}</td>"
                 f"<td class='num {load_cls}'>{p.load_ms} ms</td>"
                 f"<td class='num'>{p.requests}</td><td class='num'>{p.transfer_kb:.0f} KB</td>"
                 f"<td class='num'>{p.dom_nodes}</td><td class='num'>{'<b class=badt>'+str(n_iss)+'</b>' if n_iss else '<span class=oktag>0</span>'}</td></tr>")
    pages_tbl = f"""<table class="tbl"><tr><th>Page</th><th style="text-align:right">HTTP</th>
<th style="text-align:right">Load</th><th style="text-align:right">Reqs</th><th style="text-align:right">Weight</th>
<th style="text-align:right">DOM</th><th style="text-align:right">Issues</th></tr>{rows}</table>"""

    failed_checks = {i.check for i in result.issues if i.check}
    cov_rows = ""
    try:
        from .models import DEEP_CHECKS
    except Exception:
        DEEP_CHECKS = []
    by_cat: dict[str, list] = {}
    for c in DEEP_CHECKS:
        by_cat.setdefault(c.get("category", "?"), []).append(c)
    for cat_name, checks in by_cat.items():
        for c in checks:
            failed = c["id"] in failed_checks
            cov_rows += (f"<tr><td>{esc(cat_name)}</td><td>{esc(c['label'])}<br>"
                         f"<span class='checkline'>{esc(c['id'])}</span></td>"
                         f"<td><span class='{'fail' if failed else 'pass'}'>{'attention' if failed else 'pass'}</span></td></tr>")
    cov_tbl = f"<table class='tbl'><tr><th>Group</th><th>Deep check</th><th>Status</th></tr>{cov_rows}</table>"

    if result.hidden_pages:
        hid = "".join(
            f"<tr><td class='mono'>{esc(h.get('url', ''))}</td><td>{esc(h.get('via', ''))}</td>"
            f"<td class='mono'>{esc(str(h.get('detail', ''))[:160])}</td></tr>"
            for h in result.hidden_pages)
        hid_tbl = f"<table class='tbl'><tr><th>URL</th><th>Discovered via</th><th>Detail</th></tr>{hid}</table>"
    else:
        hid_tbl = "<div class='empty'>No exposed paths found. robots.txt, sitemap.xml and common probes returned nothing sensitive.</div>"

    sev_total = max(1, total)
    bar = (f"<div class='sevbar'><i style='width:{counts['critical']/sev_total*100:.1f}%;background:#b42318'></i>"
           f"<i style='width:{counts['high']/sev_total*100:.1f}%;background:#c2410c'></i>"
           f"<i style='width:{counts['medium']/sev_total*100:.1f}%;background:#a16207'></i>"
           f"<i style='width:{counts['low']/sev_total*100:.1f}%;background:#047857'></i></div>")

    html_doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Website audit — {esc(result.target)} ({health}/100)</title>
<style>{CSS}</style></head><body>
<div class="masthead"><div class="masthead-in">
<div class="brand">Website audit <small>health check</small></div>
<span class="url">{esc(result.target)}</span>
<nav><a href="#summary">Summary</a><a href="#findings">Findings</a><a href="#pages">Pages</a>
<a href="#coverage">Coverage</a><a href="#exposure">Exposure</a>
<button class="btn ghost" onclick="window.print()">Print</button></nav>
</div></div>
<div class="wrap">
<div class="hero"><div class="ring-wrap">{_ring(health)}<br>{_verdict(health, weird, verdict, sub)}</div>
<div><h1>{esc(result.target)}</h1>
<p class="lede">Crawl with link checks, button tests, SEO, accessibility, performance and security checks. Each finding lists evidence and a fix.</p>
<div class="meta-row"><span>Checked <b>{esc(result.started_at)}</b></span><span>Duration <b>{result.duration_s}s</b></span>
<span>Pages <b>{len(result.pages)}</b></span><span>Checks <b>{len(DEEP_CHECKS)}</b></span><span>Forms tested <b>{result.forms_tested}</b></span></div>
{bar}
<div class="sev-legend"><span><i class="dotk" style="background:#b42318"></i>{counts['critical']} critical</span>
<span><i class="dotk" style="background:#c2410c"></i>{counts['high']} high</span>
<span><i class="dotk" style="background:#a16207"></i>{counts['medium']} medium</span>
<span><i class="dotk" style="background:#047857"></i>{counts['low']} low</span>
<span style="margin-left:auto">{total} findings total</span></div>
</div></div>
<div class="kpis">
<div class="kpi"><div class="n">{health}<small>/100</small></div><div class="l">Health</div></div>
<div class="kpi"><div class="n">{len(result.pages)}</div><div class="l">Pages</div><small>same-origin crawl</small></div>
<div class="kpi"><div class="n">{total}</div><div class="l">Findings</div><small>{counts['critical']} critical · {counts['high']} high</small></div>
<div class="kpi"><div class="n">{avg_load:.0f}<small> ms</small></div><div class="l">Avg load</div><small>budget 3000 ms</small></div>
<div class="kpi"><div class="n">{tot_kb:.0f}<small> KB</small></div><div class="l">Weight</div><small>total transfer</small></div>
<div class="kpi"><div class="n">{len(result.hidden_pages)}</div><div class="l">Exposed paths</div><small>probe hits</small></div>
</div>
<div class="panel" id="summary"><h2>Summary</h2><p class="sub">The main issues, in plain language.</p>{_summary(result)}
<hr class="sep"><h2 style="margin-top:16px">Findings by category</h2><p class="sub">Counts per audit area.</p><div class="catgrid">{cat_cards}</div></div>
<div class="panel" id="findings"><h2>Findings ({total})</h2>
<p class="sub">Ordered critical to low. Filter by severity or search for a URL, title or check id. Select a screenshot to enlarge it.</p>
<div class="toolbar"><button class="chip active" onclick="setFilter('all',this)">All</button>
<button class="chip" onclick="setFilter('critical',this)">Critical</button>
<button class="chip" onclick="setFilter('high',this)">High</button>
<button class="chip" onclick="setFilter('medium',this)">Medium</button>
<button class="chip" onclick="setFilter('low',this)">Low</button>
<input class="search" id="q" placeholder="Search findings..." oninput="applySearch()"></div>
{sections_html}</div>
<div class="panel" id="pages"><h2>Pages ({len(result.pages)})</h2>
<p class="sub">Slowest first. Reqs is sub-resources, Weight is transfer, DOM is element count.</p>{pages_tbl}</div>
<div class="panel" id="coverage"><h2>Coverage ({len(DEEP_CHECKS)} checks)</h2>
<p class="sub">Every check that ran. Attention means at least one finding uses that check.</p>{cov_tbl}</div>
<div class="panel" id="exposure"><h2>Exposed paths ({len(result.hidden_pages)})</h2>
<p class="sub">robots.txt, sitemap.xml and common probes that returned HTTP 2xx. Check each one is intentional.</p>{hid_tbl}</div>
<footer>Generated by website-autospy · {esc(result.started_at)} · {result.duration_s}s ·
<span class="mono">website-autospy {esc(result.target)}</span></footer>
</div><div id="lb" title="click to close"></div><script>{JS}</script></body></html>"""
    rp = out_dir / "report.html"
    rp.write_text(html_doc)
    return rp
