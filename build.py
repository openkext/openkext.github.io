#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the OpenKext website.

    build.py CATALOG_JSON [--out _site] [--registry DIR]

CATALOG_JSON comes from the registry: `python3 registry/tools/check_registry.py catalog.json`.
DIR is a checkout of openkext/registry, used for GOVERNANCE.md and the file format docs
(default: ./registry, then ../registry).

Python 3.11+ standard library only. Everything in the catalog is community-submitted,
so every value is escaped, and only https:// URLs from it ever become links.
"""
import argparse
import base64
import datetime as _dt
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_NAME = "OpenKext"
GITHUB_ORG = "https://github.com/openkext"
REGISTRY_REPO = "https://github.com/openkext/registry"
SITE_REPO = "https://github.com/openkext/openkext.github.io"
# Same thresholds as openkext_registry/status.py in the registry.
COMMUNITY_MIN = 3
BROKEN_MIN = 2
OUT_MARKER = ".openkext-build"
FEATS_OPEN = '<ul class="features">'

STATUSES = {
    "submitted": ("Submitted", "neutral"),
    "author-tested": ("Author-tested", "blue"),
    "community-verified": ("Community-verified", "green"),
    "reported-broken": ("Reported broken", "red"),
}
RESULTS = {"works": "green", "partial": "amber", "broken": "red", "untested": "neutral"}
CATEGORIES = {
    "wifi": "Wi-Fi", "bluetooth": "Bluetooth", "audio": "Audio", "graphics": "Graphics",
    "ethernet": "Ethernet", "usb": "USB", "storage": "Storage", "input": "Input",
    "power": "Power", "sensors": "Sensors", "cpu": "CPU", "firmware": "Firmware", "other": "Other",
}
MACOS_NAMES = {
    "10.13": "High Sierra", "10.14": "Mojave", "10.15": "Catalina", "11": "Big Sur",
    "12": "Monterey", "13": "Ventura", "14": "Sonoma", "15": "Sequoia", "26": "Tahoe",
}
ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
GH_REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")


# ---------------------------------------------------------------- helpers

def esc(x):
    return html.escape("" if x is None else str(x), quote=True)


def text(x):
    """A catalog value as plain text (None and non-scalars become '')."""
    if isinstance(x, (str, int, float)) and not isinstance(x, bool):
        return str(x)
    return ""


def items(x):
    return x if isinstance(x, list) else []


def obj(x):
    return x if isinstance(x, dict) else {}


def safe_https(u):
    """Return u if it is a plain https:// URL that is safe to put in href, else None."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if not u.startswith("https://") or len(u) > 2000:
        return None
    if any(c in u for c in " \t\r\n<>\"'`\\") or any(ord(c) < 0x20 or ord(c) == 0x7F for c in u):
        return None
    p = urllib.parse.urlsplit(u)
    if p.scheme != "https" or not p.hostname or "@" in p.netloc:
        return None
    return u


def link_or_text(u, label=None):
    """An <a> for safe https URLs; escaped text for anything else."""
    safe = safe_https(u)
    shown = esc(label if label is not None else text(u))
    return f'<a href="{esc(safe)}">{shown}</a>' if safe else shown


