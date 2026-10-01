CREATE VIEW v_revenue_lines AS
SELECT
    invoice_id,
    account_id,
    account_name,
    region,
    plan,
    status,
    invoice_date,
    EXTRACT(YEAR FROM invoice_date)::INTEGER AS invoice_year,
    EXTRACT(MONTH FROM invoice_date)::INTEGER AS invoice_month,
    amount_usd,
    status IN ('paid', 'refunded') AS is_recognized,
    CASE
        WHEN status IN ('paid', 'refunded') THEN amount_usd
        ELSE 0
    END AS recognized_revenue_usd,
    status = 'refunded' AS is_refund,
    CASE WHEN status = 'refunded' THEN ABS(amount_usd) ELSE 0 END AS refund_usd,
    status IN ('pending', 'failed') AS is_exposed,
    CASE
        WHEN status IN ('pending', 'failed') THEN ABS(amount_usd)
        ELSE 0
    END AS exposed_usd
FROM invoices;

CREATE VIEW v_account_current AS
SELECT
    account_id,
    account_name,
    region,
    plan,
    seats,
    discount_pct,
    churned,
    csat_score,
    support_tickets,
    subscription_mrr_usd,
    invoice_date AS latest_invoice_date
FROM invoices
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY account_id
    ORDER BY invoice_date DESC, invoice_id DESC
) = 1;

CREATE VIEW v_account_metrics AS
WITH lifetime AS (
    SELECT
        account_id,
        SUM(recognized_revenue_usd) AS lifetime_net_revenue_usd
    FROM v_revenue_lines
    GROUP BY account_id
)
SELECT
    current.account_id,
    current.account_name,
    current.region,
    current.plan,
    current.seats,
    current.discount_pct,
    current.churned,
    current.csat_score,
    current.support_tickets,
    current.subscription_mrr_usd,
    current.latest_invoice_date,
    lifetime.lifetime_net_revenue_usd,
    CASE
        WHEN NOT current.churned THEN current.subscription_mrr_usd
        ELSE NULL
    END AS current_mrr_usd,
    NOT current.churned AS is_active
FROM v_account_current AS current
JOIN lifetime USING (account_id);
