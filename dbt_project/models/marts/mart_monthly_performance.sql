-- Mart: per company per month performance, ready for the dashboard.
-- Grain: one row per ticker per calendar month.

with daily as (

    select * from {{ ref('fct_daily_prices') }}

),

companies as (

    select * from {{ ref('dim_company') }}

),

monthly as (

    select
        ticker,
        cast(date_trunc('month', trade_date) as date)   as month_start,
        arg_max(close_price, trade_date)                as month_close,  -- last close of the month
        avg(volume)                                     as avg_volume,
        stddev_samp(daily_return)                       as volatility,   -- std dev of daily returns
        count(*)                                        as trading_days,
        max(trade_date)                                 as last_trade_date
    from daily
    group by 1, 2

),

with_returns as (

    select
        *,
        month_close / lag(month_close) over (
            partition by ticker order by month_start
        ) - 1 as monthly_return
    from monthly

)

select
    r.ticker || '_' || strftime(r.month_start, '%Y-%m') as month_id,
    r.ticker,
    c.company_name,
    c.country,
    c.segment,
    c.currency,
    r.month_start,
    r.month_close,
    r.monthly_return,
    r.avg_volume,
    r.volatility,
    r.trading_days,
    r.last_trade_date
from with_returns r
left join companies c using (ticker)   -- left join so the not_null test catches unmapped tickers
