# Sudbury Workforce site: how to edit and rebuild it

The pages at the repo root (`index.html`, `<page>/index.html`, `404.html`,
`sitemap.xml`, `robots.txt`) are **generated**. Don't edit them by hand: edit
the files in `_src/`, rebuild, check, then commit everything. GitHub Pages
serves the committed files as they are; nothing is built on GitHub. Folders
that start with `_` are not published, so nothing in `_src/` goes live.

## What lives where

| File | What it holds |
| --- | --- |
| `_src/site.json` | Settings: site `url`, business name, phones, email, WhatsApp number, areas, menu, footer links, the four feature points, the Our Work photos, and for each service its slug, name, form option, title, description, H1 and photos |
| `_src/pages/*.html` | One file per page: a JSON header, then the page's HTML. The file name is the address: `lawn-care-sudbury.html` becomes `/lawn-care-sudbury/`, `home.html` is the home page and `404.html` is the "page not found" page |
| `_src/build.py` | Builds the pages from the two above (plain Python 3, nothing to install) |
| `_src/check.py` | Checks the built pages against the rules below |
| `_src/test_site.py` | End-to-end tests: build errors, domain switch, quote form, Call buttons, 404, video loading, phone layout (uses Google Chrome when installed) |
| `_src/images.py` | Makes the web-sized images in `images/opt/` from the originals in `images/` |
| `_src/image-manifest.json` | Written by `images.py` (paths and sizes for `build.py`). Don't edit |
| `styles.css`, `script.js` | Edited by hand at the repo root (not generated) |

## Editing

- **Service pages:** title, meta description and H1 live in `site.json` under
  `services`. **Other pages:** in the JSON header at the top of the page file.
- **Page header fields:** `title`, `description` and `h1` (required), `breadcrumb`
  (required on every page except home; it is also the page name in WhatsApp
  messages), plus optional `service` (preselects the quote form and adds the
  Service markup), `parent`, `area_served`, `hero_photo` and `og_image`.
- **Page text:** the HTML after the header. The build fills in these tags:
  - `{{h1}}` (exactly once per page), `{{breadcrumb}}`, `{{cta}}` (quote and call
    buttons), `{{hero_points}}`, `{{hero_photo}}`, `{{hero_grid}}`, `{{features}}`
  - `{{services_grid}}`, `{{service_links}}`, `{{service_photos}}`, `{{work_grid}}`,
    `{{area_chips}}` or `{{area_chips group="Valley East & Capreol"}}`, `{{area_groups}}`
  - `{{img work/snow-after alt="..."}}` for any other photo
  - values: `{{phone}}`, `{{tel}}`, `{{email}}`, `{{whatsapp}}`, `{{home}}`, and
    `{{root}}` in front of every internal link, e.g. `{{root}}areas-we-serve/`
- The header, footer, quote form and mobile Call/WhatsApp bar are added to every
  page by `build.py`.
- **New job photo:** put it in `images/work/`, add it to `work` in `site.json` and
  to the matching service's `photos`, then run `images.py` and `build.py`.

## Rules

`check.py` catches the common slips (lengths, a missing H1, banned words such
as prices, discounts, guarantees or "#1" claims), but it only knows the
patterns it lists. Read any new copy against these rules yourself.

- Titles at most 60 characters and descriptions at most 155, both leading with
  the service and Sudbury or the area. One H1 per page, naming the service and
  Sudbury or the area.
- Only claim what the site already shows: the listed services, free estimates,
  same-day service, local and reliable. Never prices, rates or "$" amounts,
  discounts, insurance, WSIB, certifications, reviews or ratings, years in
  business, services that aren't on the list, or places outside Greater Sudbury.
  The existing "Affordable Rates / Fair pricing. No hidden fees." line is fine.
- Internal links are relative (only `404.html` uses full URLs, because GitHub
  serves it at any address).
- Never add `.nojekyll` (it would publish `_src/`) or a `README.md` at the repo
  root (GitHub Pages would show it as a page).

## Build, check, preview

```sh
python3 _src/build.py          # writes 11 pages + sitemap.xml + robots.txt and lists them
python3 _src/check.py          # "0 problems" means it's ready to commit
python3 _src/test_site.py      # end-to-end tests; all "ok" means nothing broke
python3 -m http.server 8000    # then open http://localhost:8000/
```

Only when photos or the video change (needs `uv` and `ffmpeg`):

```sh
uv run --with pillow python3 _src/images.py    # then run build.py again
```

## Switching to a domain

1. In `_src/site.json`, set `"url": "https://sudburyworkforce.ca/"` (keep the
   trailing slash).
2. Run `python3 _src/build.py` and `python3 _src/check.py`, then commit.

Canonical links, Open Graph tags, JSON-LD, the sitemap, robots.txt and the 404
page's links all follow the new URL; nothing else changes. Pointing the domain
at GitHub Pages (DNS and the Pages custom-domain setting) is a separate step.
Crawlers only read `robots.txt` at the root of a domain, so it has no effect
while the site lives at `dthapaml.github.io/sudbury-workforce/`.

## Get indexed

1. Add the site to [Google Search Console](https://search.google.com/search-console)
   (a URL-prefix property for the site URL) and verify it.
2. Under Sitemaps, submit `sitemap.xml`.
3. Use URL Inspection to request indexing of the main pages: the home page,
   `snow-removal-sudbury/`, `snow-removal-hanmer-valley-east/` and
   `areas-we-serve/`.

After a domain switch, add the new domain as a new property, verify it,
resubmit its `sitemap.xml` and request indexing of the main pages again.

## Publishing

Work on a branch. Merging to `main` publishes the live site, so do that only
with the owner's OK.
