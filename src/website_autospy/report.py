"""Professional site-audit HTML report (self-contained, no CDN)."""
from __future__ import annotations

import base64
import html
from pathlib import Path

from .models import AutopsyResult

# kind -> (Category label, icon, blurb)
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "security": ("Security", "🛡", "Headers, transport, cookies & exposed paths"),
    "seo": ("SEO", "🔍", "Crawlability, metadata & heading structure"),
    "a11y": ("Accessibility", "♿", "Names, labels, language & keyboard support"),
    "perf": ("Performance", "⚡", "Transfer weight, TTFB & oversized assets"),
    "slow": ("Performance", "⚡", "Transfer weight, TTFB & oversized assets"),
    "resource": ("Stability", "🧩", "Failed requests & broken sub-resources"),
    "js_error": ("Stability", "🧩", "Failed requests & broken sub-resources"),
    "broken_link": ("Links", "🔗", "Integrity & hygiene of every discovered link"),
    "content": ("Content & UX", "🖼", "Images, tap targets & layout stability"),
    "dead_button": ("Interactivity", "🖱", "Click-tested controls & latency budgets"),
    "form": ("Forms", "📝", "Structure, labels, autocomplete & safe handling"),
}

SECTION_ORDER = ["security", "seo", "a11y", "perf", "resource", "broken_link", "content", "dead_button", "form", "slow", "js_error"]
# normalise kinds into display sections
SECTION_OF = {
    "security": "security", "seo": "seo", "a11y": "a11y",
    "perf": "performance", "slow": "performance",
    "resource": "stability", "js_error": "stability",
    "broken_link": "links", "content": "content",
    "dead_button": "interactivity", "form": "forms",
}

SECTIONS: dict[str, tuple[str, str, str]] = {
    "security": ("🛡 Security", "Headers, HTTPS, cookies & exposed paths"),
    "seo": ("🔍 SEO", "Titles, meta, canonical, headings & social tags"),
    "a11y": ("♿ Accessibility", "Alt text, names, lang, focus & mobile viewport"),
    "performance": ("⚡ Performance", "Load budgets, TTFB, weight & heavy assets"),
    "stability": ("🧩 Stability", "JS runtime errors & failed sub-resources"),
    "links": ("🔗 Links", "Broken-link verification & link hygiene"),
    "content": ("🖼 Content & UX", "Broken images, tap targets & layout shift"),
    "interactivity": ("🖱 Interactivity", "Dead-button tests & click latency"),
    "forms": ("📝 Forms", "Submit controls, labels & autocomplete"),
}

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

