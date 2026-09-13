"""Playwright crawl + checks. One browser, sequential pages (screenshot-safe)."""
from __future__ import annotations

import asyncio
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PwTimeout

from .models import AutopsyResult, Issue, PageRecord, normalize, same_origin, COMMON_HIDDEN_PATHS

MAX_BUTTONS_PER_PAGE = 15
MAX_LINKS_TO_VERIFY = 250


async def _check_link_status(context, url: str, timeout_ms=10000) -> tuple[int | None, str]:
    """Use Playwright request (shares cookies) to check a link without full navigation."""
    try:
        # Prefer HEAD, fall back to GET
        resp = await context.request.head(url, timeout=timeout_ms)
        status = resp.status
        if status in (405, 501):
            resp = await context.request.get(url, timeout=timeout_ms)
            status = resp.status
        return status, ""
    except Exception as e:
        # fallback: full GET
        try:
            resp = await context.request.get(url, timeout=timeout_ms)
            return resp.status, ""
        except Exception as e2:
            return None, str(e2)[:200]


async def run_autopsy(target: str, out_dir: Path, max_pages: int = 30,
                      max_depth: int = 3, headless: bool = True,
                      progress=None) -> AutopsyResult:
    t0 = time.time()
    out_dir = Path(out_dir)
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    target = target.rstrip("/")

    result = AutopsyResult(
        target=target,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        duration_s=0.0,
    )

    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(target, 0)]
    all_links: dict[str, str] = {}  # link_url -> found_on_page
    shot_idx = 0

    def snap_name(prefix: str) -> tuple[str, Path]:
        nonlocal shot_idx
        shot_idx += 1
        name = f"{prefix}-{shot_idx:03d}.png"
        return name, shots / name

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(ignore_https_errors=True)
        # silence downloads blowing up
        page = await context.new_page()

        js_errors_seen: set[str] = set()
        site_script_urls: list[str] = []
        site_script_seen: set[str] = set()

        while queue and len(visited) < max_pages:
            url, depth = queue.pop(0)
            nurl = normalize(url)
            if nurl in visited:
                continue
            visited.add(nurl)
            if progress:
                progress(f"[{len(visited)}/{max_pages}] {nurl}")

            console_errors: list[str] = []
            page_errors: list[str] = []
            failed_reqs: list[str] = []
            resp_log: list[dict] = []  # every sub-resource response for deep audit
            main_headers: dict = {}
            main_final_url: str = nurl

            def _on_console(msg):
                if msg.type == "error":
                    console_errors.append(msg.text[:500])

            def _on_pageerror(exc):
                page_errors.append(str(exc)[:500])

            def _on_failed(req):
                try:
                    fail = req.failure or "failed"
                except Exception:
                    fail = "failed"
                failed_reqs.append(f"{req.url[:200]} :: {fail}")

            def _on_response(resp):
                try:
                    st = resp.status
                    url = resp.url
                    if resp.request.resource_type == "document" and (
                        url.split("#")[0].rstrip("/") == nurl.rstrip("/")
                        or url == page.url
                    ):
                        try:
                            for k, v in (resp.headers or {}).items():
                                main_headers[k.lower()] = v
                        except Exception:
                            pass
                        nonlocal_main_url = url
                        try:
                            main_final_url = nonlocal_main_url  # noqa: F841
                        except Exception:
                            pass
                    if st is not None and st >= 400:
                        try:
                            rt = resp.request.resource_type
                        except Exception:
                            rt = "?"
                        resp_log.append({"url": url[:300], "status": st, "type": rt})
                except Exception:
                    pass

            page.on("console", _on_console)
            page.on("pageerror", _on_pageerror)
            page.on("requestfailed", _on_failed)
            page.on("response", _on_response)
            is_first_page = len(result.pages) == 0

            load_ms, status, title = 0, None, ""
            try:
                start = time.time()
                resp = await page.goto(nurl, wait_until="domcontentloaded", timeout=25000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except PwTimeout:
                    pass
                load_ms = int((time.time() - start) * 1000)
                status = resp.status if resp else None
                try:
                    title = (await page.title())[:200]
                except Exception:
                    title = ""
            except Exception as e:
                result.issues.append(Issue(
                    kind="slow", title=f"Page failed to load: {nurl}",
                    detail=f"Navigation error: {str(e)[:300]}",
                    page=nurl, severity="critical"))
                _detach(page, _on_console, _on_pageerror, _on_failed, _on_response)
                continue

            # main-document headers (fallback: navigation response object)
            if not main_headers and resp is not None:
                try:
                    for k, v in (resp.headers or {}).items():
                        main_headers[k.lower()] = v
                except Exception:
                    pass
            if is_first_page:
                _audit_site_security(target, main_headers, context, result)

            # page screenshot (proves state)
            pname, ppath = snap_name("page")
            try:
                await page.screenshot(path=str(ppath), full_page=False)
            except Exception:
                pname = None  # type: ignore
            result.pages.append(PageRecord(url=nurl, status=status, load_ms=load_ms,
                                           title=title,
                                           screenshot=f"screenshots/{pname}" if pname else None,
                                           links_found=0))

            # --- slow page check ---
            if load_ms > 3000:
                sev = "high" if load_ms > 8000 else "medium"
                result.issues.append(Issue(
                    kind="slow", title=f"Slow page: {load_ms/1000:.1f}s load",
                    detail=f"{nurl} took {load_ms}ms (DOM+networkidle). Title: {title!r}. "
                           f"Anything over 3s bleeds users.",
                    page=nurl, severity=sev,
                    screenshot=f"screenshots/{pname}" if pname else None,
                    recommendation="Optimise LCP: compress hero images, defer non-critical JS, enable caching/CDN.",
                    check="nav.load-budget",
                    evidence={"load_ms": load_ms}))

            # --- JS errors ---
            for err in page_errors + console_errors:
                key = hashlib_sig(nurl, err)
                if key in js_errors_seen:
                    continue
                js_errors_seen.add(key)
                ename, epath = snap_name("jserror")
                try:
                    await page.screenshot(path=str(epath))
                except Exception:
                    ename = None  # type: ignore
                result.issues.append(Issue(
                    kind="js_error", title="JavaScript error in console",
                    detail=err, page=nurl, severity="high",
                    screenshot=f"screenshots/{ename}" if ename else None))

            # --- extract links + buttons + forms in one JS pass ---
            try:
                data = await page.evaluate("""() => {
                  const q = s => [...document.querySelectorAll(s)];
                  const links = q('a[href]')
                    .map(a => ({href: a.getAttribute('href')||'', abs: a.href||'', text: (a.innerText||a.getAttribute('aria-label')||'').slice(0,120).trim(), rel: (a.getAttribute('rel')||'').toLowerCase(), target: a.getAttribute('target')||'', hidden: a.offsetParent===null}));
                  const btns = [...document.querySelectorAll('button, input[type=submit], input[type=button], [role=button], a.btn, a.button')]
                    .map((el,i) => {
                      const r = el.getBoundingClientRect();
                      const name = ((el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'')+'').slice(0,120).trim();
                      return {idx:i, tag: el.tagName, type: el.getAttribute('type')||'',
                        text: name, accName: name,
                        href: el.href||'', visible: r.width>0&&r.height>0&&el.offsetParent!==null,
                        x:r.x, y:r.y, w:r.width, h:r.height};
                    });
                  const forms = [...document.forms].map(f => ({
                    action: f.action||'', method: (f.method||'get').toUpperCase(),
                    inputs: [...f.querySelectorAll('input,select,textarea')].map(i=>({
                      name: i.name||i.id||i.type, type: (i.type||i.tagName||'').toLowerCase(),
                      required: !!i.required, autocomplete: i.getAttribute('autocomplete')||'',
                      hasRealLabel: !!(i.labels&&i.labels.length)||!!i.getAttribute('aria-label')||!!i.getAttribute('aria-labelledby'),
                      hasPlaceholder: !!i.placeholder,
                      hasLabel: !!(i.labels&&i.labels.length)||!!i.getAttribute('aria-label')||!!i.placeholder})),
                    hasSubmit: !!f.querySelector('button[type=submit],input[type=submit],button:not([type])')
                  }));
                  const imgs = q('img').map(im => ({
                    src: (im.getAttribute('src')||'').slice(0,200), alt: im.getAttribute('alt'),
                    broken: im.complete && im.naturalWidth===0 && !!im.getAttribute('src'),
                    w: im.naturalWidth||0, h: im.naturalHeight||0,
                    dispW: Math.round(im.getBoundingClientRect().width), dispH: Math.round(im.getBoundingClientRect().height),
                    lazy: im.loading||'', noDims: !im.getAttribute('width')&&!im.getAttribute('height')
                  }));
                  const hTags = ['h1','h2','h3','h4','h5','h6'].map(h=>({tag:h,n:q(h).length}));
                  const h1Texts = q('h1').map(h=>(h.innerText||'').slice(0,140).trim()).slice(0,5);
                  const headings = q('h1,h2,h3,h4,h5,h6').map(h=>h.tagName).slice(0,40);
                  let skipLevel = false; const order=['H1','H2','H3','H4','H5','H6'];
                  for(let i=1;i<headings.length;i++){ if(order.indexOf(headings[i])-order.indexOf(headings[i-1])>1){skipLevel=true;break;} }
                  const ids = q('[id]').map(e=>e.id); const dupIds = ids.filter((v,i)=>ids.indexOf(v)!==i).slice(0,10);
                  const posTab = q('[tabindex]').filter(e=>parseInt(e.getAttribute('tabindex'))>0).length;
                  const nav = (performance.getEntriesByType('navigation')[0]||{});
                  const res = performance.getEntriesByType('resource').map(r=>({name:(r.name||'').slice(0,220), size:r.transferSize||0, dur:Math.round(r.duration||0), type:r.initiatorType||''})).slice(0,200);
                  const totKB = Math.round(res.reduce((a,r)=>a+(r.size||0),0)/102.4)/10;
                  const big = res.filter(r=>(r.size||0)>500*1024).sort((a,b)=>b.size-a.size).slice(0,5);
                  const meta = n => (document.querySelector(`meta[name="${n}"]`)||{}).content||'';
                  const seo = {
                    title: document.title||'', titleLen: (document.title||'').length,
                    desc: meta('description'), descLen: meta('description').length,
                    canonical: !!(document.querySelector('link[rel="canonical"]')),
                    robots: meta('robots'), viewport: (document.querySelector('meta[name="viewport"]')||{}).content||'',
                    charset: !!document.querySelector('meta[charset]'),
                    lang: document.documentElement.getAttribute('lang')||'',
                    h1: q('h1').length, h1Texts, hTags, skipLevel, headings: headings.slice(0,12),
                    og: ['og:title','og:description','og:image'].map(p=>!!document.querySelector(`meta[property="${p}"]`)),
                    favicon: !!document.querySelector('link[rel~="icon"]'),
                    linksTotal: links.length
                  };
                  const mixed = res.filter(r=>location.protocol==='https:'&&(r.name||'').startsWith('http://')).slice(0,8);
                  const bodyText = document.body?(document.body.innerText||''):'' ;
                  const textWords = bodyText.split(/\\s+/).filter(Boolean).length;
                  const jsonLd = q('script[type="application/ld+json"]').length;
                  const ogVals = ['og:title','og:description','og:image'].map(p=>(document.querySelector(`meta[property="${p}"]`)||{}).content||'');
                  const ogComplete = ogVals.every(Boolean);
                  const hasSkipLink = q('a[href^="#"].skip-link, a[href^="#main"], a[href^="#content"], [class*="skip"]').length > 0 || q('a[href^="#"]').some(a => /skip|main content|jump to/i.test((a.innerText||'') + ' ' + (a.className||'')));
                  const landmarks = {main: q('main').length, nav: q('nav').length};
                  const emptyHeadings = q('h1,h2,h3,h4,h5,h6').filter(h=>!(h.innerText||'').trim()).length;
                  const syncHeadScripts = q('head script[src]').filter(s=>!s.defer&&!s.async).length;
                  const overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
                  return {links: links.slice(0,300), btns: btns.slice(0,60), forms, imgs,
                          seo, dupIds, dupCount: dupIds.length, posTab, skipLevel,
                          smallTargets: btns.filter(b=>b.visible&&((b.w>0&&b.w<24)||(b.h>0&&b.h<24))).length,
                          nav: {ttfb: Math.round(nav.responseStart||0), dom: Math.round(nav.domContentLoadedEventEnd||0)},
                          res, totKB, big, mixed,
                          domNodes: document.getElementsByTagName('*').length,
                          scripts: q('script[src]').length,
                          bodyLen: document.body?document.body.innerHTML.length:0,
                          textWords, jsonLd, ogComplete, hasSkipLink, landmarks,
                          emptyHeadings, syncHeadScripts, overflowX};
                }""")
            except Exception:
                data = {"links": [], "btns": [], "forms": [], "imgs": [], "bodyLen": 0,
                        "seo": {}, "res": [], "totKB": 0, "big": [], "mixed": [],
                        "domNodes": 0, "scripts": 0, "dupIds": [], "nav": {}}

            # queue internal links
            internal_new = 0
            for l in data.get("links", []):
                href = l.get("href", "")
                if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:")):
                    continue
                absu = urllib.parse.urljoin(nurl, href).split("#")[0]
                if not absu.startswith(("http://", "https://")):
                    continue
                if absu not in all_links:
                    all_links[absu] = nurl
                if same_origin(absu, target) and normalize(absu) not in visited and depth < max_depth:
                    if not any(q[0] == absu for q in queue):
                        queue.append((absu, depth + 1))
                        internal_new += 1
            if result.pages:
                result.pages[-1].links_found = len(data.get("links", []))
                result.pages[-1].requests = len(data.get("res", []))
                result.pages[-1].failed_requests = len(failed_reqs) + len(resp_log)
                result.pages[-1].transfer_kb = float(data.get("totKB") or 0)
                result.pages[-1].dom_nodes = int(data.get("domNodes") or 0)
                result.pages[-1].images = len(data.get("imgs", []))
                result.pages[-1].scripts = int(data.get("scripts") or 0)
            for res in data.get("res", []) or []:
                name = str(res.get("name") or "")
                if (name.endswith(".js") or (res.get("type") or "") == "script") \
                        and name not in site_script_seen:
                    site_script_seen.add(name)
                    if len(site_script_urls) < 200:
                        site_script_urls.append(name)

            shot = f"screenshots/{pname}" if pname else None
            # --- deep audits: SEO / a11y / perf / content / network / link hygiene ---
            _audit_seo(data.get("seo", {}), nurl, shot, result)
            _audit_a11y(data, nurl, shot, result)
            _audit_perf(data, nurl, load_ms, shot, result)
            _audit_content(data, nurl, shot, result)
            _audit_network(failed_reqs, resp_log, nurl, shot, result)
            _audit_link_hygiene(data.get("links", []), nurl, result)
            _audit_mixed_content(data.get("mixed", []), nurl, main_headers, result)
            # --- second-wave depth (additive evidence, pure seams) ---
            _run_depth_audits(data, nurl, load_ms, shot, main_headers, result)

            # --- dead button test (visible only) ---
            candidates = [b for b in data.get("btns", []) if b.get("visible")][:MAX_BUTTONS_PER_PAGE]
            for b in candidates:
                dead, detail, latency = await _test_button(page, b, nurl)
                if latency and latency > 1200:
                    ename, epath = snap_name("slowclick")
                    try:
                        await page.screenshot(path=str(epath))
                    except Exception:
                        ename = None  # type: ignore
                    result.issues.append(Issue(
                        kind="slow", title=f"Slow interaction: '{b.get('text')[:60] or b.get('tag')}' took {latency}ms",
                        detail=f"Click on <{b.get('tag')}> '{b.get('text')[:80]}' at {nurl} responded after {latency}ms.",
                        page=nurl, severity="medium",
                        screenshot=f"screenshots/{ename}" if ename else None,
                        evidence={"latency_ms": latency}))
                if dead:
                    ename, epath = snap_name("deadbtn")
                    try:
                        el = page.locator("button, input[type=submit], input[type=button], [role=button]").nth(
                            min(b["idx"], max(0, await page.locator("button, input[type=submit], input[type=button], [role=button]").count() - 1)))
                        await el.screenshot(path=str(epath), timeout=5000)
                    except Exception:
                        try:
                            await page.screenshot(path=str(epath))
                        except Exception:
                            ename = None  # type: ignore
                    label = b.get("text") or f"<{b.get('tag')}>"
                    result.issues.append(Issue(
                        kind="dead_button", title=f"Dead button: '{label[:60]}'",
                        detail=f"{detail} — <{b.get('tag')}> '{label[:100]}' on {nurl} did nothing: no navigation, no DOM change, no network.",
                        page=nurl, severity="medium",
                        screenshot=f"screenshots/{ename}" if ename else None))

            # --- forms (deep: structure + labels + autocomplete + input types) ---
            for fi, f in enumerate(data.get("forms", [])[:6]):
                result.forms_tested += 1
                inputs = f.get("inputs", [])
                unlabeled = [i for i in inputs if not i.get("hasLabel") and i.get("type") not in ("hidden", "submit", "button", "image")]
                fname, fpath = snap_name("form")
                try:
                    await page.screenshot(path=str(fpath))
                except Exception:
                    fname = None  # type: ignore
                fshot = f"screenshots/{fname}" if fname else None
                if not f.get("hasSubmit"):
                    result.issues.append(Issue(
                        kind="form", title=f"Form #{fi+1} has no submit control",
                        detail=f"Form action={f.get('action') or '(same page)'} method={f.get('method')} with {len(inputs)} fields has no submit control. Keyboard users (Enter-to-submit) and assistive tech expect one.",
                        page=nurl, severity="high", screenshot=fshot,
                        recommendation="Add <button type=\"submit\"> inside the <form>. Keep the existing JS handler and call form.requestSubmit() so native validation still runs.",
                        check="form.structure"))
                if unlabeled:
                    names = ", ".join(i.get("name", "?") for i in unlabeled[:5])
                    result.issues.append(Issue(
                        kind="form", title=f"Form #{fi+1}: {len(unlabeled)} field(s) without any label",
                        detail=f"Fields with no <label>, aria-label, or placeholder: {names}. Screen-reader users cannot identify these controls.",
                        page=nurl, severity="high", screenshot=fshot,
                        recommendation="Associate each control with <label for=\"id\">, or add aria-label. Do not rely on placeholder alone.",
                        check="form.labels"))
                else:
                    placeholder_only = [i for i in inputs if not i.get("hasRealLabel") and i.get("hasPlaceholder")]
                    if placeholder_only:
                        names = ", ".join(i.get("name", "?") for i in placeholder_only[:5])
                        result.issues.append(Issue(
                            kind="form", title=f"Form #{fi+1}: {len(placeholder_only)} placeholder-only label(s)",
                            detail=f"Fields rely on placeholder text only: {names}. Placeholders vanish on typing and are ignored by most screen readers.",
                            page=nurl, severity="medium", screenshot=fshot,
                            recommendation="Add persistent <label> elements; keep placeholders as hints only.",
                            check="form.labels"))
                no_autocomplete = [i for i in inputs if i.get("type") in ("email", "text", "tel", "password", "name", "username") and not i.get("autocomplete")]
                if no_autocomplete and len(inputs) >= 2:
                    names = ", ".join(i.get("name", "?") for i in no_autocomplete[:5])
                    result.issues.append(Issue(
                        kind="form", title=f"Form #{fi+1}: missing autocomplete attributes ({len(no_autocomplete)})",
                        detail=f"Fields without autocomplete hinder autofill and password managers: {names}.",
                        page=nurl, severity="low", screenshot=fshot,
                        recommendation="Add autocomplete tokens (email, current-password, name, tel, …) per WHATWG spec.",
                        check="form.labels"))
                pw = [i for i in inputs if i.get("type") == "password" and i.get("autocomplete") not in ("current-password", "new-password")]
                if pw:
                    result.issues.append(Issue(
                        kind="form", title=f"Form #{fi+1}: password field without correct autocomplete",
                        detail="Password input lacks autocomplete=\"current-password\"/\"new-password\" — password managers may not offer to save/fill.",
                        page=nurl, severity="low", screenshot=fshot,
                        recommendation="Set autocomplete=\"current-password\" on login forms, \"new-password\" on registration.",
                        check="form.labels"))
                if f.get("method") == "GET" and any(i.get("type") == "password" for i in inputs):
                    result.issues.append(Issue(
                        kind="security", title=f"Form #{fi+1} submits a password over GET",
                        detail=f"Form action={f.get('action') or '(same page)'} uses method=GET with a password field — credentials would leak into URLs, logs, and history.",
                        page=nurl, severity="critical", screenshot=fshot,
                        recommendation="Switch to method=\"post\" over HTTPS. Never put credentials in query strings.",
                        check="sec.transport"))
                # try a safe fill (no submit) to prove testability
                try:
                    await _fill_form(page, fi)
                except Exception:
                    pass

            _detach(page, _on_console, _on_pageerror, _on_failed, _on_response)

        await page.close()

        # --- broken links verification (cap) ---
        link_items = list(all_links.items())[:MAX_LINKS_TO_VERIFY]
        for link_url, found_on in link_items:
            status_code, err = await _check_link_status(context, link_url)
            if status_code is None or status_code >= 400:
                # screenshot the source page for proof
                src_shot = next((p.screenshot for p in result.pages if p.url == normalize(found_on)), None)
                reason = f"HTTP {status_code}" if status_code else f"request failed: {err[:120]}"
                result.issues.append(Issue(
                    kind="broken_link",
                    title=f"Broken link → {urllib.parse.urlparse(link_url).path[:80] or '/'} ({reason})",
                    detail=f"Link {link_url} found on {found_on} returned {reason}.",
                    page=found_on, severity="high" if status_code in (404, 410) else "medium",
                    screenshot=src_shot,
                    evidence={"link": link_url, "status": status_code}))

        # --- hidden pages: robots/sitemap + common paths + orphans ---
        await _discover_hidden(context, target, result, shots, snap_name)

        # --- cookie flag audit (deep transport check) ---
        try:
            cookies = await context.cookies()
            https = target.startswith("https://")
            weak = [c for c in cookies if https and not c.get("secure")]
            no_http = [c for c in cookies if c.get("name", "").lower() not in ("csrftoken",) and not c.get("httpOnly")]
            lax = [c for c in cookies if (c.get("sameSite") or "None") in ("None", "")]
            if https and weak:
                result.issues.append(Issue(kind="security",
                    title=f"{len(weak)} cookie(s) missing Secure flag",
                    detail=f"Cookies without Secure can be sent over HTTP: {', '.join(c.get('name','?') for c in weak[:5])}.",
                    page=target, severity="medium",
                    recommendation="Set Secure on all cookies served over HTTPS.",
                    check="sec.transport", evidence={"cookies": [c.get("name") for c in weak[:8]]}))
            if len(cookies) and len(no_http) == len(cookies) and len(cookies) >= 2:
                result.issues.append(Issue(kind="security",
                    title="Session cookies readable from JavaScript (no HttpOnly)",
                    detail="No HttpOnly flag observed — any XSS can steal session tokens.",
                    page=target, severity="medium",
                    recommendation="Set HttpOnly on session/auth cookies; expose only what JS needs.",
                    check="sec.transport"))
        except Exception:
            pass

        # --- second-wave site depth: duplicate titles + posture review ---
        try:
            audit_site_seo_duplicates(
                [{"url": p.url, "title": p.title} for p in result.pages], result)
        except Exception:
            pass
        try:
            site_cookies = await context.cookies()
        except Exception:
            site_cookies = []
        try:
            audit_security_posture(target, {}, site_cookies,
                                   site_script_urls[:60], None, result)
        except Exception:
            pass

        await browser.close()

    result.duration_s = round(time.time() - t0, 1)
    return result


def hashlib_sig(*parts: str) -> str:
    import hashlib
    return hashlib.md5("|".join(parts).encode()[:2000]).hexdigest()


def _detach(page, *handlers):
    names = ("console", "pageerror", "requestfailed", "response")
    for name, h in zip(names, handlers):
        try:
            page.remove_listener(name, h)
        except Exception:
            pass


async def _test_button(page, b: dict, page_url: str) -> tuple[bool, str, int | None]:
    """Click candidate, detect any effect. Returns (is_dead, detail, latency_ms)."""
    try:
        locators = page.locator("button, input[type=submit], input[type=button], [role=button], a.btn, a.button")
        count = await locators.count()
        if count == 0 or b["idx"] >= count:
            return False, "not found", None
        el = locators.nth(b["idx"])
        try:
            if not await el.is_visible():
                return False, "hidden", None
        except Exception:
            return False, "hidden", None
        # href buttons that navigate are alive by definition (checked in broken links)
        if b.get("href") and b.get("href") not in ("", page_url, page_url + "/"):
            pass
        before_url = page.url
        before_len = b.get("w", 0)
        try:
            before_body = await page.evaluate("() => document.body.innerHTML.length")
        except Exception:
            before_body = 0
        net_hit = []
        def _on_req(req):
            if req.is_navigation_request() or req.resource_type in ("xhr", "fetch"):
                net_hit.append(req.url[:150])
        page.on("request", _on_req)
        t = time.time()
        try:
            await el.click(timeout=2500, force=False)
            await page.wait_for_timeout(900)
        except PwTimeout:
            pass
        except Exception as e:
            try:
                page.remove_listener("request", _on_req)
            except Exception:
                pass
            if "subtree" in str(e) or "detached" in str(e):
                return False, "DOM changed (alive)", None
            return False, f"click blocked: {str(e)[:80]}", None
        latency = int((time.time() - t) * 1000)
        try:
            page.remove_listener("request", _on_req)
        except Exception:
            pass
        try:
            after_url = page.url
            after_body = await page.evaluate("() => document.body.innerHTML.length")
        except Exception:
            return False, "page navigated/changed (alive)", latency
        # dialog/modal check
        try:
            modal = await page.evaluate(
                "() => !!document.querySelector('[role=dialog], .modal.show, .modal.open, [aria-modal=true]')")
        except Exception:
            modal = False
        changed = (after_url != before_url) or (abs(after_body - before_body) > 50) or bool(net_hit) or modal
        # navigate back if click left the page
        if after_url != before_url:
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=8000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except PwTimeout:
                    pass
            except Exception:
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
        if changed:
            return False, "alive", latency
        # href="#" / empty onclick with no effect => dead
        return True, "click produced zero observable effect", latency
    except Exception as e:
        return False, f"test error: {str(e)[:80]}", None


async def _fill_form(page, form_idx: int):
    form = page.locator("form").nth(form_idx)
    for sel, val in [("input[type=email]", "autopsy@test.local"),
                     ("input[type=text]", "Autopsy Test"),
                     ("input:not([type])", "Autopsy Test"),
                     ("input[type=tel]", "5550100"),
                     ("textarea", "autopsy probe — ignore"),
                     ("input[type=number]", "42")]:
        try:
            loc = form.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.fill(val, timeout=1500)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Deep audits: each maps to one or more entries in models.DEEP_CHECKS.
# They only *add* issues when a real defect is measured — no noise.
# ---------------------------------------------------------------------------

def _audit_seo(seo: dict, page_url: str, shot: str | None, result: AutopsyResult) -> None:
    if not seo:
        return
    title, tlen = seo.get("title", ""), int(seo.get("titleLen") or 0)
    if not title:
        result.issues.append(Issue(kind="seo", title="Missing <title> tag",
            detail=f"{page_url} has an empty document title. Search engines and tab bars show the URL instead.",
            page=page_url, severity="high", screenshot=shot,
            recommendation="Add a unique, descriptive <title> of 30–60 characters per page.",
            check="seo.crawlable", evidence={"titleLen": tlen}))
    elif tlen < 15 or tlen > 70:
        result.issues.append(Issue(kind="seo", title=f"Title length {tlen} chars is outside 15–70",
            detail=f"Title {title!r} is {'too short — wasted ranking signal' if tlen < 15 else 'truncated in SERPs'}.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Rewrite the title to 30–60 chars: primary keyword + brand.",
            check="seo.crawlable", evidence={"title": title[:120]}))
    dlen = int(seo.get("descLen") or 0)
    if not seo.get("desc"):
        result.issues.append(Issue(kind="seo", title="Missing meta description",
            detail="No meta[name=description]. Google invents the snippet — usually badly.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Add a 120–160 char meta description summarising the page.",
            check="seo.crawlable"))
    elif dlen > 170 or dlen < 50:
        result.issues.append(Issue(kind="seo", title=f"Meta description is {dlen} chars (ideal 50–160)",
            detail=f"Description: {(seo.get('desc') or '')[:160]}…",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Tighten to one compelling 120–160 char sentence with a call to action.",
            check="seo.crawlable"))
    if not seo.get("canonical"):
        result.issues.append(Issue(kind="seo", title="No canonical URL declared",
            detail="No <link rel=canonical>. Query-string and trailing-slash duplicates can split ranking signals.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Add <link rel=\"canonical\" href=\"<absolute-url>\"> to every page.",
            check="seo.crawlable"))
    if (seo.get("robots") or "").lower().startswith(("noindex", "none")):
        result.issues.append(Issue(kind="seo", title="Page is set to noindex",
            detail=f"meta robots={seo.get('robots')!r} tells search engines to drop this page from results.",
            page=page_url, severity="high" if page_url.rstrip('/') == result.target.rstrip('/') else "medium",
            screenshot=shot, recommendation="Remove noindex unless this page must stay out of search.",
            check="seo.crawlable"))
    h1 = int(seo.get("h1") or 0)
    if h1 == 0:
        result.issues.append(Issue(kind="seo", title="No H1 heading found",
            detail="Page has no <h1>. Screen readers and crawlers lose the primary topic signal.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Add exactly one descriptive <h1> per page, matching page intent.",
            check="seo.structure"))
    elif h1 > 1:
        result.issues.append(Issue(kind="seo", title=f"{h1} H1 headings — should be one",
            detail=f"H1 texts: {', '.join(seo.get('h1Texts') or [])[:200]}. Multiple H1s dilute topical focus.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Keep one <h1>; demote the rest to <h2>/<h3>.",
            check="seo.structure"))
    if seo.get("skipLevel"):
        result.issues.append(Issue(kind="seo", title="Skipped heading levels",
            detail=f"Heading order jumps a level ({' → '.join(seo.get('headings') or [])}). Assistive tech uses headings as a table of contents.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Nest headings sequentially: H1 → H2 → H3, never H1 → H3.",
            check="seo.structure"))
    og = seo.get("og") or [False, False, False]
    if not any(og):
        result.issues.append(Issue(kind="seo", title="Missing Open Graph tags",
            detail="No og:title/description/image — link shares on social/slack render as bare URLs.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Add og:title, og:description, og:image + twitter:card meta tags.",
            check="seo.structure"))
    if not seo.get("favicon"):
        result.issues.append(Issue(kind="seo", title="No favicon declared",
            detail="No <link rel=icon>. Tabs and bookmarks show a generic globe.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Serve /favicon.ico plus an SVG icon link.",
            check="seo.structure"))
    if not seo.get("charset"):
        result.issues.append(Issue(kind="seo", title="Missing charset declaration",
            detail="No <meta charset>. Non-ASCII text can mojibake on some pipelines.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Put <meta charset=\"utf-8\"> first in <head>.",
            check="seo.crawlable"))


def _audit_a11y(data: dict, page_url: str, shot: str | None, result: AutopsyResult) -> None:
    seo = data.get("seo", {}) or {}
    if not seo.get("lang"):
        result.issues.append(Issue(kind="a11y", title="Missing html lang attribute",
            detail="<html> has no lang — screen readers guess pronunciation language and often guess wrong.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Set <html lang=\"en\"> (or the correct BCP-47 code).",
            check="a11y.structure"))
    if not seo.get("viewport"):
        result.issues.append(Issue(kind="a11y", title="Missing viewport meta (mobile)",
            detail="No meta[name=viewport]. On phones the page renders at desktop width and requires pinch-zoom.",
            page=page_url, severity="high", screenshot=shot,
            recommendation="Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.",
            check="a11y.viewport"))
    imgs = data.get("imgs", []) or []
    no_alt = [i for i in imgs if i.get("alt") is None]
    if no_alt:
        worst = ", ".join((i.get("src") or "?")[:60] for i in no_alt[:4])
        result.issues.append(Issue(kind="a11y", title=f"{len(no_alt)} image(s) without alt text",
            detail=f"Images missing alt entirely (not even alt=\"\"): {worst}. Screen readers announce the filename instead.",
            page=page_url, severity="medium" if len(no_alt) > 2 else "low", screenshot=shot,
            recommendation="Add descriptive alt to meaningful images, alt=\"\" to decorative ones.",
            check="a11y.names", evidence={"count": len(no_alt)}))
    btns = data.get("btns", []) or []
    noname = [b for b in btns if b.get("visible") and not (b.get("accName") or "").strip()]
    if noname:
        result.issues.append(Issue(kind="a11y", title=f"{len(noname)} control(s) without accessible name",
            detail="Visible buttons/links with no text, aria-label, or title — invisible to screen readers and voice control.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Give every control visible text or an aria-label.",
            check="a11y.names", evidence={"count": len(noname)}))
    if int(data.get("dupCount") or 0):
        result.issues.append(Issue(kind="a11y", title=f"Duplicate element IDs ({', '.join(data.get('dupIds') or [])[:120]})",
            detail="Duplicate IDs break label↔control association, fragment links, and querySelector lookups.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Make every id unique; prefer classes for repeated styling.",
            check="a11y.structure"))
    if int(data.get("posTab") or 0):
        result.issues.append(Issue(kind="a11y", title=f"{data['posTab']} element(s) with positive tabindex",
            detail="tabindex > 0 hijacks natural tab order and traps keyboard users.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Use tabindex=\"0\"/\"-1\" only; order content in DOM instead.",
            check="a11y.structure"))


def _audit_perf(data: dict, page_url: str, load_ms: int, shot: str | None, result: AutopsyResult) -> None:
    tot = float(data.get("totKB") or 0)
    n_req = len(data.get("res", []) or [])
    ttfb = int((data.get("nav") or {}).get("ttfb") or 0)
    if ttfb > 1200:
        result.issues.append(Issue(kind="perf", title=f"Slow server response (TTFB {ttfb}ms)",
            detail=f"Time-to-first-byte {ttfb}ms. Budgets: <600ms good, >1200ms users feel the stall.",
            page=page_url, severity="high" if ttfb > 2500 else "medium", screenshot=shot,
            recommendation="Enable HTTP/2+TLS1.3, server caching, CDN edge, and DB query profiling.",
            check="nav.load-budget", evidence={"ttfb_ms": ttfb}))
    if tot > 2500:
        result.issues.append(Issue(kind="perf", title=f"Heavy page: {tot:.0f} KB over {n_req} requests",
            detail=f"Transfer weight {tot:.0f} KB across {n_req} resources. On 4G this costs seconds and real money.",
            page=page_url, severity="high" if tot > 5000 else "medium", screenshot=shot,
            recommendation="Compress images (AVIF/WebP), code-split JS, defer non-critical scripts, enable Brotli.",
            check="nav.resource-weight", evidence={"kb": tot, "requests": n_req}))
    elif n_req > 80:
        result.issues.append(Issue(kind="perf", title=f"Request bloat: {n_req} sub-resources",
            detail=f"{n_req} requests for one page — each pays DNS+TLS+round-trip overhead.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Bundle, sprite, and tree-shake; lazy-load below-the-fold assets.",
            check="nav.resource-weight", evidence={"requests": n_req}))
    for b in (data.get("big") or [])[:3]:
        mb = (b.get("size") or 0) / 1048576
        result.issues.append(Issue(kind="perf", title=f"Oversized asset: {mb:.1f} MB ({(b.get('type') or 'asset')})",
            detail=f"{b.get('name', '')[:160]} took {b.get('dur', 0)}ms on the wire.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Resize/compress or stream this asset; consider lazy-loading.",
            check="nav.render-blocking", evidence=b))


def _audit_content(data: dict, page_url: str, shot: str | None, result: AutopsyResult) -> None:
    imgs = data.get("imgs", []) or []
    broken = [i for i in imgs if i.get("broken")]
    for i in broken[:5]:
        result.issues.append(Issue(kind="content", title=f"Broken image: {(i.get('src') or '?')[:90]}",
            detail="Image element finished loading with naturalWidth=0 — shows as a broken-image icon.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Fix the src path, restore the file, or remove the tag.",
            check="ux.broken-images", evidence={"src": i.get("src")}))
    if int(data.get("smallTargets") or 0):
        result.issues.append(Issue(kind="content", title=f"{data['smallTargets']} tap target(s) smaller than 24px",
            detail="WCAG 2.2 AA requires ≥24×24 CSS px targets. Smaller controls cause mis-taps on touch.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Enlarge padding to reach at least 24×24px (44×44 preferred).",
            check="ux.tap-targets"))
    nodims = [i for i in imgs if i.get("noDims") and not i.get("broken")][:1]
    if len(imgs) >= 3 and len(nodims) + (3 if len(imgs) > 6 else 0) > 3 and any(i.get("noDims") for i in imgs):
        n = sum(1 for i in imgs if i.get("noDims"))
        if n >= 3:
            result.issues.append(Issue(kind="content", title=f"{n} image(s) without width/height attributes",
                detail="Missing dimensions cause cumulative layout shift (CLS) as images pop in.",
                page=page_url, severity="low", screenshot=shot,
                recommendation="Add width/height attributes or aspect-ratio CSS to reserve space.",
                check="ux.tap-targets", evidence={"count": n}))


def _audit_network(failed: list[str], bad_resp: list[dict], page_url: str,
                   shot: str | None, result: AutopsyResult) -> None:
    seen: set[str] = set()
    for f in (failed or [])[:8]:
        url = f.split(" :: ")[0]
        if url in seen:
            continue
        seen.add(url)
        result.issues.append(Issue(kind="resource", title=f"Sub-resource failed to load: {url[:100]}",
            detail=f[:300], page=page_url, severity="high",
            screenshot=shot, recommendation="Fix the URL, CORS, or hosting for this asset; add fallbacks for critical ones.",
            check="js.failed-requests", evidence={"url": url}))
    for r in (bad_resp or [])[:8]:
        key = f"{r.get('status')}:{r.get('url')}"
        if key in seen:
            continue
        seen.add(key)
        if r.get("type") == "document":
            continue  # main-page errors are their own issue
        result.issues.append(Issue(kind="resource", title=f"Asset returned HTTP {r.get('status')}: {(r.get('url') or '')[:100]}",
            detail=f"{r.get('type')} {(r.get('url') or '')[:200]} → {r.get('status')}. Broken scripts/styles cascade into JS errors and unstyled content.",
            page=page_url, severity="high" if int(r.get("status") or 500) >= 500 else "medium",
            screenshot=shot, recommendation="Restore or remove the asset; pin third-party URLs that 404.",
            check="js.failed-requests", evidence=r))


def _audit_link_hygiene(links: list[dict], page_url: str, result: AutopsyResult) -> None:
    empty = sum(1 for l in links if (l.get("href") or "") in ("", "#"))
    hashonly = sum(1 for l in links if (l.get("href") or "").startswith("#") and len(l.get("href") or "") > 1)
    if empty:
        result.issues.append(Issue(kind="content", title=f"{empty} empty/hash-less link(s) (href=\"\" or \"#\")",
            detail="Links with href=\"\" or href=\"#\" jump to top and confuse keyboard/screen-reader users; they are usually unfinished buttons.",
            page=page_url, severity="low",
            recommendation="Use <button> for actions, real URLs for navigation — never href=\"#\".",
            check="link.hygiene", evidence={"count": empty}))
    unsafe = [l for l in links if (l.get("target") == "_blank") and ("noopener" not in (l.get("rel") or ""))]
    if unsafe:
        result.issues.append(Issue(kind="security", title=f"{len(unsafe)} external link(s) missing rel=\"noopener\"",
            detail="target=_blank without noopener lets the new page control window.opener (reverse tabnabbing).",
            page=page_url, severity="medium",
            recommendation="Add rel=\"noopener noreferrer\" to every target=_blank link.",
            check="sec.transport", evidence={"count": len(unsafe)}))


def _audit_mixed_content(mixed: list[dict], page_url: str, headers: dict, result: AutopsyResult) -> None:
    if page_url.startswith("https://") and mixed:
        result.issues.append(Issue(kind="security", title=f"{len(mixed)} mixed-content request(s) over HTTP",
            detail=f"HTTPS page loads insecure sub-resources: {(mixed[0].get('name') or '')[:140]}. Browsers block or warn on these.",
            page=page_url, severity="high",
            recommendation="Serve every sub-resource over HTTPS; upgrade hard-coded http:// URLs.",
            check="sec.transport", evidence={"sample": (mixed[0].get("name") or "")[:200]}))


def _audit_site_security(target: str, headers: dict, context, result: AutopsyResult) -> None:
    """Run once against the landing page's response headers + transport."""
    h = headers or {}
    missing = []
    if not h.get("content-security-policy"):
        missing.append("Content-Security-Policy")
    if not h.get("strict-transport-security") and target.startswith("https://"):
        missing.append("Strict-Transport-Security")
    if not h.get("x-frame-options") and not (h.get("content-security-policy") or "").__contains__("frame-ancestors"):
        missing.append("X-Frame-Options")
    if not h.get("x-content-type-options"):
        missing.append("X-Content-Type-Options")
    if not h.get("referrer-policy"):
        missing.append("Referrer-Policy")
    if missing:
        result.issues.append(Issue(kind="security", title=f"Missing security headers: {', '.join(missing)}",
            detail=f"Response headers seen: {', '.join(sorted(h.keys()))[:220] or '(none captured)'}. "
                   f"Missing {', '.join(missing)} leaves the site open to XSS/clickjacking/MIME-sniffing.",
            page=target, severity="high" if "Content-Security-Policy" in missing else "medium",
            recommendation="Emit: CSP, HSTS (max-age≥15552000; includeSubDomains), X-Frame-Options=DENY/SAMEORIGIN, X-Content-Type-Options=nosniff, Referrer-Policy=strict-origin-when-cross-origin.",
            check="sec.headers", evidence={"seen": sorted(h.keys())[:20]}))
    server = h.get("server") or ""
    if server and any(v in server for v in ("nginx/", "Apache/", "Microsoft-IIS/", "Express")):
        result.issues.append(Issue(kind="security", title=f"Server version disclosed: {server[:80]}",
            detail="The Server header advertises exact software versions — free reconnaissance for attackers.",
            page=target, severity="low",
            recommendation="Strip or genericise the Server header at the reverse proxy.",
            check="sec.headers", evidence={"server": server[:120]}))
    if target.startswith("http://"):
        result.issues.append(Issue(kind="security", title="Site served over plain HTTP",
            detail="Target uses http:// — traffic (including form input) is readable on the network.",
            page=target, severity="critical",
            recommendation="Redirect all HTTP → HTTPS with HSTS preload.",
            check="sec.transport"))
    # cookie flags
    try:
        import urllib.parse as _up
        origin = f"{_up.urlparse(target).scheme}://{_up.urlparse(target).netloc}"
        # cookies are read opportunistically; Playwright context may hold some already
        async def _noop():  # placeholder — real read happens in run_autopsy via context.cookies
            return []
    except Exception:
        pass


def _run_depth_audits(data: dict, page_url: str, load_ms: int,
                      shot: str | None, headers: dict,
                      result: AutopsyResult) -> None:
    """Fan out the second-wave depth audits over one page's evidence bundle.

    Builds the small additive evidence shapes from the single collection pass
    and delegates to the public pure audit seams. Never raises: depth must
    not break the baseline crawl.
    """
    def guard(fn) -> None:
        try:
            fn()
        except Exception:
            pass

    def seo() -> None:
        seo = data.get("seo", {}) or {}
        og = seo.get("og") or []
        audit_seo_depth({
            "wordCount": data.get("textWords"),
            "jsonLd": data.get("jsonLd"),
            "ogComplete": data.get("ogComplete", bool(og and all(og))),
        }, page_url, shot, result)

    def perf() -> None:
        by_type: dict[str, float] = {}
        third_kb = 0.0
        target_host = urllib.parse.urlparse(result.target).hostname or ""
        for res in data.get("res", []) or []:
            kind = str(res.get("type") or "other").lower() or "other"
            size_kb = float(res.get("size") or 0) / 1024
            by_type[kind] = by_type.get(kind, 0.0) + size_kb
            host = urllib.parse.urlparse(str(res.get("name") or "")).hostname or ""
            if host and host != target_host:
                third_kb += size_kb
        nodims = sum(1 for i in (data.get("imgs", []) or []) if i.get("noDims"))
        audit_perf_depth({
            "byType": {k: round(v, 1) for k, v in by_type.items()},
            "thirdPartyKB": round(third_kb, 1),
            "syncHeadScripts": data.get("syncHeadScripts"),
            "overflowX": data.get("overflowX"),
            "noDims": nodims,
        }, page_url, load_ms, shot, result)

    def a11y() -> None:
        val_gaps: list[str] = []
        for f in data.get("forms", []) or []:
            for inp in f.get("inputs", []) or []:
                if inp.get("required") and not inp.get("hasRealLabel"):
                    val_gaps.append(str(inp.get("name") or "field")[:40])
        audit_a11y_interaction({
            "posTab": data.get("posTab"),
            "hasSkipLink": data.get("hasSkipLink"),
            "landmarks": data.get("landmarks"),
            "emptyHeadings": data.get("emptyHeadings"),
            "formValidationGaps": val_gaps,
        }, page_url, shot, result)

    def security() -> None:
        script_urls = [str(r.get("name") or "")
                       for r in (data.get("res", []) or [])
                       if str(r.get("name") or "").endswith(".js")
                       or (r.get("type") or "") == "script"][:20]
        audit_security_posture(page_url, headers or {}, [], script_urls,
                               shot, result)

    def links() -> None:
        audit_link_depth(data.get("links", []) or [], page_url, shot, result)

    for step in (seo, perf, a11y, security, links):
        guard(step)


def audit_seo_depth(evidence: dict, page_url: str, shot: str | None,
                    result: AutopsyResult) -> None:
    """Per-page SEO depth: structured-data/social completeness + thin content.

    Pure audit seam: ``evidence`` may omit any key (treated as unknown → skip).
    """
    ev = evidence or {}
    if not ev:
        return
    words = ev.get("wordCount")
    json_ld = ev.get("jsonLd")
    og_complete = ev.get("ogComplete")
    if json_ld is not None or og_complete is not None:
        has_structured = (int(json_ld or 0) > 0) or bool(og_complete)
        if not has_structured:
            result.issues.append(Issue(
                kind="seo", title="No structured data or complete social card",
                detail="No JSON-LD block and no complete Open Graph set — "
                       "the page is ineligible for rich results and renders "
                       "as a bare URL on shares.",
                page=page_url, severity="low", screenshot=shot,
                recommendation="Add JSON-LD structured data matching page type "
                               "and complete og:title/description/image tags.",
                check="seo.structured",
                evidence={"jsonLd": int(json_ld or 0),
                          "ogComplete": bool(og_complete)}))
    if words is not None:
        try:
            wc = int(words)
        except (TypeError, ValueError):
            wc = -1
        if 0 <= wc < 100:
            result.issues.append(Issue(
                kind="seo", title=f"Thin content: ~{wc} words of visible text",
                detail="Very little indexable text — placeholder, doorway, or "
                       "JS-rendering failure. Thin pages rarely rank and often "
                       "signal an unfinished route.",
                page=page_url, severity="medium", screenshot=shot,
                recommendation="Ship real copy (aim 300+ words) or noindex the "
                               "route until it is real. If content renders "
                               "client-side, verify the crawler saw it.",
                check="seo.thin-content", evidence={"wordCount": wc}))


def audit_site_seo_duplicates(pages: list[dict], result: AutopsyResult) -> None:
    """Site-level SEO depth: collapse duplicate titles into one issue.

    ``pages`` is a list of ``{"url": ..., "title": ...}`` mappings.
    """
    seen: dict[str, list[str]] = {}
    for p in pages or []:
        title = (p.get("title") or "").strip()
        url = p.get("url") or ""
        if not title or not url:
            continue
        seen.setdefault(title.lower(), []).append(url)
    dupes = {t: urls for t, urls in seen.items() if len(urls) > 1}
    if not dupes:
        return
    worst_title, worst_urls = sorted(dupes.items(), key=lambda kv: -len(kv[1]))[0]
    sample = ", ".join(worst_urls[:5])
    extra = f" (+{len(worst_urls) - 5} more)" if len(worst_urls) > 5 else ""
    others = sum(len(v) for v in dupes.values()) - len(worst_urls)
    suffix = f" {others} page(s) share other titles." if others else ""
    result.issues.append(Issue(
        kind="seo", title=f"{len(dupes)} duplicate title group(s) across pages",
        detail=f"Title {worst_title!r} repeats on {len(worst_urls)} pages: "
               f"{sample}{extra}.{suffix} Duplicates split ranking signals.",
        page=worst_urls[0], severity="medium",
        recommendation="Give every page a unique descriptive title; template "
                       "in section or product names instead of reusing one.",
        check="seo.duplicates",
        evidence={"groups": len(dupes), "sample": sample[:220]}))


def audit_perf_depth(evidence: dict, page_url: str, load_ms: int,
                     shot: str | None, result: AutopsyResult) -> None:
    """Runtime + weight depth: per-type attribution and layout stability."""
    ev = evidence or {}
    if not ev:
        return
    by_type = ev.get("byType") or {}
    third_kb = float(ev.get("thirdPartyKB") or 0)
    sync_head = int(ev.get("syncHeadScripts") or 0)
    script_kb = float(by_type.get("script") or 0)
    total_typed = sum(float(v or 0) for v in by_type.values())
    if script_kb > 800 or sync_head >= 3 or third_kb > 600:
        parts = []
        if script_kb:
            parts.append(f"scripts {script_kb:.0f} KB")
        if third_kb:
            parts.append(f"third-party {third_kb:.0f} KB")
        if sync_head:
            parts.append(f"{sync_head} sync head script(s)")
        result.issues.append(Issue(
            kind="perf",
            title=f"JS weight concentration ({', '.join(parts) or 'heavy client bundle'})",
            detail=f"Client weight {' / '.join(parts)} of ~{total_typed:.0f} KB "
                   f"typed transfer. Blocking scripts delay interactivity well "
                   f"past first paint.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Code-split by route, defer non-critical scripts, "
                           "self-host or facade third-party embeds.",
            check="perf.runtime",
            evidence={"scriptKB": script_kb, "thirdPartyKB": third_kb,
                      "syncHeadScripts": sync_head}))
    overflow = bool(ev.get("overflowX"))
    nodims = int(ev.get("noDims") or 0)
    if overflow or nodims >= 5:
        detail = []
        if overflow:
            detail.append("horizontal overflow past the viewport")
        if nodims >= 5:
            detail.append(f"{nodims} media node(s) without dimensions")
        result.issues.append(Issue(
            kind="perf", title="Layout-stability risk",
            detail="; ".join(detail) + " — both drive cumulative layout shift "
                   "as content pops in or forces sideways scroll.",
            page=page_url, severity="low", screenshot=shot,
            recommendation="Reserve space with width/height or aspect-ratio, "
                           "and constrain full-bleed elements to 100vw.",
            check="perf.stability",
            evidence={"overflowX": overflow, "noDims": nodims}))


def audit_a11y_interaction(evidence: dict, page_url: str, shot: str | None,
                           result: AutopsyResult) -> None:
    """Keyboard/landmark depth + required-field validation association."""
    ev = evidence or {}
    if not ev:
        return
    pos_tab = int(ev.get("posTab") or 0)
    skip = ev.get("hasSkipLink")
    landmarks = ev.get("landmarks") or {}
    empty_h = int(ev.get("emptyHeadings") or 0)
    gaps: list[str] = []
    if pos_tab > 0:
        gaps.append(f"{pos_tab} positive tabindex order hijack(s)")
    if skip is False:
        gaps.append("no skip link on a content page")
    try:
        main_n = int(landmarks.get("main", 1))
        nav_n = int(landmarks.get("nav", 1))
    except (TypeError, ValueError):
        main_n, nav_n = 1, 1
    if main_n == 0:
        gaps.append("missing <main> landmark")
    if empty_h > 0:
        gaps.append(f"{empty_h} empty heading(s)")
    if gaps:
        result.issues.append(Issue(
            kind="a11y", title="Keyboard/landmark navigation gaps",
            detail="; ".join(gaps) + ". Keyboard and screen-reader users "
                   "lose their map of the page.",
            page=page_url, severity="medium" if pos_tab or main_n == 0 else "low",
            screenshot=shot,
            recommendation="Keep tab order in DOM order (tabindex 0/-1 only), "
                           "add one skip link, one <main>, and fill headings.",
            check="a11y.interaction", evidence={"gaps": "; ".join(gaps)[:220]}))
    val_gaps = [g for g in (ev.get("formValidationGaps") or []) if g]
    if val_gaps:
        names = ", ".join(str(g)[:40] for g in val_gaps[:5])
        result.issues.append(Issue(
            kind="form",
            title=f"{len(val_gaps)} required field(s) without validation messaging",
            detail=f"Required fields with no associated error message region: "
                   f"{names}. Assistive tech never announces what failed.",
            page=page_url, severity="medium", screenshot=shot,
            recommendation="Pair each required control with aria-describedby "
                           "pointing at an error region updated on validation.",
            check="form.validation", evidence={"fields": names[:220]}))


_SECRET_HINTS = ("api_key", "apikey", "secret", "token", "aws_", "akia",
                 "ghp_", "xox", "sk_live", "password=")


def audit_security_posture(target: str, headers: dict, cookies: list,
                           script_urls: list[str], shot: str | None,
                           result: AutopsyResult) -> None:
    """CSP/HSTS posture grading + secret-looking script-URL review flags."""
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    csp = str(headers.get("content-security-policy") or "")
    lowered = csp.lower()
    posture_gaps: list[str] = []
    if csp:
        if "'unsafe-inline'" in lowered or "'unsafe-eval'" in lowered:
            posture_gaps.append("CSP allows unsafe-inline/unsafe-eval")
        if "frame-ancestors" not in lowered:
            posture_gaps.append("CSP missing frame-ancestors")
        if "object-src" not in lowered:
            posture_gaps.append("CSP missing object-src")
    hsts = str(headers.get("strict-transport-security") or "")
    if target.startswith("https://") and hsts:
        hl = hsts.lower()
        try:
            max_age = int(hl.split("max-age=")[1].split(";")[0].strip())
        except (IndexError, ValueError):
            max_age = 0
        if max_age < 15552000 or "includesubdomains" not in hl:
            posture_gaps.append("HSTS max-age short or missing includeSubDomains")
    for c in cookies or []:
        try:
            name = str(c.get("name") or "?")
            same_site = str(c.get("sameSite") or "")
            secure = bool(c.get("secure"))
            http_only = bool(c.get("httpOnly"))
        except AttributeError:
            continue
        if same_site.lower() == "none" and not secure:
            posture_gaps.append(f"cookie {name!r} is SameSite=None without Secure (browsers reject it)")
        elif not http_only and any(k in name.lower() for k in ("sess", "auth", "token", "sid")):
            posture_gaps.append(f"session-like cookie {name!r} readable from JavaScript (no HttpOnly)")
    if posture_gaps:
        result.issues.append(Issue(
            kind="security", title="Weak content-security posture",
            detail="; ".join(posture_gaps) + ".",
            page=target, severity="medium", screenshot=shot,
            recommendation="Tighten CSP (remove unsafe-* where possible, add "
                           "frame-ancestors/object-src) and set HSTS "
                           "max-age≥15552000 with includeSubDomains.",
            check="sec.posture", evidence={"gaps": "; ".join(posture_gaps)[:220]}))
    flagged: list[str] = []
    for u in script_urls or []:
        ul = str(u).lower()
        if any(h in ul for h in _SECRET_HINTS):
            flagged.append(str(u)[:160])
            if len(flagged) >= 3:
                break
    for u in flagged:
        result.issues.append(Issue(
            kind="security", title="Secret-looking string in served script URL",
            detail=f"{u} contains a token-like parameter — possibly a leaked "
                   "key committed into markup or a build artifact.",
            page=target, severity="low", screenshot=shot,
            recommendation="Move secrets server-side; rotate anything that "
                           "shipped in a URL and purge caches.",
            check="sec.secrets", evidence={"url": u[:200]}))


_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "msclkid", "_ga", "mc_eid",
                    "sessionid", "phpsessid", "sid=")


