#!/usr/bin/env python3
"""Merge the Postgres payload and the Givemeanode payload into payload.json.

  python3 assemble.py pg.json gman.json

pg.json    the single JSON value returned by payload_query.sql.
gman.json  {"pnl": <market_pnl response>, "orgs": <audit_search items array>}

Every figure is copied through arithmetically -- nothing here invents a number.
The only judgement encoded is which orgs are internal/test (dropped) and the
standing caveats, which are fixed text.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = json.load(open(os.path.join(HERE, "names.json")))

INTERNAL = {"sfcompute", "give_me_a_node"}
# Givemeanode orgs that must never appear as signups.
DROP_EMAIL_EXACT = {"tmano66@pm.me", "tmano66@protonmail.com"}
FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "proton.me", "protonmail.com", "pm.me", "icloud.com", "me.com", "aol.com",
    "live.com", "msn.com", "qq.com", "163.com", "yandex.ru", "mail.ru",
    "yopmail.com", "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "tempmail.com", "trashmail.com", "sharklasers.com",
}
DISPOSABLE = {"yopmail.com", "mailinator.com", "guerrillamail.com",
              "10minutemail.com", "tempmail.com", "trashmail.com", "sharklasers.com"}


def pct(prior, current):
    prior, current = float(prior), float(current)
    if prior == 0:
        return None
    return (current - prior) / prior * 100.0


def nice(account_id, fallback):
    return NAMES.get(account_id) or fallback or account_id


def classify(email, org_name, org_of_one):
    dom = (email or "").split("@")[-1].lower()
    if dom.endswith(".edu") or dom.endswith(".ac.uk"):
        return "Academic"
    if not org_of_one and org_name:
        return "Company"
    if dom in FREE_MAIL:
        return "Individual"
    return "Company" if dom else "Individual"


def main():
    pg = json.load(open(sys.argv[1]))
    gm = json.load(open(sys.argv[2]))
    pnl, orgs = gm["pnl"], gm["orgs"]

    # --- Givemeanode signups: drop internal/test, dedupe by email -------------
    seen, gman_rows = set(), []
    for it in orgs:
        email = (it.get("actor_display") or "").strip().lower()
        details = it.get("details") or {}
        name = details.get("name") or ""
        if email.endswith("@sfcompute.com") or email in DROP_EMAIL_EXACT:
            continue
        if re.search(r"test", name, re.I):
            continue
        if email in seen:
            continue
        seen.add(email)
        org_of_one = bool(details.get("org_of_one"))
        ts = it.get("occurred_at", "")
        gman_rows.append({
            "person": None if org_of_one else name,
            "email": email,
            "company": "org of one" if org_of_one else name,
            "type": classify(email, name, org_of_one),
            "product": "Givemeanode",
            "time": f"{ts[5:7]}-{ts[8:10]} {ts[11:16]}",
            # Sort key must be the SAME shape for both products, or the two
            # lists never interleave: "2026-09-04T20:46" > "09-04 19:29" for
            # every row, so Givemeanode would always sort first.
            "_sort": f"{ts[5:7]}-{ts[8:10]} {ts[11:16]}",
        })

    od_rows = []
    for r in pg.get("signups_on_demand_rows") or []:
        r = dict(r)
        r["_sort"] = r.get("time", "")
        od_rows.append(r)

    signups = sorted(od_rows + gman_rows, key=lambda r: r["_sort"], reverse=True)
    for r in signups:
        r.pop("_sort", None)

    # --- trends ---------------------------------------------------------------
    t = pg["trends_raw"]
    trends = [
        ("Day over day — spend", "Yesterday vs the day before", t["s_dod_pri"], t["s_dod_cur"]),
        ("Week over week — spend", "Last 7 days vs the 7 before", t["s_wow_pri"], t["s_wow_cur"]),
        ("Day over day — funding", "Yesterday vs the day before", t["f_dod_pri"], t["f_dod_cur"]),
        ("Week over week — funding", "Last 7 days vs the 7 before", t["f_wow_pri"], t["f_wow_cur"]),
    ]
    trend_rows = [{"label": l, "sublabel": s, "prior": p, "current": c,
                   "change_pct": pct(p, c)} for l, s, p, c in trends]

    def rank(rows, funding=False):
        out = []
        for r in rows:
            bits = []
            if r.get("email"):
                bits.append(r["email"])
            if funding:
                if r.get("via_stripe"):
                    bits.append("Stripe")
                if r.get("via_manual"):
                    bits.append("manual grant")
            elif (r.get("fills") or 0) > 100:
                bits.append(f"{r['fills']:,} fills")
            out.append({"name": nice(r["account_id"], r.get("name")),
                        "internal": r["account_id"] in INTERNAL,
                        "detail": " · ".join(bits),
                        "amount": r["amount"]})
        return out

    spenders = rank(pg.get("spenders") or [])
    funders = rank(pg.get("funders") or [], funding=True)

    watch = []
    for r in signups:
        dom = (r.get("email") or "").split("@")[-1].lower()
        if dom in DISPOSABLE:
            watch.append(f"<code>{r['email']}</code> is a disposable-mailbox address")
    domains = {}
    for r in signups:
        dom = (r.get("email") or "").split("@")[-1].lower()
        if dom and dom not in FREE_MAIL:
            domains.setdefault(dom, []).append(r["email"])
    for dom, addrs in domains.items():
        if len(addrs) > 1:
            watch.append(f"{', '.join(f'<code>{a}</code>' for a in addrs)} are "
                         f"separate orgs on the same domain, likely one person")

    ext = pg["external"]
    notes = []
    if watch:
        notes.append("<b>Watch list:</b> " + "; ".join(watch) +
                     " &mdash; all are counted in the totals above.")
    notes.append(
        "<b>Signups</b> &mdash; On Demand from <code>public.accounts</code> joined to the "
        "account's first member; Givemeanode from <code>org.create</code> in the platform "
        "audit log. Orgs of one carry no separate name, so the person shows as "
        "&ldquo;--&rdquo; and the email is the identifier. Internal "
        "<code>@sfcompute.com</code> signups and test orgs are excluded. The window is a "
        "rolling 24h, so the last few rows also appeared in yesterday's report.")
    notes.append(
        "<b>Company type</b> is inferred: an explicit business name wins, then a corporate "
        "email domain, then the account's own <code>entity_type</code>. Free-mail and "
        "disposable domains classify as Individual, <code>.edu</code> / <code>.ac.uk</code> "
        "as Academic.")
    notes.append(
        "<b>Spend</b> is the buyer side of filled market trades "
        "(<code>v0_credit_trade</code>, order subaccount) in the Abel ledger. "
        "<b>Funding</b> is Stripe credit purchases plus manual credit grants, excluding the "
        "internal <code>sfc_stripe</code>, <code>sfc_cash</code> and "
        "<code>sfc_market_ops</code> settlement accounts.")
    notes.append(
        f"Accounts tagged <b>internal</b> are SF Compute's own "
        f"(<code>sfcompute</code>, <code>give_me_a_node</code>). They are shown rather than "
        f"hidden so the net total reconciles. Excluding them, customers spent "
        f"<b>${ext['spent']:,.0f}</b> and funded <b>${ext['funded']:,.0f}</b>.")
    notes.append(
        "The 30-day chart is dominated by two outlier days &mdash; 18 Aug ($1.07M spend) and "
        "12 Aug ($957k funding), both single large market trades. Month-over-month "
        "percentages against August are distorted by them.")
    notes.append(
        "Givemeanode per-customer spend is not exposed by the platform ledger &mdash; its "
        "usage rolls up into the single <code>give_me_a_node</code> account. Product-level "
        "revenue above comes from the treasury P&amp;L.")

    payload = {
        "date": pg["date"],
        "window_end_utc": pg["window_end_utc"],
        "gman_period": pg["gman_period"],
        "signups_total": len(signups),
        "signups_on_demand": len(od_rows),
        "signups_gman": len(gman_rows),
        "signups": signups,
        "funded_usd": pg["funded_usd"],
        "funded_accts": pg["funded_accts"],
        "spent_usd": pg["spent_usd"],
        "spent_accts": pg["spent_accts"],
        "external": ext,
        "chart": pg["chart"],
        "trends": trend_rows,
        "spenders": spenders,
        "funders": funders,
        # market_pnl returns MICRO-dollars. Divide by 1e6. A previous run
        # reported these 1000x too high.
        "gman": {
            "mtd_revenue": pnl["revenue_usd_micros"] / 1e6,
            "jobs_revenue": pnl["job_revenue_usd_micros"] / 1e6,
            "resale_recovery": pnl["resale_recovery_usd_micros"] / 1e6,
        },
        "notes": notes,
    }
    with open(os.path.join(HERE, "payload.json"), "w") as f:
        json.dump(payload, f, indent=1)
    print(f"payload.json: {len(signups)} signups "
          f"({len(od_rows)} On Demand + {len(gman_rows)} Givemeanode), "
          f"{len(spenders)} spenders, {len(funders)} funders, "
          f"gman MTD ${payload['gman']['mtd_revenue']:,.2f}")


if __name__ == "__main__":
    sys.exit(main())
