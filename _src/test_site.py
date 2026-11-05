#!/usr/bin/env python3
"""Tests for the cases in the build spec's I/O matrix.

    python3 _src/test_site.py

Standard library only. The browser cases drive Google Chrome headless and are
skipped when Chrome isn't installed (set CHROME=/path/to/chrome to point at
it). Every test that builds works on a throwaway copy of the site, so the real
files are never rewritten.
"""

import functools
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urldefrag, urljoin, urlsplit
from urllib.request import urlopen

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
SITE = json.loads((SRC / 'site.json').read_text(encoding='utf-8'))
LIVE_URL = SITE['url']


def tel(phone):
    """The tel: link build.py writes: 11 digits, adding the leading 1 only when it's missing."""
    digits = re.sub(r'\D', '', phone)
    return 'tel:+' + (digits if len(digits) == 11 else '1' + digits)


MAIN_TEL = tel(SITE['business']['phone'])
PAGES = ['index.html', '404.html'] + [f'{p.stem}/index.html' for p in sorted((SRC / 'pages').glob('*.html'))
                                      if p.stem not in ('home', '404')]
OUTPUTS = PAGES + ['sitemap.xml', 'robots.txt']
EXTERNAL = ('tel:', 'mailto:', 'https://wa.me/', 'https://fonts.googleapis.com', 'https://fonts.gstatic.com')


def find_chrome():
    candidates = [os.environ.get('CHROME'), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                  shutil.which('google-chrome'), shutil.which('chromium'), shutil.which('chromium-browser')]
    return next((c for c in candidates if c and Path(c).exists()), None)


CHROME = find_chrome()


# --------------------------------------------------------------------------- helpers

def copy_site(test):
    """A throwaway copy of the repo (without .git), removed when the test ends."""
    tmp = Path(tempfile.mkdtemp(prefix='swf-test-'))
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    site = tmp / 'site'
    shutil.copytree(ROOT, site, ignore=shutil.ignore_patterns('.git', '__pycache__', '.DS_Store'))
    return site


def run(site, script):
    return subprocess.run([sys.executable, str(site / '_src' / script)], cwd=site,
                          capture_output=True, text=True, timeout=300)


def build(test, site):
    result = run(site, 'build.py')
    test.assertEqual(result.returncode, 0, result.stderr)
    return result


def set_url(site, url):
    path = site / '_src' / 'site.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['url'] = url
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def edit(path, old, new):
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise AssertionError(f'{old!r} not found in {path}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


class Refs(HTMLParser):
    """Every URL a page points at, plus the hrefs of anchors by class."""

    def __init__(self):
        super().__init__()
        self.urls = []
        self.by_class = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ('href', 'src', 'poster', 'data-src'):
            if a.get(key):
                self.urls.append(a[key])
        for key in ('srcset',):
            if a.get(key):
                self.urls += [part.split()[0] for part in a[key].split(',') if part.strip()]
        if tag == 'a' and a.get('href'):
            for name in (a.get('class') or '').split():
                self.by_class.setdefault(name, []).append(a['href'])


def refs(text):
    parser = Refs()
    parser.feed(text)
    return parser


class PagesHandler(http.server.SimpleHTTPRequestHandler):
    """Serves a site under a path prefix and answers misses with 404.html, like GitHub Pages.

    Two extra paths serve the browser tests: /__hold__ answers only once a harness
    calls /__done__, so Chrome's page load (and --dump-dom) waits for the result.
    """

    prefix = '/'
    done = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/__hold__', '/__done__'):
            if path == '/__done__':
                self.done.set()
            elif self.done.wait(60):
                self.done.clear()
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def translate_path(self, path):
        clean = urlsplit(path).path
        if not clean.startswith(self.prefix):
            return str(Path(self.directory) / '__not_served__')
        return super().translate_path('/' + clean[len(self.prefix):])

    def send_error(self, code, message=None, explain=None):
        page = Path(self.directory) / '404.html'
        if code != 404 or not page.exists():
            return super().send_error(code, message, explain)
        body = page.read_bytes()
        self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser stopped reading, e.g. after a favicon probe


def serve(test, directory, prefix='/sudbury-workforce/'):
    """Start a local server for `directory` at `prefix`; returns its base URL."""
    handler = functools.partial(type('Handler', (PagesHandler,), {'prefix': prefix, 'done': threading.Event()}),
                                directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    test.addCleanup(httpd.server_close)
    test.addCleanup(httpd.shutdown)
    return f'http://127.0.0.1:{httpd.server_address[1]}{prefix}'


def fetch(url):
    try:
        with urlopen(url, timeout=30) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def chrome_result(test, url):
    """Load a harness page in headless Chrome and return the JSON it passes to report().

    Chrome runs in real time (a virtual-time budget stops IntersectionObserver
    from updating after a scroll); the harness's /__hold__ image keeps the page
    loading until report() is called, then --dump-dom prints the result.
    """
    profile = tempfile.mkdtemp(prefix='swf-chrome-')
    test.addCleanup(shutil.rmtree, profile, ignore_errors=True)
    out = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                          f'--user-data-dir={profile}', '--window-size=1200,900', '--dump-dom', url],
                         capture_output=True, text=True, timeout=180)
    match = re.search(r'<pre id="result">(.*?)</pre>', out.stdout, re.S)
    if not match or not match.group(1).strip():
        raise AssertionError(f'no result from Chrome for {url}\n{out.stderr[-2000:]}')
    return json.loads(html.unescape(match.group(1)))


