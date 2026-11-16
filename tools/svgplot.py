#!/usr/bin/env python3
"""svgplot - the project's charts, written as SVG by plain Python."""

import math
from xml.sax.saxutils import escape

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Light and dark values of every colour.
LIGHT = {
    "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "n": "#898781",
    "box": "#f3f2ee", "tint": "#e4eefb", "band": "#f8f7f4",
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100",
    "s5": "#e87ba4", "s6": "#008300", "s7": "#6250d6", "s8": "#e34948",
    "o1": "#86b6ef", "o2": "#5598e7", "o3": "#2a78d6", "o4": "#1c5cab", "o5": "#0d366b",
}
DARK = {
    "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#383835", "n": "#898781",
    "box": "#262624", "tint": "#172a44", "band": "#1f1f1e",
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500",
    "s5": "#d55181", "s6": "#008300", "s7": "#9085e9", "s8": "#e66767",
    "o1": "#184f95", "o2": "#256abf", "o3": "#3987e5", "o4": "#86b6ef", "o5": "#cde2fb",
}
SERIES = [key for key in LIGHT if key[0] in "son" and key not in ("surface",)]

CHAR = 0.56                 # average width of a character, in font sizes


def text_width(text, size):
    return CHAR * size * len(text)


def num(value):
    """A coordinate: one decimal, no trailing zero."""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def nice_ticks(top, count=4):
    """Round tick values from 0 to at least `top`."""
    if top <= 0:
        return [0, 1]
    raw = top / count
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw - 1e-12)
    ticks = [0]
    while ticks[-1] < top - 1e-9:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


class Panel:
    def __init__(self, width, height, body):
        self.width, self.height, self.body = width, height, body


def text(x, y, content, size=11, colour="muted", anchor="start", weight=None, halo=False):
    """halo: outline the text in the surface colour, for a label that lies
    over a line."""
    extra = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{num(x)}" y="{num(y)}" font-size="{size}" text-anchor="{anchor}" '
            f'class="t-{colour}{" halo" if halo else ""}"{extra}>{escape(str(content))}</text>')


def _frame(width, height, title, y_title, x_title, left):
    """Margins of a plot area and the text around it."""
    top = 8 + (18 if title else 0) + (16 if y_title else 0)
    bottom = 24 + (16 if x_title else 0)
    parts = []
    if title:
        parts.append(text(0, 14, title, 12, "ink", weight=600))
    if y_title:
        parts.append(text(0, top - 8, y_title, 11, "muted"))
    if x_title:
        parts.append(text(left + (width - left - 8) / 2, height - 4, x_title, 11, "muted",
                          "middle"))
    return top, bottom, parts


def _y_axis(ticks, y_fmt, left, right, y_of):
    parts = []
    for tick in ticks:
        y = y_of(tick)
        parts.append(f'<line x1="{num(left)}" x2="{num(right)}" y1="{num(y)}" y2="{num(y)}" '
                     f'class="{"axis" if tick == 0 else "grid"}"/>')
        parts.append(text(left - 6, y + 4, y_fmt(tick), 11, "muted", "end"))
    return parts


def _left_margin(ticks, y_fmt):
    return 10 + max(text_width(y_fmt(tick), 11) for tick in ticks)


def line_panel(x_labels, series, width=720, height=320, title=None, y_title=None,
               x_title=None, y_fmt=str, y_max=None, end_labels=True, x_every=1,
               reference=None):
    """Lines over evenly spaced x positions."""
    top_value = y_max or max((v for _, _, values in series for v in values if v is not None),
                             default=1)
    if reference:
        top_value = max(top_value, reference)
    ticks = nice_ticks(top_value)
    left = _left_margin(ticks, y_fmt)
    label_room = 0
    if end_labels:
        label_room = 8 + max(text_width(name, 11) for name, _, _ in series)
    top, bottom, parts = _frame(width, height, title, y_title, x_title, left)
    right = width - 10 - label_room
    plot_height = height - top - bottom

    def y_of(value):
        return top + plot_height * (1 - value / ticks[-1])

    def x_of(index):
        if len(x_labels) == 1:
            return (left + right) / 2
        return left + 12 + (right - left - 24) * index / (len(x_labels) - 1)

    parts += _y_axis(ticks, y_fmt, left, right, y_of)
    for index, label in enumerate(x_labels):
        if index % x_every == 0:
            parts.append(text(x_of(index), height - bottom + 16, label, 11, "muted", "middle"))
    if reference:
        y = y_of(reference)
        parts.append(f'<line x1="{num(left)}" x2="{num(right)}" y1="{num(y)}" y2="{num(y)}" '
                     'class="mark"/>')
    ends = []
    for name, colour, values in series:
        points = [(x_of(i), y_of(v)) for i, v in enumerate(values) if v is not None]
        if not points:
            continue
        path, pen_down = [], False
        for i, v in enumerate(values):
            if v is None:
                pen_down = False
                continue
            path.append(f'{"L" if pen_down else "M"}{num(x_of(i))},{num(y_of(v))}')
            pen_down = True
        parts.append(f'<path d="{" ".join(path)}" class="k-{colour}"/>')
        for i, v in enumerate(values):
            if v is not None:
                parts.append(f'<circle cx="{num(x_of(i))}" cy="{num(y_of(v))}" r="3.5" '
                             f'class="f-{colour} ring"><title>{escape(name)}, '
                             f'{escape(str(x_labels[i]))}: {escape(y_fmt(v))}</title></circle>')
        ends.append((points[-1][1], points[-1][0], name))
    if end_labels and ends:
        ordered = sorted(ends)
        if all(b[0] - a[0] >= 12 for a, b in zip(ordered, ordered[1:])):
            for y, x, name in ends:
                parts.append(text(x + 8, y + 4, name, 11, "ink2"))
    return Panel(width, height, "".join(parts))