def gh_repo(source):
    m = GH_REPO_RE.match(safe_https(source) or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def q(s):
    return urllib.parse.quote(str(s), safe="")


def version_key(v):
    """'10.15.7' -> 1015, '15.8' -> 1500, '26' -> 2600. 0 when unknown."""
    parts = re.findall(r"\d+", text(v))
    if not parts:
        return 0
    major = int(parts[0])
    if major == 10:
        return 1000 + (int(parts[1]) if len(parts) > 1 else 0)
    return major * 100


def major_of(v):
    parts = re.findall(r"\d+", text(v))
    if not parts:
        return ""
    return f"10.{parts[1]}" if parts[0] == "10" and len(parts) > 1 else parts[0]


def macos_label(v):
    m = major_of(v)
    name = MACOS_NAMES.get(m)
    return f"{v} {name}" if name else text(v)


def badge(label, color, title=None):
    t = f' title="{esc(title)}"' if title else ""
    return f'<span class="badge badge-{color}"{t}>{esc(label)}</span>'


def status_badge(status):
    label, color = STATUSES.get(status, (text(status) or "unknown", "neutral"))
    return badge(label, color)


def result_badge(result):
    r = text(result)
    return badge(r or "unknown", RESULTS.get(r, "neutral"))


# ---------------------------------------------------------------- markdown

_FENCE = re.compile(r"^( *)(`{3,}|~{3,})\s*([\w+.-]*)\s*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
_LIST = re.compile(r"^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_TSEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _md_url_ok(u):
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in u) or any(c in u for c in " <>\"'`\\"):
        return False
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", u)
    if m:
        return m.group(1).lower() in ("https", "http", "mailto")
    return True  # relative path or #fragment


def slugify(s):
    s = re.sub(r"[^\w\- ]", "", s.lower(), flags=re.UNICODE).strip()
    return re.sub(r" ", "-", s) or "section"


class Markdown:
    """A small Markdown subset. Raw HTML is always escaped, never passed through.

    resolve(url) -> url lets the caller rewrite links (for example `GOVERNANCE.md` to the
    governance page). Links whose scheme is not http(s) or mailto become plain text.
    """

    def __init__(self, resolve=None):
        self.resolve = resolve or (lambda u: u)
        self.slugs = {}
        self.toc = []  # (level, id, html)

    def render(self, src, shift=0):
        src = src.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").expandtabs(4)
        self.shift = shift
        return self._blocks(src.split("\n"))

    # -- blocks
    def _starts_block(self, lines, i):
        line = lines[i]
        if _FENCE.match(line) or _HEADING.match(line) or _HR.match(line) or _QUOTE.match(line):
            return True
        if _LIST.match(line) and _LIST.match(line).group(4).strip():
            return True
        return self._is_table(lines, i)

    @staticmethod
    def _is_table(lines, i):
        return "|" in lines[i] and i + 1 < len(lines) and "-" in lines[i + 1] and bool(_TSEP.match(lines[i + 1])) \
            and "|" in lines[i + 1]

    def _blocks(self, lines, tight=False):
        out, i, n = [], 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            m = _FENCE.match(line)
            if m:
                ind, fence, lang = len(m.group(1)), m.group(2), m.group(3)
                i += 1
                code = []
                while i < n and not (lines[i].strip().startswith(fence[0] * len(fence))
                                     and not lines[i].strip().strip(fence[0])):
                    ln = lines[i]
                    code.append(ln[min(ind, _indent(ln)):])
                    i += 1
                i += 1
                cls = f' class="language-{esc(lang)}"' if lang else ""
                out.append(f"<pre><code{cls}>{esc(chr(10).join(code))}</code></pre>")
                continue
            m = _HEADING.match(line)
            if m:
                level = min(6, len(m.group(1)) + self.shift)
                raw = m.group(2)
                plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw).replace("`", "").replace("*", "")
                base = slugify(plain)
                k = self.slugs.get(base, 0)
                self.slugs[base] = k + 1
                hid = base if k == 0 else f"{base}-{k}"
                inner = self._inline(raw)
                if level == 2:
                    self.toc.append((level, hid, inner))
                out.append(f'<h{level} id="{esc(hid)}">{inner}</h{level}>')
                i += 1
                continue
            if _HR.match(line):
                out.append("<hr>")
                i += 1
                continue
            if self._is_table(lines, i):
                i = self._table(lines, i, out)
                continue
            if _QUOTE.match(line):
                body = []
                while i < n and lines[i].strip() and _QUOTE.match(lines[i]):
                    body.append(_QUOTE.match(lines[i]).group(1))
                    i += 1
                out.append(f"<blockquote>{self._blocks(body)}</blockquote>")
                continue
            m = _LIST.match(line)
            if m and m.group(4).strip():
                i = self._list(lines, i, out)
                continue
            para = [line.strip()]
            i += 1
            while i < n and lines[i].strip() and not self._starts_block(lines, i):
                para.append(lines[i].strip())
                i += 1
            inner = self._inline("\n".join(para))
            out.append(inner if tight else f"<p>{inner}</p>")
        return "\n".join(out)

    def _table(self, lines, i, out):
        def cells(row):
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|") and not row.endswith("\\|"):
                row = row[:-1]
            parts, cur, code = [], "", False
            j = 0
            while j < len(row):
                c = row[j]
                if c == "\\" and j + 1 < len(row) and row[j + 1] == "|":
                    cur += "|"
                    j += 2
                    continue
                if c == "`":
                    code = not code
                if c == "|" and not code:
                    parts.append(cur.strip())
                    cur = ""
                else:
                    cur += c
                j += 1
            parts.append(cur.strip())
            return parts

        head = cells(lines[i])
        aligns = []
        for s in cells(lines[i + 1]):
            left, right = s.startswith(":"), s.endswith(":")
            aligns.append("center" if left and right else "right" if right else "")
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip() and "|" in lines[i]:
            rows.append(cells(lines[i]))
            i += 1

        def cell(tag, val, k):
            a = aligns[k] if k < len(aligns) else ""
            cls = f' class="a-{a}"' if a else ""
            return f"<{tag}{cls}>{self._inline(val)}</{tag}>"

        h = "".join(cell("th", v, k) for k, v in enumerate(head))
        body = "".join(
            "<tr>" + "".join(cell("td", (r[k] if k < len(r) else ""), k) for k in range(len(head))) + "</tr>"
            for r in rows)
        out.append(f'<div class="table-wrap"><table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>')
        return i

    def _list(self, lines, i, out):
        n = len(lines)
        first = _LIST.match(lines[i])
        base = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        start = int(first.group(2)[:-1]) if ordered else 1
        entries, loose = [], False
        while i < n:
            m = _LIST.match(lines[i])
            if not m or len(m.group(1)) != base or m.group(2)[0].isdigit() != ordered:
                break
            content = len(m.group(1)) + len(m.group(2)) + max(1, min(4, len(m.group(3))))
            body = [m.group(4)]
            i += 1
            while i < n:
                ln = lines[i]
                if not ln.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _indent(lines[j]) >= content:
                        body += [""] * (j - i)
                        i = j
                        loose = True
                        continue
                    break
                ind = _indent(ln)
                if ind >= content:
                    body.append(ln[content:])
                elif _LIST.match(ln) and ind > base:
                    body.append(ln[ind:] if ind < content else ln[content:])
                elif _LIST.match(ln) or _HEADING.match(ln) or _FENCE.match(ln) or _HR.match(ln) or _QUOTE.match(ln):
                    break
                else:
                    body.append(ln.strip())  # lazy continuation
                i += 1
            entries.append(body)
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j > i and j < n:
                m2 = _LIST.match(lines[j])
                if m2 and len(m2.group(1)) == base and m2.group(2)[0].isdigit() == ordered:
                    loose = True
                    i = j
        tag = "ol" if ordered else "ul"
        st = f' start="{start}"' if ordered and start != 1 else ""
        lis = "".join(f"<li>{self._blocks(b, tight=not loose)}</li>" for b in entries)
        out.append(f"<{tag}{st}>{lis}</{tag}>")
        return i

    # -- inline
    def _link(self, url, inner):
        url = self.resolve(url.strip())
        if not url or not _md_url_ok(url):
            return inner
        return f'<a href="{esc(url)}">{inner}</a>'

    def _inline(self, s):
        store = []

        def put(h):
            store.append(h)
            return f"\x00{len(store) - 1}\x00"

        s = s.replace("\x00", "")
        s = re.sub(r"(`+)(.+?)(?<!`)\1(?!`)", lambda m: put(f"<code>{esc(m.group(2).strip())}</code>"), s,
                   flags=re.S)
        s = re.sub(r"<((?:https?|mailto):[^\s<>]+)>", lambda m: put(self._link(m.group(1), esc(m.group(1)))), s)
        s = re.sub(r"\[([^\]]+)\]\(\s*<?([^\s)<>]+)>?(?:\s+\"[^\"]*\")?\s*\)",
                   lambda m: put(self._link(m.group(2), self._emph(esc(m.group(1))))), s)
        s = self._emph(esc(s))
        while "\x00" in s:
            s = re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], s)
        return s

    @staticmethod
    def _emph(s):
        s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s, flags=re.S)
        s = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<strong>\1</strong>", s, flags=re.S)
        s = re.sub(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])", r"<em>\1</em>", s, flags=re.S)
        s = re.sub(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)", r"<em>\1</em>", s, flags=re.S)
        return s


