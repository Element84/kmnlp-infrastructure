"""Generates the high level AWS architecture diagram as a hand-laid-out SVG.

Regenerate with:
    uv run --with cairosvg python docs/architecture_diagram.py

Output is written to docs/architecture.{svg,png}. The SVG needs only the standard library;
the PNG (rendered at 2x) uses cairosvg, or `rsvg-convert` if it is on the PATH instead.
Pass --svg-only to skip the PNG.

Layout is explicit rather than left to graphviz so that edges never cross nodes:
  * the API service sits on the left and fans out to its dependencies through one bus,
  * each component is a horizontal lane on the right,
  * services outside the VPC (S3 data buckets, Bedrock) sit in a column on the far right.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

# --------------------------------------------------------------------------- style

FONT = "Inter, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
INK = "#16191F"
MUTED = "#5F6B7A"
EDGE = "#7D8998"

# AWS architecture icon category colours.
CATEGORY = {
    "network": ("#8C4FFF", "Networking & analytics"),
    "compute": ("#ED7100", "Compute"),
    "database": ("#C925D1", "Database"),
    "storage": ("#7AA116", "Storage"),
    "integration": ("#E7157B", "Application integration"),
    "ai": ("#01A88D", "Machine learning"),
    "client": ("#232F3E", None),
}

W, H = 180, 60  # node card size
ICON = 40

# Column x positions.
USERS_X = 40
L1, L2 = 300, 530
R1, R2, R3, R4 = 820, 1050, 1280, 1510
EXT = 1780
BUS_X = 765

# --------------------------------------------------------------------------- icons
# Simple white line glyphs drawn on a 40x40 tile.

S = 'fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'

GLYPHS = {
    "users": f'<circle cx="20" cy="15" r="5" {S}/><path d="M10 30c0-6 4.5-9 10-9s10 3 10 9" {S}/>',
    "cdn": (
        f'<circle cx="20" cy="20" r="11" {S}/><ellipse cx="20" cy="20" rx="5" ry="11" {S}/>'
        f'<path d="M9 20h22" {S}/>'
    ),
    "lb": (
        f'<circle cx="13" cy="20" r="4" {S}/><path d="M17 20h12M17 18l10-7M17 22l10 7" {S}/>'
        '<circle cx="29" cy="11" r="2" fill="#fff"/><circle cx="31" cy="20" r="2" fill="#fff"/>'
        '<circle cx="29" cy="29" r="2" fill="#fff"/>'
    ),
    "container": (
        f'<path d="M20 8l11 6v12l-11 6-11-6V14z" {S}/><path d="M9 14l11 6 11-6M20 20v12" {S}/>'
    ),
    "db": (
        f'<ellipse cx="20" cy="12" rx="10" ry="4" {S}/>'
        f'<path d="M10 12v16c0 2.2 4.5 4 10 4s10-1.8 10-4V12M10 20c0 2.2 4.5 4 10 4s10-1.8 10-4" {S}/>'
    ),
    "bucket": (
        f'<ellipse cx="20" cy="12" rx="11" ry="4" {S}/>'
        f'<path d="M9 12l3 17c.3 1.7 3.8 3 8 3s7.7-1.3 8-3l3-17" {S}/>'
    ),
    "k8s": (
        f'<path d="M20 7l11 6.5v13L20 33 9 26.5v-13z" {S}/>'
        f'<path d="M16 14v12M16 20l7-6M18 19l5 7" {S}/>'
    ),
    "steps": (
        f'<rect x="15" y="7" width="10" height="7" rx="2" {S}/>'
        f'<rect x="8" y="26" width="10" height="7" rx="2" {S}/>'
        f'<rect x="22" y="26" width="10" height="7" rx="2" {S}/>'
        f'<path d="M20 14v6M13 26v-6h14v6" {S}/>'
    ),
    "search": f'<circle cx="18" cy="18" r="8" {S}/><path d="M24 24l7 7" {S}/>',
    "cache": (
        f'<rect x="9" y="10" width="22" height="6" rx="2" {S}/>'
        f'<rect x="9" y="17" width="22" height="6" rx="2" {S}/>'
        f'<rect x="9" y="24" width="22" height="6" rx="2" {S}/>'
    ),
    "files": (
        f'<path d="M8 13a2 2 0 012-2h6l3 3h11a2 2 0 012 2v12a2 2 0 01-2 2H10a2 2 0 01-2-2z" {S}/>'
    ),
    "model": (
        f'<path d="M20 7c1 7 4 11 13 13-9 2-12 6-13 13-1-7-4-11-13-13 9-2 12-6 13-13z" {S}/>'
    ),
}

# --------------------------------------------------------------------------- drawing

out: list[str] = []


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cluster(x, y, w, h, label, *, fill="#FFFFFF", stroke="#D3DAE3", dash=None):
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1"{dash_attr}/>'
        f'<text x="{x + 18}" y="{y + 27}" font-size="13" font-weight="600" fill="#232F3E" '
        f'letter-spacing="0.2">{esc(label)}</text>'
    )


def node(x, y, title, subtitle, glyph, category):
    colour = CATEGORY[category][0]
    out.append(
        f'<g><rect x="{x}" y="{y}" width="{W}" height="{H}" rx="10" fill="#FFFFFF" '
        f'stroke="#C9D1DB" stroke-width="1"/>'
        f'<rect x="{x + 10}" y="{y + 10}" width="{ICON}" height="{ICON}" rx="8" fill="{colour}"/>'
        f'<g transform="translate({x + 10} {y + 10})">{GLYPHS[glyph]}</g>'
        f'<text x="{x + 62}" y="{y + 27}" font-size="14" font-weight="600" fill="{INK}">{esc(title)}</text>'
        f'<text x="{x + 62}" y="{y + 45}" font-size="12" fill="{MUTED}">{esc(subtitle)}</text></g>'
    )


def rounded_path(points, r=12):
    """Orthogonal polyline with rounded corners."""
    d = [f"M{points[0][0]} {points[0][1]}"]
    for i in range(1, len(points) - 1):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[i + 1]

        def towards(ax, ay, bx, by, dist):
            length = max(abs(bx - ax), abs(by - ay))
            k = min(dist, length / 2) / length if length else 0
            return ax + (bx - ax) * k, ay + (by - ay) * k

        ax, ay = towards(x1, y1, x0, y0, r)
        bx, by = towards(x1, y1, x2, y2, r)
        d.append(f"L{ax:g} {ay:g} Q{x1} {y1} {bx:g} {by:g}")
    d.append(f"L{points[-1][0]} {points[-1][1]}")
    return " ".join(d)


def edge(*points, dashed=False, arrow=True):
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    head = ' marker-end="url(#head)"' if arrow else ""
    out.append(
        f'<path d="{rounded_path(points)}" fill="none" stroke="{EDGE}" stroke-width="1.6"{dash}{head}/>'
    )


def label(x, y, text, anchor="start"):
    out.append(
        f'<text x="{x}" y="{y}" font-size="12" font-style="italic" fill="{MUTED}" '
        f'text-anchor="{anchor}">{esc(text)}</text>'
    )


def mid(y):
    return y + H // 2


# --------------------------------------------------------------------------- layout

# Row y positions.
FRONT_Y = 74
TOP_Y = 268  # Ingestion and Dask lanes
SEARCH_Y = 372
LLM_Y = 500  # LiteLLM lane, API service and Users
LLM_DB_Y = 600
LF_Y = 828  # Langfuse ALB and web
LF_ROWS = (748, 828, 908)

WIDTH, HEIGHT = 2000, 1100
PNG_SCALE = 2

# Containers first so they sit underneath everything else.
cluster(280, 30, 470, 124, "Frontend")
out.append(
    '<rect x="260" y="184" width="1470" height="824" rx="18" fill="#F4F8FC" '
    'stroke="#8FA8C8" stroke-width="1.2"/>'
    '<rect x="276" y="196" width="44" height="22" rx="6" fill="#DCE8F5"/>'
    '<text x="298" y="211.5" font-size="13" font-weight="700" fill="#2F4A6B" '
    'text-anchor="middle">VPC</text>'
)
cluster(280, 224, 470, 124, "Ingestion")
cluster(280, 456, 470, 224, "API service")
cluster(800, 224, 450, 124, "Dask compute")
cluster(800, 456, 450, 224, "LiteLLM")
cluster(800, 704, 910, 284, "Langfuse")
out.append(
    f'<rect x="{EXT - 16}" y="{TOP_Y - 20}" width="{W + 32}" height="{LLM_Y + H - TOP_Y + 74}" rx="14" '
    'fill="none" stroke="#B7C1CD" stroke-width="1" stroke-dasharray="5 5"/>'
    f'<text x="{EXT + 90}" y="{LLM_Y + H + 40}" font-size="12" fill="{MUTED}" '
    'text-anchor="middle">Regional services, outside the VPC</text>'
)

# Edges next, so cards are drawn over their ends.
# Users
edge((USERS_X + 90, LLM_Y), (USERS_X + 90, mid(FRONT_Y)), (L1 - 2, mid(FRONT_Y)))
edge((USERS_X + W, mid(LLM_Y)), (L1 - 2, mid(LLM_Y)))
edge((USERS_X + 90, LLM_Y + H), (USERS_X + 90, LF_Y + 45), (R1 - 2, LF_Y + 45))

# Frontend
edge((L1 + W, mid(FRONT_Y)), (L2 - 2, mid(FRONT_Y)))

# Ingestion
edge((L1 + W, mid(TOP_Y)), (L2 - 2, mid(TOP_Y)))
edge((L2 + 90, TOP_Y), (L2 + 90, 204), (EXT + 90, 204), (EXT + 90, TOP_Y - 2))

# API service
edge((L1 + W, mid(LLM_Y)), (L2 - 2, mid(LLM_Y)))
edge((L2 + 90, LLM_Y + H), (L2 + 90, LLM_DB_Y - 2))

# API fan-out bus
edge((L2 + W, mid(LLM_Y)), (BUS_X, mid(LLM_Y)), arrow=False)
edge((BUS_X, mid(LLM_Y)), (BUS_X, mid(TOP_Y)), (R1 - 2, mid(TOP_Y)))
edge((BUS_X, mid(SEARCH_Y)), (R1 - 2, mid(SEARCH_Y)))
edge((BUS_X, mid(LLM_Y)), (R1 - 2, mid(LLM_Y)))
edge((BUS_X, mid(LLM_Y)), (BUS_X, LF_Y + 15), (R1 - 2, LF_Y + 15), dashed=True)
label(BUS_X + 8, LF_Y + 8, "traces")

# Dask
edge((R1 + W, mid(TOP_Y)), (R2 - 2, mid(TOP_Y)))
edge((R2 + W, mid(TOP_Y)), (EXT - 2, mid(TOP_Y)))

# LiteLLM
edge((R1 + W, mid(LLM_Y)), (R2 - 2, mid(LLM_Y)))
edge((R2 + 90, LLM_Y + H), (R2 + 90, LLM_DB_Y - 2))
edge((R2 + W, mid(LLM_Y)), (EXT - 2, mid(LLM_Y)))

# Langfuse
lf_bus = R2 + W + 25
edge((R1 + W, mid(LF_Y)), (R2 - 2, mid(LF_Y)))
edge((R2 + W, mid(LF_Y)), (lf_bus, mid(LF_Y)), arrow=False)
for row in LF_ROWS:
    edge((lf_bus, mid(LF_Y)), (lf_bus, mid(row)), (R3 - 2, mid(row)))
edge((R3 + W, mid(LF_ROWS[2])), (R4 - 2, mid(LF_ROWS[2])))

# Nodes.
node(USERS_X, LLM_Y, "Users", "Browser", "users", "client")

node(L1, FRONT_Y, "CloudFront", "CDN", "cdn", "network")
node(L2, FRONT_Y, "Website bucket", "S3 static site", "bucket", "storage")

node(L1, TOP_Y, "State machine", "Step Functions", "steps", "integration")
node(L2, TOP_Y, "Ingest task", "Fargate task", "container", "compute")

node(L1, LLM_Y, "ALB", "Application LB", "lb", "network")
node(L2, LLM_Y, "API", "Fargate service", "container", "compute")
node(L2, LLM_DB_Y, "API DB", "Aurora", "db", "database")

node(R1, TOP_Y, "NLB", "Network LB", "lb", "network")
node(R2, TOP_Y, "Dask cluster", "EKS on Fargate", "k8s", "compute")

node(R1, SEARCH_Y, "OpenSearch", "Geocoding index", "search", "network")

node(R1, LLM_Y, "ALB", "Application LB", "lb", "network")
node(R2, LLM_Y, "LiteLLM proxy", "Fargate service", "container", "compute")
node(R2, LLM_DB_Y, "LiteLLM DB", "Aurora", "db", "database")

node(R1, LF_Y, "ALB", "Application LB", "lb", "network")
node(R2, LF_Y, "Web + worker", "Fargate service", "container", "compute")
node(R3, LF_ROWS[0], "Redis", "ElastiCache", "cache", "database")
node(R3, LF_ROWS[1], "Langfuse DB", "Aurora", "db", "database")
node(R3, LF_ROWS[2], "ClickHouse", "Fargate service", "container", "compute")
node(R4, LF_ROWS[2], "EFS", "ClickHouse volume", "files", "storage")

node(EXT, TOP_Y, "Data buckets", "S3", "bucket", "storage")
node(EXT, LLM_Y, "Bedrock", "Foundation models", "model", "ai")


# Legend.
lx, ly = 280, 1050
for key in ("network", "compute", "database", "storage", "integration", "ai"):
    colour, name = CATEGORY[key]
    out.append(
        f'<rect x="{lx}" y="{ly - 11}" width="14" height="14" rx="4" fill="{colour}"/>'
        f'<text x="{lx + 22}" y="{ly}" font-size="12" fill="{MUTED}">{esc(name)}</text>'
    )
    lx += 22 + round(len(name) * 6.1) + 30
lx += 20
out.append(
    f'<path d="M{lx} {ly - 4}h34" stroke="{EDGE}" stroke-width="1.6" marker-end="url(#head)"/>'
    f'<text x="{lx + 44}" y="{ly}" font-size="12" fill="{MUTED}">Request / data flow</text>'
)
lx += 190
out.append(
    f'<path d="M{lx} {ly - 4}h34" stroke="{EDGE}" stroke-width="1.6" stroke-dasharray="6 5" '
    'marker-end="url(#head)"/>'
    f'<text x="{lx + 44}" y="{ly}" font-size="12" fill="{MUTED}">Telemetry</text>'
)

svg = (
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
    f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{FONT}" role="img">'
    "<title>High level AWS architecture</title>"
    '<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" '
    'markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">'
    f'<path d="M0 0.5L10 5L0 9.5z" fill="{EDGE}"/></marker></defs>'
    f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#FFFFFF"/>' + "".join(out) + "</svg>\n"
)


def write_png(svg_path: Path, png_path: Path) -> None:
    """Render the SVG with cairosvg, falling back to rsvg-convert."""
    try:
        import cairosvg
    except ImportError:
        cairosvg = None

    if cairosvg is not None:
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), scale=PNG_SCALE)
    elif shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-z", str(PNG_SCALE), "-o", str(png_path), str(svg_path)],
            check=True,
        )
    else:
        sys.exit(
            "Cannot render PNG: install cairosvg (e.g. `uv run --with cairosvg ...`) "
            "or put rsvg-convert on the PATH. Use --svg-only to skip the PNG."
        )


args = [a for a in sys.argv[1:] if not a.startswith("--")]
svg_only = "--svg-only" in sys.argv[1:]
stem = Path(args[0]).with_suffix("") if args else Path(__file__).with_name("architecture")

ElementTree.fromstring(svg)  # fail here, not in the PNG renderer, if the SVG is malformed

svg_path = stem.with_suffix(".svg")
svg_path.write_text(svg)
print(f"wrote {svg_path}")

if not svg_only:
    png_path = stem.with_suffix(".png")
    write_png(svg_path, png_path)
    print(f"wrote {png_path}")