def _column(x, y, width, base, colour, tip):
    """A column from the baseline `base` up to y, rounded at the top."""
    height = base - y
    radius = min(4, width / 2, max(height, 0))
    if height <= 0.05:
        return ""
    if radius < 1:
        shape = (f'<rect x="{num(x)}" y="{num(y)}" width="{num(width)}" '
                 f'height="{num(height)}" class="f-{colour}">')
        return shape + f"<title>{escape(tip)}</title></rect>"
    d = (f"M{num(x)},{num(base)} V{num(y + radius)} Q{num(x)},{num(y)} {num(x + radius)},{num(y)} "
         f"H{num(x + width - radius)} Q{num(x + width)},{num(y)} {num(x + width)},{num(y + radius)} "
         f"V{num(base)} Z")
    return f'<path d="{d}" class="f-{colour}"><title>{escape(tip)}</title></path>'


def bar_panel(groups, series, width=720, height=320, title=None, y_title=None, x_title=None,
              y_fmt=str, y_max=None, reference=None, value_labels=False):
    """Grouped columns: one group per entry of `groups`, one column per
    series."""
    top_value = y_max or max((v for _, _, values in series for v in values if v is not None),
                             default=1)
    if reference:
        top_value = max(top_value, reference)
    ticks = nice_ticks(top_value)
    left = _left_margin(ticks, y_fmt)
    top, bottom, parts = _frame(width, height, title, y_title, x_title, left)
    right = width - 8
    plot_height = height - top - bottom

    def y_of(value):
        return top + plot_height * (1 - min(value, ticks[-1]) / ticks[-1])

    parts += _y_axis(ticks, y_fmt, left, right, y_of)
    slot = (right - left) / len(groups)
    gap = 2
    labels = []                             # drawn last, over the reference line
    bar = min(24, (slot * 0.8 - gap * (len(series) - 1)) / len(series))
    block = bar * len(series) + gap * (len(series) - 1)
    for g, group in enumerate(groups):
        x0 = left + slot * g + (slot - block) / 2
        for s, (name, colour, values) in enumerate(series):
            value = values[g]
            if value is None:
                continue
            x = x0 + s * (bar + gap)
            parts.append(_column(x, y_of(value), bar, y_of(0), colour,
                                 f"{name}, {group}: {y_fmt(value)}"))
            if value_labels:
                labels.append(text(x + bar / 2, y_of(value) - 4, y_fmt(value), 10, "ink2",
                                   "middle", halo=True))
        parts.append(text(left + slot * (g + 0.5), height - bottom + 16, group, 11, "muted",
                          "middle"))
    if reference:
        y = y_of(reference)
        parts.append(f'<line x1="{num(left)}" x2="{num(right)}" y1="{num(y)}" y2="{num(y)}" '
                     'class="mark"/>')
    return Panel(width, height, "".join(parts + labels))