def md_to_html(src, resolve=None, shift=0):
    return Markdown(resolve).render(src, shift)


# ---------------------------------------------------------------- page shell

LOGO = ('<svg class="logo" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">'
        '<rect x="5" y="5" width="14" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round"/><rect x="9" y="9" width="6" height="6" rx="1" '
        'fill="currentColor"/></svg>')

NAV = [("catalog/", "Catalog", "catalog"), ("docs/", "Docs", "docs"),
       ("docs/submit/", "Submit", "submit"), ("docs/report/", "Report a test", "report")]


class Site:
    def __init__(self, catalog, out, registry_dir=None, built=None):
        self.catalog = catalog
        self.out = out
        self.registry_dir = registry_dir
        self.built = built or _dt.datetime.now(_dt.timezone.utc)
        kexts = [k for k in items(catalog.get("kexts")) if isinstance(k, dict)]
        self.skipped = [text(k.get("id")) for k in kexts if not ID_RE.match(text(k.get("id")))]
        seen, self.kexts = set(), []
        for k in kexts:
            kid = text(k.get("id"))
            if ID_RE.match(kid) and kid not in seen:
                seen.add(kid)
                self.kexts.append(k)
        self.kexts.sort(key=lambda k: (text(k.get("name")) or text(k.get("id"))).casefold())
        self.written = []

    # -- output
    def write(self, rel, content):
        path = os.path.join(self.out, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        self.written.append(rel)

    def page(self, rel, title, body, *, desc="", active=None, script=None, absolute=False):
        depth = rel.count("/")
        base = "/" if absolute else "../" * depth or "./"
        csp_script = "'none'"
        script_tag = ""
        if script:
            digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
            csp_script = f"'sha256-{digest}'"
            script_tag = f"<script>{script}</script>"
        csp = (f"default-src 'none'; style-src 'self'; img-src 'self'; script-src {csp_script}; "
               "base-uri 'none'; form-action 'none'")
        cur = ' aria-current="page"'
        nav = "".join(
            f'<li><a href="{base}{href}"{cur if key == active else ""}>{label}</a></li>'
            for href, label, key in NAV)
        full_title = f"{title} · {SITE_NAME}" if title != SITE_NAME else SITE_NAME
        when = self.built.strftime("%Y-%m-%d %H:%M UTC")
        doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="color-scheme" content="light dark">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc or 'Open-source hackintosh drivers, ports and driver-development guides, with test reports from the community.')}">
<link rel="icon" href="{base}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{base}assets/style.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header"><div class="wrap">
<a class="brand" href="{base}">{LOGO}<span>open<b>kext</b></span></a>
<nav aria-label="Main"><ul>{nav}<li><a href="{GITHUB_ORG}">GitHub</a></li></ul></nav>
</div></header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer"><div class="wrap">
<p>OpenKext is a community catalog of open-source drivers for macOS on non-Apple hardware. It hosts no binaries and no Apple software, and it is not affiliated with Apple.</p>
<p>Catalog data: CC0-1.0, from <a href="{REGISTRY_REPO}">openkext/registry</a>, also as <a href="{base}catalog.json">catalog.json</a>. Site: MIT, <a href="{SITE_REPO}">openkext/openkext.github.io</a>. Built {esc(when)}.</p>
</div></footer>
{script_tag}
</body>
</html>
"""
        self.write(rel, doc)

    # -- shared pieces
    def card(self, k, base):
        kid = text(k.get("id"))
        st = obj(k.get("status"))
        mac = obj(k.get("macos"))
        cats = ", ".join(esc(CATEGORIES.get(text(c), text(c))) for c in items(k.get("categories")))
        rng = f"macOS {esc(text(mac.get('min')))} to {esc(text(mac.get('max')))}" if mac else ""
        meta = [x for x in (cats, rng, esc(text(k.get("license"))),
                            f"release {esc(text(st.get('release')))}" if st.get("release") else "") if x]
        return (f'<li data-id="{esc(kid)}"><article class="card">'
                f'<h3><a href="{base}kext/{esc(kid)}/">{esc(text(k.get("name")) or kid)}</a></h3>'
                f'{status_badge(text(st.get("status")))}'
                f'<p>{esc(text(k.get("summary")))}</p>'
                f'<p class="meta">{" · ".join(meta)}</p></article></li>')

    def latest_date(self, k):
        dates = [text(obj(r).get("date")) for r in items(k.get("releases"))]
        return max(dates) if dates else ""

    # -- pages
    def home(self):
        base = "./"
        recent = sorted(self.kexts, key=self.latest_date, reverse=True)[:6]
        if recent:
            cards = f'<ul class="cards">{"".join(self.card(k, base) for k in recent)}</ul>'
        else:
            cards = '<p class="empty">No projects yet. <a href="docs/submit/">Submit the first one.</a></p>'
        n = len(self.kexts)
        body = f"""
