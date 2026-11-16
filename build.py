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


# ---------------------------------------------------------------- shared components
# Small HTML builders for the components in static/style.css (class list at the top of that
# file). Every argument that can hold catalog data is escaped here. Arguments named *_html are
# trusted markup built by the caller and are inserted as they are.

MONO_HUES = 12
_STOP_WORDS = {"a", "an", "and", "for", "of", "on", "the", "to", "with"}


def initials(name):
    """Two letters for a monogram: 'AirportItlwm with AWDL' -> 'AI', 'Good Wi-Fi' -> 'GW'."""
    words = [w for w in re.findall(r"[^\W_]+", text(name), flags=re.UNICODE)]
    if not words:
        return "?"
    caps = re.findall(r"[A-Z]", words[0])
    if len(caps) >= 2:
        return "".join(caps[:2])
    sig = [w for w in words if w.lower() not in _STOP_WORDS] or words
    if len(sig) >= 2:
        return (sig[0][0] + sig[1][0]).upper()
    return sig[0][:2].upper() if len(sig[0]) > 1 else sig[0].upper()


def hue_index(key):
    """A stable 0..MONO_HUES-1 bucket for an id, so a project keeps its color across builds."""
    return hashlib.sha256(text(key).encode("utf-8")).digest()[0] % MONO_HUES


def monogram(k, size=None):
    """A square avatar with a project's initials, tinted by a hue derived from its id.

    Decorative (aria-hidden): always place it next to the project name. size: None, 'sm' or 'lg'.
    """
    k = obj(k)
    kid = text(k.get("id"))
    letters = initials(text(k.get("name")) or kid)
    sz = f" monogram-{size}" if size in ("sm", "lg") else ""
    return f'<span class="monogram mono-h{hue_index(kid)}{sz}" aria-hidden="true">{esc(letters)}</span>'


def chip(label, kind=None, title=None):
    """A small inline label. kind: 'hw' (PCI/USB id, monospace), 'cat' (category), 'ver' (version,
    monospace) or None (plain)."""
    cls = f" chip-{kind}" if kind in ("hw", "cat", "ver") else ""
    t = f' title="{esc(title)}"' if title else ""
    return f'<span class="chip{cls}"{t}>{esc(label)}</span>'


def category_chip(c):
    return chip(CATEGORIES.get(text(c), text(c)), "cat")


def hash_chip(value):
    """A monospace hash that truncates to fit; the full value stays in the text (copyable) and title."""
    v = text(value)
    return f'<code class="hash-chip" title="{esc(v)}">{esc(v)}</code>'


def stat_tile(label, value, hint=None):
    h = f'<span class="stat-hint">{esc(hint)}</span>' if hint else ""
    return (f'<div class="stat"><span class="stat-value">{esc(value)}</span>'
            f'<span class="stat-label">{esc(label)}</span>{h}</div>')


def stat_tiles(tiles_html):
    """Wrap several stat_tile() results in the responsive grid."""
    return f'<div class="stats">{"".join(tiles_html)}</div>'


def button(label, href, variant="primary", size=None):
    """A link styled as a button. variant: primary, secondary or ghost; size: None, 'sm' or 'lg'.
    href is used as given (escaped): pass only site-relative paths or safe_https() results."""
    v = variant if variant in ("primary", "secondary", "ghost") else "primary"
    s = f" btn-{size}" if size in ("sm", "lg") else ""
    return f'<a class="btn btn-{v}{s}" href="{esc(href)}">{esc(label)}</a>'


def breadcrumbs(trail):
    """trail: [(label, href or None), ...]; the last entry is the current page."""
    lis = []
    for i, (label, href) in enumerate(trail):
        last = i == len(trail) - 1
        if href and not last:
            lis.append(f'<li><a href="{esc(href)}">{esc(label)}</a></li>')
        else:
            lis.append(f'<li><span aria-current="page">{esc(label)}</span></li>' if last
                       else f"<li><span>{esc(label)}</span></li>")
    return f'<nav class="crumbs" aria-label="Breadcrumb"><ol>{"".join(lis)}</ol></nav>'


