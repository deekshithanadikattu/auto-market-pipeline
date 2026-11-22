-- Staging: clean, rename and type the raw prices. One row per ticker per trading day.

with source as (

    select * from {{ source('raw', 'prices') }}

),

renamed as (

    select
        trim(ticker)                as ticker,
        cast(date as date)          as trade_date,
        cast(open as double)        as open_price,
        cast(high as double)        as high_price,
        cast(low as double)         as low_price,
        cast(close as double)       as close_price,
        cast(volume as bigint)      as volume,
        ingested_at
    from source
    where close is not null          -- a day without a close price is unusable

),

deduped as (

    -- raw already has a primary key, but staging should guarantee its own grain
    select
        *,
        row_number() over (
            partition by ticker, trade_date
            order by ingested_at desc
        ) as row_num
    from renamed

)

select
    ticker || '_' || strftime(trade_date, '%Y-%m-%d') as price_id,
    ticker,
    trade_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    ingested_at
from deduped
where row_num = 1
