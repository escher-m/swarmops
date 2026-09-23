"""The SwarmOps mark, defined once.

Three agents orbiting one sealed record. The centre node is the ledger entry;
the satellites are the actors circling it, sized and faded by distance so the
mark reads as depth rather than as a flat triangle.

Everything that renders the logo -- the dashboard CSS, the marketing site CSS,
the favicons and the social card -- is generated from this file, so they cannot
drift apart.
"""

# viewBox is 24x24.
CENTRE = (12.0, 12.0, 3.45)

# (cx, cy, r, opacity). Angles are roughly 120 degrees apart but deliberately
# not exact: a perfect triangle reads as a static logo, a slightly irregular one
# reads as motion caught mid-orbit.
SATELLITES = [
    (12.0,  3.55, 2.15, 1.00),
    (19.35, 16.1, 1.80, 0.68),
    (4.75,  16.1, 1.40, 0.44),
]


def svg_inner(colour: str = "#fff") -> str:
    cx, cy, r = CENTRE
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colour}"/>']
    for sx, sy, sr, op in SATELLITES:
        o = "" if op >= 1 else f' opacity="{op}"'
        parts.append(f'<circle cx="{sx}" cy="{sy}" r="{sr}" fill="{colour}"{o}/>')
    return "".join(parts)


def svg(colour: str = "#fff", size: int | None = None) -> str:
    dim = f' width="{size}" height="{size}"' if size else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"{dim}>{svg_inner(colour)}</svg>'


def css_data_uri(colour: str = "white") -> str:
    """Percent-encoded for use inside a CSS url()."""
    raw = svg(colour).replace('"', "'")
    return ('url("data:image/svg+xml,'
            + raw.replace("<", "%3C").replace(">", "%3E").replace("#", "%23")
            + '")')


def favicon_data_uri(badge: str = "%238b7cf6") -> str:
    """A rounded badge with the mark inside, for <link rel=icon>."""
    inner = svg_inner("white").replace('"', "'").replace("<", "%3C").replace(">", "%3E")
    return (
        "data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        f"%3Crect width='32' height='32' rx='8' fill='{badge}'/%3E"
        f"%3Cg transform='translate(4 4)'%3E{inner}%3C/g%3E%3C/svg%3E"
    )
