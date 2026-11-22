-- Singular test: a day's high can never be below its low.
-- Returns the bad rows; zero rows = pass.
select *
from {{ ref('stg_prices') }}
where high_price < low_price