<section class="intro">
<h1>Open-source drivers for macOS on PC hardware, tested by the people who use them</h1>
<p class="lead">OpenKext is a community catalog of open-source kexts, driver ports and patches for hackintosh hardware, with public test reports and guides for writing your own drivers. Each entry links to the project's own repository and GitHub releases, with SHA-256 hashes the registry checks.</p>
<p><a class="button" href="catalog/">Browse the catalog ({n} project{'s' if n != 1 else ''})</a> <a class="button secondary" href="docs/submit/">Submit a project</a></p>
</section>
<section aria-labelledby="how">
<h2 id="how">How it works</h2>
<ol class="points">
<li><h3>Fixed rules approve listings</h3><p>A bot checks every pull request against published rules: open-source license, public source, release hashes. If they pass, it merges.</p></li>
<li><h3>Test reports decide status</h3><p>A project becomes community-verified when {COMMUNITY_MIN} people who don't maintain it report its newest release working. Nobody sets a status by hand.</p></li>
<li><h3>Maintainers handle edge cases</h3><p>Unlisted licenses, abandoned projects, removed reports and rule changes go to a person. Everything else goes through without waiting for one.</p></li>
</ol>
<p><a href="docs/governance/">Read the rules</a></p>
</section>
<section aria-labelledby="recent">
<h2 id="recent">Recently released</h2>
{cards}
</section>
<section class="cta" aria-labelledby="help">
<h2 id="help">Take part</h2>
<div class="cta-grid">
<div><h3>Report a test</h3><p>Ran a driver from the catalog? Add a short TOML file with your result, macOS build and hardware. It takes a few minutes and it is what moves a project to verified.</p><p><a href="docs/report/">How to report a test</a></p></div>
<div><h3>Submit a project</h3><p>Maintain an open-source kext, patch or porting tool? Add one file describing it and its releases. The bot answers in a couple of minutes.</p><p><a href="docs/submit/">How to submit</a></p></div>
<div><h3>Write or port a driver</h3><p>Guides on building kexts, reading IOKit classes, porting drivers to new chips and new macOS versions, and the legal lines to stay inside.</p><p><a href="docs/driver-development/">Driver development guide</a></p></div>
</div>
</section>
"""
        self.page("index.html", SITE_NAME, body)

    def catalog_page(self):
        base = "../"
        majors = set()
        index = []
        for k in self.kexts:
            mac = obj(k.get("macos"))
            lo, hi = version_key(mac.get("min")), version_key(mac.get("max"))
            for m in list(MACOS_NAMES) + [major_of(mac.get("min")), major_of(mac.get("max"))]:
                if m and lo and hi and lo <= version_key(m) <= hi:
                    majors.add(m)
            ids, words = [], [text(k.get("id")), text(k.get("name")), text(k.get("summary"))]
            for h in items(k.get("hardware")):
                h = obj(h)
                ids += [text(x) for x in items(h.get("pci_ids")) + items(h.get("usb_ids"))]
                words += [text(h.get("vendor")), text(h.get("description"))]
            index.append({
                "id": text(k.get("id")),
                "c": [text(c) for c in items(k.get("categories"))],
                "s": text(obj(k.get("status")).get("status")),
                "min": text(mac.get("min")), "max": text(mac.get("max")),
                "q": " ".join(words + ids).lower(),
            })
        cats = sorted({c for e in index for c in e["c"]}, key=lambda c: CATEGORIES.get(c, c))
        opt = lambda v, label: f'<option value="{esc(v)}">{esc(label)}</option>'  # noqa: E731
        cat_opts = "".join(opt(c, CATEGORIES.get(c, c)) for c in cats)
        st_opts = "".join(opt(s, label) for s, (label, _) in STATUSES.items())
        mac_opts = "".join(opt(m, f"macOS {m} {MACOS_NAMES.get(m, '')}".strip())
                           for m in sorted(majors, key=version_key, reverse=True))
        data = json.dumps(index, separators=(",", ":"), ensure_ascii=True)
        data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        listing = "".join(self.card(k, base) for k in self.kexts)
        n = len(self.kexts)
        body = f"""
