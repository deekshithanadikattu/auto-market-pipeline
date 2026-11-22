-- Fact: daily prices with window-function metrics.
-- Grain: one row per ticker per trading day. Join to dim_company on ticker.

with prices as (

    select * from {{ ref('stg_prices') }}

),

windowed as (

    select
        price_id,
        ticker,
        trade_date,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        ingested_at,

        lag(close_price) over (
            partition by ticker order by trade_date
        ) as prev_close,

        avg(close_price) over (
            partition by ticker order by trade_date
            rows between 19 preceding and current row
        ) as ma_20d_raw,

        count(*) over (
            partition by ticker order by trade_date
            rows between 19 preceding and current row
        ) as ma_20d_obs,

        -- ~252 trading days in a year
        max(high_price) over (
            partition by ticker order by trade_date
            rows between 251 preceding and current row
        ) as high_52w

    from prices

)

select
    price_id,
    ticker,
    trade_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    close_price / prev_close - 1                          as daily_return,
    case when ma_20d_obs = 20 then ma_20d_raw end         as ma_20d,   -- null until 20 days exist
    high_52w,
    close_price / high_52w - 1                            as pct_from_52w_high,
    ingested_at
from windowed
