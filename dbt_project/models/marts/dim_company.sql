-- Dimension: one row per company, from the hand-written seed.

select
    ticker,
    company_name,
    country,
    segment,
    currency
from {{ ref('companies') }}
