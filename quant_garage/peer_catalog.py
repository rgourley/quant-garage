"""
Shared curated peer catalog for valuation-sanity-check, pitch-comps,
and earnings-drilldown.

Massive's /v1/related-companies is SIC-narrow and misses obvious
sector clusters (BNPL 6141, CATV 4841, biotech, gaming), so the
skills fall through to a WARN and force the caller to hand in
`--peers`. This catalog is the "well-known clusters" pre-check
applied before the API fallback. Add sparingly — a stale entry is
worse than an honest miss (Q1).

Format: {SUBJECT_TICKER: [PEER_1, PEER_2, ...]}. Keep peer count
between 3 and 8 (below 3 tanks band width; above 8 blows fetch cost).
"""
from __future__ import annotations

PEER_OVERRIDES: dict[str, list[str]] = {
    # ----- Software majors -----
    "CRM":  ["ORCL", "SAP", "NOW", "WDAY", "ADBE", "INTU", "PANW", "CRWD"],
    "ORCL": ["CRM", "SAP", "MSFT", "ADBE", "NOW", "WDAY", "INTU"],
    "ADBE": ["CRM", "ORCL", "INTU", "NOW", "WDAY", "SAP"],
    "NOW":  ["CRM", "WDAY", "ADBE", "ORCL", "INTU", "PANW"],
    "WDAY": ["CRM", "NOW", "ADBE", "INTU", "ORCL"],
    "INTU": ["CRM", "ADBE", "ORCL", "NOW", "WDAY"],
    "PANW": ["CRWD", "FTNT", "ZS", "S", "CHKP", "OKTA"],
    "CRWD": ["PANW", "FTNT", "ZS", "S", "OKTA"],
    # ----- Mega-cap tech -----
    "AAPL":  ["NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSM", "AVGO"],
    "NVDA":  ["AMD", "AVGO", "TSM", "MU", "ARM", "QCOM", "INTC"],
    "MSFT":  ["GOOGL", "AMZN", "META", "ORCL", "CRM", "AAPL"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX", "SNAP"],
    "META":  ["GOOGL", "SNAP", "PINS", "NFLX", "AMZN"],
    "AMZN":  ["GOOGL", "META", "MSFT", "AAPL", "SHOP", "WMT"],
    "TSLA":  ["NIO", "RIVN", "LCID", "F", "GM"],
    # ----- Banks + brokerages -----
    "JPM": ["BAC", "WFC", "C", "GS", "MS"],
    "GS":  ["MS", "JPM", "BAC", "C"],
    "HOOD": ["SCHW", "IBKR", "LPLA", "MKTX", "VIRT"],
    "SOFI": ["ALLY", "KEY", "CFG", "RF", "SCHW", "IBKR"],
    # ----- Payments + BNPL -----
    "V":  ["MA", "PYPL", "AXP"],
    "MA": ["V", "PYPL", "AXP"],
    "PYPL": ["XYZ", "V", "MA", "AXP", "AFRM"],
    "AFRM": ["PYPL", "XYZ", "UPST", "BFH", "SEZL"],
    "UPST": ["AFRM", "PYPL", "SEZL", "BFH"],
    # ----- Streaming / CTV / ad-tech -----
    "NFLX": ["DIS", "WBD", "PSKY", "AMZN", "GOOGL"],
    "ROKU": ["NFLX", "DIS", "WBD", "TTD", "FUBO", "PSKY"],
    "TTD":  ["ROKU", "PUBM", "MGNI", "APP"],
    "SNAP": ["META", "PINS", "GOOGL", "NFLX"],
    "PINS": ["SNAP", "META", "GOOGL", "NFLX"],
    # ----- Gaming / sportsbooks -----
    "DKNG": ["FLUT", "PENN", "MGM", "CZR"],
    "MGM":  ["CZR", "LVS", "WYNN", "DKNG"],
    # ----- EV / auto -----
    "RIVN": ["TSLA", "LCID", "NIO", "F", "GM"],
    "LCID": ["TSLA", "RIVN", "NIO"],
    # ----- Pharma -----
    "LLY":  ["NVO", "PFE", "MRK", "ABBV", "BMY", "AMGN"],
    "MRK":  ["LLY", "PFE", "ABBV", "BMY", "AMGN", "JNJ"],
    "NVO":  ["LLY", "PFE", "MRK", "ABBV"],
    # ----- Biotech (small-cap growth) -----
    "ALLO": ["BEAM", "NTLA", "CRSP", "EDIT", "LYEL", "CABA", "RCKT"],
    # ----- Cloud infra / observability / SaaS security -----
    "NET":  ["FSLY", "DDOG", "CRWD", "ZS", "OKTA"],
    "DDOG": ["NET", "ESTC", "MDB", "CRWD", "PANW"],
    "MDB":  ["DDOG", "NET", "ESTC", "SNOW"],
    "OKTA": ["CRWD", "PANW", "ZS", "S"],
    "ZS":   ["CRWD", "PANW", "S", "OKTA", "NET"],
    "SNOW": ["MDB", "DDOG", "NET", "PLTR"],
    "PLTR": ["SNOW", "MDB", "DDOG", "NET"],
    # ----- Marketplaces / e-com platforms -----
    "SHOP": ["ETSY", "AMZN", "EBAY", "MELI"],
    "ETSY": ["EBAY", "W", "AMZN", "SHOP"],
    # ----- Rideshare / delivery / gig -----
    "UBER": ["LYFT", "DASH", "GRAB"],
    "LYFT": ["UBER", "DASH"],
    "DASH": ["UBER", "LYFT"],
    # ----- Travel / hospitality -----
    "ABNB": ["BKNG", "EXPE", "MAR", "HLT"],
    "BKNG": ["EXPE", "ABNB", "MAR", "HLT"],
    # ----- Media / streaming -----
    "DIS":  ["NFLX", "WBD", "PSKY", "ROKU"],
    # ----- Aero / defense -----
    "BA":   ["LMT", "NOC", "GD", "RTX"],
    "LMT":  ["RTX", "GD", "NOC", "BA"],
    "RTX":  ["LMT", "GD", "NOC", "HII"],
    # ----- Health insurance / managed care -----
    "UNH":  ["ELV", "HUM", "CI", "CVS"],
    "ELV":  ["UNH", "HUM", "CI", "CVS"],
    "HUM":  ["UNH", "ELV", "CI", "CVS"],
    "CVS":  ["UNH", "ELV", "HUM", "CI"],
    # ----- Retail -----
    "WMT":  ["TGT", "COST", "KR", "DG"],
    "TGT":  ["WMT", "KR", "DG", "COST"],
    "COST": ["WMT", "TGT", "KR"],
    "HD":   ["LOW", "TSCO", "BLDR"],
    "LOW":  ["HD", "TSCO", "BLDR"],
    # ----- Consumer staples -----
    "KO":   ["PEP", "MDLZ", "KDP"],
    "PEP":  ["KO", "MDLZ", "KDP"],
    # ----- QSR -----
    "SBUX": ["MCD", "YUM", "CMG", "DPZ"],
    "MCD":  ["SBUX", "YUM", "CMG", "WEN", "DPZ"],
    # ----- Legacy autos (mid/large-cap auto majors) -----
    "F":    ["GM", "STLA", "TSLA", "RIVN"],
    "GM":   ["F", "STLA", "TSLA", "RIVN"],
    # ----- Industrials -----
    "CAT":  ["DE", "PCAR", "CMI"],
    "DE":   ["CAT", "AGCO", "CNH"],
    # ----- Energy -----
    "XOM": ["CVX", "COP", "EOG", "OXY"],
    "CVX": ["XOM", "COP", "EOG", "OXY"],
}