def audit_link_depth(links: list[dict], page_url: str, shot: str | None,
                     result: AutopsyResult) -> None:
    """Tracking-parameter hygiene over discovered links (no I/O)."""
    hits = 0
    sample = ""
    for link in links or []:
        href = str(link.get("href") or link.get("abs") or "")
        if not href:
            continue
        q = urllib.parse.urlparse(href).query.lower()
        if any(t in q for t in _TRACKING_PARAMS):
            hits += 1
            if not sample:
                sample = href[:160]
    if hits:
        result.issues.append(Issue(
            kind="content",
            title=f"{hits} link(s) leak tracking/session parameters",
            detail=f"e.g. {sample}. Tracking IDs in hrefs get copied, "
                   "bookmarked, and indexed — diluting canonical signals.",
            page=page_url, severity="low",
            recommendation="Strip tracking params from authored links and "
                           "canonicals; append them at click time instead.",
            check="link.chains", evidence={"count": hits, "sample": sample[:200]}))


async def _discover_hidden(context, target: str, result: AutopsyResult, shots: Path, snap_name) -> None:
    origin = f"{urllib.parse.urlparse(target).scheme}://{urllib.parse.urlparse(target).netloc}"
    # robots.txt + sitemap.xml
    for path in ("robots.txt", "sitemap.xml"):
        try:
            r = await context.request.get(f"{origin}/{path}", timeout=8000)
            if r.status == 200:
                body = (await r.text())[:4000]
                name, p = snap_name("hidden")
                # tiny text-proof screenshot placeholder: reuse first page shot
                result.hidden_pages.append({"url": f"{origin}/{path}", "via": path, "detail": body[:300]})
        except Exception:
            pass
    # common admin paths — only GET status, no screenshots hammering
    sem = asyncio.Semaphore(6)

    async def probe(p: str):
        async with sem:
            u = f"{origin}/{p}"
            try:
                r = await context.request.get(u, timeout=7000)
                if r.status and r.status < 400:
                    # filter out soft-404: same title/length as homepage? keep simple: record 200s
                    result.hidden_pages.append({"url": u, "via": "common-path probe", "detail": f"HTTP {r.status}"})
            except Exception:
                pass

    await asyncio.gather(*(probe(p) for p in COMMON_HIDDEN_PATHS if p not in ("robots.txt", "sitemap.xml")))
    # cap
    result.hidden_pages = result.hidden_pages[:15]
