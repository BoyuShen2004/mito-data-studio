"""Stitching phase 4: an offline yes/no review page for seam merge candidates.

``build_review`` turns ``candidates.csv`` into a self-contained folder
(``index.html`` plus PNG crops) that works opened straight from disk: no
server, no network, no external scripts. Each card shows the raw EM around one
contact, alone and with both labels outlined, and the page exports the
reviewer's answers as ``decisions.csv`` for phase 5.

Only the image and label slices a card shows are read, plus each seam's two
facing label planes (to locate the contacts). The design and file formats are
in ``docs/engineering/stitching.md``.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import shutil
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from annotation.stitching import (
    AXES,
    DECISION_FIELDS,
    StitchingError,
    Tile,
    build_inventory,
    check_seams_csv,
    collect_tiles,
    face_plane,
    load_tile_ids,
    load_tiles_csv,
    open_tile,
    read_csv,
    require_stitchable,
    tile_shape,
    write_csv,
)

DEFAULT_CROP = 160
COLOR_A = (255, 140, 0)     # label_a outline
COLOR_B = (0, 200, 255)     # label_b outline
NO_DATA = (40, 44, 52)      # outside both tiles
NO_IMAGE_GRAY = 96          # inside a tile that has no raw image
FAINT = 0.4                 # white blend for other labels' outlines
PAIRING_FIELDS = ("tile", "image_tile", "image_path", "status")


# --- image pairing -----------------------------------------------------------

def pair_images(tiles: list[Tile], image_dirs) -> tuple[dict[str, Tile | None], list[Tile]]:
    """Match each prediction tile to the raw image tile with the same box.

    Returns ``({tile name: image tile or None}, image tiles with no prediction)``.
    Suffixes after the box are ignored (``..._im_xy.h5`` pairs with ``..._im.h5``).
    """
    by_box: dict[tuple, Tile] = {}
    for directory in image_dirs:
        directory = Path(directory)
        if not directory.is_dir():
            raise StitchingError(f"image directory not found: {directory}")
        found, _ = collect_tiles(directory)
        for image in found:
            if image.box in by_box:
                raise StitchingError(
                    "two image tiles share box {}-{}_{}-{}_{}-{}: ".format(*image.box)
                    + f"{by_box[image.box].path} and {image.path}")
            by_box[image.box] = image
    pairing = {t.name: by_box.get(t.box) for t in tiles}
    boxes = {t.box for t in tiles}
    unused = sorted((img for img in by_box.values() if img.box not in boxes), key=lambda t: t.sort_key)
    for tile in tiles:
        image = pairing[tile.name]
        if image is None:
            continue
        shape = tile_shape(image.path)
        if shape != tile.shape:
            raise StitchingError(
                f"image {image.path} has shape {'x'.join(map(str, shape))} "
                f"but tile {tile.name} is {'x'.join(map(str, tile.shape))}")
    return pairing, unused


def pairing_rows(tiles: list[Tile], pairing: dict[str, Tile | None], unused: list[Tile]) -> list[dict]:
    rows = []
    for t in tiles:
        image = pairing[t.name]
        rows.append({"tile": t.name, "image_tile": image.name if image else "",
                     "image_path": image.path if image else "", "status": "matched" if image else "no_image"})
    rows += [{"tile": "", "image_tile": img.name, "image_path": img.path, "status": "no_prediction"}
             for img in unused]
    return rows


# --- geometry ----------------------------------------------------------------

def _round(value: float) -> int:
    return int(math.floor(value + 0.5))


@dataclass(frozen=True)
class CardView:
    """What a card shows: one or two XY slices of the same window.

    ``slices`` is ``((side, z), ...)``: ``(None, z)`` for X/Y seams (both tiles
    stitched in one slice), ``("a", z)`` and ``("b", z)`` for Z seams.
    ``window`` is ``(y0, y1, x0, x1)`` in region coordinates; it may extend past
    the tiles, which is drawn as no-data. ``seam_px`` is the seam line's offset
    from the left (X seam) or top (Y seam) edge of the window.
    """
    axis: int
    slices: tuple
    window: tuple[int, int, int, int]
    seam_px: int | None


def contact_positions(plane_a, plane_b, seam, label_a: int, label_b: int) -> np.ndarray:
    """Region (z, y, x) of every face position where ``label_a`` meets ``label_b``.

    The seam-axis coordinate is ``seam.position`` (the boundary).
    """
    rc = np.argwhere((plane_a == label_a) & (plane_b == label_b))
    out = np.full((len(rc), 3), seam.position, dtype=np.int64)
    for column, axis in enumerate(a for a in range(3) if a != seam.axis):
        out[:, axis] = rc[:, column] + seam.box[2 * axis]
    return out


def card_view(seam, positions: np.ndarray, crop: int = DEFAULT_CROP) -> CardView:
    """Choose the slice(s) and crop window for one contact.

    Z seams: tile A's last slice and tile B's first, centred on the contact's
    mean (y, x). X/Y seams: the contact slice closest to the contact's mean z
    (ties go to the smaller z), with the seam through the middle of the window
    and the other axis centred on the contact's mean at that slice.
    """
    if not len(positions):
        raise StitchingError(f"seam {seam.seam}: candidate has no contact on the facing planes")
    half = crop // 2
    if seam.axis == 0:
        cy, cx = (_round(positions[:, a].mean()) for a in (1, 2))
        return CardView(0, (("a", seam.position - 1), ("b", seam.position)),
                        (cy - half, cy - half + crop, cx - half, cx - half + crop), None)
    zs = np.unique(positions[:, 0])
    z = int(zs[np.argmin(np.abs(zs - positions[:, 0].mean()))])
    across_axis = 1 if seam.axis == 2 else 2
    centre = _round(positions[positions[:, 0] == z, across_axis].mean())
    along = (seam.position - half, seam.position - half + crop)
    across = (centre - half, centre - half + crop)
    window = across + along if seam.axis == 2 else along + across
    return CardView(seam.axis, ((None, z),), window, half)


# --- rendering ---------------------------------------------------------------

@dataclass
class Panel:
    glob: np.ndarray    # global label id per pixel (0 = background or no data)
    em: np.ndarray      # raw image values (float64)
    valid: np.ndarray   # inside one of the tiles
    has_em: np.ndarray  # inside a tile with a raw image


def read_panel(z: int, window, sources) -> Panel:
    """One XY slice of ``window`` stitched from ``sources``.

    ``sources`` is ``[(tile, label_volume, image_volume or None, id_offset)]``.
    Each source contributes only the part of the window inside its box, read as
    a single sub-plane.
    """
    y0, y1, x0, x1 = window
    shape = (y1 - y0, x1 - x0)
    panel = Panel(np.zeros(shape, np.uint64), np.zeros(shape, np.float64),
                  np.zeros(shape, bool), np.zeros(shape, bool))
    for tile, labels, image, offset in sources:
        if not tile.lo(0) <= z < tile.hi(0):
            continue
        ys = max(y0, tile.lo(1)), min(y1, tile.hi(1))
        xs = max(x0, tile.lo(2)), min(x1, tile.hi(2))
        if ys[1] <= ys[0] or xs[1] <= xs[0]:
            continue
        key = (z - tile.lo(0), slice(ys[0] - tile.lo(1), ys[1] - tile.lo(1)),
               slice(xs[0] - tile.lo(2), xs[1] - tile.lo(2)))
        dst = (slice(ys[0] - y0, ys[1] - y0), slice(xs[0] - x0, xs[1] - x0))
        local = np.asarray(labels[key]).astype(np.uint64)
        panel.glob[dst] = np.where(local != 0, local + np.uint64(offset), np.uint64(0))
        panel.valid[dst] = True
        if image is not None:
            panel.em[dst] = np.asarray(image[key], dtype=np.float64)
            panel.has_em[dst] = True
    return panel


def gray_levels(panels: list[Panel], uint8_source: bool) -> list[np.ndarray]:
    """EM as uint8 for display: unchanged for uint8 images, otherwise
    stretched between the 0.5th and 99.5th percentiles of the card's pixels."""
    if uint8_source:
        return [p.em.astype(np.uint8) for p in panels]
    values = np.concatenate([p.em[p.has_em] for p in panels]) if panels else np.empty(0)
    lo, hi = np.percentile(values, (0.5, 99.5)) if values.size else (0.0, 1.0)
    scale = 255.0 / (hi - lo) if hi > lo else 0.0
    return [np.clip((p.em - lo) * scale, 0, 255).astype(np.uint8) for p in panels]