<h1>Catalog</h1>
<p class="lead">Open-source drivers, patches and porting tools for macOS on non-Apple hardware. Status comes from test reports; <a href="../docs/governance/">see how</a>.</p>
<form id="filters" class="filters" role="search" hidden>
<label>Search <input type="search" name="q" placeholder="Name or device ID, e.g. 8086:2725" autocomplete="off" spellcheck="false"></label>
<label>Category <select name="category"><option value="">All</option>{cat_opts}</select></label>
<label>Status <select name="status"><option value="">All</option>{st_opts}</select></label>
<label>macOS <select name="macos"><option value="">Any</option>{mac_opts}</select></label>
</form>
<p id="count" class="count" aria-live="polite">{n} project{'s' if n != 1 else ''}</p>
<ul id="kexts" class="cards list">{listing}</ul>
<p id="empty" class="empty" {'hidden' if n else ''}>{'No projects match these filters.' if n else 'No projects yet. <a href="../docs/submit/">Submit the first one.</a>'}</p>
<script type="application/json" id="catalog-data">{data}</script>
"""
        self.page("catalog/index.html", "Catalog", body, active="catalog", script=CATALOG_JS,
                  desc="Open-source hackintosh drivers with their status, hardware IDs and supported macOS versions.")

    def status_text(self, k):
        st = obj(k.get("status"))
        s = text(st.get("status"))
        rel = esc(text(st.get("release")))
        testers = [text(t) for t in items(st.get("testers"))]
        broken = [text(t) for t in items(st.get("broken_reports"))]

        def people(ls):
            return ", ".join(link_or_text(f"https://github.com/{t}", t) if LOGIN_RE.match(t) else esc(t) for t in ls)

        if s == "community-verified":
            return (f"{len(testers)} people who don't maintain this project report release {rel} working or partly "
                    f"working: {people(testers)}.")
        if s == "reported-broken":
            return (f"{len(broken)} people who don't maintain this project report release {rel} broken "
                    f"({people(broken)}), more than report it working. Read their notes before installing.")
        need = max(0, COMMUNITY_MIN - len(testers))
        more = (f" {need} more report{'s' if need != 1 else ''} from people who don't maintain it "
                f"will make it community-verified.") if need else ""
        if testers:
            others = (f" {len(testers)} {'person' if len(testers) == 1 else 'people'} who don't maintain it "
                      f"confirmed it: {people(testers)}.")
        else:
            others = " Nobody else has confirmed it yet." if s == "author-tested" else ""
        if s == "author-tested":
            return f"A maintainer reports release {rel} working.{others}{more}"
        if s == "submitted":
            first = (f"Nobody has reported release {rel} working yet." if not testers and not broken
                     else f"No maintainer has reported release {rel} working.")
            return (f"Listed after the rules checked the source, license and release hashes. {first}"
                    f"{others}{more}")
        return "Unknown status."

    def kext_page(self, k):
        kid = text(k.get("id"))
        rel_path = f"kext/{kid}/index.html"
        base = "../../"
        name = text(k.get("name")) or kid
        st = obj(k.get("status"))
        repo = gh_repo(k.get("source"))
        maint = [text(m) for m in items(k.get("maintainers"))]
        maint_l = {m.lower() for m in maint}

        links = [f'<a class="button" href="{esc(repo)}">Source</a>' if repo else
                 f'<span>Source: {link_or_text(k.get("source"))}</span>']
        if repo:
            links.append(f'<a class="button secondary" href="{esc(repo)}/releases">Releases</a>')
        if safe_https(k.get("homepage")):
            links.append(f'<a class="button secondary" href="{esc(safe_https(k.get("homepage")))}">Homepage</a>')
        links.append(f'<a class="button secondary" href="{REGISTRY_REPO}/blob/main/kexts/{q(kid)}/kext.toml">'
                     f'Registry entry</a>')

        mac = obj(k.get("macos"))
        rows = [
            ("Kind", esc(text(k.get("kind")))),
            ("Categories", ", ".join(esc(CATEGORIES.get(text(c), text(c))) for c in items(k.get("categories")))),
            ("License", f"<code>{esc(text(k.get('license')))}</code>" +
             (f" (file: <code>{esc(text(k.get('license_file')))}</code>)" if k.get("license_file") else "")),
            ("Maintainers", ", ".join(link_or_text(f"https://github.com/{m}", m) if LOGIN_RE.match(m) else esc(m)
                                      for m in maint)),
            ("Tested on", f"macOS {esc(macos_label(text(mac.get('min'))))} to {esc(macos_label(text(mac.get('max'))))}"
             if mac else "Not stated"),
            ("AI disclosure", esc(text(k.get("ai_disclosure"))) or "Not stated"),
        ]
        based = items(k.get("based_on"))
        if based:
            rows.append(("Based on", "<ul class='plain'>" + "".join(f"<li>{link_or_text(b)}</li>" for b in based)
                         + "</ul>"))
        details = "".join(f"<dt>{a}</dt><dd>{b}</dd>" for a, b in rows if b)

        hw = items(k.get("hardware"))
        if hw:
            trs = "".join(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    esc(text(obj(h).get("vendor"))), esc(text(obj(h).get("description"))),
                    " ".join(f"<code>{esc(text(x))}</code>" for x in items(obj(h).get("pci_ids"))) or "",
                    " ".join(f"<code>{esc(text(x))}</code>" for x in items(obj(h).get("usb_ids"))) or "")
                for h in hw)
            hw_html = ('<div class="table-wrap"><table><thead><tr><th>Vendor</th><th>Device</th><th>PCI IDs</th>'
                       f'<th>USB IDs</th></tr></thead><tbody>{trs}</tbody></table></div>')
        else:
            hw_html = "<p>No hardware IDs listed.</p>"

        rels = list(enumerate(items(k.get("releases"))))
        rels.sort(key=lambda p: (text(obj(p[1]).get("date")), p[0]), reverse=True)
        rel_html = []
        for _, r in rels:
            r = obj(r)
            tag = text(r.get("tag"))
            title = esc(text(r.get("version")))
            if repo and tag:
                title = f'<a href="{esc(repo)}/releases/tag/{q(tag)}">{title}</a>'
            assets = []
            for a in items(r.get("assets")):
                a = obj(a)
                an = text(a.get("name"))
                nm = f'<a href="{esc(repo)}/releases/download/{q(tag)}/{q(an)}">{esc(an)}</a>' if repo and tag and an \
                    else esc(an)
                assets.append(f"<tr><td>{nm}</td><td>{esc(text(a.get('macos'))) or 'any'}</td>"
                              f'<td><code class="hash">{esc(text(a.get("sha256")))}</code></td></tr>')
            notes = f'<p class="notes">{esc(text(r.get("notes")))}</p>' if r.get("notes") else ""
            rel_html.append(
                f'<section class="release"><h3>{title}</h3>'
                f'<p class="meta">Tag <code>{esc(tag)}</code> · {esc(text(r.get("date")))}</p>{notes}'
                '<div class="table-wrap"><table><thead><tr><th>Asset</th><th>macOS</th><th>SHA-256</th></tr></thead>'
                f'<tbody>{"".join(assets)}</tbody></table></div></section>')

        reps = sorted(items(k.get("reports")), key=lambda r: text(obj(r).get("date")), reverse=True)
        rep_rows = []
        for r in reps:
            r = obj(r)
            login = text(r.get("login"))
            who = link_or_text(f"https://github.com/{login}", login) if LOGIN_RE.match(login) else esc(login)
            if login.lower() in maint_l:
                who += ' <span class="tag">maintainer</span>'
            path = text(r.get("path"))
            if path and re.match(r"^kexts/[\w./-]+$", path) and ".." not in path:
                who += f'<br><a class="small" href="{REGISTRY_REPO}/blob/main/{esc(path)}">file</a>'
            m = obj(r.get("macos"))
            h = obj(r.get("hardware"))
            hw_lines = [esc(text(h.get(f))) for f in ("cpu", "smbios", "board", "bootloader") if h.get(f)]
            dev = " ".join(f"<code>{esc(text(x))}</code>" for x in items(h.get("device_ids")))
            if dev:
                hw_lines.append(dev)
            feats = "".join(f"<li><code>{esc(fk)}</code> {result_badge(fv)}</li>"
                            for fk, fv in obj(r.get("features")).items())
            asset = f'<br><span class="small">{esc(text(r.get("asset")))}</span>' if r.get("asset") else ""
            rep_rows.append(
                f"<tr><td>{who}</td><td>{result_badge(r.get('result'))}</td>"
                f"<td>{esc(text(r.get('version')))}{asset}</td>"
                f"<td>{esc(text(m.get('version')))}<br><code>{esc(text(m.get('build')))}</code></td>"
                f"<td>{'<br>'.join(hw_lines)}</td>"
                f"<td>{FEATS_OPEN + feats + '</ul>' if feats else ''}</td>"
                f'<td class="notes">{esc(text(r.get("notes")))}</td>'
                f"<td>{esc(text(r.get('date')))}</td></tr>")
        if rep_rows:
            rep_html = ('<div class="table-wrap"><table class="reports"><thead><tr><th>Tester</th><th>Result</th>'
                        '<th>Release</th><th>macOS</th><th>Hardware</th><th>Features</th><th>Notes</th>'
                        f'<th>Date</th></tr></thead><tbody>{"".join(rep_rows)}</tbody></table></div>')
        else:
            rep_html = "<p>No test reports yet.</p>"

        # A report template for this project, prefilled with the newest release.
        latest = text(st.get("release"))
        newest = obj(rels[0][1]) if rels else {}
        first_asset = text(obj((items(newest.get("assets")) or [{}])[0]).get("name"))
        tmpl = "\n".join([
            f"kext = {json.dumps(kid)}",
            f"version = {json.dumps(latest)}",
            *([f"asset = {json.dumps(first_asset, ensure_ascii=False)}"] if first_asset else []),
            'result = "works"                 # works | partial | broken',
            f"date = {self.built.date().isoformat()}",
            'notes = "What you tested and what failed."',
            "",
            "[macos]",
            'version = ""                     # sw_vers -productVersion',
            'build = ""                       # sw_vers -buildVersion',
            "",
            "[hardware]",
            'cpu = ""',
            'smbios = ""',
            '# bootloader = ""               # optional',
            '# device_ids = ["vvvv:dddd"]    # optional',
            "",
        ])
        fname = re.sub(r"[^a-z0-9._-]", "-", latest.lower()) or "report"
        new_url = (f"{REGISTRY_REPO}/new/main/kexts/{q(kid)}/reports?filename="
                   f"{q('YOUR-LOGIN/' + fname + '-BUILD.toml')}&value={q(tmpl)}")

        body = f"""