CSS = """
:root{--bg:#f4f6fb;--card:#ffffff;--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;
--blue:#2563eb;--navy:#0f172a;--crit:#dc2626;--high:#ea580c;--med:#b45309;--low:#047857;
--critbg:#fef2f2;--highbg:#fff7ed;--medbg:#fffbeb;--lowbg:#ecfdf5}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;font-size:15px;line-height:1.55}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
/* top bar */
.topbar{position:sticky;top:0;z-index:50;background:rgba(15,23,42,.97);color:#e2e8f0;backdrop-filter:blur(6px);border-bottom:1px solid #1e293b}
.topbar-in{max-width:1200px;margin:0 auto;padding:10px 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.4px}
.brand .dot{width:11px;height:11px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 4px rgba(34,197,94,.18)}
.brand small{font-weight:500;color:#94a3b8}
.topbar .url{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:#bfdbfe;word-break:break-all}
.topbar nav{margin-left:auto;display:flex;gap:4px;flex-wrap:wrap}
.topbar nav a{color:#cbd5e1;font-size:13px;padding:6px 10px;border-radius:8px}
.topbar nav a:hover{background:#1e293b;color:#fff;text-decoration:none}
.btn{background:var(--blue);color:#fff;border:0;border-radius:9px;padding:8px 14px;font-size:13px;font-weight:700;cursor:pointer}
.btn:hover{background:#1d4ed8}.btn.ghost{background:transparent;border:1px solid #334155;color:#e2e8f0}
/* layout */
.wrap{max-width:1200px;margin:0 auto;padding:26px 22px 60px}
.hero{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:26px;display:grid;grid-template-columns:220px 1fr;gap:26px;box-shadow:0 8px 28px rgba(15,23,42,.06)}
@media(max-width:820px){.hero{grid-template-columns:1fr}}
.ring-wrap{text-align:center}
.ring{width:180px;height:180px}
.ring .num{font-size:38px;font-weight:800}.ring .of{font-size:13px;color:var(--mut)}
.verdict-pill{display:inline-block;margin-top:10px;padding:5px 14px;border-radius:99px;font-size:13px;font-weight:800;letter-spacing:.3px}
.v-good{background:#dcfce7;color:#166534}.v-ok{background:#fef9c3;color:#854d0e}.v-bad{background:#ffedd5;color:#9a3412}.v-crit{background:#fee2e2;color:#991b1b}
.hero h1{margin:0 0 4px;font-size:26px;letter-spacing:-.3px}
.hero .lede{color:var(--mut);margin:0 0 12px}
.meta-row{display:flex;gap:16px;flex-wrap:wrap;color:var(--mut);font-size:13px;margin-bottom:12px}
.meta-row b{color:var(--ink)}
.sevbar{display:flex;height:14px;border-radius:99px;overflow:hidden;background:#eef2f7;margin:8px 0 4px}
.sevbar i{display:block;height:100%}
.sev-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--mut)}
.dotk{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px}
/* kpis */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.kpi .n{font-size:26px;font-weight:800}.kpi .l{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.8px}
.kpi small{color:var(--mut);font-size:12px}
/* panels */
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin-top:16px;box-shadow:0 4px 16px rgba(15,23,42,.04)}
.panel h2{margin:0 0 2px;font-size:19px}.panel .sub{color:var(--mut);font-size:13.5px;margin:0 0 12px}
.catgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.cat{background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:13px 14px}
.cat b{font-size:14.5px}.cat p{margin:2px 0 8px;color:var(--mut);font-size:12.5px}
.cat .counts{display:flex;gap:6px;flex-wrap:wrap}
.pill{font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:99px;background:#eef2f7;color:#334155}
.sumlist{margin:6px 0 0;padding-left:20px}.sumlist li{margin:5px 0}
/* toolbar */
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0 4px}
.chip{border:1px solid var(--line);background:#fff;border-radius:99px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer;color:#334155}
.chip.active{background:var(--navy);color:#fff;border-color:var(--navy)}
.search{margin-left:auto;border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-size:13.5px;min-width:220px}
/* findings */
.sec-t{margin:26px 0 4px;font-size:18px;display:flex;align-items:baseline;gap:10px}
.sec-t .cnt{font-size:12.5px;background:#0f172a;color:#fff;border-radius:99px;padding:2px 10px;font-weight:700}
.sec-d{color:var(--mut);font-size:13px;margin:0 0 10px}
.find{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:10px 0;border-left:6px solid var(--med)}
.find.critical{border-left-color:var(--crit)}.find.high{border-left-color:var(--high)}.find.medium{border-left-color:#d97706}.find.low{border-left-color:#10b981}
.find-head{display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}
.find-head h3{margin:0;font-size:15.5px;flex:1;min-width:200px}
.badge{font-size:11px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;padding:3px 10px;border-radius:99px;white-space:nowrap}
.b-critical{background:var(--critbg);color:var(--crit);border:1px solid #fecaca}
.b-high{background:var(--highbg);color:var(--high);border:1px solid #fed7aa}
.b-medium{background:var(--medbg);color:var(--med);border:1px solid #fde68a}
.b-low{background:var(--lowbg);color:var(--low);border:1px solid #a7f3d0}
.b-cat{background:#eef2ff;color:#4338ca;border:1px solid #c7d2fe}
.find .where{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;color:var(--mut);word-break:break-all;margin:6px 0}
.find p.desc{margin:6px 0;color:#334155;font-size:14px}
.fix{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:10px 12px;font-size:13.5px;margin:8px 0}
.fix b{color:#166534}
.checkline{font-size:12px;color:var(--mut);font-family:ui-monospace,Menlo,Consolas,monospace}
.find img.ev{max-width:100%;border:1px solid var(--line);border-radius:10px;margin-top:10px;cursor:zoom-in;max-height:340px;object-fit:contain;background:#f8fafc}
details.evbox{margin-top:8px;font-size:12.5px}details.evbox summary{cursor:pointer;color:var(--blue);font-weight:600}
details.evbox pre{background:#0f172a;color:#dbeafe;padding:12px;border-radius:10px;overflow:auto;font-size:12px}
/* tables */
table.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.7px;color:var(--mut);padding:9px 10px;border-bottom:2px solid var(--line)}
.tbl td{padding:9px 10px;border-bottom:1px solid #f1f5f9;vertical-align:top}
.tbl tr:hover td{background:#f8fafc}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;word-break:break-all}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.oktag{color:#15803d;font-weight:700}.badt{color:#b91c1c;font-weight:700}.warnt{color:#b45309;font-weight:700}
.pass{background:#dcfce7;color:#166534;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700}
.fail{background:#fee2e2;color:#991b1b;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700}
/* lightbox */
#lb{position:fixed;inset:0;background:rgba(2,6,23,.85);display:none;align-items:center;justify-content:center;z-index:99;padding:20px}
#lb img{max-width:94vw;max-height:90vh;border-radius:12px;background:#fff}
#lb.show{display:flex}
footer{color:var(--mut);text-align:center;font-size:12.5px;padding:26px}
.empty{background:#f8fafc;border:1px dashed #cbd5e1;border-radius:12px;padding:16px;color:var(--mut);font-size:14px}
@media print{.topbar nav,.toolbar,.btn,#lb{display:none}.wrap{padding:0}.panel,.hero{box-shadow:none;break-inside:avoid}}
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
    # score = health 0..100
    color = "#16a34a" if score >= 80 else "#ca8a04" if score >= 55 else "#ea580c" if score >= 30 else "#dc2626"
    r = 74
    circ = 2 * 3.14159 * r
    off = circ * (1 - score / 100)
    return f"""<svg class="ring" viewBox="0 0 180 180">