def hstack_panel(rows, series, width=720, title=None, x_fmt=str, x_max=None, x_title=None,
                 row_height=22):
    """Stacked horizontal bars: one bar per row, one segment per series, the
    first series at the left."""
    totals = [sum(values[r] or 0 for _, _, values in series) for r in range(len(rows))]
    ticks = nice_ticks(x_max or max(totals + [1e-9]))
    left = 10 + max(text_width(row, 11) for row in rows)
    right = width - 16
    top = 8 + (18 if title else 0)
    base = top + row_height * len(rows)
    height = base + 24 + (16 if x_title else 0)
    parts = [text(0, 14, title, 12, "ink", weight=600)] if title else []

    def x_of(value):
        return left + (right - left) * value / ticks[-1]

    for tick in ticks:
        x = x_of(tick)
        parts.append(f'<line x1="{num(x)}" x2="{num(x)}" y1="{num(top)}" y2="{num(base)}" '
                     f'class="{"axis" if tick == 0 else "grid"}"/>')
        parts.append(text(x, base + 16, x_fmt(tick), 11, "muted", "middle"))
    if x_title:
        parts.append(text((left + right) / 2, height - 4, x_title, 11, "muted", "middle"))
    bar = min(14, row_height - 6)
    for r, row in enumerate(rows):
        y = top + row_height * r + (row_height - bar) / 2
        parts.append(text(left - 8, y + bar / 2 + 4, row, 11, "ink2", "end"))
        present = [(name, colour, values[r]) for name, colour, values in series if values[r]]
        before = 0
        for index, (name, colour, value) in enumerate(present):
            x_low, x_high = x_of(before), x_of(before + value)
            last = index == len(present) - 1
            if not last:
                x_high -= 2                 # the gap to the next segment
            tip = f"{name}, {row}: {x_fmt(value)}"
            if x_high - x_low > 0.05:
                radius = min(4, bar / 2, x_high - x_low) if last else 0
                if radius >= 1:
                    d = (f"M{num(x_low)},{num(y)} H{num(x_high - radius)} "
                         f"Q{num(x_high)},{num(y)} {num(x_high)},{num(y + radius)} "
                         f"V{num(y + bar - radius)} Q{num(x_high)},{num(y + bar)} "
                         f"{num(x_high - radius)},{num(y + bar)} H{num(x_low)} Z")
                    parts.append(f'<path d="{d}" class="f-{colour}">'
                                 f"<title>{escape(tip)}</title></path>")
                else:
                    parts.append(f'<rect x="{num(x_low)}" y="{num(y)}" '
                                 f'width="{num(x_high - x_low)}" height="{num(bar)}" '
                                 f'class="f-{colour}"><title>{escape(tip)}</title></rect>')
            before += value
    return Panel(width, height, "".join(parts))


def hbar_panel(rows, width=720, title=None, x_fmt=str, x_max=None, x_title=None,
               reference=None, row_height=30, tick_fmt=None):
    """Horizontal bars, one per row, each with its value at the tip."""
    top_value = x_max or max(row[2] for row in rows)
    ticks = nice_ticks(max(top_value, reference or 0))
    left = 10 + max(text_width(row[0], 12) for row in rows)
    tail = max(text_width(x_fmt(row[2]) + ("  " + row[3] if len(row) > 3 else ""), 11)
               for row in rows)
    right = width - 14 - tail
    top = 8 + (18 if title else 0)
    height = top + row_height * len(rows) + 24 + (16 if x_title else 0)
    parts = [text(0, 14, title, 12, "ink", weight=600)] if title else []

    def x_of(value):
        return left + (right - left) * value / ticks[-1]

    base = top + row_height * len(rows)
    for tick in ticks:
        x = x_of(tick)
        parts.append(f'<line x1="{num(x)}" x2="{num(x)}" y1="{num(top)}" y2="{num(base)}" '
                     f'class="{"axis" if tick == 0 else "grid"}"/>')
        parts.append(text(x, base + 16, (tick_fmt or x_fmt)(tick), 11, "muted", "middle"))
    if x_title:
        parts.append(text((left + right) / 2, height - 4, x_title, 11, "muted", "middle"))
    bar = min(18, row_height - 10)
    labels = []                             # drawn last, over the reference line
    for index, row in enumerate(rows):
        label, colour, value = row[:3]
        y = top + row_height * index + (row_height - bar) / 2
        x_end = x_of(value)
        radius = min(4, bar / 2, max(x_end - left, 0))
        d = (f"M{num(left)},{num(y)} H{num(x_end - radius)} Q{num(x_end)},{num(y)} "
             f"{num(x_end)},{num(y + radius)} V{num(y + bar - radius)} "
             f"Q{num(x_end)},{num(y + bar)} {num(x_end - radius)},{num(y + bar)} "
             f"H{num(left)} Z")
        parts.append(f'<path d="{d}" class="f-{colour}"><title>{escape(label)}: '
                     f"{escape(x_fmt(value))}</title></path>")
        parts.append(text(left - 8, y + bar / 2 + 4, label, 12, "ink2", "end"))
        labels.append(text(x_end + 6, y + bar / 2 + 4, x_fmt(value), 11, "ink", weight=600,
                           halo=True))
        if len(row) > 3:
            labels.append(text(x_end + 6 + text_width(x_fmt(value) + "  ", 11),
                               y + bar / 2 + 4, row[3], 11, "muted", halo=True))
    if reference:
        x = x_of(reference)
        parts.append(f'<line x1="{num(x)}" x2="{num(x)}" y1="{num(top)}" y2="{num(base)}" '
                     'class="mark"/>')
    return Panel(width, height, "".join(parts + labels))


