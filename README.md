# openkext.github.io

The website of OpenKext, a community catalog of open-source drivers (kexts), driver ports and driver-development guides for macOS on non-Apple hardware. Live at [openkext.org](https://openkext.org).

The catalog itself lives in [openkext/registry](https://github.com/openkext/registry). This repository only turns it into a static site. To list a project or report a test, open a pull request there, not here.

## How it's built

`build.py` is a static site generator in one file, Python 3.11+ standard library only:

- **Input:** the catalog JSON that `registry/tools/check_registry.py` writes, the Markdown guides in `content/`, and three files read from a registry checkout (`GOVERNANCE.md`, `docs/kext-format.md`, `docs/report-format.md`).
- **Output:** `_site/`, with a home page, the catalog, one page per project under `kext/<id>/`, the docs under `docs/`, a `404.html`, and a copy of `catalog.json` at the root for anyone who wants the data.

Catalog data is submitted by the community, so the generator treats it as untrusted: every value is HTML-escaped, only plain `https://` URLs become links, entries with an invalid `id` are skipped, and the JSON embedded in the catalog page is escaped so it can't close its `<script>` tag. Each page carries a Content-Security-Policy that allows no scripts except the catalog filter, by hash. The Markdown converter supports headings, paragraphs, lists, links, inline code, fenced code, tables, blockquotes, bold and italic, and escapes any raw HTML.

The catalog page lists every project in plain HTML. A small inline script adds search (name, PCI or USB ID), a status control, category and macOS filters and sorting by name or newest release, all kept in the URL; without JavaScript the full list is shown. The search box on the home page is a plain form that opens the catalog with `?q=`. There are no external scripts, fonts or trackers.

All links inside the site are relative, so it works on `openkext.org` and on `openkext.github.io` alike. The custom domain comes from the `CNAME` file; `openkext.com` redirects to `openkext.org` at Cloudflare.

## Output for LLMs and tools

`llm.py` writes, next to the HTML:

- `llms.txt` and `llms-full.txt` ([llmstxt.org](https://llmstxt.org) format);
- an `index.md` Markdown version of every page (linked from each page's `<head>` as `rel="alternate"`);
- a static JSON API: `api/index.json`, `api/kexts/<id>.json` and `api/hardware.json` (PCI/USB id to projects);
- the registry's JSON Schemas under `schema/`;
- schema.org JSON-LD on project pages, `sitemap.xml`, and a `robots.txt` that welcomes AI crawlers.

Absolute URLs use `https://<CNAME>` when a `CNAME` file exists, otherwise `https://openkext.github.io` (override with `OPENKEXT_SITE_URL`). Community text in Markdown is flattened to one line with Markdown syntax escaped, and project pages mark which fields are submitted data.

## Run it locally

With the registry checked out next to this repository:

```sh
python3 ../registry/tools/check_registry.py /tmp/c.json && python3 build.py /tmp/c.json && python3 -m http.server -d _site
```

Then open http://localhost:8000. `build.py --help` lists the options (`--out`, `--registry`).

Tests:

```sh
python3 -m unittest discover -s tests
```

## Deployment

`.github/workflows/pages.yml` checks out this repository and `openkext/registry`, builds the catalog and the site, and deploys to GitHub Pages. It runs on every push to `main`, every hour (so merged registry changes appear without a push here), on manual dispatch, and on a `registry-updated` repository dispatch. Pull requests run the tests and a build without deploying.

Pages must be set to **Settings → Pages → Source: GitHub Actions**.

### Custom domain

Add the domain in Settings → Pages → Custom domain, and add a `CNAME` file with the domain at the root of this repository; `build.py` copies it into `_site/`. Nothing else needs to change.

## Editing the guides

The guides are Markdown files in `content/`. Link to another guide by its page name (`[Submit a project](submit.md)`) and to other site pages with `site:` (`[catalog](site:catalog/)`). The governance and file-format text comes from the registry, so change it there.

The driver development and porting guides are adapted from the docs of the [AX210 AirDrop/AWDL project](https://github.com/kevineduardo/ax210-airdrop-awdl-hackintosh) (MIT).

## License

MIT, see [LICENSE](LICENSE). The catalog data is CC0-1.0, as stated in the registry.