<circle cx="90" cy="90" r="{r}" fill="none" stroke="#eef2f7" stroke-width="16"/>
<circle cx="90" cy="90" r="{r}" fill="none" stroke="{color}" stroke-width="16" stroke-linecap="round"
 stroke-dasharray="{circ:.1f}" stroke-dashoffset="{off:.1f}" transform="rotate(-90 90 90)"/>
<text x="90" y="88" text-anchor="middle" class="num" fill="#0f172a">{score}</text>
<text x="90" y="110" text-anchor="middle" class="of" fill="#64748b">/ 100 health</text></svg>"""


def _verdict(score: int, weird: int, verdict: str, sub: str) -> str:
    cls = "v-good" if score >= 80 else "v-ok" if score >= 55 else "v-bad" if score >= 30 else "v-crit"
    return f"<span class='verdict-pill {cls}'>{html.escape(verdict)} · {weird}/100 risk</span><p class='lede' style='margin-top:10px'>{html.escape(sub)}</p>"


def _summary(result: AutopsyResult) -> str:
    """Auto-written executive summary from measured data (no fluff)."""
    bullets: list[str] = []
    counts = result.severity_counts()
    total = sum(counts.values())
    if total == 0:
        return "<ul class='sumlist'><li><b>No defects measured.</b> All deep checks passed on the crawled pages. Re-run after the next deploy — health decays fast.</li></ul>"
    # top offending categories
    by_kind: dict[str, int] = {}
    for i in result.issues:
        by_kind[i.kind] = by_kind.get(i.kind, 0) + 1
    top = sorted(by_kind.items(), key=lambda kv: -kv[1])[:3]
    names = {"security": "security", "seo": "SEO", "a11y": "accessibility", "perf": "performance",
             "slow": "load performance", "resource": "broken sub-resources", "js_error": "JS errors",
             "broken_link": "broken links", "content": "content/UX", "dead_button": "dead controls", "form": "forms"}
    topline = ", ".join(f"<b>{n}</b> ({names.get(k, k)})" for k, n in top)
    bullets.append(f"<li>Largest defect clusters: {topline}. Fix in that order — it removes the most user pain per hour.</li>")
    if result.pages:
        worst = max(result.pages, key=lambda p: p.load_ms)
        avg = sum(p.load_ms for p in result.pages) / len(result.pages)
        bullets.append(f"<li>Slowest page is <span class='mono'>{html.escape(worst.url)}</span> at <b>{worst.load_ms} ms</b> (average {avg:.0f} ms across {len(result.pages)} pages). Budget is 3000 ms.</li>")
    sec = [i for i in result.issues if i.kind == "security"]
    if sec:
        crit = sum(1 for i in sec if i.severity in ("critical", "high"))
        bullets.append(f"<li>Security needs attention: <b>{len(sec)} finding(s)</b>{f', {crit} high/critical' if crit else ''} — start with headers and mixed content, they are one-deploy fixes.</li>")
    dead = len(result.dead_buttons)
    if dead:
        bullets.append(f"<li><b>{dead} control(s) do nothing when clicked</b> — each is a conversion leak. Replace or remove them before the next release.</li>")
    if result.hidden_pages:
        bullets.append(f"<li><b>{len(result.hidden_pages)} exposed path(s)</b> respond with HTTP 2xx (robots/sitemap plus common admin probes). Confirm each is intentional.</li>")
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

    # group issues into display sections
    grouped: dict[str, list] = {k: [] for k in SECTIONS}
    for i in result.issues:
        sec = SECTION_OF.get(i.kind, "content")
        grouped.setdefault(sec, []).append(i)
    for lst in grouped.values():
        lst.sort(key=lambda i: (SEV_RANK.get(i.severity, 2), i.title))

    # per-page issue counts
    per_page: dict[str, int] = {}
    for i in result.issues:
        per_page[i.page] = per_page.get(i.page, 0) + 1

    # ---- category overview cards ----
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
            f"<span class='pill' style='background:{'#fee2e2' if s=='critical' else '#ffedd5' if s=='high' else '#fef9c3' if s=='medium' else '#dcfce7'}'>{v} {s}</span>"
            for s, v in c.items() if v
        ) or "<span class='pill'>0 issues ✓</span>"
        cat_cards += f"<div class='cat'><b>{sec_title}</b><p>{sec_desc}</p><div class='counts'>{pills}</div></div>"

    # ---- findings html ----
    def finding_card(i) -> str:
        cat = CATEGORIES.get(i.kind, ("General", "•", ""))[0]
        ev = ""
        if i.evidence:
            rows = "".join(f"{esc(str(k))}: {esc(str(v))[:220]}" for k, v in list(i.evidence.items())[:8])
            ev = f"<details class='evbox'><summary>Evidence ({len(i.evidence)} fields)</summary><pre>{rows}</pre></details>"
        fix = f"<div class='fix'><b>How to fix → </b>{esc(i.recommendation)}</div>" if i.recommendation else ""
        check = f"<div class='checkline'>check: {esc(i.check or i.kind)} · kind: {esc(i.kind)}</div>" if (i.check or i.kind) else ""
        return f"""<div class="find {esc(i.severity)}" data-sev="{esc(i.severity)}">
