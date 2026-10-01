-- E1 — 2024 recognized revenue
SELECT
    SUM(recognized_revenue_usd) AS recognized_revenue_usd
FROM v_revenue_lines
WHERE invoice_year = 2024;

-- E2 — highest average MRR region
SELECT
    region,
    AVG(current_mrr_usd) AS average_mrr_usd
FROM v_account_metrics
WHERE is_active
GROUP BY region
ORDER BY average_mrr_usd DESC, region ASC
LIMIT 1;

-- E3 — Enterprise vs Starter churn
SELECT
    plan,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END) AS churned_accounts,
    COUNT(*) AS account_count,
    SUM(CASE WHEN churned THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS churn_rate
FROM v_account_metrics
WHERE plan IN ('Enterprise', 'Starter')
GROUP BY plan
ORDER BY
    CASE plan WHEN 'Enterprise' THEN 1 WHEN 'Starter' THEN 2 END;

-- E4 — all-time refunds
SELECT
    SUM(refund_usd) AS refund_usd
FROM v_revenue_lines;

-- E7 — top 5 accounts by lifetime net revenue with current CSAT
SELECT
    account_id,
    account_name,
    lifetime_net_revenue_usd,
    csat_score,
    COALESCE(csat_score <= 2, FALSE) AS low_csat
FROM v_account_metrics
ORDER BY lifetime_net_revenue_usd DESC, account_id ASC
LIMIT 5;

-- E8 — pending/failed invoice count and exposed USD
SELECT
    COUNT(*) FILTER (WHERE is_exposed) AS exposed_invoice_count,
    SUM(exposed_usd) AS exposed_usd
FROM v_revenue_lines;
