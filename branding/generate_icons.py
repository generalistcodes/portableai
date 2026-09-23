#!/usr/bin/env python3
"""Generate every PortableAI icon from branding/logo-source.svg.

Do not hand-edit ui/static/logo.*, packaging/linux/icons/, or
branding/generated/ — change logo-source.svg and re-run this script.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

WEB_PNG_SIZE = 512
LINUX_DIRICON_SIZE = 256
LINUX_HICOLOR_SIZES = (16, 32, 48, 64, 128, 256, 512)
MASTER_SIZE = 1024
RENDER_SCALE = 2  # supersample, then LANCZOS down to MASTER_SIZE

# iconutil's iconset names → pixel size
MACOS_ICONSET = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)

# PNG-in-ICNS types used by modern macOS (same sizes iconutil embeds).
ICNS_PNG_TYPES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
    (b"ic11", 32),
    (b"ic12", 64),
    (b"ic13", 256),
    (b"ic14", 512),
)

# Xcode AppIcon.appiconset: idiom / size / scale → pixel size.
IOS_APPICON = (
    ("AppIcon-20x20@2x.png", 40, "iphone", "20x20", "2x"),
    ("AppIcon-20x20@3x.png", 60, "iphone", "20x20", "3x"),
    ("AppIcon-29x29@2x.png", 58, "iphone", "29x29", "2x"),
    ("AppIcon-29x29@3x.png", 87, "iphone", "29x29", "3x"),
    ("AppIcon-40x40@2x.png", 80, "iphone", "40x40", "2x"),
    ("AppIcon-40x40@3x.png", 120, "iphone", "40x40", "3x"),
    ("AppIcon-60x60@2x.png", 120, "iphone", "60x60", "2x"),
    ("AppIcon-60x60@3x.png", 180, "iphone", "60x60", "3x"),
    ("AppIcon-20x20@1x-ipad.png", 20, "ipad", "20x20", "1x"),
    ("AppIcon-20x20@2x-ipad.png", 40, "ipad", "20x20", "2x"),
    ("AppIcon-29x29@1x-ipad.png", 29, "ipad", "29x29", "1x"),
    ("AppIcon-29x29@2x-ipad.png", 58, "ipad", "29x29", "2x"),
    ("AppIcon-40x40@1x-ipad.png", 40, "ipad", "40x40", "1x"),
    ("AppIcon-40x40@2x-ipad.png", 80, "ipad", "40x40", "2x"),
    ("AppIcon-76x76@1x-ipad.png", 76, "ipad", "76x76", "1x"),
    ("AppIcon-76x76@2x-ipad.png", 152, "ipad", "76x76", "2x"),
    ("AppIcon-83.5x83.5@2x-ipad.png", 167, "ipad", "83.5x83.5", "2x"),
    ("AppIcon-1024x1024.png", 1024, "ios-marketing", "1024x1024", "1x"),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _parse_color(value: str) -> tuple[int, int, int, int]:
    raw = value.strip().lower()
    if raw.startswith("#") and len(raw) == 7:
        return (int(raw[1:3], 16), int(raw[3:5], 16), int(raw[5:7], 16), 255)
    if raw.startswith("#") and len(raw) == 4:
        r, g, b = raw[1], raw[2], raw[3]
        return (int(r * 2, 16), int(g * 2, 16), int(b * 2, 16), 255)
    raise ValueError(f"unsupported color {value!r} in logo-source.svg")


def _parse_percent(value: str, default: float) -> float:
    if value is None:
        return default
    text = value.strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100.0
    return float(text)


def _parse_points(value: str) -> list[tuple[float, float]]:
    nums = [float(p) for p in re.split(r"[,\s]+", value.strip()) if p]
    if len(nums) % 2:
        raise ValueError("odd number of polygon coordinates")
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]


def _parse_transform(value: str) -> tuple[float, float, float]:
    """Return (translate_x, translate_y, scale) for translate() scale()."""
    if not value:
        return (0.0, 0.0, 1.0)
    tr = re.search(
        r"translate\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)", value
    )
    sc = re.search(r"scale\(\s*([-\d.]+)\s*\)", value)
    tx = float(tr.group(1)) if tr else 0.0
    ty = float(tr.group(2)) if tr else 0.0
    scale = float(sc.group(1)) if sc else 1.0
    return tx, ty, scale


def load_logo_geometry(svg_path: Path) -> dict:
    """Read the canonical badge SVG. Fails loudly if the mark is not the badge."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    viewbox = (root.get("viewBox") or "").split()
    if len(viewbox) != 4:
        raise ValueError(f"{svg_path} is missing viewBox")
    vb_w = float(viewbox[2])
    vb_h = float(viewbox[3])

    gradient = None
    circle = None
    group_fill = (255, 255, 255, 255)
    group_tx, group_ty, group_scale = 0.0, 0.0, 1.0
    polygons: list[list[tuple[float, float]]] = []

    for el in root.iter():
        name = _local_tag(el.tag)
        if name == "linearGradient":
            stops = []
            for stop in el:
                if _local_tag(stop.tag) != "stop":
                    continue
                stops.append(
                    (
                        _parse_percent(stop.get("offset"), 0.0),
                        _parse_color(stop.get("stop-color") or "#000000"),
                    )
                )
            if len(stops) < 2:
                raise ValueError(f"{svg_path} gradient needs at least two stops")
            gradient = {
                "x1": _parse_percent(el.get("x1"), 0.0),
                "y1": _parse_percent(el.get("y1"), 0.0),
                "x2": _parse_percent(el.get("x2"), 1.0),
                "y2": _parse_percent(el.get("y2"), 1.0),
                "c0": stops[0][1],
                "c1": stops[-1][1],
            }
        elif name == "circle":
            circle = {
                "cx": float(el.get("cx")),
                "cy": float(el.get("cy")),
                "r": float(el.get("r")),
            }
        elif name == "g":
            if el.get("fill"):
                group_fill = _parse_color(el.get("fill"))
            group_tx, group_ty, group_scale = _parse_transform(el.get("transform") or "")
        elif name == "polygon":
            polygons.append(_parse_points(el.get("points") or ""))

    if gradient is None or circle is None or len(polygons) < 2:
        raise ValueError(
            f"{svg_path} is not the PortableAI badge "
            "(expected a linearGradient, a circle, and two polygons)"
        )
    return {
        "vb_w": vb_w,
        "vb_h": vb_h,
        "gradient": gradient,
        "circle": circle,
        "fill": group_fill,
        "tx": group_tx,
        "ty": group_ty,
        "scale": group_scale,
        "polygons": polygons,
    }