<div class="find-head"><h3>{esc(i.title)}</h3>
<span class="badge b-{esc(i.severity)}">{esc(i.severity)}</span><span class="badge b-cat">{esc(cat)}</span></div>
<div class="where">↳ {esc(i.page)}</div>
<p class="desc">{esc(i.detail)}</p>{fix}{check}{ev}{_img_tag(out_dir, i.screenshot)}</div>"""

    sections_html = ""
    for sec_key, (sec_title, sec_desc) in SECTIONS.items():
        items = grouped.get(sec_key, [])
        sections_html += f"<h3 class='sec-t' id='sec-{sec_key}'>{sec_title} <span class='cnt'>{len(items)}</span></h3><p class='sec-d'>{sec_desc}</p>"
        sections_html += "".join(finding_card(i) for i in items) if items else \
            "<div class='empty'>✓ Pass — no defects measured in this category on crawled pages.</div>"

    # ---- pages table ----
    if result.pages:
        avg_load = sum(p.load_ms for p in result.pages) / len(result.pages)
        tot_kb = sum(p.transfer_kb or 0 for p in result.pages)
    else:
        avg_load, tot_kb = 0, 0
    rows = ""
    for p in sorted(result.pages, key=lambda p: -p.load_ms):
        n_iss = per_page.get(p.url, 0)
        load_cls = "badt" if p.load_ms > 3000 else "warnt" if p.load_ms > 1500 else "oktag"
        rows += (f"<tr><td class='mono'>{esc(p.url)}<br><span style='color:#64748b'>{esc((p.title or '')[:70])}</span></td>"
                 f"<td class='num'>{p.status if p.status is not None else '—'}</td>"
                 f"<td class='num {load_cls}'>{p.load_ms} ms</td>"
                 f"<td class='num'>{p.requests}</td><td class='num'>{p.transfer_kb:.0f} KB</td>"
                 f"<td class='num'>{p.dom_nodes}</td><td class='num'>{'<b class=badt>'+str(n_iss)+'</b>' if n_iss else '<span class=oktag>0</span>'}</td></tr>")
    pages_tbl = f"""<table class="tbl"><tr><th>Page</th><th style="text-align:right">HTTP</th>
