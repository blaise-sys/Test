-- Returns the ENTIRE Postgres half of payload.json as one JSON value.
-- Run it through the Postgres MCP and write the single returned value straight
-- to payload.json -- do NOT retype any figure by hand. The Givemeanode signups
-- (audit_search) and product line (market_pnl) are merged in afterwards.
WITH bounds AS (
  SELECT NOW() - INTERVAL '24 hours' AS w_start, NOW() AS w_end
),
free_mail AS (
  SELECT ARRAY['gmail.com','googlemail.com','yahoo.com','hotmail.com','outlook.com',
               'proton.me','protonmail.com','pm.me','icloud.com','me.com','aol.com',
               'live.com','msn.com','qq.com','163.com','yandex.ru','mail.ru',
               'yopmail.com','mailinator.com','guerrillamail.com','10minutemail.com',
               'tempmail.com','trashmail.com','sharklasers.com'] AS d
),
spend AS (
  SELECT p.account_id,
         COALESCE(NULLIF(pii.business_name,''),
                  NULLIF(TRIM(COALESCE(pii.first_name,'')||' '||COALESCE(pii.last_name,'')),''),
                  p.account_id) AS display_name,
         pii.primary_email,
         SUM(-p.credit_ud)/1e6 AS amount,
         COUNT(*) AS fills
  FROM abel_denormalized_materialized_postings p
  LEFT JOIN account_pii pii ON pii.account_id = p.account_id, bounds b
  WHERE p.code='v0_credit_trade' AND p.subaccount='order' AND p.credit_ud<0
    AND p.effective_at > b.w_start AND p.effective_at <= b.w_end
  GROUP BY 1,2,3
),
fund AS (
  SELECT p.account_id,
         COALESCE(NULLIF(pii.business_name,''),
                  NULLIF(TRIM(COALESCE(pii.first_name,'')||' '||COALESCE(pii.last_name,'')),''),
                  p.account_id) AS display_name,
         pii.primary_email,
         SUM(p.credit_ud)/1e6 AS amount,
         COUNT(*) AS n,
         BOOL_OR(p.code='v0_credit_stripe_purchase') AS via_stripe,
         BOOL_OR(p.code='manual') AS via_manual
  FROM abel_denormalized_materialized_postings p
  LEFT JOIN account_pii pii ON pii.account_id = p.account_id, bounds b
  WHERE p.code IN ('v0_credit_stripe_purchase','manual') AND p.subaccount='main'
    AND p.credit_ud>0
    AND p.account_id NOT IN ('sfc_stripe','sfc_cash','sfc_market_ops')
    AND p.effective_at > b.w_start AND p.effective_at <= b.w_end
  GROUP BY 1,2,3
),
days AS (
  SELECT generate_series((NOW() AT TIME ZONE 'UTC')::date - INTERVAL '30 days',
                         (NOW() AT TIME ZONE 'UTC')::date, INTERVAL '1 day')::date AS d
),
-- ONE grouped scan over the 30-day range. Do NOT go back to a correlated
-- subquery per day: 31 days x 2 metrics = 62 scans and the MCP call times out
-- at 30s. The code IN (...) predicate must stay -- it lets the index prune.
daily AS (
  SELECT (effective_at AT TIME ZONE 'UTC')::date AS dt,
         SUM(CASE WHEN code='v0_credit_trade' AND subaccount='order' AND credit_ud<0
                  THEN -credit_ud ELSE 0 END)/1e6 AS spend,
         SUM(CASE WHEN code IN ('v0_credit_stripe_purchase','manual') AND subaccount='main'
                   AND credit_ud>0
                   AND account_id NOT IN ('sfc_stripe','sfc_cash','sfc_market_ops')
                  THEN credit_ud ELSE 0 END)/1e6 AS funding
  FROM abel_denormalized_materialized_postings
  WHERE effective_at >= (NOW() AT TIME ZONE 'UTC')::date - INTERVAL '30 days'
    AND code IN ('v0_credit_trade','v0_credit_stripe_purchase','manual')
  GROUP BY 1
),
series AS (
  SELECT TO_CHAR(days.d,'MM-DD') AS day,
         COALESCE(daily.spend,0)::float   AS spend,
         COALESCE(daily.funding,0)::float AS funding,
         days.d AS dt
  FROM days LEFT JOIN daily ON daily.dt = days.d
),
-- The account's FIRST member supplies the person's name and email.
-- Names live in user_pii (NOT account_pii, and NOT clerk_users -- both are
-- empty for these rows); the email lives in user_identities.
sig AS (
  SELECT a.id AS account_id, a.created_at, a.entity_type,
         pii.business_name,
         NULLIF(TRIM(COALESCE(up.first_name,'')||' '||COALESCE(up.last_name,'')),'') AS person,
         COALESCE(
           (SELECT ui.email FROM user_identities ui
            WHERE ui.user_id = fm.user_id AND ui.deleted_at IS NULL
            ORDER BY ui.created_at LIMIT 1),
           pii.primary_email) AS email
  FROM accounts a
  LEFT JOIN account_pii pii ON pii.account_id=a.id
  LEFT JOIN LATERAL (SELECT am.user_id FROM account_memberships am
                     WHERE am.account_id=a.id AND am.deleted_at IS NULL
                     ORDER BY am.created_at ASC LIMIT 1) fm ON TRUE
  LEFT JOIN user_pii up ON up.user_id = fm.user_id, bounds b
  WHERE a.created_at > b.w_start AND a.created_at <= b.w_end AND a.deleted_at IS NULL
    -- internal + test orgs never appear in the report
    AND COALESCE(a.id,'') NOT ILIKE '%test%'
    AND COALESCE(pii.business_name,'') NOT ILIKE '%test%'
),
sig_typed AS (
  SELECT s.*,
         SPLIT_PART(COALESCE(s.email,''),'@',2) AS domain,
         CASE
           WHEN SPLIT_PART(COALESCE(s.email,''),'@',2) LIKE '%.edu'
             OR SPLIT_PART(COALESCE(s.email,''),'@',2) LIKE '%.ac.uk' THEN 'Academic'
           WHEN NULLIF(s.business_name,'') IS NOT NULL THEN 'Company'
           WHEN SPLIT_PART(COALESCE(s.email,''),'@',2) IN (SELECT UNNEST(d) FROM free_mail)
             THEN 'Individual'
           WHEN COALESCE(s.email,'') <> '' THEN 'Company'
           ELSE COALESCE(INITCAP(s.entity_type),'Individual')
         END AS inferred_type
  FROM sig s
),
spend_tot AS (SELECT COALESCE(SUM(amount),0) t, COUNT(*) c,
                     COALESCE(SUM(amount) FILTER (WHERE account_id NOT IN ('sfcompute','give_me_a_node')),0) ext
              FROM spend),
