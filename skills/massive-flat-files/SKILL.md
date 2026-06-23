---
name: massive-flat-files
description: Foundation skill for bulk historical workflows backed by Massive's S3 flat files. Use whenever you need more than a few hundred ticker-days of trades, quotes, or aggregates. Faster, cheaper, and rate-limit-free compared to REST. Included with any paid Massive plan.
---

# massive-flat-files

The bulk historical foundation. Any skill that needs more than a handful
of ticker-days reads this first.

## Why this exists

REST is great for "what's AAPL trading right now." It falls over when
you need every trade for every name for every day across the last decade.
Flat files give you that data as gzipped CSVs in an S3 bucket. No
rate limits. No per-call overhead. Pull a year of minute aggregates for
the entire US universe in a few minutes.

## Access

The S3 bucket lives at `s3://flatfiles/`. Auth uses your Massive API key
as the S3 access key (the secret key is the same value). Endpoint is
`https://files.polygon.io` (legacy hostname, still works).

**Entitlement gotcha (verified 2026-06-23):** flat-files access is not
automatically included with every paid plan despite what the marketing
page implies. Test runs from a Stocks Business + Options Business +
Benzinga add-on key returned `403 Forbidden` on every list, head, and
get operation against the bucket. The same key happily serves
`/v3/reference/tickers`, `/v2/aggs/grouped/...`, snapshots, and
options chains over REST. Verify flat-files entitlement on your key
before building a workflow on it: hit
`s3.head_object(Bucket='flatfiles', Key='us_stocks_sip/day_aggs_v1/2026/06/2026-06-19.csv.gz')`
in a quick `boto3` probe; a 403 means the key is not provisioned for
the bucket and you need to contact support or use the REST fallback.

**REST fallback for day aggregates:** when flat-files are unavailable,
`GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true`
returns all US-listed stocks for that date in one call (~10,000 rows).
Throughput is equivalent to one S3 day-bucket per trading day, just
running over REST instead of S3. The schema is `T` / `c` / `v` / `o` /
`h` / `l` / `vw` / `n` (capitalized fields) versus the flat-file
schema of `ticker` / `close` / `volume` / etc (lowercase); your loader
should normalize.

```bash
aws configure set aws_access_key_id ${MASSIVE_API_KEY} --profile massive
aws configure set aws_secret_access_key ${MASSIVE_API_KEY} --profile massive
aws configure set endpoint_url https://files.polygon.io --profile massive

aws s3 ls s3://flatfiles/us_stocks_sip/day_aggs_v1/2026/06/ --profile massive
```

Python with `boto3` or `s3fs` works the same way. The web-based File
Browser at [massive.com/dashboard/file-browser](https://massive.com/dashboard/file-browser)
is the easiest way to discover paths.

## Path layout

Files partition by asset class, data type, and date:

```
s3://flatfiles/{asset_class}/{data_type}/{yyyy}/{mm}/{yyyy-mm-dd}.csv.gz
```

Asset classes available:
- `us_stocks_sip`: US stocks
- `us_options_opra`: US options
- `global_crypto`: Crypto
- `global_forex`: FX
- `us_indices`: US indices

Data types per asset class:
- `trades_v1`: every tick
- `quotes_v1`: every NBBO update
- `minute_aggs_v1`: minute OHLCV
- `day_aggs_v1`: daily OHLCV

A single day of stocks trades is a few GB compressed. Plan disk and
parallelism accordingly.

## Reading a day file

```python
import pandas as pd

df = pd.read_csv(
    "s3://flatfiles/us_stocks_sip/day_aggs_v1/2026/06/2026-06-20.csv.gz",
    storage_options={
        "key": MASSIVE_API_KEY,
        "secret": MASSIVE_API_KEY,
        "client_kwargs": {"endpoint_url": "https://files.polygon.io"},
    },
)
```

Schema is documented per data type at
[massive.com/docs/flat-files](https://massive.com/docs/flat-files).

## Parallelism

The bucket has no per-request rate limit. Workflows should fan out across
days or symbols freely. Practical cap is your egress bandwidth and disk
write speed, not anything on the Massive side.

```python
from concurrent.futures import ThreadPoolExecutor

def fetch_day(date):
    return pd.read_csv(s3_path(date), storage_options=opts)

with ThreadPoolExecutor(max_workers=16) as pool:
    frames = list(pool.map(fetch_day, dates))
```

16-32 workers is a healthy default. Beyond that you saturate the pipe.

## Cost

Included in every paid Massive plan at no extra charge. No egress fees
from Massive. AWS egress to your local machine is free (Massive eats it).

If you're downloading TB-scale data to your own AWS account in a
different region, you may see standard AWS inter-region transfer fees,
but those are between you and AWS, not Massive.

## When to use REST instead

- Looking up one or two names: REST is faster than spinning up an S3
  client and decompressing a multi-GB file
- Real-time intraday state: flat files are end-of-day only
- Reference data (ticker list, exchange list, splits): REST endpoints
  exist for these and they're tiny

## What lives here

- `references/paths.md`: full path layout reference for every asset class
  and data type
- `references/schemas.md`: column schemas per data type
- `references/parallelism.md`: download patterns for large pulls

## What does NOT live here

REST patterns ([`massive-api-patterns`](../massive-api-patterns)) and
live streams ([`massive-websockets`](../massive-websockets)).
