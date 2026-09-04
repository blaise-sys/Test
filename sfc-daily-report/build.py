#!/usr/bin/env python3
"""Build the SF Compute "Daily Signups and Spend Report" from payload.json.

Emits report.html and report_inline.html, then self-checks the output the way
the original run.sh did: size, no surviving <style> block, and no font:0/0
shorthand (Gmail discards it, so every chart bar must carry height, line-height
and font-size as longhand).

Exit non-zero on any failed check -- callers must not email a failed build.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# --- design tokens, recovered verbatim from the 2026-09-04 sent report --------
INK = "#0b0b0b"
SECOND = "#52514e"
MUTED = "#898781"
RULE = "#c3c2b7"
BORDER = "#e1e0d9"
TRACK = "#f4f4f1"
SPEND = "#2a78d6"
FUNDING = "#eb6834"
GREEN = "#1baf7a"
UP = "#0ca30c"
DOWN = "#d03b3b"

CHART_MAX_PX = 129
CARD = f"border:1px solid {BORDER};border-radius:10px;margin:0 0 16px"
CARD_PAD = "padding:18px 20px"
H_TITLE = f"font:600 15px/1.3 Arial;color:{INK};letter-spacing:-.01em;margin:0 0 2px"
H_SUB = f"font:400 12px/1.45 Arial;color:{MUTED};margin:0 0 14px"
TH = (f"font:600 10px/1.3 Arial;color:{MUTED};text-transform:uppercase;"
      f"letter-spacing:.06em;border-bottom:1px solid {RULE};white-space:nowrap")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def usd(v):
    """Compact form used in tiles and trend cells: $7,621 / $13.2k / $1.07M."""
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}${a/1e6:.2f}M"
    if a >= 10_000:
        s = f"{a/1e3:.1f}".rstrip("0").rstrip(".")
        return f"{sign}${s}k"
    return f"{sign}${a:,.0f}"


def usd_exact(v):
    """Cent-accurate form used in the ranked spender/funder lists."""
    v = float(v)
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def card(title, sub, body):
    return (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="{CARD}">'
            f'<tr><td style="{CARD_PAD}">'
            f'<div style="{H_TITLE}">{esc(title)}</div>'
            f'<div style="{H_SUB}">{esc(sub)}</div>{body}</td></tr></table>')


def tile(label, value, sub, accent):
    return (f'<td width="25%" valign="top" style="padding:0 6px">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {BORDER};border-radius:10px"><tr>'
            f'<td style="padding:14px 14px 13px;border-top:3px solid {accent};'
            f'border-radius:10px 10px 0 0">'
            f'<div style="font:600 10px/1.3 Arial;color:{MUTED};text-transform:uppercase;'
            f'letter-spacing:.07em">{esc(label)}</div>'
            f'<div style="font:600 25px/1.15 Arial;color:{INK};margin-top:7px;'
            f'letter-spacing:-.02em">{esc(value)}</div>'
            f'<div style="font:400 11px/1.4 Arial;color:{MUTED};margin-top:4px">{esc(sub)}</div>'
            f'</td></tr></table></td>')


def bar(px, color, capped):
    """A chart bar. height, line-height AND font-size in longhand, on purpose:
    Gmail drops the font:0/0 shorthand and strips height attributes off <td>."""
    radius = ";border-radius:3px 3px 0 0" if capped else ""
    return (f'<div style="height:{px}px;line-height:{px}px;font-size:1px;'
            f'background-color:{color}{radius}"></div>')


def build_chart(series):
    """31 paired day columns. Axis capped so ordinary days stay readable."""
    vals = [v for d in series for v in (d.get("spend", 0), d.get("funding", 0)) if v > 0]
    if not vals:
        return "<div></div>", None
    ordered = sorted(vals)
    cap = ordered[max(0, int(len(ordered) * 0.90) - 1)]
    over = []
    for d in series:
        for kind in ("spend", "funding"):
            if d.get(kind, 0) > cap:
                over.append((d["day"], kind, d[kind]))
    over.sort(key=lambda x: -x[2])

    cols = []
    for d in series:
        cells = []
        for kind, color in (("spend", SPEND), ("funding", FUNDING)):
            v = max(0.0, float(d.get(kind, 0)))
            px = min(CHART_MAX_PX, int(round(v / cap * CHART_MAX_PX))) if cap else 0
            if v > 0:
                px = max(px, 3)
            capped = v > cap
            inner = bar(px, color, capped) if px else (
                f'<div style="height:3px;line-height:3px;font-size:1px;'
                f'background-color:{TRACK}"></div>')
            cells.append(
                f'<td valign="bottom" style="font-size:0;line-height:0">{inner}</td>'
                f'<td width="3" style="font-size:0;line-height:0"></td>')
        cols.append("".join(cells))

    grid = (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse"><tr valign="bottom">'
            f'{"".join(cols)}</tr></table>')

    labels = []
    for i, d in enumerate(series):
        labels.append(f'<td style="font:400 9px/1.3 Arial;color:{MUTED};text-align:center">'
                      f'{esc(d["day"]) if i % 3 == 0 else ""}</td>')
    axis = (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-top:6px"><tr>{"".join(labels)}</tr></table>')

    legend = (
        f'<div style="margin:0 0 10px">'
        f'<span style="display:inline-block;margin-right:16px;font:400 11px/1.4 Arial;'
        f'color:{SECOND};white-space:nowrap"><span style="display:inline-block;width:9px;'
        f'height:9px;border-radius:2px;vertical-align:middle;margin-right:5px;'
        f'background-color:{SPEND}"></span>Spend</span>'
        f'<span style="display:inline-block;margin-right:16px;font:400 11px/1.4 Arial;'
        f'color:{SECOND};white-space:nowrap"><span style="display:inline-block;width:9px;'
        f'height:9px;border-radius:2px;vertical-align:middle;margin-right:5px;'
        f'background-color:{FUNDING}"></span>Funding</span></div>')

    note = ""
    if over:
        shown = "; ".join(f"{d} {k} {usd(v)}" for d, k, v in over[:3])
        more = f" (+{len(over)-3} more)" if len(over) > 3 else ""
        note = (f'<div style="font:400 10px/1.45 Arial;color:{MUTED};margin:0 0 8px">'
                f'Axis capped at {usd(cap)}/day so ordinary days stay readable. '
                f'{len(over)} bar(s) run past the top &mdash; {esc(shown)}{esc(more)}.</div>')
    return legend + note + grid + axis, cap


def build_trends(rows):
    out = [f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
           f'style="border-collapse:collapse"><tr>'
           f'<th align="left" style="{TH}">Period</th>'
           f'<th align="right" style="{TH}">Prior</th>'
           f'<th align="right" style="{TH}">Current</th>'
           f'<th align="right" style="{TH}">Change</th></tr>']
    for r in rows:
        pct = r.get("change_pct")
        if pct is None:
            chg, col = "&mdash;", MUTED
        else:
            arrow = "&#9650; +" if pct >= 0 else "&#9660; "
            chg, col = f"{arrow}{pct:.1f}%", (UP if pct >= 0 else DOWN)
        out.append(
            f'<tr><td style="font:600 12px/1.4 Arial;color:{INK};padding:11px 8px 11px 0;'
            f'border-bottom:1px solid {BORDER};vertical-align:top">{esc(r["label"])}'
            f'<div style="font:400 11px/1.4 Arial;color:{MUTED};margin-top:3px">'
            f'{esc(r.get("sublabel",""))}</div></td>'
            f'<td align="right" style="font:400 12px/1.4 Arial;color:{SECOND};'
            f'padding:11px 0;border-bottom:1px solid {BORDER};vertical-align:top;'
            f'white-space:nowrap">{usd(r["prior"])}</td>'
            f'<td align="right" style="font:600 12px/1.4 Arial;color:{INK};'
            f'padding:11px 0;border-bottom:1px solid {BORDER};vertical-align:top;'
            f'white-space:nowrap">{usd(r["current"])}</td>'
            f'<td align="right" style="font:600 12px/1.4 Arial;color:{col};'
            f'padding:11px 0 11px 8px;border-bottom:1px solid {BORDER};vertical-align:top;'
            f'white-space:nowrap">{chg}</td></tr>')
    out.append("</table>")
    return "\n".join(out)


def build_ranked(rows):
    out = [f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
           f'style="border-collapse:collapse">']
    for r in rows:
        tag = ""
        if r.get("internal"):
            tag = (f'<span style="font:600 9px/1.3 Arial;color:{MUTED};'
                   f'text-transform:uppercase;letter-spacing:.06em;border:1px solid {BORDER};'
                   f'border-radius:3px;padding:1px 4px;margin-left:6px">internal</span>')
        out.append(
            f'<tr><td style="font:600 12px/1.4 Arial;color:{INK};padding:9px 8px 9px 0;'
            f'border-bottom:1px solid {BORDER}">{esc(r["name"])}{tag}'
            f'<div style="font:400 11px/1.4 Arial;color:{MUTED};margin-top:2px">'
            f'{esc(r.get("detail",""))}</div></td>'
            f'<td align="right" style="font:600 12px/1.4 Arial;color:{INK};padding:9px 0;'
            f'border-bottom:1px solid {BORDER};white-space:nowrap;vertical-align:top">'
            f'{usd_exact(r["amount"])}</td></tr>')
    out.append("</table>")
    return "\n".join(out)


def build_signups(rows):
    out = [f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
           f'style="border-collapse:collapse"><tr>'
           f'<th align="left" style="{TH}">Person</th>'
           f'<th align="left" style="{TH}">Company / Account</th>'
           f'<th align="left" style="{TH}">Type &amp; product</th>'
           f'<th align="right" style="{TH}">Time (UTC)</th></tr>']
    for r in rows:
        person = r.get("person") or "--"
        out.append(
            f'<tr><td style="font:400 12px/1.4 Arial;color:{INK};padding:9px 8px 9px 0;'
            f'border-bottom:1px solid {BORDER};vertical-align:top">{esc(person)}'
            f'<div style="font:400 11px/1.4 Arial;color:{MUTED};margin-top:2px">'
            f'{esc(r.get("email",""))}</div></td>'
            f'<td style="font:400 12px/1.4 Arial;color:{SECOND};padding:9px 8px;'
            f'border-bottom:1px solid {BORDER};vertical-align:top">'
            f'{esc(r.get("company",""))}</td>'
            f'<td style="font:400 12px/1.4 Arial;color:{SECOND};padding:9px 8px;'
            f'border-bottom:1px solid {BORDER};vertical-align:top">{esc(r.get("type",""))}'
            f'<div style="font:400 11px/1.4 Arial;color:{MUTED};margin-top:2px">'
            f'{esc(r.get("product",""))}</div></td>'
            f'<td align="right" style="font:400 11px/1.4 Arial;color:{MUTED};padding:9px 0;'
            f'border-bottom:1px solid {BORDER};vertical-align:top;white-space:nowrap">'
            f'{esc(r.get("time",""))}</td></tr>')
    out.append("</table>")
    return "\n".join(out)


def render(p):
    net = float(p["spent_usd"]) * -1 + float(p["funded_usd"])
    parts = []
    parts.append(
        f'<div style="margin:0;padding:0"><table width="100%" cellpadding="0" '
        f'cellspacing="0" border="0" style="margin:0;padding:0"><tr>'
        f'<td align="center" style="padding:22px 12px">'
        f'<table width="720" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:720px"><tr><td>')

    parts.append(
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 18px"><tr><td>'
        f'<div style="font:600 21px/1.25 Arial;color:{INK};letter-spacing:-.02em">'
        f'Daily Signups &amp; Spend</div>'
        f'<div style="font:400 12px/1.5 Arial;color:{MUTED};margin-top:4px">'
        f'{esc(p["date"])} &middot; 24h to {esc(p["window_end_utc"])} &middot; '
        f'all figures USD</div></td></tr></table>')

    parts.append(
        '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        + tile("New signups", str(p["signups_total"]),
               f'{p["signups_on_demand"]} On Demand · {p["signups_gman"]} Givemeanode', GREEN)
        + tile("Funded", usd(p["funded_usd"]), f'{p["funded_accts"]} account(s)', FUNDING)
        + tile("Spent", usd(p["spent_usd"]), f'{p["spent_accts"]} account(s)', SPEND)
        + tile("Net", usd(abs(net)), "net inflow" if net >= 0 else "net outflow", GREEN)
        + '</tr></table><div style="height:16px;font-size:0;line-height:0"></div>')

    chart, _ = build_chart(p.get("chart", []))
    parts.append(card("Daily spend vs. account funding", "Last 31 days, paired per day.", chart))
    parts.append(card("Spend trends", "Day over day, week over week, month over month.",
                      build_trends(p.get("trends", []))))
    parts.append(card(f'New signups ({p["signups_total"]})',
                      "Across On Demand and Givemeanode.",
                      build_signups(p.get("signups", []))))
    parts.append(card("Spend by account", "Who spent, ranked.",
                      build_ranked(p.get("spenders", []))))
    parts.append(card("Funding by account", "Deposits and credit top-ups, ranked.",
                      build_ranked(p.get("funders", []))))

    g = p.get("gman", {})
    gman = ('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            + "".join(
                f'<td width="33%" valign="top" style="padding:0 6px 0 0">'
                f'<div style="font:600 10px/1.3 Arial;color:{MUTED};text-transform:uppercase;'
                f'letter-spacing:.07em">{esc(lbl)}</div>'
                f'<div style="font:600 19px/1.2 Arial;color:{INK};margin-top:5px">'
                f'{usd(g.get(key,0))}</div></td>'
                for lbl, key in (("MTD revenue", "mtd_revenue"),
                                 ("Jobs revenue", "jobs_revenue"),
                                 ("Resale recovery", "resale_recovery")))
            + '</tr></table>')
    parts.append(card("Givemeanode product line",
                      f'{p.get("gman_period","")} month-to-date, from the treasury P&L. '
                      f'Figures are micro-dollars ÷ 1e6.', gman))

    notes = "".join(f'<li style="margin:0 0 5px">{n}</li>' for n in p.get("notes", []))
    parts.append(
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="{CARD}">'
        f'<tr><td style="{CARD_PAD}">'
        f'<div style="{H_TITLE}">Method &amp; caveats</div>'
        f'<ul style="font:400 11px/1.6 Arial;color:{SECOND};margin:10px 0 0;padding-left:16px">'
        f'{notes}</ul></td></tr></table>')

    parts.append(
        f'<div style="font:400 10px/1.5 Arial;color:{MUTED};text-align:center;'
        f'padding:18px 0 6px">Generated automatically from the SF Compute platform ledger, '
        f'the Givemeanode audit log and treasury P&amp;L.</div>'
        f'</td></tr></table></td></tr></table></div>')
    return "\n".join(parts)


def selfcheck(html):
    errs = []
    if "<style" in html.lower():
        errs.append("a <style> block survived; Gmail strips it")
    if re.search(r"font:\s*0/0", html):
        errs.append("font:0/0 shorthand survived; Gmail discards it "
                    "(needs font-size/line-height longhand)")
    n = len(html.encode())
    if n > 102_400:
        errs.append(f"{n} bytes exceeds Gmail's 102KB clip threshold")
    if n < 10_000:
        errs.append(f"{n} bytes is implausibly small; the build likely lost sections")
    for m in re.finditer(r'<div style="height:(\d+)px;line-height:(\d+)px;font-size:(\d+)px', html):
        if m.group(1) != m.group(2):
            errs.append("a chart bar has height != line-height")
            break
    return errs, n


def main():
    path = os.path.join(HERE, "payload.json")
    with open(path) as f:
        payload = json.load(f)
    html = render(payload)
    # The generator emits inline styles directly, so report.html and the inlined
    # form are identical; both are written to keep the original two-file contract.
    with open(os.path.join(HERE, "report.html"), "w") as f:
        f.write(html)
    with open(os.path.join(HERE, "report_inline.html"), "w") as f:
        f.write(html)
    errs, n = selfcheck(html)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"OK: report_inline.html {n} bytes, "
          f"{html.count('background-color:'+SPEND)} spend bars, "
          f"{html.count('background-color:'+FUNDING)} funding bars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