<nav class="crumbs" aria-label="Breadcrumb"><a href="{base}catalog/">Catalog</a> / <span>{esc(name)}</span></nav>
<header class="kext-head">
<h1>{esc(name)}</h1>
{status_badge(text(st.get("status")))}
<p class="lead">{esc(text(k.get("summary")))}</p>
<p class="actions">{" ".join(links)}</p>
</header>
<section aria-labelledby="status"><h2 id="status">Status</h2>
<p>{status_badge(text(st.get("status")))} {self.status_text(k)}</p>
<p class="small">Status is computed from test reports of the newest release. <a href="{base}docs/governance/">How status works</a>.</p>
</section>
<section aria-labelledby="details"><h2 id="details">Details</h2><dl class="details">{details}</dl></section>
<section aria-labelledby="hardware"><h2 id="hardware">Hardware</h2>{hw_html}</section>
<section aria-labelledby="releases"><h2 id="releases">Releases</h2>
<p class="small">Downloads come from the project's GitHub releases. Check the SHA-256 with <code>shasum -a 256 FILE</code>.</p>
{"".join(rel_html) or "<p>No releases listed.</p>"}</section>
<section aria-labelledby="reports"><h2 id="reports">Test reports ({len(reps)})</h2>{rep_html}</section>
<section aria-labelledby="add-report" class="cta"><h2 id="add-report">Add your report</h2>
<p>Tested this project? Save this as <code>kexts/{esc(kid)}/reports/YOUR-LOGIN/{esc(fname)}-BUILD.toml</code> in the registry, fill it in, and open a pull request. <a href="{base}docs/report/">Report format</a>.</p>
<pre><code>{esc(tmpl)}</code></pre>
<p><a class="button" href="{esc(new_url)}">Open it in the GitHub editor</a> <span class="small">Replace YOUR-LOGIN and BUILD in the file name first.</span></p>
</section>
"""
        self.page(rel_path, name, body, active="catalog", desc=text(k.get("summary"))[:280])

    # -- docs
    def registry_file(self, rel):
        if not self.registry_dir:
            return None
        p = os.path.join(self.registry_dir, *rel.split("/"))
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
        return None

    def resolver(self, base, origin_dir, origin):
        """Rewrite links in a Markdown source. origin: 'content' or 'registry'."""
        reg_map = {"GOVERNANCE.md": "docs/governance/", "docs/kext-format.md": "docs/submit/#format",
                   "docs/report-format.md": "docs/report/#format", "README.md": ""}

        def resolve(url):
            if url.startswith("site:"):
                return base + url[5:]
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url) or url.startswith(("#", "/")):
                return url
            path, _, frag = url.partition("#")
            frag = f"#{frag}" if frag else ""
            if origin == "content":
                slug = path.removesuffix(".md")
                if path.endswith(".md") and "/" not in path and slug in DOC_SLUGS:
                    return f"{base}docs/{slug}/{frag}"
                return url
            norm = posixpath.normpath(posixpath.join(origin_dir, path))
            if norm in reg_map:
                target = base + reg_map[norm]
                return target + frag if "#" not in target or not frag else target.split("#")[0] + frag
            return f"{REGISTRY_REPO}/blob/main/{norm}{frag}"

        return resolve

    def doc_page(self, slug, title, desc, parts, edit_url):
        base = "../../"
        md = None
        html_parts = []
        for kind, rel, shift, wrap_id in parts:
            if kind == "content":
                with open(os.path.join(HERE, "content", rel), encoding="utf-8") as f:
                    src = f.read()
                resolve = self.resolver(base, "", "content")
            else:
                src = self.registry_file(rel)
                resolve = self.resolver(base, posixpath.dirname(rel), "registry")
                if src is None:
                    print(f"warning: registry file {rel} not found, linking to GitHub instead", file=sys.stderr)
                    src = f"# {title}\n\nRead [{rel}]({REGISTRY_REPO}/blob/main/{rel}) in the registry.\n"
            if md is None:
                md = Markdown(resolve)
            md.resolve = resolve
            h = md.render(src, shift)
            html_parts.append(f'<section id="{wrap_id}">{h}</section>' if wrap_id else h)
        toc = "".join(f'<li><a href="#{esc(i)}">{t}</a></li>' for _, i, t in md.toc)
        cur = ' aria-current="page"'
        side = "".join(
            f'<li><a href="{base}docs/{s}/"{cur if s == slug else ""}>{esc(t)}</a></li>'
            for s, t, _ in DOCS)
        content = "".join(html_parts)
        if len(md.toc) > 2:
            box = f'<details class="toc"><summary>On this page</summary><ul>{toc}</ul></details>'
            end = content.find("</h1>")
            content = content[:end + 5] + box + content[end + 5:] if end >= 0 else box + content
        body = f"""