fund_tot  AS (SELECT COALESCE(SUM(amount),0) t, COUNT(*) c,
                     COALESCE(SUM(amount) FILTER (WHERE account_id NOT IN ('sfcompute','give_me_a_node')),0) ext
              FROM fund),
bucket AS (
  SELECT
    (SELECT COALESCE(SUM(spend),0) FROM series WHERE dt = (NOW() AT TIME ZONE 'UTC')::date - 1) AS s_dod_cur,
    (SELECT COALESCE(SUM(spend),0) FROM series WHERE dt = (NOW() AT TIME ZONE 'UTC')::date - 2) AS s_dod_pri,
    (SELECT COALESCE(SUM(spend),0) FROM series WHERE dt > (NOW() AT TIME ZONE 'UTC')::date - 8
                                                 AND dt <= (NOW() AT TIME ZONE 'UTC')::date) AS s_wow_cur,
    (SELECT COALESCE(SUM(spend),0) FROM series WHERE dt > (NOW() AT TIME ZONE 'UTC')::date - 15
                                                 AND dt <= (NOW() AT TIME ZONE 'UTC')::date - 8) AS s_wow_pri,
    (SELECT COALESCE(SUM(funding),0) FROM series WHERE dt = (NOW() AT TIME ZONE 'UTC')::date - 1) AS f_dod_cur,
    (SELECT COALESCE(SUM(funding),0) FROM series WHERE dt = (NOW() AT TIME ZONE 'UTC')::date - 2) AS f_dod_pri,
    (SELECT COALESCE(SUM(funding),0) FROM series WHERE dt > (NOW() AT TIME ZONE 'UTC')::date - 8
                                                 AND dt <= (NOW() AT TIME ZONE 'UTC')::date) AS f_wow_cur,
    (SELECT COALESCE(SUM(funding),0) FROM series WHERE dt > (NOW() AT TIME ZONE 'UTC')::date - 15
                                                 AND dt <= (NOW() AT TIME ZONE 'UTC')::date - 8) AS f_wow_pri
)
SELECT JSON_BUILD_OBJECT(
  'date', TO_CHAR((SELECT w_end FROM bounds) AT TIME ZONE 'UTC','YYYY-MM-DD'),
  'window_end_utc', TO_CHAR((SELECT w_end FROM bounds) AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI')||' UTC',
  'gman_period', TO_CHAR(NOW(),'FMMonth'),
  'spent_usd', (SELECT ROUND(t::numeric,2) FROM spend_tot),
  'spent_accts', (SELECT c FROM spend_tot),
  'funded_usd', (SELECT ROUND(t::numeric,2) FROM fund_tot),
  'funded_accts', (SELECT c FROM fund_tot),
  'external', JSON_BUILD_OBJECT('spent', (SELECT ROUND(ext::numeric,2) FROM spend_tot),
                                'funded', (SELECT ROUND(ext::numeric,2) FROM fund_tot)),
  'spenders', (SELECT COALESCE(JSON_AGG(JSON_BUILD_OBJECT(
                 'account_id', account_id, 'name', display_name,
                 'internal', account_id IN ('sfcompute','give_me_a_node'),
                 'email', primary_email, 'fills', fills,
                 'amount', ROUND(amount::numeric,2)) ORDER BY amount DESC),'[]'::json) FROM spend),
  'funders', (SELECT COALESCE(JSON_AGG(JSON_BUILD_OBJECT(
                 'account_id', account_id, 'name', display_name,
                 'internal', account_id IN ('sfcompute','give_me_a_node'),
                 'email', primary_email, 'n', n, 'via_stripe', via_stripe,
                 'via_manual', via_manual,
                 'amount', ROUND(amount::numeric,2)) ORDER BY amount DESC),'[]'::json) FROM fund),
  'chart', (SELECT COALESCE(JSON_AGG(JSON_BUILD_OBJECT('day',day,'spend',ROUND(spend::numeric,2),
                 'funding',ROUND(funding::numeric,2)) ORDER BY dt),'[]'::json) FROM series),
  'signups_on_demand', (SELECT COUNT(*) FROM sig_typed
                        WHERE COALESCE(email,'') NOT LIKE '%@sfcompute.com'),
  'signups_on_demand_rows', (SELECT COALESCE(JSON_AGG(JSON_BUILD_OBJECT(
                 'person', person, 'email', email,
                 'company', COALESCE(NULLIF(business_name,''), account_id),
                 'type', inferred_type, 'product','On Demand',
                 'time', TO_CHAR(created_at AT TIME ZONE 'UTC','MM-DD HH24:MI'),
                 'created_at', created_at) ORDER BY created_at DESC),'[]'::json)
                 FROM sig_typed WHERE COALESCE(email,'') NOT LIKE '%@sfcompute.com'),
  'trends_raw', (SELECT JSON_BUILD_OBJECT(
                   's_dod_cur',ROUND(s_dod_cur::numeric,2),'s_dod_pri',ROUND(s_dod_pri::numeric,2),
                   's_wow_cur',ROUND(s_wow_cur::numeric,2),'s_wow_pri',ROUND(s_wow_pri::numeric,2),
                   'f_dod_cur',ROUND(f_dod_cur::numeric,2),'f_dod_pri',ROUND(f_dod_pri::numeric,2),
                   'f_wow_cur',ROUND(f_wow_cur::numeric,2),'f_wow_pri',ROUND(f_wow_pri::numeric,2))
                 FROM bucket)
) AS payload;