def _lerp(c0, c1, t: float) -> tuple[int, int, int, int]:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))  # type: ignore[return-value]


def rasterize_source(svg_path: Path, size: int) -> Image.Image:
    """Paint logo-source.svg at `size` px via supersampled Pillow drawing.

    Geometry is parsed from the SVG (colors, points, transform) so edits to
    the source flow through. This is not a second hand-drawn mark.
    """
    geo = load_logo_geometry(svg_path)
    hi = size * RENDER_SCALE
    sx = hi / geo["vb_w"]
    sy = hi / geo["vb_h"]
    img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    px = img.load()
    g = geo["gradient"]
    x1, y1 = g["x1"] * (hi - 1), g["y1"] * (hi - 1)
    x2, y2 = g["x2"] * (hi - 1), g["y2"] * (hi - 1)
    dx, dy = x2 - x1, y2 - y1
    denom = dx * dx + dy * dy or 1.0
    cx = geo["circle"]["cx"] * sx
    cy = geo["circle"]["cy"] * sy
    r = geo["circle"]["r"] * min(sx, sy)
    r2 = r * r
    c0, c1 = g["c0"], g["c1"]
    for y in range(hi):
        for x in range(hi):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) > r2:
                continue
            t = ((x - x1) * dx + (y - y1) * dy) / denom
            px[x, y] = _lerp(c0, c1, t)

    draw = ImageDraw.Draw(img)
    tx, ty, sc = geo["tx"], geo["ty"], geo["scale"]
    for poly in geo["polygons"]:
        pts = [
            ((x * sc + tx) * sx, (y * sc + ty) * sy)
            for x, y in poly
        ]
        draw.polygon(pts, fill=geo["fill"])
    return img.resize((size, size), Image.Resampling.LANCZOS)


