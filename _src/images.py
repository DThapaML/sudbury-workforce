#!/usr/bin/env python3
"""Sudbury Workforce asset pipeline.

    uv run --with pillow python3 _src/images.py [--force]

Reads the masters in images/ and images/work/ (never modifies them) and writes
web-ready copies to images/opt/ -- commit those -- plus _src/image-manifest.json,
which build.py reads for every path, width and height.

  Photos   images/*.jpg, images/work/*.jpg -> centre-cropped to 4:3 (the shape
           every photo slot on the site shows), WebP + JPEG at 240/480/800 px
           wide (never wider than the master), and a 1200x630 Open Graph card.
  Logo     images/logo.png -> WebP + PNG at 1x/2x/3x of the 48px header height.
  Favicon  images/favicon.png -> favicon-32.png and apple-touch-icon.png.
  Video    images/work/*.mp4 -> H.264 without audio, cropped to 4:3, plus a
           JPEG poster frame (needs ffmpeg/ffprobe on PATH).

The manifest records each master's sha1. Outputs are reused only while their
master still has that sha1 (and they are newer than this script); --force
redoes everything. Files in images/opt/ that no master produces any more are
deleted.
"""

import functools
import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageOps

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
IMAGES = ROOT / 'images'
OUT = IMAGES / 'opt'
MANIFEST = SRC / 'image-manifest.json'
SCRIPT = Path(__file__).resolve()

PHOTO_WIDTHS = (240, 480, 800)
WEBP_QUALITY = 74
JPEG_QUALITY = 80
OG_SIZE = (1200, 630)
OG_PANEL = 520                    # white logo panel on the left of each OG card
OG_FOCUS = {                      # crop centre (x, y in 0..1) when the subject is off-centre
    'work/lawn-turf': (0.78, 0.5),
}
LOGO_HEIGHTS = (48, 96, 144)      # header shows the logo 40-48px tall
NAVY = (21, 61, 119)              # --navy in styles.css
VIDEO_WIDTH = 640                 # the clip plays in a card at most ~460px wide
VIDEO_CRF = 32
POSTER_WIDTH = 480                # shown only until the clip starts
POSTER_QUALITY = 70

FORCE = '--force' in sys.argv[1:]
produced = set()
written = []


def rel(path):
    return path.relative_to(ROOT).as_posix()


def stored_hashes():
    """Master sha1s recorded by the last run (empty without a usable manifest)."""
    try:
        return json.loads(MANIFEST.read_text(encoding='utf-8')).get('masters', {})
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


STORED = stored_hashes()


@functools.lru_cache(maxsize=None)
def sha1(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


def fresh(outputs, masters):
    """True when every master is byte-for-byte what the last run processed and
    every output exists and is newer than this script. Master mtimes can't be
    trusted: a photo replaced in place can keep, or even predate, the old time."""
    if FORCE:
        return False
    if any(STORED.get(rel(m)) != sha1(m) for m in masters):
        return False
    script_time = SCRIPT.stat().st_mtime
    return all(o.exists() and o.stat().st_mtime >= script_time for o in outputs)


def claim(path):
    produced.add(rel(path))
    return path


def save(img, path, fmt, **opts):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, fmt, **opts)
    written.append(path)


def load_rgb(path):
    with Image.open(path) as im:
        icc = im.info.get('icc_profile')
        im = ImageOps.exif_transpose(im)
        rgb = im.convert('RGB')
    if icc:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if 'srgb' not in ImageCms.getProfileDescription(profile).lower():
            rgb = ImageCms.profileToProfile(rgb, profile, ImageCms.createProfile('sRGB'))
    return rgb


def load_rgba(path):
    with Image.open(path) as im:
        return im.convert('RGBA')


def crop_4x3(img):
    w, h = img.size
    if w * 3 > h * 4:
        cw, ch = round(h * 4 / 3), h
    else:
        cw, ch = w, round(w * 3 / 4)
    left, top = (w - cw) // 2, (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch))


def widths_for(crop_width):
    widths = [w for w in PHOTO_WIDTHS if w <= crop_width]
    if crop_width < PHOTO_WIDTHS[-1] and crop_width not in widths:
        widths.append(crop_width)
    return widths


def trimmed_logo(logo):
    return logo.crop(logo.getchannel('A').getbbox())


