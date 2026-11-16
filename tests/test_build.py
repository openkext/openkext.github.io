# SPDX-License-Identifier: MIT
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "catalog.json")
BUILT = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc)


def md(s, **kw):
    return build.md_to_html(s, **kw)


class MarkdownTest(unittest.TestCase):
    def test_headings_get_ids(self):
        self.assertEqual(md("# Hello `world`"), '<h1 id="hello-world">Hello <code>world</code></h1>')
        self.assertIn('<h2 id="a-b">', md("## A b ##"))
        out = md("## Same\n\n## Same")
        self.assertIn('id="same"', out)
        self.assertIn('id="same-1"', out)

    def test_heading_shift(self):
        self.assertTrue(md("# T", shift=1).startswith("<h2"))

    def test_paragraphs_and_inline(self):
        out = md("One **bold** and *it* and _it2_ and `co*de`.\nSame paragraph.\n\nSecond.")
        self.assertEqual(out, "<p>One <strong>bold</strong> and <em>it</em> and <em>it2</em> and "
                              "<code>co*de</code>.\nSame paragraph.</p>\n<p>Second.</p>")

    def test_snake_case_is_not_emphasis(self):
        self.assertEqual(md("ic_awdl_listen and a_b_c"), "<p>ic_awdl_listen and a_b_c</p>")

    def test_lists(self):
        self.assertEqual(md("- a\n- b"), "<ul><li>a</li><li>b</li></ul>")
        self.assertEqual(md("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>")
        self.assertEqual(md("3. a\n4. b"), '<ol start="3"><li>a</li><li>b</li></ol>')
        nested = md("- a\n  - b\n  - c\n- d")
        self.assertEqual(nested, "<ul><li>a\n<ul><li>b</li><li>c</li></ul></li><li>d</li></ul>")

    def test_fenced_code_inside_list(self):
        out = md("1. Run:\n   ```sh\n   echo <hi>\n   ```\n2. Done")
        self.assertIn('<pre><code class="language-sh">echo &lt;hi&gt;</code></pre>', out)
        self.assertIn("<li>Done</li>", out)

    def test_fenced_code_is_escaped_and_not_parsed(self):
        out = md("```\n# not a heading\n<script>x</script>\n**no**\n```")
        self.assertEqual(out, "<pre><code># not a heading\n&lt;script&gt;x&lt;/script&gt;\n**no**</code></pre>")

    def test_table(self):
        out = md("| A | B |\n|---|--:|\n| `x|y` | **b** |\n| c \\| d | e |")
        self.assertIn("<th>A</th>", out)
        self.assertIn('<th class="a-right">B</th>', out)
        self.assertIn("<td><code>x|y</code></td>", out)
        self.assertIn("<td>c | d</td>", out)
        self.assertIn("<strong>b</strong>", out)
        self.assertTrue(out.startswith('<div class="table-wrap"><table>'))

    def test_links(self):
        self.assertEqual(md("[a](https://x.org/p?q=1&r=2)"), '<p><a href="https://x.org/p?q=1&amp;r=2">a</a></p>')
        self.assertEqual(md("[a](other.md#frag)"), '<p><a href="other.md#frag">a</a></p>')
        self.assertEqual(md("<https://x.org>"), '<p><a href="https://x.org">https://x.org</a></p>')

    def test_link_resolver(self):
        out = md("[g](GOVERNANCE.md#licenses)", resolve=lambda u: "../gov/#licenses" if u.startswith("GOV") else u)
        self.assertEqual(out, '<p><a href="../gov/#licenses">g</a></p>')

    def test_dangerous_links_become_text(self):
        for url in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "vbscript:x"):
            out = md(f"[click]({url})")
            self.assertNotIn("<a", out, url)
            self.assertIn("click", out)

    def test_raw_html_is_escaped(self):
        out = md('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n# <b>h</b>\n\n- <i>x</i>')
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<b>", out)
        self.assertNotIn("<i>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_link_text_and_attribute_escaping(self):
        out = md('[<b>x</b>](https://a.org/"onmouseover=alert(1))')
        self.assertNotIn("<b>", out)
        self.assertNotIn('"onmouseover', out)

    def test_blockquote_and_hr(self):
        self.assertEqual(md("> quoted\n> more"), "<blockquote><p>quoted\nmore</p></blockquote>")
        self.assertEqual(md("---"), "<hr>")


class SafeUrlTest(unittest.TestCase):
    def test_only_plain_https(self):
        ok = ["https://github.com/a/b", "https://example.org/x?y=1#z"]
        bad = ["http://example.org", "javascript:alert(1)", " javascript:alert(1)", "HTTPS://x.org",
               "https://", "https://a.org/\"x", "https://a.org/<x>", "https://user@evil.org/",
               "https://a.org/\nx", "data:text/html,x", None, 5, ["https://a.org"]]
        for u in ok:
            self.assertEqual(build.safe_https(u), u)
        for u in bad:
            self.assertIsNone(build.safe_https(u), repr(u))

    def test_link_or_text(self):
        self.assertEqual(build.link_or_text("javascript:alert(1)"), "javascript:alert(1)")
        self.assertEqual(build.link_or_text("<x>"), "&lt;x&gt;")
        self.assertEqual(build.link_or_text("https://a.org"), '<a href="https://a.org">https://a.org</a>')

    def test_gh_repo(self):
        self.assertEqual(build.gh_repo("https://github.com/a/b.git"), "https://github.com/a/b")
        self.assertIsNone(build.gh_repo("https://gitlab.com/a/b"))
        self.assertIsNone(build.gh_repo("javascript:alert(1)//github.com/a/b"))

    def test_version_key(self):
        self.assertLess(build.version_key("10.15.7"), build.version_key("11"))
        self.assertLess(build.version_key("15.8"), build.version_key("26"))
        self.assertEqual(build.version_key("15.8"), build.version_key("15"))
        self.assertEqual(build.version_key("garbage"), 0)


class SiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = os.path.join(cls.tmp, "_site")
        reg = os.path.join(cls.tmp, "registry")
        os.makedirs(os.path.join(reg, "docs"))
        with open(os.path.join(reg, "GOVERNANCE.md"), "w") as f:
            f.write("# Governance\n\nSee [the format](docs/kext-format.md) and [rules](openkext_registry/rules.py).\n\n"
                    "## Licenses\n\nMIT.\n")
        with open(os.path.join(reg, "docs", "kext-format.md"), "w") as f:
            f.write("# kext.toml\n\nBack to [governance](../GOVERNANCE.md#licenses).\n")
        # docs/report-format.md is missing on purpose: the page must still build.
        cls.site = build.build_site(FIXTURE, cls.out, reg, BUILT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def read(self, rel):
        with open(os.path.join(self.out, *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def test_expected_files(self):
        for rel in ["index.html", "404.html", "catalog.json", ".nojekyll", "assets/style.css", "assets/favicon.svg",
                    "catalog/index.html", "kext/good-wifi/index.html", "kext/evil/index.html", "docs/index.html",
                    "docs/submit/index.html", "docs/report/index.html", "docs/governance/index.html",
                    "docs/driver-development/index.html", "docs/porting-hardware/index.html",
                    "docs/porting-macos/index.html"]:
            self.assertTrue(os.path.isfile(os.path.join(self.out, *rel.split("/"))), rel)

    def test_invalid_id_skipped(self):
        self.assertEqual(sorted(k["id"] for k in self.site.kexts), ["evil", "good-wifi"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape")))
        for dirpath, _, files in os.walk(self.out):
            self.assertTrue(os.path.realpath(dirpath).startswith(os.path.realpath(self.out)))

    def test_catalog_json_is_a_copy(self):
        with open(FIXTURE, "rb") as a, open(os.path.join(self.out, "catalog.json"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_hostile_values_escaped(self):
        page = self.read("kext/evil/index.html")
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertNotIn("<svg onload", page)
        self.assertNotIn("<b>mallory", page)
        self.assertNotIn("<i>LICENSE", page)
        self.assertIn("&lt;script&gt;alert(&#x27;summary&#x27;)&lt;/script&gt;", page)
        self.assertNotIn("<!--", page)

    def test_only_https_links_from_catalog(self):
        page = self.read("kext/evil/index.html")
        hrefs = re.findall(r'href="([^"]*)"', page)
        for h in hrefs:
            self.assertFalse(h.lower().startswith(("javascript:", "data:", "http:")), h)
        self.assertIn("https://ok.example/upstream", hrefs)
        self.assertNotIn("https://user@evil.example/", hrefs)
        self.assertFalse(any("bad.example" in h for h in hrefs))
        # javascript: source: no source, release or download links built on it
        self.assertFalse(any("alert" in h for h in hrefs))
        self.assertIn("Source: javascript:alert(&#x27;source&#x27;)", page)  # shown as plain text
        self.assertNotIn("../../../etc/passwd", " ".join(hrefs))

    def test_embedded_json_cannot_close_script(self):
        page = self.read("catalog/index.html")
        m = re.search(r'<script type="application/json" id="catalog-data">(.*?)</script>', page, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("<", m.group(1))
        data = json.loads(m.group(1))
        self.assertEqual({d["id"] for d in data}, {"evil", "good-wifi"})
        self.assertEqual(page.count("<script"), 2)

    def test_csp_hash_matches_script(self):
        import base64
        import hashlib
        page = self.read("catalog/index.html")
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", page)
        self.assertIn("script-src 'none'", self.read("index.html"))

    def test_catalog_lists_every_project_without_js(self):
        page = self.read("catalog/index.html")
        self.assertIn('data-id="good-wifi"', page)
        self.assertIn('data-id="evil"', page)
        self.assertIn('<form id="filters" class="filters" role="search" hidden>', page)

    def test_project_page_content(self):
        page = self.read("kext/good-wifi/index.html")
        self.assertIn("badge-green", page)
        self.assertIn("Community-verified", page)
        self.assertIn('href="https://github.com/example/good-wifi/releases/tag/v1.1.0"', page)
        self.assertIn('href="https://github.com/example/good-wifi/releases/download/v1.1.0/Good%201.1.0.zip"', page)
        self.assertIn("1111111111111111111111111111111111111111111111111111111111111111", page)
        self.assertIn('href="https://example.org/good-wifi"', page)
        self.assertIn("24G84", page)
        self.assertIn("sleep-wake", page)
        self.assertIn('href="https://github.com/bob"', page)
        # newest release first
        self.assertLess(page.index(">1.1.0<"), page.index(">1.0.0<"))

    def test_relative_links(self):
        self.assertIn('href="../../assets/style.css"', self.read("kext/good-wifi/index.html"))
        self.assertIn('href="./assets/style.css"', self.read("index.html"))
        self.assertIn('href="/assets/style.css"', self.read("404.html"))
        for rel in ("index.html", "catalog/index.html", "kext/good-wifi/index.html"):
            self.assertNotIn("openkext.github.io/", self.read(rel).replace("openkext/openkext.github.io", ""))

    def test_registry_docs_links_rewritten(self):
        gov = self.read("docs/governance/index.html")
        self.assertIn('href="../../docs/submit/#format"', gov)
        self.assertIn('href="https://github.com/openkext/registry/blob/main/openkext_registry/rules.py"', gov)
        sub = self.read("docs/submit/index.html")
        self.assertIn('<section id="format">', sub)
        self.assertIn('href="../../docs/governance/#licenses"', sub)
        rep = self.read("docs/report/index.html")
        self.assertIn("https://github.com/openkext/registry/blob/main/docs/report-format.md", rep)

    def test_guides_credit_source(self):
        for slug in ("driver-development", "porting-hardware", "porting-macos"):
            page = self.read(f"docs/{slug}/index.html")
            self.assertIn("github.com/kevineduardo/ax210-airdrop-awdl-hackintosh", page)
            self.assertIn("MIT license", page)
        self.assertIn('id="legal-boundaries"', self.read("docs/driver-development/index.html"))

    def test_refuses_to_delete_unrelated_dir(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "keep.txt"), "w") as f:
                f.write("x")
            with self.assertRaises(SystemExit):
                build.build_site(FIXTURE, d, None, BUILT)
            self.assertTrue(os.path.exists(os.path.join(d, "keep.txt")))
        finally:
            shutil.rmtree(d)


class ProseTest(unittest.TestCase):
    def test_no_em_or_en_dashes(self):
        paths = [os.path.join(ROOT, "content", n) for n in os.listdir(os.path.join(ROOT, "content"))]
        paths.append(os.path.join(ROOT, "build.py"))
        for p in paths:
            with open(p, encoding="utf-8") as f:
                self.assertNotRegex(f.read(), "[–—]", p)


if __name__ == "__main__":
    unittest.main()