def callout(body_html, kind="note", title=None):
    """A note or warning box. body_html is trusted markup; title is escaped."""
    kd = kind if kind in ("note", "warning") else "note"
    t = f'<p class="callout-title">{esc(title)}</p>' if title else ""
    return f'<div class="callout callout-{kd}" role="note">{t}{body_html}</div>'


def empty_state(title, body_html="", action_html=""):
    """An empty panel: what is missing and what to do about it. title is escaped."""
    b = f"<p>{body_html}</p>" if body_html else ""
    a = f'<p class="empty-action">{action_html}</p>' if action_html else ""
    return f'<div class="empty-state"><p class="empty-title">{esc(title)}</p>{b}{a}</div>'


LADDER = [("submitted", "Submitted", "neutral"), ("author-tested", "Author-tested", "blue"),
          ("community-verified", "Community-verified", "green")]


def status_ladder(k, compact=False):
    """The road to community-verified: submitted, author-tested, community-verified, with
    COMMUNITY_MIN tester slots filled by reports from people who don't maintain the project.

    A reported-broken project keeps its ladder and gets a red note under it.
    """
    st = obj(obj(k).get("status"))
    s = text(st.get("status"))
    testers = len(items(st.get("testers")))
    broken = len(items(st.get("broken_reports")))
    author = bool(st.get("author_tested")) or s == "author-tested"
    filled = min(testers, COMMUNITY_MIN)
    reached = {"submitted": True, "author-tested": author, "community-verified": s == "community-verified"}
    current = s if s in reached else ("author-tested" if author else "submitted")
    steps = []
    for key, label, color in LADDER:
        cls = ["ladder-step", f"s-{color}"]
        if reached[key]:
            cls.append("is-reached")
        if key == current:
            cls.append("is-current")
        state = ", current" if key == current else (", done" if reached[key] else ", not yet")
        aria = ' aria-current="step"' if key == current else ""
        extra = ""
        if key == "community-verified":
            slots = "".join(f'<span class="slot{" is-filled" if i < filled else ""}"></span>'
                            for i in range(COMMUNITY_MIN))
            extra = (f'<span class="ladder-slots" aria-hidden="true">{slots}</span>'
                     f'<span class="ladder-count">{filled} of {COMMUNITY_MIN} testers</span>')
        steps.append(f'<li class="{" ".join(cls)}"{aria}><span class="ladder-node" aria-hidden="true"></span>'
                     f'<span class="ladder-label">{label}<span class="sr-only">{state}</span></span>{extra}</li>')
    note = ""
    if s == "reported-broken":
        note = (f'<p class="ladder-broken">{badge("Reported broken", "red")} '
                f'{broken} report{"s" if broken != 1 else ""} from people who don\'t maintain it say the newest '
                f'release is broken.</p>')
    mods = (" ladder-compact" if compact else "") + (" is-broken" if s == "reported-broken" else "")
    return (f'<div class="ladder{mods}"><ol class="ladder-steps" aria-label="Status">{"".join(steps)}</ol>'
            f"{note}</div>")


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

LOGO = ('<svg class="logo" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true" focusable="false">'
        '<rect class="logo-tile" width="32" height="32" rx="8"/>'
        '<g class="logo-glyph" fill="none" stroke-width="2" stroke-linecap="round">'
        '<rect x="10" y="10" width="12" height="12" rx="2"/>'
        '<path d="M13.5 6v4M18.5 6v4M13.5 22v4M18.5 22v4M6 13.5h4M6 18.5h4M22 13.5h4M22 18.5h4"/></g>'
        '<rect class="logo-die" x="13.5" y="13.5" width="5" height="5" rx="1"/></svg>')
ARROW = ('<svg class="icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">'
         '<path d="M5 11 11 5M6 5h5v5" fill="none" stroke="currentColor" stroke-width="1.6" '
         'stroke-linecap="round" stroke-linejoin="round"/></svg>')