def _outline(mask: np.ndarray) -> np.ndarray:
    """Pixels of ``mask`` with a 4-neighbour outside it (window edges don't count)."""
    p = np.pad(mask, 1, mode="edge")
    inner = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return mask & ~inner


def _label_edges(glob: np.ndarray) -> np.ndarray:
    """Labelled pixels with a 4-neighbour of a different label."""
    p = np.pad(glob, 1, mode="edge")
    c = p[1:-1, 1:-1]
    differs = (p[:-2, 1:-1] != c) | (p[2:, 1:-1] != c) | (p[1:-1, :-2] != c) | (p[1:-1, 2:] != c)
    return differs & (glob != 0)


def em_rgb(panel: Panel, gray: np.ndarray) -> np.ndarray:
    rgb = np.repeat(gray[..., None], 3, axis=2)
    rgb[panel.valid & ~panel.has_em] = NO_IMAGE_GRAY
    rgb[~panel.valid] = NO_DATA
    return rgb


def overlay_rgb(panel: Panel, rgb: np.ndarray, global_a: int, global_b: int) -> np.ndarray:
    """``rgb`` with label_a and label_b outlined in COLOR_A / COLOR_B and every
    other label's outline blended faintly toward white."""
    out = rgb.copy()
    is_a, is_b = panel.glob == np.uint64(global_a), panel.glob == np.uint64(global_b)
    faint = _label_edges(panel.glob) & ~is_a & ~is_b
    out[faint] = (out[faint] * (1 - FAINT) + 255 * FAINT).astype(np.uint8)
    out[_outline(is_a)] = COLOR_A
    out[_outline(is_b)] = COLOR_B
    return out


