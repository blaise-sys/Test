# SF Compute — Daily Signups and Spend Report

Runs unattended in the cloud every morning and emails the report to
**blaise@, emily@, montana@sfcompute.com** (all three on the To line).
Subject is exactly `Daily Signups and Spend Report` — no date appended.

## Provenance

The original runbook and `run.sh` lived on a laptop and were lost. This
directory is a reconstruction, rebuilt entirely from cloud sources:

- **Template + chart CSS** — extracted from the report Gmail actually sent on
  2026-09-04 (65,783 bytes, already inlined).
- **Ledger semantics + SQL** — reconstructed against the live database and
  then *validated by replaying sent reports*. See "Validation" below.

Nothing here depends on a laptop. The repo is the only durable state; each
scheduled run starts in a fresh container and clones it.

## Ledger semantics

Table `public.abel_denormalized_materialized_postings` (the Abel ledger).

| Column | Meaning |
|---|---|
| `credit_ud` | signed **micro-dollars** — divide by 1e6 |
| `code` | transaction code |
| `subaccount` | `main` or `order` |

- **SPEND** = `v0_credit_trade`, `subaccount='order'`, `credit_ud < 0` (buyer side).
- **FUNDING** = `v0_credit_stripe_purchase` **and** `manual`, `subaccount='main'`,
  `credit_ud > 0`. `manual` is manual credit grants — **do not drop it**: it is
  ~$1.04M across 11 accounts in a typical 30 days, and omitting it silently
  understates funding on any day one lands.
- **Settlement accounts excluded from funding**: `sfc_stripe`, `sfc_cash`,
  `sfc_market_ops`.
- **Internal accounts**: `sfcompute`, `give_me_a_node`. These are **shown and
  tagged `internal`, never hidden**, so the net total reconciles. The
  external-only totals go in `notes[]` because the headline net is often
  majority-internal.

Traps:
- `v0_credit_trade` with `credit_ud > 0` on `main` is the **seller** side, not
  funding. `v0_buy_order_reserve` / `_release` are escrow, not funding.
- `transaction_view` looks like the ledger but is empty for recent dates. Wrong table.
- Signup names are in **`user_pii`**, joined via `account_memberships.user_id`.
  Not `account_pii` (that's the org) and not `clerk_users` (empty for these).
  The email is in **`user_identities.email`**.

## Validation

The queries were replayed against two already-sent reports:

| | Reconstructed | Sent report |
|---|---|---|
| Sep 3 funding | $9,300.00 / 5 accts | $9,300 / 5 ✓ |
| Sep 4 funding | $13,250.00 / 6 accts | $13,250 / 6 ✓ |
| Sep 4 spend | 11 accts, every account to the cent | 11 accts ✓ |
| Spend DoD | 7881.00 → 5972.27 | $7,881 → $5,972 ✓ |
| Funding DoD | 7300.00 → 11750.00 | $7,300 → $11,750 ✓ |
| 08-18 outlier | 1,072,511.65 | $1.07M ✓ |

Only `sfcompute` drifts, by ~$2: it posts continuously (~3,500 postings/day),
so a window label rounded to the minute moves it. Not a semantic error.

## Daily steps

1. **Postgres** — run `payload_query.sql` through the Postgres MCP. It returns
   the whole Postgres half as **one JSON value**. Write it verbatim to
   `pg.json`. Do not retype figures by hand.
   - It must stay a single grouped scan. A correlated subquery per day is
     62 scans and the MCP call times out at 30s.
2. **Givemeanode** — `audit_search(action="org.create", since="24h", limit=100)`
   and `market_pnl()` for the current month. Write
   `{"pnl": <market_pnl>, "orgs": <items>}` to `gman.json`.
   - `market_pnl` returns **micro-dollars**. `assemble.py` divides by 1e6.
     A previous run reported these 1000× too high.
3. **Assemble** — `python3 assemble.py pg.json gman.json`. Drops
   `@sfcompute.com`, `tmano66@*` and `*test*` orgs, dedupes by email, builds
   `signups[]` as ONE list across both products newest-first (`MM-DD HH:MM`),
   computes trends, and writes the standing caveats.
4. **Build** — `./run.sh`. Self-checks size, surviving `<style>`, `font:0/0`,
   and `height == line-height` on every bar. **Non-zero exit means DO NOT EMAIL** —
   report the failure instead.
5. **Send** — read `report_inline.html` and send via Gmail. Never send
   `report.html`.

## The chart CSS is load-bearing

Each bar is a `<div>` setting `height`, `line-height` **and** `font-size` in
longhand:

```html
<div style="height:129px;line-height:129px;font-size:1px;background-color:#2a78d6"></div>
```

Gmail discards the `font:0/0` shorthand and strips `height` attributes off
`<td>`, which is why the height lives on a `div` and why the redundancy stays.
Do not "tidy" this. Palette: spend `#2a78d6`, funding `#eb6834`, track
`#f4f4f1`, max bar 129px.

## Accuracy rules

- Never invent or estimate a number. Every figure comes from a query result.
- Keep the standing `notes[]`: inferred company type, internal accounts shown
  and tagged, outlier days distorting month-over-month, and Givemeanode
  per-customer spend being unavailable from the platform ledger.
- Report external-only spend/funding totals in `notes[]`.

## Scheduling

Routine `trig_01J3Kp7vkEA7D9GG4qrH45ab`, cron `50 15 * * *` (UTC), fresh
session per fire, push + email notifications on.

**Timezone.** Cron is UTC and does not follow DST. `15:50 UTC` is 08:50 **PDT**.
When the US falls back to PST (early November), change the cron to `50 16 * * *`
or the report starts landing at 07:50 local. Changing it back in March.

**Connectors — the thing most likely to break this.** A fired session only has
`mcp__*` tools if the Routine itself carries the connector grants. The
`connectors` parameter on `create_trigger` is disabled for this org, and grants
do not pass through from the creating session, so a Routine minted from an
agent session fires with **no Postgres, no Givemeanode and no Gmail** and cannot
do any of the work. Fix it by attaching the three connectors to the Routine in
the claude.ai Routines UI (Postgres MCP, Givemeanode, Gmail). Verify by firing
the Routine once and checking that it reports all three tools present.

## Known gaps

- `names.json` is a display-name map for accounts with no
  `account_pii.business_name` (`spl`, `tear_labs`). A new account with no PII
  shows its raw account id until a line is added. Cosmetic, never numeric.
- The `*test*` filter does not catch every internal probe account
  (e.g. `probe_full_ui`). Add patterns to `assemble.py` as they appear.