def harness(site, name, script):
    """Write a test page next to the site copy. Its script loads a page into the
    390px-wide iframe and calls report(result) when done."""
    folder = site / '_test'
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body>'
        '<iframe id="page" style="width:390px;height:700px;border:0"></iframe>'
        '<pre id="result"></pre><img src="/__hold__" alt="" style="display:none">'
        '<script>function report(out) {'
        ' document.getElementById("result").textContent = JSON.stringify(out); fetch("/__done__"); }'
        f'\n{script}</script></body></html>', encoding='utf-8')
    return f'_test/{name}'


# --------------------------------------------------------------------------- matrix: Build

class BuildTests(unittest.TestCase):
    def test_build_writes_every_page_sitemap_and_robots(self):
        site = copy_site(self)
        result = build(self, site)
        self.assertIn(f'{len(OUTPUTS)} files written: {len(PAGES)} HTML pages, sitemap.xml, robots.txt',
                      result.stdout)
        for out in OUTPUTS:
            self.assertIn(out, result.stdout.split())
            self.assertTrue((site / out).is_file(), out)

    def test_missing_page_field_stops_the_build_naming_file_and_field(self):
        site = copy_site(self)
        edit(site / '_src/pages/lawn-care-sudbury.html', ',\n  "breadcrumb": "Lawn Care in Sudbury"', '')
        result = run(site, 'build.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('_src/pages/lawn-care-sudbury.html: missing field "breadcrumb"', result.stderr)

    def test_committed_output_matches_the_sources(self):
        site = copy_site(self)
        build(self, site)
        for out in OUTPUTS:
            self.assertEqual((site / out).read_text(encoding='utf-8'), (ROOT / out).read_text(encoding='utf-8'),
                             f'{out} is out of date: run python3 _src/build.py')


# --------------------------------------------------------------------------- matrix: Domain switch

class DomainSwitchTests(unittest.TestCase):
    def test_changing_url_only_changes_urls(self):
        new_url = 'https://sudburyworkforce.ca/'
        live = copy_site(self)
        build(self, live)
        moved = copy_site(self)
        set_url(moved, new_url)
        build(self, moved)
        for out in OUTPUTS:
            before = (live / out).read_text(encoding='utf-8')
            after = (moved / out).read_text(encoding='utf-8')
            self.assertNotIn(LIVE_URL, after, out)
            self.assertEqual(after, before.replace(LIVE_URL, new_url), out)
        check = run(moved, 'check.py')
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


# --------------------------------------------------------------------------- matrix: No WhatsApp

class CallTests(unittest.TestCase):
    def test_every_call_button_dials_the_main_number(self):
        for out in PAGES:
            found = refs((ROOT / out).read_text(encoding='utf-8')).by_class
            for css in ('btn-call', 'mobile-bar-call', 'btn-outline'):
                hrefs = found.get(css, [])
                if css != 'btn-outline':
                    self.assertTrue(hrefs, f'{out}: no .{css} link')
                for href in hrefs:
                    self.assertEqual(href, MAIN_TEL, f'{out}: .{css}')


class ServicesMenuTests(unittest.TestCase):
    def test_every_page_menu_lists_every_service_page(self):
        slugs = [s['slug'] for s in SITE['services']]
        for out in PAGES:
            text = (ROOT / out).read_text(encoding='utf-8')
            menu = re.search(r'<ul class="sub-menu" id="nav-services">(.*?)</ul>', text, re.S)
            self.assertTrue(menu, f'{out}: no Services menu')
            page_url = LIVE_URL + ('' if out == 'index.html' else out.replace('index.html', ''))
            resolved = [urljoin(page_url, h) for h in re.findall(r'href="([^"]+)"', menu.group(1))]
            self.assertEqual(resolved, [LIVE_URL + f'{slug}/' for slug in slugs] + [LIVE_URL + '#services'], out)


# --------------------------------------------------------------------------- matrix: Unknown URL

class NotFoundTests(unittest.TestCase):
    def test_unknown_url_gets_a_404_whose_links_all_work(self):
        site = copy_site(self)
        base = serve(self, site)
        set_url(site, base)
        build(self, site)
        status, body = fetch(base + 'nope/x')
        self.assertEqual(status, 404)
        self.assertIn('class="main-nav"', body)
        self.assertIn(f'href="{MAIN_TEL}"', body)
        self.assertIn('<h1>', body)
        checked = 0
        for ref in set(refs(body).urls):
            if ref.startswith(EXTERNAL):
                continue
            if ref.startswith('#'):
                self.assertIn(f'id="{ref[1:]}"', body, f'404 page link {ref} has no target on the page')
                continue
            url, _ = urldefrag(urljoin(base + 'nope/x', ref))
            self.assertTrue(url.startswith(base), f'404 link escapes the site: {ref}')
            status, _ = fetch(url)
            self.assertEqual(status, 200, f'404 page link {ref} -> {url}')
            checked += 1
        self.assertGreater(checked, 20)


# --------------------------------------------------------------------------- matrix: Checks

class CheckTests(unittest.TestCase):
    def test_check_passes_on_the_site(self):
        result = subprocess.run([sys.executable, str(SRC / 'check.py')], cwd=ROOT, capture_output=True, text=True,
                                timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('0 problems', result.stdout)

    def test_check_lists_each_violation_and_fails(self):
        site = copy_site(self)
        edit(site / '_src/pages/snow-removal-sudbury.html', '<h2>Snow Clearing for Sudbury Homes</h2>',
             '<h2>Snow Clearing for Sudbury Homes</h2>\n    <p>Get 10% off, only $40, fully insured.</p>')
        build(self, site)
        result = run(site, 'check.py')
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        for label in ('banned (% off)', 'banned (price ($ + digits))', 'banned (insurance claim)'):
            self.assertIn(f'snow-removal-sudbury/index.html: {label}', output)

    def test_check_names_a_master_photo_replaced_after_images_py_ran(self):
        site = copy_site(self)
        master = site / 'images/work/snow-before.jpg'
        old = master.stat()
        shutil.copyfile(site / 'images/work/snow-after.jpg', master)
        # Replaced in place with an older timestamp, as in f7be1a5: mtimes alone would miss it.
        os.utime(master, (old.st_atime - 86400, old.st_mtime - 86400))
        result = run(site, 'check.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('images/work/snow-before.jpg: changed since images.py ran -- run images.py', result.stdout)


# --------------------------------------------------------------------------- matrix: Service-page quote, Video

@unittest.skipUnless(CHROME, 'Google Chrome not found (set CHROME=/path/to/chrome)')
class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.site = copy_site(self)
        self.base = serve(self, self.site)

    def test_quote_on_the_snow_page_opens_whatsapp_with_service_and_page(self):
        page = harness(self.site, 'quote.html', r'''
var frame = document.getElementById('page');
frame.addEventListener('load', function () {
  var w = frame.contentWindow, d = w.document, opened = [];
  w.open = function (url) { opened.push(url); return null; };
  var form = d.getElementById('inquiry-form'), out = {preselected: form.elements.service.value};
  form.requestSubmit();
  out.errorShown = !d.getElementById('form-error').hidden;
  out.openedWhenIncomplete = opened.length;
  form.elements.name.value = 'Test Person';
  form.elements.phone.value = '705-555-0100';
  form.elements.message.value = 'Driveway after every snowfall';
  form.requestSubmit();
  out.opened = opened;
  out.serviceAfterReset = form.elements.service.value;
  report(out);
});
frame.src = '../snow-removal-sudbury/';
''')
        out = chrome_result(self, self.base + page)
        self.assertEqual(out['preselected'], 'Snow & Ice Clearing')
        self.assertTrue(out['errorShown'])
        self.assertEqual(out['openedWhenIncomplete'], 0)
        self.assertEqual(len(out['opened']), 1)
        url = urlsplit(out['opened'][0])
        self.assertEqual(f'{url.scheme}://{url.netloc}{url.path}', f'https://wa.me/{SITE["business"]["whatsapp"]}')
        text = parse_qs(url.query)['text'][0]
        self.assertIn('Service: Snow & Ice Clearing', text)
        self.assertIn('Page: Snow Removal in Sudbury', text)
        self.assertEqual(out['serviceAfterReset'], 'Snow & Ice Clearing')

    def layout(self, widths):
        """Load every page at each width (the harness iframe is resized before each
        load); returns {(width, path): what the page shows}."""
        set_url(self.site, self.base)  # the 404 page's absolute links then point at this server
        build(self, self.site)
        paths = ['' if out == 'index.html' else out.replace('index.html', '') for out in PAGES]
        page = harness(self.site, 'layout-' + '-'.join(map(str, widths)) + '.html', r'''
var pages = ''' + json.dumps(paths) + r''', widths = ''' + json.dumps(widths) + r''';
var i = 0, results = [], frame = document.getElementById('page');
function shown(w, el) {
  if (!el) return false;
  var style = w.getComputedStyle(el);
  return el.offsetWidth > 0 && el.offsetHeight > 0 && style.display !== 'none' && style.visibility !== 'hidden';
}
function next() {
  frame.style.width = widths[Math.floor(i / pages.length)] + 'px';
  frame.src = '../' + pages[i % pages.length];
}
frame.addEventListener('load', function () {
  var w = frame.contentWindow, d = w.document, h1s = d.querySelectorAll('h1'), nav = d.getElementById('main-nav');
  results.push({width: widths[Math.floor(i / pages.length)], path: pages[i % pages.length],
    callVisible: shown(w, d.querySelector('.btn-call')),
    barCall: shown(w, d.querySelector('.mobile-bar-call')), barWhatsApp: shown(w, d.querySelector('.mobile-bar-wa')),
    bar: shown(w, d.querySelector('.mobile-bar')), toggle: shown(w, d.querySelector('.nav-toggle')),
    inlineNav: shown(w, nav) && w.getComputedStyle(nav).position !== 'absolute',
    h1: h1s.length ? h1s[0].textContent.trim() : null, h1Count: h1s.length,
    scrollWidth: d.documentElement.scrollWidth, clientWidth: d.documentElement.clientWidth});
  i += 1;
  if (i < pages.length * widths.length) next(); else report(results);
});
next();
''')
        results = {(r['width'], r['path']): r for r in chrome_result(self, self.base + page)}
        self.assertEqual(sorted(results), sorted((w, p) for w in widths for p in paths))
        return results

    def test_every_page_at_phone_width_has_call_button_local_h1_and_no_sideways_scroll(self):
        places = ['Sudbury', 'Valley East'] + [p for g in SITE['areas'] for p in g['places']]
        for (width, path), r in self.layout([390]).items():
            name = path or 'home'
            self.assertTrue(r['callVisible'], f'{name}: header Call button not visible at 390px')
            self.assertTrue(r['barCall'] and r['barWhatsApp'], f'{name}: mobile Call/WhatsApp bar not visible at 390px')
            self.assertEqual(r['h1Count'], 1, name)
            self.assertTrue(any(p in r['h1'] for p in places), f'{name}: H1 "{r["h1"]}" names no area')
            self.assertLessEqual(r['scrollWidth'], r['clientWidth'], f'{name}: scrolls sideways at 390px')

    def test_every_page_at_tablet_and_desktop_widths_shows_the_right_menu(self):
        for (width, path), r in self.layout([800, 1000, 1100, 1200]).items():
            name = f'{path or "home"} at {width}px'
            self.assertLessEqual(r['scrollWidth'], r['clientWidth'], f'{name}: scrolls sideways')
            self.assertFalse(r['bar'], f'{name}: mobile Call/WhatsApp bar should be hidden')
            if width < 1100:
                self.assertTrue(r['toggle'], f'{name}: menu button should show')
            else:
                self.assertTrue(r['inlineNav'], f'{name}: the nav should show inline')
                self.assertFalse(r['toggle'], f'{name}: menu button should be hidden')

    def test_services_menu_opens_on_click_and_closes_on_escape_or_outside_click(self):
        page = harness(self.site, 'menu.html', r'''
var frame = document.getElementById('page'), out = {}, step = 0;
function state(w, d) {
  var sub = d.getElementById('nav-services');
  return {open: w.getComputedStyle(sub).display !== 'none' && sub.offsetHeight > 0,
          expanded: d.querySelector('.nav-dropdown-toggle').getAttribute('aria-expanded')};
}
frame.addEventListener('load', function () {
  var w = frame.contentWindow, d = w.document, button = d.querySelector('.nav-dropdown-toggle');
  if (step === 0) {  // desktop: a click opens it, Escape closes it
    out.links = d.querySelectorAll('#nav-services a').length;
    out.desktopClosed = state(w, d);
    button.click();
    out.desktopOpen = state(w, d);
    button.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    out.desktopEscape = state(w, d);
    step = 1;
    frame.style.width = '390px';
    frame.src = '../?phone';
  } else {  // phone: open the menu, tap Services, then tap elsewhere
    d.getElementById('nav-toggle').click();
    out.phoneClosed = state(w, d);
    button.click();
    out.phoneOpen = state(w, d);
    d.querySelector('main').click();
    out.phoneOutside = state(w, d);
    report(out);
  }
});
frame.style.width = '1200px';
frame.src = '../';
''')
        out = chrome_result(self, self.base + page)
        closed, opened = {'open': False, 'expanded': 'false'}, {'open': True, 'expanded': 'true'}
        self.assertEqual(out['links'], len(SITE['services']) + 1)
        self.assertEqual(out['desktopClosed'], closed)
        self.assertEqual(out['desktopOpen'], opened)
        self.assertEqual(out['desktopEscape'], closed)
        self.assertEqual(out['phoneClosed'], closed)
        self.assertEqual(out['phoneOpen'], opened)
        self.assertEqual(out['phoneOutside'], closed)

    def test_video_downloads_only_when_scrolled_near(self):
        page = harness(self.site, 'video.html', r'''
var frame = document.getElementById('page');
frame.addEventListener('load', function () {
  var w = frame.contentWindow, v = w.document.querySelector('video');
  function mp4() {
    return w.performance.getEntriesByType('resource').filter(function (e) { return /\.mp4(\?|$)/.test(e.name); }).length;
  }
  var out = {srcAtLoad: v.getAttribute('src'), dataSrc: v.getAttribute('data-src'), mp4AtLoad: mp4()};
  setTimeout(function () {
    out.srcWhileOffscreen = v.getAttribute('src');
    out.mp4WhileOffscreen = mp4();
    // "instant" overrides the site's smooth scrolling, so the jump happens now.
    v.scrollIntoView({behavior: 'instant', block: 'center'});
    setTimeout(function () {
      out.srcAfterScroll = v.getAttribute('src');
      out.muted = v.muted;
      out.loop = v.loop;
      out.paused = v.paused;
      out.controls = v.controls;
      report(out);
    }, 1500);
  }, 1500);
});
frame.src = '../';
''')
        out = chrome_result(self, self.base + page)
        self.assertIsNone(out['srcAtLoad'])
        self.assertTrue(out['dataSrc'].endswith('.mp4'))
        self.assertEqual(out['mp4AtLoad'], 0)
        self.assertIsNone(out['srcWhileOffscreen'])
        self.assertEqual(out['mp4WhileOffscreen'], 0)
        self.assertTrue(out['srcAfterScroll'] and out['srcAfterScroll'].endswith('.mp4'))
        self.assertTrue(out['muted'])
        self.assertTrue(out['loop'])
        self.assertFalse(out['paused'], 'the clip should be playing once scrolled near')
        self.assertTrue(out['controls'])

    def test_video_loads_but_stays_paused_under_reduced_motion(self):
        page = harness(self.site, 'video-reduced-motion.html', r'''
var frame = document.getElementById('page'), base = new URL('../', location.href).href;
fetch(base).then(function (r) { return r.text(); }).then(function (text) {
  var stub = 'var realMatchMedia = window.matchMedia.bind(window);'
    + 'window.matchMedia = function (query) {'
    + '  if (String(query).indexOf("prefers-reduced-motion: reduce") === -1) return realMatchMedia(query);'
    + '  return {matches: true, media: query, onchange: null, addListener: function () {},'
    + '    removeListener: function () {}, addEventListener: function () {}, removeEventListener: function () {},'
    + '    dispatchEvent: function () { return false; }};'
    + '};';
  frame.addEventListener('load', function () {
    var w = frame.contentWindow, v = w.document.querySelector('video');
    v.scrollIntoView({behavior: 'instant', block: 'center'});
    setTimeout(function () {
      report({reduced: w.matchMedia('(prefers-reduced-motion: reduce)').matches,
              src: v.getAttribute('src'), paused: v.paused, controls: v.controls});
    }, 1500);
  });
  frame.srcdoc = text.replace('<head>', '<head><base href="' + base + '"><script>' + stub + '<\/script>');
});
''')
        out = chrome_result(self, self.base + page)
        self.assertTrue(out['reduced'])
        self.assertTrue(out['src'] and out['src'].endswith('.mp4'), 'the clip should still load when scrolled near')
        self.assertTrue(out['paused'], 'the clip should not autoplay under reduced motion')
        self.assertTrue(out['controls'])

    def test_video_loads_on_page_load_without_intersection_observer(self):
        page = harness(self.site, 'video-fallback.html', r'''
var frame = document.getElementById('page'), base = new URL('../', location.href).href;
fetch(base).then(function (r) { return r.text(); }).then(function (text) {
  var inject = '<base href="' + base + '"><script>delete window.IntersectionObserver;<\/script>';
  frame.addEventListener('load', function () {
    setTimeout(function () {
      var w = frame.contentWindow, v = w.document.querySelector('video');
      report({noObserver: !('IntersectionObserver' in w), src: v.getAttribute('src')});
    }, 500);
  });
  frame.srcdoc = text.replace('<head>', '<head>' + inject);
});
''')
        out = chrome_result(self, self.base + page)
        self.assertTrue(out['noObserver'])
        self.assertTrue(out['src'] and out['src'].endswith('.mp4'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
