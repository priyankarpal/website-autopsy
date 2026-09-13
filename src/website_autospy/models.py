"""Core autopsy engine: crawl with Playwright + run health checks."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from pathlib import Path

COMMON_HIDDEN_PATHS = [
    "admin", "administrator", "login", "signin", "signup", "register",
    "dashboard", "panel", "private", "secret", "hidden", "test", "staging",
    "dev", "development", "backup", "bak", "old", "archive", "api", "api/docs",
    "swagger", "graphql", ".git/HEAD", ".env", "wp-admin", "wp-login.php",
    "phpinfo.php", "server-status", "sitemap.xml", "robots.txt",
]


DEEP_CHECKS: list[dict] = [
    # id, category, label — engine runs each of these; report shows pass/fail
    {"id": "nav.reachability", "category": "Crawl", "label": "Page reachability & HTTP status per URL"},
    {"id": "nav.load-budget", "category": "Performance", "label": "Page load budget ≤ 3s (DOM + network idle)"},
    {"id": "nav.resource-weight", "category": "Performance", "label": "Transfer weight & request-count budget per page"},
    {"id": "nav.render-blocking", "category": "Performance", "label": "Render-blocking / oversized asset detection"},
    {"id": "js.runtime-errors", "category": "Stability", "label": "JS runtime errors (pageerror + console.error)"},
    {"id": "js.failed-requests", "category": "Stability", "label": "Failed sub-resource requests & 4xx/5xx assets"},
    {"id": "link.integrity", "category": "Links", "label": "Broken-link verification (HEAD → GET, status capture)"},
    {"id": "link.hygiene", "category": "Links", "label": "Empty / hash-only / unsafe-external link hygiene"},
    {"id": "ux.dead-buttons", "category": "Interactivity", "label": "Dead-button click test (nav / DOM / network / modal)"},
    {"id": "ux.click-latency", "category": "Interactivity", "label": "Click latency budget ≤ 1.2s per control"},
    {"id": "ux.broken-images", "category": "Content", "label": "Broken-image scan (naturalWidth / failed loads)"},
    {"id": "ux.tap-targets", "category": "Content", "label": "Undersized tap-target & image-dimension scan"},
    {"id": "form.structure", "category": "Forms", "label": "Form structure: submit control, method/action, safe fill"},
    {"id": "form.labels", "category": "Forms", "label": "Label association, placeholder-only & autocomplete audit"},
    {"id": "seo.crawlable", "category": "SEO", "label": "Title, meta description, canonical, robots & sitemap"},
    {"id": "seo.structure", "category": "SEO", "label": "Heading hierarchy (single H1, no skipped levels), OG & favicon"},
    {"id": "a11y.names", "category": "Accessibility", "label": "Image alt text & control accessible names"},
    {"id": "a11y.structure", "category": "Accessibility", "label": "html lang, duplicate IDs, heading order, focusability"},
    {"id": "a11y.viewport", "category": "Accessibility", "label": "Viewport meta & mobile-readiness checks"},
    {"id": "sec.headers", "category": "Security", "label": "Security headers (CSP, HSTS, X-Frame, X-CTO, Referrer)"},
    {"id": "sec.transport", "category": "Security", "label": "HTTPS enforcement, mixed content & cookie flags"},
    {"id": "sec.exposure", "category": "Security", "label": "Exposed paths: robots/sitemap + admin/.env/.git probes"},
    {"id": "seo.duplicates", "category": "SEO", "label": "Duplicate titles/descriptions + thin-content detection across pages"},
    {"id": "seo.thin-content", "category": "SEO", "label": "Thin-content flag per page (very little visible text)"},
    {"id": "seo.structured", "category": "SEO", "label": "Structured-data (JSON-LD) + social-card completeness per page"},
    {"id": "perf.runtime", "category": "Performance", "label": "Per-type transfer + third-party/render-blocking attribution"},
    {"id": "perf.stability", "category": "Performance", "label": "Layout-stability signals (overflow, undimensioned media)"},
    {"id": "a11y.interaction", "category": "Accessibility", "label": "Keyboard reachability, landmarks + heading-outline depth"},
    {"id": "form.validation", "category": "Forms", "label": "Required-field validation-message association"},
    {"id": "sec.posture", "category": "Security", "label": "CSP strength + HSTS/cookie-flag posture grading"},
    {"id": "sec.secrets", "category": "Security", "label": "Secret-looking strings in served script URLs"},
    {"id": "link.chains", "category": "Content", "label": "Tracking-parameter + redirect-chain hygiene"},
]


def same_origin(a: str, b: str) -> bool:
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


def normalize(url: str) -> str:
    p = urllib.parse.urlparse(url)
    # drop fragment, trailing slash (except root)
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((p.scheme, p.netloc, path, "", p.query, ""))


@dataclass
class Issue:
    kind: str  # dead_button | broken_link | js_error | slow | form | hidden | seo | a11y | perf | security | resource | content | mobile
    title: str
    detail: str
    page: str
    severity: str = "medium"  # low/medium/high/critical
    screenshot: str | None = None  # relative path
    evidence: dict = field(default_factory=dict)
    recommendation: str = ""  # how to fix — shown in report
    check: str = ""  # machine name of the deep check, e.g. "seo.title-missing"


@dataclass
class PageRecord:
    url: str
    status: int | None
    load_ms: int
    title: str = ""
    screenshot: str | None = None
    links_found: int = 0
    # deep-check telemetry (filled by engine, displayed in report)
    requests: int = 0
    failed_requests: int = 0
    transfer_kb: float = 0.0
    dom_nodes: int = 0
    images: int = 0
    scripts: int = 0


@dataclass
class AutopsyResult:
    target: str
    started_at: str
    duration_s: float
    pages: list[PageRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    forms_tested: int = 0
    hidden_pages: list[dict] = field(default_factory=list)

    @property
    def dead_buttons(self): return [i for i in self.issues if i.kind == "dead_button"]

    @property
    def broken_links(self): return [i for i in self.issues if i.kind == "broken_link"]

    @property
    def js_errors(self): return [i for i in self.issues if i.kind == "js_error"]

    @property
    def slow(self): return [i for i in self.issues if i.kind == "slow"]

    @property
    def form_issues(self): return [i for i in self.issues if i.kind == "form"]

    @property
    def seo_issues(self): return [i for i in self.issues if i.kind == "seo"]

    @property
    def a11y_issues(self): return [i for i in self.issues if i.kind == "a11y"]

    @property
    def perf_issues(self): return [i for i in self.issues if i.kind == "perf"]

    @property
    def security_issues(self): return [i for i in self.issues if i.kind == "security"]

    @property
    def resource_issues(self): return [i for i in self.issues if i.kind == "resource"]

    @property
    def content_issues(self): return [i for i in self.issues if i.kind == "content"]

    def severity_counts(self) -> dict:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for i in self.issues:
            if i.severity in counts:
                counts[i.severity] += 1
        for h in self.hidden_pages:
            counts["medium"] += 1
        return counts

    def health_score(self) -> int:
        """0-100, 100 = perfect. Inverse of weirdness."""
        return max(0, 100 - self.weirdness_score())

    def checks_run(self) -> list[dict]:
        """Coverage ledger: every deep check the engine performs."""
        return [dict(c) for c in DEEP_CHECKS]

    def weirdness_score(self) -> int:
        weights = {
            "dead_button": 7, "broken_link": 5, "js_error": 8,
            "slow": 4, "form": 6, "seo": 3, "a11y": 4,
            "perf": 4, "security": 7, "resource": 5, "content": 3,
        }
        sev_factor = {"critical": 4.0, "high": 2.5, "medium": 1.25, "low": 0.5}
        score = sum(weights.get(i.kind, 4) * sev_factor.get(i.severity, 1) for i in self.issues)
        score += len(self.hidden_pages) * 2
        # scale mildly by density so single-page scans don't max out on nits
        pages = max(1, len(self.pages))
        density_bonus = min(10, int(score / max(2, pages)))
        return max(0, min(100, int(score + density_bonus) if score else 0))

    def verdict(self) -> tuple[str, str]:
        s = self.weirdness_score()
        if s == 0: return ("Excellent — no issues found", "All deep checks passed on the crawled pages. Keep monitoring after each deploy.")
        if s < 25: return ("Good — minor findings", "Mostly low-severity polish items (metadata, headers, labels). Quick wins, no user-facing breakage.")
        if s < 50: return ("Needs work — notable issues", "A mix of medium/high findings is affecting UX, SEO, or robustness. Schedule fixes before the next release.")
        if s < 75: return ("Poor — significant defects", "Multiple high-severity defects: broken flows, JS errors, or security gaps. Users are impacted.")
        return ("Critical — urgent action required", "Severe defects across several categories. Treat the top critical/high findings as release blockers.")

    def to_dict(self):
        d = asdict(self)
        d["weirdness_score"] = self.weirdness_score()
        d["verdict"], d["verdict_sub"] = self.verdict()
        return d
