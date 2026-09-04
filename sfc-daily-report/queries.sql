-- SF Compute Daily Signups and Spend Report -- verified SQL.
--
-- Ledger: public.abel_denormalized_materialized_postings ("the Abel ledger").
--   credit_ud   signed MICRO-dollars. Divide by 1e6 for USD.
--   code        transaction code.
--   subaccount  'main' | 'order'.
--
-- SPEND   = v0_credit_trade, subaccount 'order', credit_ud < 0 (buyer side).
-- FUNDING = v0_credit_stripe_purchase + manual, subaccount 'main', credit_ud > 0.
-- Settlement accounts excluded from funding: sfc_stripe, sfc_cash, sfc_market_ops.
-- Internal accounts, SHOWN and TAGGED (never hidden): sfcompute, give_me_a_node.
--
-- Validated by replaying the 2026-09-03 and 2026-09-04 sent reports: funding
-- matched to the penny on both; every per-account spend figure matched to the
-- cent. Only 'sfcompute' drifts by ~$2 because it posts continuously (3,460
-- postings/day) and the report's window label is rounded to the minute.

-- :start and :end bound a rolling 24h window. Substitute literals.

-- 1. SPEND BY ACCOUNT ---------------------------------------------------------
SELECT p.account_id,
       COALESCE(NULLIF(pii.business_name, ''),
                NULLIF(TRIM(COALESCE(pii.first_name,'') || ' ' || COALESCE(pii.last_name,'')), ''),
                p.account_id)                        AS display_name,
       pii.primary_email,
       SUM(-p.credit_ud) / 1e6                       AS spend_usd,
       COUNT(*)                                      AS fills
FROM abel_denormalized_materialized_postings p
LEFT JOIN account_pii pii ON pii.account_id = p.account_id
WHERE p.code = 'v0_credit_trade'
  AND p.subaccount = 'order'
  AND p.credit_ud < 0
  AND p.effective_at > :start AND p.effective_at <= :end
GROUP BY 1, 2, 3
ORDER BY spend_usd DESC;

-- 2. FUNDING BY ACCOUNT -------------------------------------------------------
SELECT p.account_id,
       COALESCE(NULLIF(pii.business_name, ''),
                NULLIF(TRIM(COALESCE(pii.first_name,'') || ' ' || COALESCE(pii.last_name,'')), ''),
                p.account_id)                        AS display_name,
       pii.primary_email,
       SUM(p.credit_ud) / 1e6                        AS funded_usd,
       COUNT(*)                                      AS n,
       BOOL_OR(p.code = 'v0_credit_stripe_purchase') AS via_stripe,
       BOOL_OR(p.code = 'manual')                    AS via_manual_grant
FROM abel_denormalized_materialized_postings p
LEFT JOIN account_pii pii ON pii.account_id = p.account_id
WHERE p.code IN ('v0_credit_stripe_purchase', 'manual')
  AND p.subaccount = 'main'
  AND p.credit_ud > 0
  AND p.account_id NOT IN ('sfc_stripe', 'sfc_cash', 'sfc_market_ops')
  AND p.effective_at > :start AND p.effective_at <= :end
GROUP BY 1, 2, 3
ORDER BY funded_usd DESC;

-- 3. 30-DAY DAILY SERIES (chart) ----------------------------------------------
WITH days AS (
  SELECT generate_series(
           (NOW() AT TIME ZONE 'UTC')::date - INTERVAL '30 days',
           (NOW() AT TIME ZONE 'UTC')::date, INTERVAL '1 day')::date AS d
)
SELECT TO_CHAR(days.d, 'MM-DD') AS day,
       COALESCE((SELECT SUM(-credit_ud)/1e6 FROM abel_denormalized_materialized_postings
                 WHERE code='v0_credit_trade' AND subaccount='order' AND credit_ud<0
                   AND effective_at >= days.d AND effective_at < days.d + 1), 0) AS spend,
       COALESCE((SELECT SUM(credit_ud)/1e6 FROM abel_denormalized_materialized_postings
                 WHERE code IN ('v0_credit_stripe_purchase','manual') AND subaccount='main'
                   AND credit_ud>0
                   AND account_id NOT IN ('sfc_stripe','sfc_cash','sfc_market_ops')
                   AND effective_at >= days.d AND effective_at < days.d + 1), 0) AS funding
FROM days ORDER BY days.d;

-- 4. TREND BUCKETS ------------------------------------------------------------
-- Each bucket is the same aggregate over two windows. metric='spend' uses the
-- SPEND predicate; metric='funding' uses the FUNDING predicate.
--   Day over day   : yesterday          vs the day before
--   Week over week : last 7 days        vs the 7 before
--   Month to date  : month-to-date      vs the same days elapsed last month
-- On Sunday add a completed-week row; on the 1st add a completed-month row.
SELECT SUM(-credit_ud)/1e6 AS spend_usd
FROM abel_denormalized_materialized_postings
WHERE code='v0_credit_trade' AND subaccount='order' AND credit_ud<0
  AND effective_at >= :bucket_start AND effective_at < :bucket_end;

-- 5. ON DEMAND SIGNUPS (last 24h), joined to the account's first member -------
SELECT a.id                              AS account_id,
       a.created_at,
       a.entity_type,
       pii.business_name,
       TRIM(COALESCE(m_pii.first_name,'') || ' ' || COALESCE(m_pii.last_name,'')) AS person,
       COALESCE(m_pii.primary_email, pii.primary_email) AS email
FROM accounts a
LEFT JOIN account_pii pii ON pii.account_id = a.id
LEFT JOIN LATERAL (
    SELECT am.user_id FROM account_memberships am
    WHERE am.account_id = a.id AND am.deleted_at IS NULL
    ORDER BY am.created_at ASC LIMIT 1
) fm ON TRUE
LEFT JOIN account_pii m_pii ON m_pii.account_id = fm.user_id
WHERE a.created_at > :start AND a.created_at <= :end
  AND a.deleted_at IS NULL
ORDER BY a.created_at DESC;