<div class="docs">
<nav class="docs-nav" aria-label="Docs"><p class="nav-title"><a href="{base}docs/">Docs</a></p><ul>{side}</ul></nav>
<article class="prose">
{content}
<p class="edit small"><a href="{esc(edit_url)}">Edit this page on GitHub</a></p>
</article>
</div>
"""
        active = slug if slug in ("submit", "report") else "docs"
        self.page(f"docs/{slug}/index.html", title, body, active=active, desc=desc)

    def docs(self):
        for slug, title, desc in DOCS:
            parts, edit = DOC_SOURCES[slug]
            self.doc_page(slug, title, desc, parts, edit)
        lis = "".join(f'<li><a href="{s}/">{esc(t)}</a><p>{esc(d)}</p></li>' for s, t, d in DOCS)
        body = f"""
<h1>Docs</h1>
<p class="lead">How to take part in the catalog, how decisions are made, and how to write and port macOS drivers.</p>
<ul class="doc-list">{lis}</ul>
"""
        self.page("docs/index.html", "Docs", body, active="docs")

    def not_found(self):
        body = """
<h1>Page not found</h1>
<p>Nothing here. Try the <a href="/catalog/">catalog</a> or the <a href="/docs/">docs</a>.</p>
"""
        self.page("404.html", "Page not found", body, absolute=True)

    def build(self, catalog_src):
        if os.path.exists(self.out):
            if os.listdir(self.out) and not os.path.exists(os.path.join(self.out, OUT_MARKER)):
                raise SystemExit(f"refusing to delete {self.out}: not empty and not a previous build")
            shutil.rmtree(self.out)
        os.makedirs(self.out)
        open(os.path.join(self.out, OUT_MARKER), "w").close()
        open(os.path.join(self.out, ".nojekyll"), "w").close()
        shutil.copytree(os.path.join(HERE, "static"), os.path.join(self.out, "assets"))
        if os.path.isfile(os.path.join(HERE, "CNAME")):
            shutil.copyfile(os.path.join(HERE, "CNAME"), os.path.join(self.out, "CNAME"))
        shutil.copyfile(catalog_src, os.path.join(self.out, "catalog.json"))
        self.home()
        self.catalog_page()
        for k in self.kexts:
            self.kext_page(k)
        self.docs()
        self.not_found()
        for s in self.skipped:
            print(f"warning: skipped entry with invalid id {s!r}", file=sys.stderr)


DOCS = [
    ("submit", "Submit a project", "List your open-source kext, patch or tool in the catalog."),
    ("report", "Report a test", "Tell others whether a project works on your hardware."),
    ("governance", "Governance", "The rules the bot applies, how status is computed, and what maintainers decide."),
    ("driver-development", "Driver development guide",
     "Legal boundaries, tools, reading IOKit classes, build hygiene and testing on hardware."),
    ("porting-hardware", "Porting a driver to new hardware",
     "Bring a Linux or BSD driver to macOS, then add the features macOS expects."),
    ("porting-macos", "Porting a driver to a new macOS version",
     "Find what a new macOS release broke and fix it without guessing."),
]
DOC_SLUGS = {s for s, _, _ in DOCS}
DOC_SOURCES = {
    # slug: ([(kind, path, heading shift, wrapper id)], edit URL)
    "submit": ([("content", "submit.md", 0, None), ("registry", "docs/kext-format.md", 1, "format")],
               f"{SITE_REPO}/blob/main/content/submit.md"),
    "report": ([("content", "testing.md", 0, None), ("registry", "docs/report-format.md", 1, "format")],
               f"{SITE_REPO}/blob/main/content/testing.md"),
    "governance": ([("registry", "GOVERNANCE.md", 0, None)], f"{REGISTRY_REPO}/blob/main/GOVERNANCE.md"),
    "driver-development": ([("content", "driver-development.md", 0, None)],
                           f"{SITE_REPO}/blob/main/content/driver-development.md"),
    "porting-hardware": ([("content", "porting-hardware.md", 0, None)],
                         f"{SITE_REPO}/blob/main/content/porting-hardware.md"),
    "porting-macos": ([("content", "porting-macos.md", 0, None)],
                      f"{SITE_REPO}/blob/main/content/porting-macos.md"),
}

CATALOG_JS = r"""(function () {
  var data = JSON.parse(document.getElementById("catalog-data").textContent);
  var form = document.getElementById("filters");
  var list = document.querySelectorAll("#kexts > li");
  var count = document.getElementById("count");
  var empty = document.getElementById("empty");
  if (!form || !list.length) return;
  var byId = {};
  data.forEach(function (k) { byId[k.id] = k; });
  var el = form.elements;
  function key(v) {
    var p = String(v || "").match(/\d+/g);
    if (!p) return 0;
    var a = parseInt(p[0], 10);
    return a === 10 ? 1000 + (p[1] ? parseInt(p[1], 10) : 0) : a * 100;
  }
  function apply() {
    var words = el.q.value.toLowerCase().split(/\s+/).filter(Boolean);
    var c = el.category.value, s = el.status.value, m = key(el.macos.value), n = 0;
    list.forEach(function (li) {
      var k = byId[li.getAttribute("data-id")];
      var ok = !!k;
      if (ok && c) ok = k.c.indexOf(c) >= 0;
      if (ok && s) ok = k.s === s;
      if (ok && m) ok = key(k.min) <= m && m <= key(k.max);
      if (ok && words.length) ok = words.every(function (w) { return k.q.indexOf(w) >= 0; });
      li.hidden = !ok;
      if (ok) n++;
    });
    count.textContent = n + (n === 1 ? " project" : " projects");
    empty.hidden = n !== 0;
    var p = new URLSearchParams();
    ["q", "category", "status", "macos"].forEach(function (f) { if (el[f].value) p.set(f, el[f].value); });
    var qs = p.toString();
    history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  }
  var init = new URLSearchParams(location.search);
  ["q", "category", "status", "macos"].forEach(function (f) {
    var v = init.get(f);
    if (v === null) return;
    if (el[f].tagName === "SELECT" && !Array.prototype.some.call(el[f].options, function (o) { return o.value === v; })) return;
    el[f].value = v;
  });
  form.hidden = false;
  form.addEventListener("input", apply);
  form.addEventListener("change", apply);
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  apply();
})();"""


def find_registry(explicit):
    if explicit:
        return explicit
    for d in ("registry", os.path.join("..", "registry")):
        for root in (os.getcwd(), HERE):
            p = os.path.normpath(os.path.join(root, d))
            if os.path.isfile(os.path.join(p, "GOVERNANCE.md")):
                return p
    return None


def build_site(catalog_path, out, registry_dir=None, built=None):
    with open(catalog_path, encoding="utf-8") as f:
        catalog = json.load(f)
    if not isinstance(catalog, dict) or catalog.get("schema") != 1:
        raise SystemExit(f"{catalog_path}: expected a catalog with schema 1")
    site = Site(catalog, out, registry_dir, built)
    site.build(catalog_path)
    return site


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the OpenKext website.")
    ap.add_argument("catalog", help="catalog JSON written by registry/tools/check_registry.py")
    ap.add_argument("--out", default=os.path.join(HERE, "_site"), help="output directory (default: _site)")
    ap.add_argument("--registry", help="registry checkout (default: ./registry or ../registry)")
    a = ap.parse_args(argv)
    reg = find_registry(a.registry)
    if not reg:
        print("warning: registry checkout not found; governance and format pages will link to GitHub",
              file=sys.stderr)
    site = build_site(a.catalog, a.out, reg)
    print(f"OK: {len(site.written)} pages, {len(site.kexts)} project(s), written to {a.out}")


if __name__ == "__main__":
    main()