ICON_PATHS = {
    "search": "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
    "file": "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h4",
    "shield": "M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6zM9 12l2 2 4-4",
    "people": "M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M21 20v-1"
              "a4 4 0 0 0-3-3.9M15.5 4.1a3.5 3.5 0 0 1 0 6.8",
    "check": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12.5l2.7 2.7L16 9.8",
    "flask": "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3M7.5 15h9",
    "upload": "M12 16V4M7 9l5-5 5 5M5 20h14",
    "code": "M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16",
}


def icon(name):
    """A decorative 20px line icon (aria-hidden) from ICON_PATHS."""
    return ('<svg class="i" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">'
            f'<path d="{ICON_PATHS[name]}" fill="none" stroke="currentColor" stroke-width="1.7" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def dot(color):
    """A status color dot for the catalog's status control; '' for no color."""
    ok = color in ("neutral", "blue", "green", "amber", "red")
    return f'<span class="seg-dot d-{color}" aria-hidden="true"></span>' if ok else ""


PAGE_CSS = ("pages-home-catalog.css", "pages-kext-docs.css")  # phase-specific sheets, loaded after style.css


def lockup(base):
    return f'<a class="brand" href="{base}">{LOGO}<span class="wordmark">open<b>kext</b></span></a>'


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

    def page(self, rel, title, body, *, desc="", active=None, script=None, absolute=False, jsonld=None):
        depth = rel.count("/")
        base = "/" if absolute else "../" * depth or "./"
        csp_script = "'none'"
        script_tag = ""
        if script:
            digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
            csp_script = f"'sha256-{digest}'"
            script_tag = f"<script>{script}</script>"
        csp = (f"default-src 'none'; style-src 'self'; img-src 'self'; script-src {csp_script}; "
               "base-uri 'none'; form-action 'self'")
        cur = ' aria-current="page"'
        nav = "".join(
            f'<li><a href="{base}{href}"{cur if key == active else ""}>{label}</a></li>'
            for href, label, key in NAV)
        page_css = "".join(f'<link rel="stylesheet" href="{base}assets/{c}">\n' for c in PAGE_CSS)
        extra_head = ""
        if rel.endswith("index.html"):
            extra_head += '<link rel="alternate" type="text/markdown" href="index.md">\n'
        extra_head += f'<link rel="alternate" type="text/plain" title="LLM index" href="{base}llms.txt">\n'
        extra_head += f'<link rel="sitemap" type="application/xml" href="{base}sitemap.xml">\n'
        if jsonld:
            extra_head += f'<script type="application/ld+json">{jsonld}</script>\n'
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
<meta name="theme-color" content="#f6f7f9" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0b0d10" media="(prefers-color-scheme: dark)">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc or 'Open-source hackintosh drivers, ports and driver-development guides, with test reports from the community.')}">
<link rel="icon" href="{base}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{base}assets/style.css">
{page_css}{extra_head}</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header"><div class="wrap header-bar">
{lockup(base)}
<nav class="site-nav" aria-label="Main"><ul>{nav}</ul></nav>
<a class="header-gh" href="{GITHUB_ORG}">GitHub{ARROW}</a>
</div></header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer"><div class="wrap">
<div class="footer-grid">
<div class="footer-about">
{lockup(base)}
<p>A community catalog of open-source drivers for macOS on non-Apple hardware. It hosts no binaries and no Apple software, and it is not affiliated with Apple.</p>
</div>
<nav class="footer-nav" aria-label="Footer">
<div><p class="footer-h">Catalog</p><ul><li><a href="{base}catalog/">Browse projects</a></li><li><a href="{base}catalog.json">catalog.json</a></li><li><a href="{base}api/index.json">JSON API</a></li><li><a href="{base}llms.txt">llms.txt</a></li></ul></div>
<div><p class="footer-h">Take part</p><ul><li><a href="{base}docs/submit/">Submit a project</a></li><li><a href="{base}docs/report/">Report a test</a></li><li><a href="{base}docs/governance/">Governance</a></li><li><a href="{base}docs/">All docs</a></li></ul></div>
<div><p class="footer-h">Source</p><ul><li><a href="{REGISTRY_REPO}">openkext/registry</a></li><li><a href="{SITE_REPO}">openkext.github.io</a></li><li><a href="{GITHUB_ORG}">GitHub organization</a></li></ul></div>
</nav>
</div>
<p class="footer-base">Catalog data CC0-1.0, site code MIT. <span class="mono">Built <time datetime="{self.built.strftime('%Y-%m-%dT%H:%MZ')}">{esc(when)}</time></span></p>
</div></footer>
{script_tag}
</body>
</html>
"""
        self.write(rel, doc)

    # -- shared pieces
    def hw_ids(self, k):
        """A project's PCI and USB ids, in order, without duplicates."""
        ids = []
        for h in items(k.get("hardware")):
            h = obj(h)
            for x in items(h.get("pci_ids")) + items(h.get("usb_ids")):
                t = text(x)
                if t and t not in ids:
                    ids.append(t)
        return ids

    def totals(self):
        """Numbers for the home page: projects, test reports, community-verified, hardware ids."""
        ids, reports, verified = set(), 0, 0
        for k in self.kexts:
            ids.update(i.lower() for i in self.hw_ids(k))
            st = obj(k.get("status"))
            rt = st.get("reports_total")
            ok = isinstance(rt, int) and not isinstance(rt, bool) and rt >= 0
            reports += rt if ok else len(items(k.get("reports")))
            verified += text(st.get("status")) == "community-verified"
        return {"projects": len(self.kexts), "reports": reports, "verified": verified, "hw": len(ids)}

    def card(self, k, base, hw_max=4):
        """A project card: name, summary, hardware ids, meta, and a compact status ladder."""
        kid = text(k.get("id"))
        st = obj(k.get("status"))
        mac = obj(k.get("macos"))
        cats = "".join(category_chip(c) for c in items(k.get("categories")))
        rng = (f'<span class="meta-item">macOS {esc(text(mac.get("min")))} to {esc(text(mac.get("max")))}</span>'
               if mac else "")
        rel = chip(text(st.get("release")), "ver", "Newest release") if st.get("release") else ""
        ids = self.hw_ids(k)
        hw = "".join(chip(i, "hw") for i in ids[:hw_max])
        if len(ids) > hw_max:
            hw += chip(f"+{len(ids) - hw_max} more", title="All hardware IDs are on the project page")
        hw_html = f'<p class="chips kcard-hw"><span class="sr-only">Hardware IDs: </span>{hw}</p>' if hw else ""
        return (f'<li data-id="{esc(kid)}"><article class="card card-link kcard">'
                f'<div class="card-head">{monogram(k)}<div class="card-title">'
                f'<h3><a href="{base}kext/{esc(kid)}/">{esc(text(k.get("name")) or kid)}</a></h3></div></div>'
                f'<p class="card-summary">{esc(text(k.get("summary")))}</p>{hw_html}'
                f'<div class="card-meta">{cats}{rng}{rel}</div>'
                f'<div class="kcard-status">{status_ladder(k, compact=True)}</div></article></li>')

    def latest_date(self, k):
        dates = [text(obj(r).get("date")) for r in items(k.get("releases"))]
        return max(dates) if dates else ""

    # -- pages
    def home(self):
        base = "./"
        t = self.totals()
        n = t["projects"]
        recent = sorted(self.kexts, key=self.latest_date, reverse=True)[:6]
        examples = []
        for k in recent:
            for i in self.hw_ids(k)[:2]:
                if i not in examples and len(examples) < 3:
                    examples.append(i)
        tries = "".join(f'<a class="hw-try" href="catalog/?q={esc(q(i))}">{chip(i, "hw")}</a>' for i in examples)
        try_html = f'<p class="hero-try"><span>Try</span>{tries}</p>' if tries else ""
        tiles = stat_tiles([
            stat_tile("Projects", n, "open source"),
            stat_tile("Test reports", t["reports"], "works, partial or broken"),
            stat_tile("Community-verified", t["verified"], f"{COMMUNITY_MIN} outside testers each"),
            stat_tile("Hardware IDs", t["hw"], "PCI and USB"),
        ])
        if recent:
            cards = f'<ul class="cards">{"".join(self.card(k, base, hw_max=3) for k in recent)}</ul>'
        else:
            cards = empty_state("No projects yet", "The catalog is waiting for its first entry.",
                                button("Submit a project", "docs/submit/"))
        steps = [
            ("file", "Submit", "The maintainer adds one <code>kext.toml</code> file by pull request: source, "
                               "license and releases."),
            ("shield", "Rules check", "A bot checks the license, the public source and the SHA-256 of every "
                                      "release. If they pass, it merges."),
            ("people", "Test reports", "People who run the driver add a short report: works, partial or broken, "
                                       "with their macOS build and hardware."),
            ("check", "Status", f"{COMMUNITY_MIN} working reports from people who don't maintain it make it "
                                "community-verified. Nobody sets a status by hand."),
        ]
        ladder = (f'<p class="pipe-badges">{badge("Submitted", "neutral")}{badge("Author-tested", "blue")}'
                  f'{badge("Community-verified", "green")}</p>')
        pipe = "".join(
            f'<li class="pipe-step"><span class="pipe-node">{icon(ic)}</span><p class="pipe-k">Step {i}</p>'
            f'<h3>{title}</h3><p>{body}</p>{ladder if i == len(steps) else ""}</li>'
            for i, (ic, title, body) in enumerate(steps, 1))
        all_link = f'<a class="sec-link" href="catalog/">Browse the catalog</a>' if n else ""
        body = f"""
<div class="home">
<section class="hero" aria-labelledby="hero-title">
<div class="hero-main">
<p class="eyebrow">Open-source hackintosh drivers</p>
<h1 id="hero-title">Find a driver for your hardware <span class="hero-accent">and see who it works for</span></h1>
<p class="lead">OpenKext lists open-source kexts, driver ports and patches for macOS on PC hardware. A project's status comes from test reports by people who don't maintain it.</p>
<form class="hero-search" action="catalog/" method="get" role="search">
<label class="hero-label" for="hero-q">Search by device ID or name</label>
<div class="hero-field">{icon("search")}<input id="hero-q" type="search" name="q" placeholder="e.g. 8086:2725…" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search"><button class="btn btn-primary" type="submit">Search</button></div>
<p class="hero-hint">Your device IDs are in System Information, under PCI or USB, or in <code>lspci -nn</code> on Linux.</p>
{try_html}
</form>
</div>
<div class="hero-stats">
<h2 class="sr-only">The catalog in numbers</h2>
{tiles}
</div>
</section>
<section class="panel pipeline-wrap" aria-labelledby="how">
<div class="sec-head"><h2 id="how">How a driver gets its status</h2><a class="sec-link" href="docs/governance/">Read the rules</a></div>
<ol class="pipeline">{pipe}</ol>
<p class="small pipe-foot">Unlisted licenses, abandoned projects and removed reports go to a maintainer. Everything else follows the rules above.</p>
</section>
<section aria-labelledby="recent">
<div class="sec-head"><h2 id="recent">Recently released</h2>{all_link}</div>
{cards}
</section>
<section class="paths" aria-labelledby="help">
<div class="sec-head"><h2 id="help">Take part</h2></div>
<ul class="paths-grid">
<li class="path path-main"><span class="path-ico">{icon("flask")}</span><h3>Test a driver</h3><p>Ran a driver from the catalog? A short file with your result, macOS build and hardware is what moves a project to community-verified.</p>{button("Report a test", "docs/report/")}</li>
<li class="path"><span class="path-ico">{icon("upload")}</span><h3>Submit a driver</h3><p>Maintain an open-source kext, patch or porting tool? Add one file that describes it and its releases. The bot answers in a couple of minutes.</p>{button("Submit a project", "docs/submit/", "secondary")}</li>
<li class="path"><span class="path-ico">{icon("code")}</span><h3>Write a driver</h3><p>Guides on building kexts, reading IOKit classes, porting drivers to new chips and new macOS versions, and the legal lines to stay inside.</p>{button("Read the guides", "docs/driver-development/", "secondary")}</li>
</ul>
</section>
</div>
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
            words = [text(k.get("id")), text(k.get("name")), text(k.get("summary"))]
            for h in items(k.get("hardware")):
                h = obj(h)
                words += [text(h.get("vendor")), text(h.get("description"))]
            index.append({
                "id": text(k.get("id")),
                "c": [text(c) for c in items(k.get("categories"))],
                "s": text(obj(k.get("status")).get("status")),
                "min": text(mac.get("min")), "max": text(mac.get("max")),
                "d": self.latest_date(k),
                "q": " ".join(words + self.hw_ids(k)).lower(),
            })
        n = len(self.kexts)
        cats = sorted({c for e in index for c in e["c"]}, key=lambda c: CATEGORIES.get(c, c))
        opt = lambda v, label: f'<option value="{esc(v)}">{esc(label)}</option>'  # noqa: E731
        cat_opts = "".join(opt(c, CATEGORIES.get(c, c)) for c in cats)
        mac_opts = "".join(opt(m, f"macOS {m} {MACOS_NAMES.get(m, '')}".strip())
                           for m in sorted(majors, key=version_key, reverse=True))
        seg = [("", "All", "", n)] + [
            (s, STATUSES[s][0], STATUSES[s][1], sum(e["s"] == s for e in index))
            for s in ("community-verified", "author-tested", "submitted", "reported-broken")]
        segs = "".join(
            f'<label class="seg"><input type="radio" name="status" value="{esc(v)}"{"" if v else " checked"}>'
            f'<span class="seg-label">{dot(c)}{esc(label)} <span class="seg-n">{cnt}</span></span></label>'
            for v, label, c, cnt in seg if cnt or not v)
        data = json.dumps(index, separators=(",", ":"), ensure_ascii=True)
        data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        listing = "".join(self.card(k, base) for k in self.kexts)
        if n:
            no_match = empty_state(
                "No projects match",
                '<span id="empty-why">Nothing matches these filters.</span> '
                "Device IDs look like <code>8086:2725</code>, vendor then device.",
                '<button type="button" class="btn btn-secondary" id="empty-clear">Clear filters</button> '
                '<a class="btn btn-ghost" href="../docs/submit/">Submit a project</a>')
            empty = f'<div id="empty" hidden>{no_match}</div>'
            finder = f"""<form id="filters" class="finder" role="search" hidden>
<div class="finder-search"><label class="sr-only" for="cat-q">Search by name or device ID</label>{icon("search")}<input id="cat-q" type="search" name="q" placeholder="Name or ID, e.g. 8086:2725…" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search"></div>
<fieldset class="segmented"><legend class="sr-only">Status</legend>{segs}</fieldset>
<div class="finder-row">
<label class="field">Category <select name="category"><option value="">All categories</option>{cat_opts}</select></label>
<label class="field">macOS <select name="macos"><option value="">Any version</option>{mac_opts}</select></label>
<label class="field">Sort <select name="sort"><option value="">Name</option><option value="recent">Recently released</option></select></label>
</div>
</form>
<noscript><p class="small">Search and filters need JavaScript. Every project is listed below; your browser's find (Cmd+F or Ctrl+F) works for device IDs.</p></noscript>
<div class="results-bar"><p id="count" class="count" aria-live="polite">{n} project{'s' if n != 1 else ''}</p><button type="button" class="btn btn-ghost btn-sm" id="clear" hidden>Clear filters</button></div>
<ul id="kexts" class="cards list">{listing}</ul>
{empty}
<script type="application/json" id="catalog-data">{data}</script>"""
        else:
            finder = empty_state("No projects yet", "The catalog is waiting for its first entry.",
                                 button("Submit a project", "../docs/submit/"))
        body = f"""
<div class="catalog">
<header class="cat-head">
<div><h1>Catalog</h1>
<p class="lead">Open-source drivers, patches and porting tools for macOS on non-Apple hardware. Status comes from test reports, <a href="../docs/governance/">see how</a>.</p></div>
{button("Submit a project", "../docs/submit/", "secondary")}
</header>
{finder}
</div>
"""
        self.page("catalog/index.html", "Catalog", body, active="catalog", script=CATALOG_JS if n else None,
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
        import llm
        self.page(rel_path, name, body, active="catalog", desc=text(k.get("summary"))[:280],
                  jsonld=llm.kext_jsonld(k, llm.site_url()))

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
        import llm
        llm.build_llm(self)
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
  var form = document.getElementById("filters");
  var ul = document.getElementById("kexts");
  if (!form || !ul) return;
  var data = JSON.parse(document.getElementById("catalog-data").textContent);
  var list = Array.prototype.slice.call(ul.children);
  var count = document.getElementById("count");
  var empty = document.getElementById("empty");
  var why = document.getElementById("empty-why");
  var clear = document.getElementById("clear");
  var el = form.elements, total = list.length, sorted = "";
  var F = ["q", "status", "category", "macos", "sort"];
  var byId = {};
  data.forEach(function (k) { byId[k.id] = k; });
  function key(v) {
    var p = String(v || "").match(/\d+/g);
    if (!p) return 0;
    var a = parseInt(p[0], 10);
    return a === 10 ? 1000 + (p[1] ? parseInt(p[1], 10) : 0) : a * 100;
  }
  function val(f) { return el[f].value; }
  function set(f, v) {
    var e = el[f];
    if (e.options || e.type) e.value = v;
    else Array.prototype.forEach.call(e, function (r) { r.checked = r.value === v; });
  }
  function date(li) { var k = byId[li.getAttribute("data-id")]; return k ? k.d : ""; }
  function apply() {
    var words = val("q").toLowerCase().split(/\s+/).filter(Boolean)
      .map(function (w) { return w.replace(/^0x/, ""); });
    var c = val("category"), s = val("status"), m = key(val("macos")), n = 0;
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
    if (val("sort") !== sorted) {
      sorted = val("sort");
      var order = list.slice();
      if (sorted === "recent") order.sort(function (a, b) { var x = date(a), y = date(b); return x > y ? -1 : x < y ? 1 : 0; });
      order.forEach(function (li) { ul.appendChild(li); });
    }
    count.textContent = n === total ? total + (total === 1 ? " project" : " projects")
      : "Showing " + n + " of " + total + (total === 1 ? " project" : " projects");
    clear.hidden = !F.some(function (f) { return f !== "sort" && val(f); });
    empty.hidden = n !== 0;
    why.textContent = val("q").trim() ? "Nothing matches “" + val("q").trim() + "” with these filters."
      : "Nothing matches these filters.";
    var p = new URLSearchParams();
    F.forEach(function (f) { if (val(f)) p.set(f, val(f)); });
    var qs = p.toString();
    history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  }
  function reset() {
    ["q", "status", "category", "macos"].forEach(function (f) { set(f, ""); });
    apply();
    el.q.focus();
  }
  var init = new URLSearchParams(location.search);
  F.forEach(function (f) {
    var v = init.get(f);
    if (v === null) return;
    var opts = el[f].options || el[f];
    if (f !== "q" && !Array.prototype.some.call(opts, function (o) { return o.value === v; })) return;
    set(f, v);
  });
  form.hidden = false;
  form.addEventListener("input", apply);
  form.addEventListener("change", apply);
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  clear.addEventListener("click", reset);
  document.getElementById("empty-clear").addEventListener("click", reset);
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
