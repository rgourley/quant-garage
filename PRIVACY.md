# Privacy

Quant Garage runs on your machine. It has no server, no account system, and no analytics. The author receives no data from your use of it.

## What the skills send, and where

| Host | Why | What is sent |
|---|---|---|
| `api.massive.com`, `api.polygon.io` | Market data (bars, quotes, options, news, reference data) | Your Massive API key and the tickers, dates and filters of each request |
| `files.polygon.io` | Flat files for bulk daily bars | Your Massive S3 credentials (optional) |
| `www.sec.gov`, `data.sec.gov` | EDGAR filings and the ticker-to-CIK map, as a fallback | The ticker's CIK and a User-Agent. No key or credential |

Massive's handling of the data it receives is covered by the [Massive privacy policy](https://massive.com/legal/privacy). SEC requests fall under [sec.gov privacy](https://www.sec.gov/privacy).

## What is stored

- Your API key and S3 credentials are held by Claude Code as sensitive plugin settings, or by your own environment variables. This repo never writes them to disk.
- Some skills cache market data under a `.cache` folder next to the code on your machine. Delete the folder to clear it.
- Portfolio files you pass to `portfolio-mark`, `risk-report` and similar skills stay local. They are read to compute the output and are not sent anywhere.

## Personal data

The skills read public records that include names, such as insider names on Form 4 filings, 13F filers and analyst names on ratings. They do not collect, store or transmit personal data about you.

## Contact

Questions or concerns: open an issue at https://github.com/rgourley/quant-garage/issues or email rgourley@gmail.com.