def _save_png(path: Path, rgb: np.ndarray) -> None:
    Image.fromarray(np.ascontiguousarray(rgb), mode="RGB").save(path, format="PNG")


# --- page --------------------------------------------------------------------

def parse_shard(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    try:
        i, n = (int(v) for v in text.split("/"))
    except ValueError:
        raise StitchingError(f"--shard must look like i/n, got {text!r}") from None
    if not 1 <= i <= n:
        raise StitchingError(f"--shard {text}: need 1 <= i <= n")
    return i, n


def shard_range(count: int, shard: tuple[int, int] | None) -> range:
    """Contiguous, deterministic slice of ``count`` candidate rows for shard i of n."""
    if shard is None:
        return range(count)
    i, n = shard
    return range((i - 1) * count // n, i * count // n)


def candidate_key(row: dict) -> str:
    return f"{row['seam']}:{row['label_a']}:{row['label_b']}"


def _prepare_out(out: Path, overwrite: bool) -> None:
    if out.exists() and any(out.iterdir()):
        if not overwrite:
            raise StitchingError(f"{out} is not empty; pass --overwrite to replace its review page")
        (out / "index.html").unlink(missing_ok=True)
        shutil.rmtree(out / "crops", ignore_errors=True)
    (out / "crops").mkdir(parents=True, exist_ok=True)


@dataclass
class ReviewResult:
    out: Path
    cards: list[dict]
    pairing: list[dict]


def read_only_keys(path, candidates: list[dict]) -> set[str]:
    """Candidate keys listed in ``path`` (any CSV with seam, tile and label
    columns, such as an auto-accept sample); rows matching no candidate are refused."""
    rows = read_csv(path)
    known = {candidate_key(c): (c["tile_a"], c["tile_b"]) for c in candidates}
    keys, unknown = set(), []
    for lineno, row in enumerate(rows, 2):
        try:
            key = candidate_key(row)
        except KeyError as exc:
            raise StitchingError(f"{path}: missing column {exc}") from None
        if known.get(key) != (row.get("tile_a"), row.get("tile_b")):
            unknown.append(f"{path}:{lineno}: seam {row['seam']} labels {row['label_a']}/{row['label_b']}")
        keys.add(key)
    if unknown:
        raise StitchingError("--only rows that match no candidates.csv row:\n" + "\n".join(f"  {u}" for u in unknown))
    return keys


def build_review(work, image_dirs, out=None, *, crop: int = DEFAULT_CROP, shard: str | None = None,
                 overwrite: bool = False, only=None) -> ReviewResult:
    work = Path(work)
    if crop < 2:
        raise StitchingError("--crop must be at least 2 pixels")
    shard_n = parse_shard(shard)
    inventory = build_inventory(load_tiles_csv(work))
    require_stitchable(inventory)
    check_seams_csv(work, inventory)
    tile_ids = load_tile_ids(work, inventory.tiles)
    candidates = read_csv(work / "candidates.csv")
    pairing, unused = pair_images(inventory.tiles, image_dirs)
    pair_rows = pairing_rows(inventory.tiles, pairing, unused)
    write_csv(work / "image_pairing.csv", PAIRING_FIELDS, pair_rows)

    if out is None:
        stem = "review" if only is None else f"review_{Path(only).stem}"
        out = work / (stem if shard_n is None else f"{stem}_{shard_n[0]}of{shard_n[1]}")
    out = Path(out)
    _prepare_out(out, overwrite)

    tiles = {t.name: t for t in inventory.tiles}
    seams = {s.seam: s for s in inventory.seams}
    indices = list(range(len(candidates)))
    if only is not None:
        keys = read_only_keys(only, candidates)
        indices = [i for i in indices if candidate_key(candidates[i]) in keys]
    selected = [(indices[k], candidates[indices[k]]) for k in shard_range(len(indices), shard_n)]
    by_seam: dict[int, list] = {}
    for index, row in selected:
        by_seam.setdefault(int(row["seam"]), []).append((index, row))

    cards = []
    for seam_id in sorted(by_seam):
        seam = seams.get(seam_id)
        if seam is None:
            raise StitchingError(f"candidates.csv names seam {seam_id}, which seams.csv lacks; rerun stitch_candidates")
        tile_a, tile_b = tiles[seam.tile_a], tiles[seam.tile_b]
        with ExitStack() as stack:
            labels = {side: stack.enter_context(open_tile(t.path)) for side, t in (("a", tile_a), ("b", tile_b))}
            images = {side: stack.enter_context(open_tile(pairing[t.name].path)) if pairing[t.name] else None
                      for side, t in (("a", tile_a), ("b", tile_b))}
            plane_a = face_plane(labels["a"], tile_a, seam, side="a")
            plane_b = face_plane(labels["b"], tile_b, seam, side="b")
            sources = {side: (t, labels[side], images[side], tile_ids[t.name]["id_offset"])
                       for side, t in (("a", tile_a), ("b", tile_b))}
            uint8_source = all(v is None or np.dtype(v.dtype) == np.uint8 for v in images.values())
            for index, row in by_seam[seam_id]:
                cards.append(_render_card(out, index, row, seam, plane_a, plane_b, sources, uint8_source, crop))

    cards.sort(key=lambda c: c["index"])
    only_bytes = Path(only).read_bytes() if only is not None else b""
    store = hashlib.sha256((work / "candidates.csv").read_bytes() + (shard or "").encode() + only_bytes).hexdigest()[:16]
    export_stem = "decisions" if only is None else f"decisions_{Path(only).stem}"
    export_name = f"{export_stem}.csv" if shard_n is None else f"{export_stem}_{shard_n[0]}of{shard_n[1]}.csv"
    title = "Seam review" + ("" if only is None else f" ({Path(only).name})") \
        + ("" if shard_n is None else f" (shard {shard_n[0]} of {shard_n[1]})")
    (out / "index.html").write_text(render_html(cards, store=store, export_name=export_name, title=title,
                                                crop=crop, missing_images=[r["tile"] for r in pair_rows
                                                                if r["status"] == "no_image"]))
    return ReviewResult(out, cards, pair_rows)


def _render_card(out, index, row, seam, plane_a, plane_b, sources, uint8_source, crop) -> dict:
    label_a, label_b = int(row["label_a"]), int(row["label_b"])
    positions = contact_positions(plane_a, plane_b, seam, label_a, label_b)
    if len(positions) != int(row["contact_voxels"]):
        raise StitchingError(
            f"seam {seam.seam} labels {label_a}/{label_b}: candidates.csv says {row['contact_voxels']} "
            f"contact voxels, the tiles show {len(positions)}; rerun stitch_candidates")
    view = card_view(seam, positions, crop)
    panels = []
    for side, z in view.slices:
        used = [sources["a"], sources["b"]] if side is None else [sources[side]]
        panels.append(read_panel(z, view.window, used))
    grays = gray_levels(panels, uint8_source)

    cid = f"c{index:06d}"
    images = []
    for (side, z), panel, gray in zip(view.slices, panels, grays):
        stem = cid if side is None else f"{cid}_{side}"
        rgb = em_rgb(panel, gray)
        _save_png(out / "crops" / f"{stem}_em.png", rgb)
        _save_png(out / "crops" / f"{stem}_ov.png", overlay_rgb(panel, rgb, int(row["global_a"]), int(row["global_b"])))
        images.append({"side": side, "z": z, "em": f"crops/{stem}_em.png", "ov": f"crops/{stem}_ov.png"})
    return {
        "index": index, "id": cid, "key": candidate_key(row), "view": view, "images": images,
        "no_image": [t.name for t, _, image, _ in (sources["a"], sources["b"]) if image is None],
        **{f: row[f] for f in ("seam", "axis", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b",
                               "contact_voxels", "frac_a", "frac_b")},
    }


def page_data(cards: list[dict], *, store: str, export_name: str) -> dict:
    """The JSON the page script reads. IDs stay strings: they can exceed 2**53."""
    keep = ("id", "key", "seam", "tile_a", "label_a", "tile_b", "label_b", "global_a", "global_b")
    return {"store": store, "export_name": export_name, "fields": list(DECISION_FIELDS),
            "cards": [{k: str(c[k]) for k in keep} for c in cards]}


def _card_html(card: dict, position: int, total: int, crop: int) -> str:
    e = html.escape
    view: CardView = card["view"]
    line = ""
    if view.seam_px is not None:
        cls, prop = ("seam-v", "left") if view.axis == 2 else ("seam-h", "top")
        line = f'<span class="{cls}" style="{prop}:{100 * view.seam_px / crop:.4f}%"></span>'

    def figure(kind: str, caption: str) -> str:
        frames = "".join(
            f'<div class="frame"><img src="{e(img[kind])}" alt="{e(caption)}" loading="lazy" '
            f'width="{crop}" height="{crop}">{line}</div>' for img in card["images"])
        cls = "pair" if len(card["images"]) == 2 else "single"
        return f'<figure><div class="{cls}">{frames}</div><figcaption>{e(caption)}</figcaption></figure>'

    zs = card["images"]
    if view.axis == 0:
        where = f"A z={zs[0]['z']} | B z={zs[1]['z']}"
    else:
        where = f"z={zs[0]['z']}, seam {'x' if view.axis == 2 else 'y'}={card_seam_position(card)}"
    y0, y1, x0, x1 = view.window
    warn = "".join(f'<p class="warn">No raw image for {e(t)}; shown as flat gray.</p>' for t in card["no_image"])
    return f"""<section class="card" id="{e(card['id'])}" hidden>
<h2>Candidate {position} of {total} &middot; seam {e(card['seam'])} ({e(card['axis'])})</h2>
<p class="decision">Undecided</p>
<dl>
<dt><span class="swatch a"></span>A</dt><dd>{e(card['tile_a'])} &middot; label {e(card['label_a'])} &middot; global {e(card['global_a'])}</dd>
<dt><span class="swatch b"></span>B</dt><dd>{e(card['tile_b'])} &middot; label {e(card['label_b'])} &middot; global {e(card['global_b'])}</dd>
<dt>Contact</dt><dd>{e(card['contact_voxels'])} voxels &middot; frac_a {e(card['frac_a'])} &middot; frac_b {e(card['frac_b'])}</dd>
<dt>View</dt><dd>{e(where)} &middot; y {y0}&ndash;{y1} &middot; x {x0}&ndash;{x1}</dd>
</dl>
{warn}<div class="views">
{figure("em", "EM")}
{figure("ov", "EM with outlines")}
</div>
</section>"""


def card_seam_position(card: dict) -> int:
    view: CardView = card["view"]
    y0, _, x0, _ = view.window
    return (x0 if view.axis == 2 else y0) + view.seam_px


def render_html(cards: list[dict], *, store: str, export_name: str, title: str, crop: int = DEFAULT_CROP,
                missing_images=()) -> str:
    data = json.dumps(page_data(cards, store=store, export_name=export_name), ensure_ascii=True)
    data = data.replace("</", "<\\/")
    body = "\n".join(_card_html(c, i + 1, len(cards), crop) for i, c in enumerate(cards))
    missing = ""
    if missing_images:
        missing = (f'<p class="warn">{len(missing_images)} tile(s) have no raw image; '
                   "see image_pairing.csv in the work directory.</p>")
    return (_PAGE.replace("{{TITLE}}", html.escape(title))
            .replace("{{MISSING}}", missing)
            .replace("{{CARDS}}", body)
            .replace("{{DATA}}", data)
            .replace("{{COLOR_A}}", "rgb({},{},{})".format(*COLOR_A))
            .replace("{{COLOR_B}}", "rgb({},{},{})".format(*COLOR_B)))


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root { --bg: #16181d; --panel: #1f232a; --text: #e6e6e6; --muted: #9aa3ad; --seam: #ffe14d;
        --a: {{COLOR_A}}; --b: {{COLOR_B}}; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.4 -apple-system, system-ui, sans-serif; }
header.bar { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center;
             padding: 10px 16px; background: var(--panel); border-bottom: 1px solid #333; }
header.bar h1 { font-size: 16px; margin: 0 8px 0 0; }
button, label.file { font: inherit; padding: 5px 10px; border-radius: 6px; border: 1px solid #444;
                     background: #2b3038; color: var(--text); cursor: pointer; }
button.yes { border-color: #3c8d5a; } button.no { border-color: #a24848; } button.unsure { border-color: #a08a3a; }
input[type=text] { font: inherit; padding: 4px 8px; border-radius: 6px; border: 1px solid #444; background: #111; color: var(--text); width: 9em; }
label.file input { display: none; }
#progress, #position, #message { color: var(--muted); }
main { padding: 16px; }
.card h2 { font-size: 16px; margin: 0 0 4px; }
.card dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 10px; margin: 8px 0 12px; }
.card dt { color: var(--muted); }
.card dd { margin: 0; overflow-wrap: anywhere; }
.decision { font-weight: 600; margin: 0; }
.card[data-decision=yes] .decision { color: #6fd08f; }
.card[data-decision=no] .decision { color: #ef7d7d; }
.card[data-decision=unsure] .decision { color: #e8c75a; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.swatch.a { background: var(--a); } .swatch.b { background: var(--b); }
.views { display: flex; flex-wrap: wrap; gap: 16px; }
figure { margin: 0; }
figcaption { color: var(--muted); margin-top: 4px; }
.single, .pair { display: flex; }
.pair { gap: 3px; background: var(--seam); padding: 0; }
.frame { position: relative; line-height: 0; }
.frame img { width: 320px; height: auto; image-rendering: pixelated; }
.pair .frame img { width: 240px; }
.seam-v, .seam-h { position: absolute; pointer-events: none; border: 0 dashed var(--seam); opacity: .85; }
.seam-v { top: 0; bottom: 0; border-left-width: 1px; }
.seam-h { left: 0; right: 0; border-top-width: 1px; }
.warn { color: #e8c75a; }
.help { color: var(--muted); }
@media (max-width: 700px) { header.bar { position: static; } .frame img, .pair .frame img { width: 100%; } }
</style>
</head>
<body>
<header class="bar">
<h1>{{TITLE}}</h1>
<button id="prev" title="Previous (left arrow)">&larr;</button>
<button class="yes" data-decision="yes">Yes (Y)</button>
<button class="no" data-decision="no">No (N)</button>
<button class="unsure" data-decision="unsure">Unsure (U)</button>
<button id="next" title="Next (right arrow)">&rarr;</button>
<span id="position"></span>
<span id="progress"></span>
<label><input type="checkbox" id="undecided-only"> Undecided only</label>
<label>Reviewer <input type="text" id="reviewer" autocomplete="off"></label>
<button id="export">Export decisions.csv</button>
<label class="file">Import decisions.csv<input type="file" id="import" accept=".csv,text/csv"></label>
<span id="message"></span>
</header>
<main>
<p class="warn" id="storage-warning" hidden>This browser is not saving progress for this page. Export decisions regularly.</p>
{{MISSING}}
<p class="help">Does label A continue as label B across the seam? Orange outlines label A, blue label B, faint white other labels; the dashed yellow line (or yellow divider) is the seam. Progress is saved in this browser.</p>
<p id="empty" hidden>No cards to show. Untick "Undecided only" to see decided cards.</p>
{{CARDS}}
</main>
<script type="application/json" id="review-data">{{DATA}}</script>
<script>
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("review-data").textContent);
  var cards = data.cards, storeKey = "stitch-review:" + data.store;
  function $(id) { return document.getElementById(id); }
  var state = {};
  try { state = JSON.parse(localStorage.getItem(storeKey)) || {}; } catch (e) { $("storage-warning").hidden = false; }
  state.decisions = state.decisions || {};
  var current = Math.max(0, Math.min(state.current || 0, cards.length - 1)), shownEl = null;
  $("reviewer").value = state.reviewer || "";

  function save() {
    state.current = current; state.reviewer = $("reviewer").value;
    try { localStorage.setItem(storeKey, JSON.stringify(state)); } catch (e) { $("storage-warning").hidden = false; }
  }
  function decisionOf(i) { var d = state.decisions[cards[i].key]; return d ? d.decision : ""; }
  function shown(i) { return !$("undecided-only").checked || !decisionOf(i); }
  function find(from, step) {
    for (var i = from + step; i >= 0 && i < cards.length; i += step) { if (shown(i)) return i; }
    return -1;
  }
  function render() {
    if (shownEl) shownEl.hidden = true;
    shownEl = null;
    var done = 0;
    for (var k = 0; k < cards.length; k++) { if (decisionOf(k)) done++; }
    $("progress").textContent = done + " / " + cards.length + " decided";
    var ok = cards.length > 0 && shown(current);
    $("empty").hidden = ok;
    $("position").textContent = ok ? "Card " + (current + 1) + " of " + cards.length : "";
    if (ok) {
      var d = decisionOf(current);
      shownEl = $(cards[current].id);
      shownEl.hidden = false;
      shownEl.setAttribute("data-decision", d);
      shownEl.querySelector(".decision").textContent = d ? "Decision: " + d : "Undecided";
    }
    save();
  }
  function go(step) { var i = find(current, step); if (i >= 0) current = i; render(); }
  function settle() {
    if (cards.length && !shown(current)) {
      var i = find(current, 1);
      if (i < 0) i = find(current, -1);
      if (i >= 0) current = i;
    }
    render();
  }
  function decide(value) {
    if (!cards.length || !shown(current)) return;
    state.decisions[cards[current].key] = {
      decision: value, reviewer: $("reviewer").value, decided_at: new Date().toISOString() };
    var i = find(current, 1);
    if (i >= 0) current = i;
    settle();
  }
  function csvField(v) {
    v = v == null ? "" : String(v);
    return /[",\\r\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  }
  function parseCsv(text) {
    var rows = [], row = [], f = "", q = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (q) {
        if (ch === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else { q = false; } } else { f += ch; }
      } else if (ch === '"') { q = true; }
      else if (ch === ",") { row.push(f); f = ""; }
      else if (ch === "\\n") { row.push(f); rows.push(row); row = []; f = ""; }
      else if (ch !== "\\r") { f += ch; }
    }
    if (f || row.length) { row.push(f); rows.push(row); }
    return rows;
  }

  Array.prototype.forEach.call(document.querySelectorAll("button[data-decision]"), function (b) {
    b.addEventListener("click", function () { decide(b.getAttribute("data-decision")); });
  });
  $("prev").addEventListener("click", function () { go(-1); });
  $("next").addEventListener("click", function () { go(1); });
  $("undecided-only").addEventListener("change", function () { this.blur(); settle(); });
  $("reviewer").addEventListener("input", save);
  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    var actions = {
      y: function () { decide("yes"); }, n: function () { decide("no"); }, u: function () { decide("unsure"); },
      arrowright: function () { go(1); }, arrowdown: function () { go(1); },
      arrowleft: function () { go(-1); }, arrowup: function () { go(-1); } };
    var act = actions[e.key.toLowerCase()];
    if (act) { e.preventDefault(); act(); }
  });
  $("export").addEventListener("click", function () {
    var lines = [data.fields.join(",")];
    cards.forEach(function (c) {
      var d = state.decisions[c.key];
      if (!d) return;
      lines.push(data.fields.map(function (f) {
        if (f === "reviewer") return csvField(d.reviewer || $("reviewer").value);
        return csvField(f in d ? d[f] : c[f]);
      }).join(","));
    });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\\n") + "\\n"], { type: "text/csv" }));
    a.download = data.export_name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    $("message").textContent = "Exported " + (lines.length - 1) + " decision(s).";
  });
  $("import").addEventListener("change", function () {
    var file = this.files[0], input = this;
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      var rows = parseCsv(String(reader.result)), head = rows.shift() || [], byKey = {}, used = 0, skipped = 0;
      cards.forEach(function (c) { byKey[c.key] = c; });
      rows.forEach(function (r) {
        if (!r.join("")) return;
        var o = {};
        head.forEach(function (h, j) { o[h.trim()] = (r[j] || "").trim(); });
        var c = byKey[o.seam + ":" + o.label_a + ":" + o.label_b];
        if (!c || c.tile_a !== o.tile_a || c.tile_b !== o.tile_b || ["yes", "no", "unsure"].indexOf(o.decision) < 0) {
          skipped++; return;
        }
        state.decisions[c.key] = { decision: o.decision, reviewer: o.reviewer, decided_at: o.decided_at };
        used++;
      });
      $("message").textContent = "Imported " + used + " decision(s); skipped " + skipped + " row(s) not on this page.";
      input.value = "";
      settle();
    };
    reader.readAsText(file);
  });
  render();
})();
</script>
</body>
</html>
"""