<th style="text-align:right">Load</th><th style="text-align:right">Reqs</th><th style="text-align:right">Weight</th>
<th style="text-align:right">DOM</th><th style="text-align:right">Issues</th></tr>{rows}</table>"""

    # ---- coverage ledger ----
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
                         f"<td><span class='{'fail' if failed else 'pass'}'>{'● attention' if failed else '✓ pass'}</span></td></tr>")
    cov_tbl = f"<table class='tbl'><tr><th>Group</th><th>Deep check</th><th>Status</th></tr>{cov_rows}</table>"

    # ---- hidden paths ----
    if result.hidden_pages:
        hid = "".join(
            f"<tr><td class='mono'>{esc(h.get('url', ''))}</td><td>{esc(h.get('via', ''))}</td>"
            f"<td class='mono'>{esc(str(h.get('detail', ''))[:160])}</td></tr>"
            for h in result.hidden_pages)
        hid_tbl = f"<table class='tbl'><tr><th>URL</th><th>Discovered via</th><th>Detail</th></tr>{hid}</table>"
    else:
        hid_tbl = "<div class='empty'>✓ No exposed admin/debug paths responded — robots/sitemap and common probes returned nothing sensitive.</div>"

    sev_total = max(1, total)
    bar = (f"<div class='sevbar'><i style='width:{counts['critical']/sev_total*100:.1f}%;background:#dc2626'></i>"
           f"<i style='width:{counts['high']/sev_total*100:.1f}%;background:#ea580c'></i>"
           f"<i style='width:{counts['medium']/sev_total*100:.1f}%;background:#d97706'></i>"
           f"<i style='width:{counts['low']/sev_total*100:.1f}%;background:#10b981'></i></div>")

    html_doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Site Audit — {esc(result.target)} · {health}/100</title>
<style>{CSS}</style></head><body>
<div class="topbar"><div class="topbar-in">
<div class="brand"><span class="dot"></span>SITE AUDIT <small>deep website health check</small></div>
<span class="url">{esc(result.target)}</span>
<nav><a href="#summary">Summary</a><a href="#findings">Findings</a><a href="#pages">Pages</a>
<a href="#coverage">Coverage</a><a href="#exposure">Exposure</a>
<button class="btn ghost" onclick="window.print()">Print / PDF</button></nav>
</div></div>
<div class="wrap">
<div class="hero"><div class="ring-wrap">{_ring(health)}<br>{_verdict(health, weird, verdict, sub)}</div>
<div><h1>Audit report — {esc(result.target)}</h1>
<p class="lede">Playwright-powered crawl with click tests, network forensics, SEO, accessibility, performance and security audits. Every finding below carries measured evidence and a fix.</p>
<div class="meta-row"><span>Examined <b>{esc(result.started_at)}</b></span><span>Duration <b>{result.duration_s}s</b></span>
<span>Pages <b>{len(result.pages)}</b></span><span>Checks <b>{len(DEEP_CHECKS)}</b></span><span>Forms tested <b>{result.forms_tested}</b></span></div>
{bar}
<div class="sev-legend"><span><i class="dotk" style="background:#dc2626"></i>{counts['critical']} critical</span>
<span><i class="dotk" style="background:#ea580c"></i>{counts['high']} high</span>
<span><i class="dotk" style="background:#d97706"></i>{counts['medium']} medium</span>
<span><i class="dotk" style="background:#10b981"></i>{counts['low']} low</span>
<span style="margin-left:auto">{total} total findings</span></div>
</div></div>
<div class="kpis">
<div class="kpi"><div class="n">{health}<small>/100</small></div><div class="l">Health score</div></div>
<div class="kpi"><div class="n">{len(result.pages)}</div><div class="l">Pages crawled</div><small>depth-limited BFS, same origin</small></div>
<div class="kpi"><div class="n">{total}</div><div class="l">Findings</div><small>{counts['critical']} crit · {counts['high']} high</small></div>
<div class="kpi"><div class="n">{avg_load:.0f}<small> ms</small></div><div class="l">Avg load</div><small>budget 3000 ms</small></div>
<div class="kpi"><div class="n">{tot_kb:.0f}<small> KB</small></div><div class="l">Crawled weight</div><small>sum of page transfers</small></div>
<div class="kpi"><div class="n">{len(result.hidden_pages)}</div><div class="l">Exposed paths</div><small>robots + probe hits</small></div>
</div>
<div class="panel" id="summary"><h2>Executive summary</h2><p class="sub">What matters most, in plain language — generated from the measurements.</p>{_summary(result)}
<h2 style="margin-top:16px">Findings by category</h2><p class="sub">Coverage across all nine audit dimensions.</p><div class="catgrid">{cat_cards}</div></div>
<div class="panel" id="findings"><h2>Detailed findings ({total})</h2>
<p class="sub">Sorted critical → low. Filter by severity or search any URL, title or check id. Screenshots are inline evidence — click to enlarge.</p>
<div class="toolbar"><button class="chip active" onclick="setFilter('all',this)">All</button>
<button class="chip" onclick="setFilter('critical',this)">Critical</button>
<button class="chip" onclick="setFilter('high',this)">High</button>
<button class="chip" onclick="setFilter('medium',this)">Medium</button>
<button class="chip" onclick="setFilter('low',this)">Low</button>
<input class="search" id="q" placeholder="Search findings…" oninput="applySearch()"></div>
{sections_html}</div>
<div class="panel" id="pages"><h2>Pages crawled ({len(result.pages)})</h2>
<p class="sub">Sorted slowest first. Reqs = sub-resources, Weight = measured transfer, DOM = element count.</p>{pages_tbl}</div>
<div class="panel" id="coverage"><h2>Audit coverage ({len(DEEP_CHECKS)} deep checks)</h2>
<p class="sub">Every check the crawler ran. “Attention” means at least one finding maps to that check — not a failure of the check itself.</p>{cov_tbl}</div>
<div class="panel" id="exposure"><h2>Exposed paths ({len(result.hidden_pages)})</h2>
<p class="sub">robots.txt, sitemap.xml and common admin/debug probes that answered HTTP 2xx. Verify each is intentional.</p>{hid_tbl}</div>
<footer>Generated by <b>website-autospy</b> · {esc(result.started_at)} · {result.duration_s}s crawl ·
<span class="mono">website-autospy {esc(result.target)}</span></footer>
</div><div id="lb" title="click to close"></div><script>{JS}</script></body></html>"""
    rp = out_dir / "report.html"
    rp.write_text(html_doc)
    return rp