def _style():
    def values(colours):
        return "".join(f"--{key}:{value};" for key, value in colours.items())

    rules = [f".chart{{{values(LIGHT)}font-family:{FONT}}}",
             f"@media (prefers-color-scheme:dark){{.chart{{{values(DARK)}}}}}",
             ".bg{fill:var(--surface)}",
             ".grid{stroke:var(--grid);stroke-width:1}",
             ".axis{stroke:var(--axis);stroke-width:1}",
             ".mark{stroke:var(--ink2);stroke-width:1}",
             ".ring{stroke:var(--surface);stroke-width:2}",
             # Diagrams: boxes, the boxes a drawing is about, regions, arrows.
             ".box{fill:var(--box);stroke:var(--axis);stroke-width:1}",
             ".box-a{fill:var(--tint);stroke:var(--s1);stroke-width:1.5}",
             ".band{fill:var(--band);stroke:var(--grid);stroke-width:1}",
             ".wire{stroke:var(--ink2);stroke-width:1.5;fill:none;stroke-linejoin:round}",
             ".bus{stroke:var(--s1);stroke-width:3;fill:none}",
             ".head{fill:var(--ink2)}", ".head-a{fill:var(--s1)}",
             ".halo{paint-order:stroke;stroke:var(--surface);stroke-width:4px;"
             "stroke-linejoin:round}"]
    for name in ("ink", "ink2", "muted"):
        rules.append(f".t-{name}{{fill:var(--{name})}}")
    for key in SERIES:
        rules.append(f".f-{key}{{fill:var(--{key})}}")
        rules.append(f".k-{key}{{stroke:var(--{key});stroke-width:2;fill:none;"
                     "stroke-linejoin:round;stroke-linecap:round}")
    return "<style>" + "".join(rules) + "</style>"


def figure(panels, cols=1, title="", subtitle="", legend=(), note="", gap=24, pad=20):
    """Arrange panels in a grid under a title, a subtitle and a legend."""
    rows = [panels[i:i + cols] for i in range(0, len(panels), cols)]
    column_width = max(panel.width for panel in panels)
    inner = column_width * min(cols, len(panels)) + gap * (min(cols, len(panels)) - 1)
    width = inner + 2 * pad
    parts, y = [], pad
    if title:
        parts.append(text(pad, y + 13, title, 15, "ink", weight=600))
        y += 24
    for line in ([subtitle] if isinstance(subtitle, str) else subtitle):
        if line:
            parts.append(text(pad, y + 11, line, 12, "ink2"))
            y += 17
    if legend:
        y += 6
        x = pad
        for name, colour, kind in legend:
            needed = 22 + text_width(name, 12) + 18
            if x + needed > width - pad and x > pad:
                x, y = pad, y + 18
            if kind == "line":
                parts.append(f'<path d="M{num(x)},{num(y + 7)} H{num(x + 16)}" '
                             f'class="k-{colour}"/>')
                parts.append(f'<circle cx="{num(x + 8)}" cy="{num(y + 7)}" r="3.5" '
                             f'class="f-{colour} ring"/>')
            else:
                parts.append(f'<rect x="{num(x + 2)}" y="{num(y + 1)}" width="12" height="12" '
                             f'rx="2" class="f-{colour}"/>')
            parts.append(text(x + 22, y + 11, name, 12, "ink2"))
            x += needed
        y += 20
    y += 8
    for row in rows:
        for index, panel in enumerate(row):
            x = pad + index * (column_width + gap)
            parts.append(f'<g transform="translate({num(x)},{num(y)})">{panel.body}</g>')
        y += max(panel.height for panel in row) + gap
    y -= gap
    if note:
        y += 18
        parts.append(text(pad, y, note, 11, "muted"))
    height = y + pad
    label = escape(title or "chart")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {num(width)} {num(height)}" '
            f'width="{num(width)}" height="{num(height)}" class="chart" role="img" '
            f'aria-label="{label}">{_style()}'
            f'<rect width="100%" height="100%" rx="8" class="bg"/>{"".join(parts)}</svg>\n')


def save(path, svg):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg)