def make_og(key, master, logo_master):
    """1200x630 card: logo on a white panel at left, the photo filling the rest."""
    out = claim(OUT / 'og' / f'{key}.jpg')
    if not fresh([out], [master, logo_master]):
        width, height = OG_SIZE
        card = Image.new('RGB', OG_SIZE, 'white')
        panel = ImageOps.fit(load_rgb(master), (width - OG_PANEL, height), Image.LANCZOS,
                             centering=OG_FOCUS.get(key, (0.5, 0.5)))
        card.paste(panel, (OG_PANEL, 0))
        logo = trimmed_logo(load_rgba(logo_master))
        lw = OG_PANEL - 100
        lh = round(logo.height * lw / logo.width)
        logo = logo.resize((lw, lh), Image.LANCZOS)
        card.paste(logo, ((OG_PANEL - lw) // 2, (height - lh) // 2 - 12), logo)
        ImageDraw.Draw(card).rectangle((0, height - 16, OG_PANEL - 1, height - 1), fill=NAVY)
        save(card, out, 'JPEG', quality=82, optimize=True, progressive=True)
    return {'src': rel(out), 'width': OG_SIZE[0], 'height': OG_SIZE[1]}


def make_photo(master, logo_master):
    key = master.relative_to(IMAGES).with_suffix('').as_posix()
    with Image.open(master) as im:
        mw, mh = ImageOps.exif_transpose(im).size
    cw, ch = crop_4x3(Image.new('1', (mw, mh))).size
    variants = []
    for w in widths_for(cw):
        h = round(w * ch / cw)
        variants.append({
            'width': w,
            'height': h,
            'webp': rel(claim(OUT / f'{key}-{w}.webp')),
            'fallback': rel(claim(OUT / f'{key}-{w}.jpg')),
        })
    outputs = [ROOT / v[k] for v in variants for k in ('webp', 'fallback')]
    if not fresh(outputs, [master]):
        crop = crop_4x3(load_rgb(master))
        for v in variants:
            img = crop.resize((v['width'], v['height']), Image.LANCZOS)
            save(img, ROOT / v['webp'], 'WEBP', quality=WEBP_QUALITY, method=6)
            save(img, ROOT / v['fallback'], 'JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)
    entry = {
        'master': rel(master),
        'width': variants[-1]['width'],
        'height': variants[-1]['height'],
        'variants': variants,
        'og': make_og(key, master, logo_master),
    }
    return key, entry


def make_logo(master):
    logo = load_rgba(master)
    variants = []
    for h in LOGO_HEIGHTS:
        w = round(logo.width * h / logo.height)
        variants.append({
            'width': w,
            'height': h,
            'webp': rel(claim(OUT / f'logo-{w}.webp')),
            'fallback': rel(claim(OUT / f'logo-{w}.png')),
        })
    outputs = [ROOT / v[k] for v in variants for k in ('webp', 'fallback')]
    if not fresh(outputs, [master]):
        for v in variants:
            img = logo.resize((v['width'], v['height']), Image.LANCZOS)
            # A flat two-colour logo: a small palette keeps both files tiny.
            png = img.quantize(colors=96, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
            save(png, ROOT / v['fallback'], 'PNG', optimize=True)
            save(png.convert('RGBA'), ROOT / v['webp'], 'WEBP', lossless=True, quality=100, method=6)
    return {'master': rel(master), 'variants': variants}


def make_icons(master):
    fav = claim(OUT / 'favicon-32.png')
    apple = claim(OUT / 'apple-touch-icon.png')
    if not fresh([fav, apple], [master]):
        img = load_rgba(master)
        # The master has a stray mark on its right edge; keep the house-and-hammer only.
        img = img.crop((0, 0, min(img.width, 320), img.height))
        mark = img.crop(img.getchannel('A').getbbox())
        side = max(mark.size)
        square = Image.new('RGBA', (side, side), (0, 0, 0, 0))
        square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
        save(square.resize((32, 32), Image.LANCZOS), fav, 'PNG', optimize=True)
        touch = Image.new('RGB', (180, 180), 'white')
        inner = square.resize((148, 148), Image.LANCZOS)
        touch.paste(inner, (16, 16), inner)
        save(touch, apple, 'PNG', optimize=True)
    return {
        'favicon': {'src': rel(fav), 'width': 32, 'height': 32},
        'apple_touch_icon': {'src': rel(apple), 'width': 180, 'height': 180},
    }


def probe_size(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
         '-of', 'csv=p=0:s=x', str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split('x')
    return int(w), int(h)


def make_video(master):
    if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
        sys.exit(f'images.py: ffmpeg and ffprobe are needed for {rel(master)}')
    key = master.relative_to(IMAGES).with_suffix('').as_posix()
    w, h = probe_size(master)
    if w * 3 > h * 4:
        cw, ch = round(h * 4 / 3), h
    else:
        cw, ch = w, round(w * 3 / 4)
    ow = min(cw, VIDEO_WIDTH)
    oh = round(ow * ch / cw)
    ow, oh = ow - ow % 2, oh - oh % 2           # H.264 wants even dimensions
    frame_filter = f'crop={cw}:{ch},scale={ow}:{oh}:flags=lanczos'
    pw = min(POSTER_WIDTH, ow)
    ph = round(pw * oh / ow)
    out = claim(OUT / f'{key}.mp4')
    poster = claim(OUT / f'{key}-poster.jpg')
    if not fresh([out, poster], [master]):
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['ffmpeg', '-v', 'error', '-y', '-i', str(master), '-an', '-vf', f'{frame_filter},format=yuv420p',
             '-c:v', 'libx264', '-preset', 'veryslow', '-crf', str(VIDEO_CRF), '-profile:v', 'high',
             '-movflags', '+faststart', '-map_metadata', '-1', str(out)],
            check=True)
        written.append(out)
        frame = subprocess.run(
            ['ffmpeg', '-v', 'error', '-i', str(master), '-frames:v', '1', '-vf', f'crop={cw}:{ch}',
             '-f', 'image2pipe', '-vcodec', 'png', '-'],
            check=True, capture_output=True).stdout
        with Image.open(io.BytesIO(frame)) as im:
            still = im.convert('RGB').resize((pw, ph), Image.LANCZOS)
            save(still, poster, 'JPEG', quality=POSTER_QUALITY, optimize=True, progressive=True)
    return key, {
        'master': rel(master),
        'src': rel(out),
        'width': ow,
        'height': oh,
        'poster': {'src': rel(poster), 'width': pw, 'height': ph},
    }


def remove_orphans():
    removed = []
    for path in sorted(OUT.rglob('*'), reverse=True):
        if path.is_file() and rel(path) not in produced and not path.name.startswith('.'):
            path.unlink()
            removed.append(rel(path))
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


def main():
    logo_master = IMAGES / 'logo.png'
    favicon_master = IMAGES / 'favicon.png'
    for required in (logo_master, favicon_master):
        if not required.exists():
            sys.exit(f'images.py: missing master {rel(required)}')

    photo_masters = sorted(
        p for folder in (IMAGES, IMAGES / 'work') for p in folder.iterdir()
        if p.suffix.lower() in ('.jpg', '.jpeg', '.png') and p not in (logo_master, favicon_master))
    video_masters = sorted((IMAGES / 'work').glob('*.mp4'))

    manifest = {
        'generated_by': '_src/images.py -- do not edit; rerun the script',
        'logo': make_logo(logo_master),
        'icons': make_icons(favicon_master),
        'images': dict(make_photo(p, logo_master) for p in photo_masters),
        'videos': dict(make_video(v) for v in video_masters),
        # check.py compares these with the masters on disk to catch a stale images/opt/.
        'masters': {rel(m): sha1(m) for m in sorted([logo_master, favicon_master, *photo_masters, *video_masters])},
    }
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'
    if not MANIFEST.exists() or MANIFEST.read_text(encoding='utf-8') != text:
        MANIFEST.write_text(text, encoding='utf-8')
        written.append(MANIFEST)

    for path in written:
        print(f'wrote {rel(path):52} {path.stat().st_size / 1024:7.1f} KB')
    for path in remove_orphans():
        print(f'removed {path} (no master produces it)')
    total = sum((ROOT / p).stat().st_size for p in produced)
    print(f'{len(produced)} files in images/opt ({total / 1024:.0f} KB); {len(written)} written this run')


if __name__ == '__main__':
    main()