def _save_png(img: Image.Image, dest: Path, size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.resize((size, size), Image.Resampling.LANCZOS).save(dest, "PNG")


def _png_bytes(img: Image.Image, size: int) -> bytes:
    buf = io.BytesIO()
    img.resize((size, size), Image.Resampling.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


def write_icns_python(dest: Path, master: Image.Image) -> None:
    """Pack PNG-in-ICNS records. Used when iconutil is not on PATH."""
    chunks = []
    for ostype, size in ICNS_PNG_TYPES:
        payload = _png_bytes(master, size)
        chunks.append(ostype + struct.pack(">I", 8 + len(payload)) + payload)
    body = b"".join(chunks)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def write_macos_icns(generated_macos: Path, master: Image.Image) -> Path:
    iconset = generated_macos / "portableai.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True, exist_ok=True)
    for name, size in MACOS_ICONSET:
        _save_png(master, iconset / name, size)
    icns = generated_macos / "portableai.icns"
    iconutil = shutil.which("iconutil")
    if iconutil:
        subprocess.check_call(
            [iconutil, "-c", "icns", str(iconset), "-o", str(icns)]
        )
    else:
        write_icns_python(icns, master)
    return icns


def write_ios_appicon(appiconset: Path, master: Image.Image) -> None:
    if appiconset.exists():
        shutil.rmtree(appiconset)
    appiconset.mkdir(parents=True, exist_ok=True)
    images = []
    for filename, pixels, idiom, size, scale in IOS_APPICON:
        _save_png(master, appiconset / filename, pixels)
        images.append(
            {
                "idiom": idiom,
                "size": size,
                "scale": scale,
                "filename": filename,
            }
        )
    catalog = {
        "images": images,
        "info": {"author": "xcode", "version": 1},
    }
    (appiconset / "Contents.json").write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
    )


def write_linux_icons(icons_dir: Path, master: Image.Image, svg_bytes: bytes) -> None:
    if icons_dir.exists():
        shutil.rmtree(icons_dir)
    _save_png(master, icons_dir / "portableai.png", LINUX_DIRICON_SIZE)
    (icons_dir / "portableai.svg").write_bytes(svg_bytes)
    for size in LINUX_HICOLOR_SIZES:
        dest = icons_dir / "hicolor" / f"{size}x{size}" / "apps" / "portableai.png"
        _save_png(master, dest, size)
    scalable = icons_dir / "hicolor" / "scalable" / "apps" / "portableai.svg"
    scalable.parent.mkdir(parents=True, exist_ok=True)
    scalable.write_bytes(svg_bytes)
    _save_png(master, icons_dir / "pixmaps" / "portableai.png", LINUX_DIRICON_SIZE)


def install_appdir(icons_dir: Path, appdir: Path) -> None:
    """Copy generated Linux icons into an AppDir where appimagetool looks."""
    png256 = icons_dir / "portableai.png"
    svg = icons_dir / "portableai.svg"
    if not png256.is_file():
        raise FileNotFoundError(f"missing generated Linux icon {png256}")
    appdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(png256, appdir / "portableai.png")
    # Real file, not a symlink — appimagetool / some file managers skip .DirIcon links.
    shutil.copyfile(png256, appdir / ".DirIcon")
    if svg.is_file():
        shutil.copyfile(svg, appdir / "portableai.svg")
    hicolor_src = icons_dir / "hicolor"
    hicolor_dst = appdir / "usr" / "share" / "icons" / "hicolor"
    if hicolor_dst.exists():
        shutil.rmtree(hicolor_dst)
    shutil.copytree(hicolor_src, hicolor_dst)
    pix_src = icons_dir / "pixmaps" / "portableai.png"
    pix_dst = appdir / "usr" / "share" / "pixmaps"
    pix_dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pix_src, pix_dst / "portableai.png")


def generate(root: Path, appdir: Path | None = None) -> None:
    source = root / "branding" / "logo-source.svg"
    if not source.is_file():
        raise FileNotFoundError(f"missing canonical logo {source}")
    svg_bytes = source.read_bytes()
    master = rasterize_source(source, MASTER_SIZE)

    web_svg = root / "ui" / "static" / "logo.svg"
    web_png = root / "ui" / "static" / "logo.png"
    web_svg.parent.mkdir(parents=True, exist_ok=True)
    web_svg.write_bytes(svg_bytes)
    _save_png(master, web_png, WEB_PNG_SIZE)
    if web_svg.read_bytes() != svg_bytes:
        raise RuntimeError("ui/static/logo.svg drifted from branding/logo-source.svg")

    linux_icons = root / "packaging" / "linux" / "icons"
    write_linux_icons(linux_icons, master, svg_bytes)

    macos_dir = root / "branding" / "generated" / "macos"
    write_macos_icns(macos_dir, master)

    ios_dir = root / "branding" / "generated" / "ios" / "AppIcon.appiconset"
    write_ios_appicon(ios_dir, master)

    if appdir is not None:
        install_appdir(linux_icons, appdir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate PortableAI icons from branding/logo-source.svg"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repo root (default: parent of branding/)",
    )
    parser.add_argument(
        "--appdir",
        type=Path,
        default=None,
        help="also install Linux icons into this AppDir (used by make_appimage.sh)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else repo_root()
    appdir = args.appdir.resolve() if args.appdir else None
    generate(root, appdir=appdir)
    print(f"generated icons from {root / 'branding' / 'logo-source.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
