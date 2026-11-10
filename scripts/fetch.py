#!/usr/bin/env python3
"""Fetch historical equity prices via OpenBB (Yahoo Finance provider).

Generic script: tickers are CLI args, nothing exchange-specific is
hardcoded beyond the pilot defaults.

Packaging notes (verified in the pilot's first run, 2026-11-10):
- There is NO `openbb-yahoo` package on PyPI — pip fails with
  "No matching distribution found for openbb-yahoo".
- The Yahoo Finance provider package is `openbb-yfinance`, and its
  OpenBB provider slug is "yfinance" (not "yahoo").
- OpenBB 5.0.0 removed the yfinance provider (it requires
  openbb-core 1.x), so this runner pins openbb<5 + openbb-yfinance<2.

Usage:
    python scripts/fetch.py [--tickers AAPL,POUL.CA] [--years 2]
                            [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
                            [--provider yahoo] [--output-dir results]

For each ticker it tries, in order, the requested provider and then
the known Yahoo slug "yfinance":
    obb.equity.price.historical(symbol=ticker, provider=<slug>,
                                start_date=..., end_date=...)
Every attempt (and its exact error) is recorded in summary.json, the
result is converted to a dataframe, a CSV is saved per ticker, and
results/summary.json carries, per ticker: rows, first/last date,
first/last close — or the exact error — plus a coverage verdict
(does the Yahoo provider return data for that ticker?).
"""
import argparse
import json
import os
import re
import sys
import traceback
from datetime import date, timedelta


def parse_args():
    p = argparse.ArgumentParser(description="OpenBB historical price fetch")
    p.add_argument("--tickers", default="AAPL,POUL.CA",
                   help="Comma-separated tickers (default: AAPL,POUL.CA)")
    p.add_argument("--years", type=float, default=2.0,
                   help="Lookback in years when --start-date is not given (default: 2)")
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--provider", default="yahoo")
    p.add_argument("--output-dir", default="results")
    return p.parse_args()


def safe_name(ticker):
    return re.sub(r"[^A-Za-z0-9]+", "_", ticker).strip("_")


def to_dataframe(result):
    """Convert an OpenBB output object to a pandas DataFrame."""
    if result is None:
        raise ValueError("OpenBB returned None")
    if hasattr(result, "to_dataframe"):
        df = result.to_dataframe()
        if df is not None:
            return df
    if hasattr(result, "results") and result.results:
        import pandas as pd
        return pd.DataFrame([r.model_dump() if hasattr(r, "model_dump") else dict(r)
                             for r in result.results])
    import pandas as pd
    if isinstance(result, pd.DataFrame):
        return result
    raise ValueError(f"Could not convert result of type {type(result)} to dataframe")


def extract_series(df):
    """Return (first_date, last_date, first_close, last_close) as strings/floats."""
    import pandas as pd
    work = df.copy()
    # Date may be the index or a 'date' column.
    date_col = None
    for cand in ("date", "Date", "timestamp"):
        if cand in work.columns:
            date_col = cand
            break
    if date_col is not None:
        dates = pd.to_datetime(work[date_col])
    else:
        # Assume index holds dates.
        work = work.reset_index()
        first_col = work.columns[0]
        dates = pd.to_datetime(work[first_col])
        df_dates = dates
        # keep aligned view for close extraction below using work
        close_col = None
        for cand in ("close", "Close"):
            if cand in work.columns:
                close_col = cand
                break
        if close_col is None:
            raise ValueError(f"No close column found; columns={list(work.columns)}")
        closes = work[close_col]
        order = dates.argsort()
        dates_sorted = dates.iloc[order]
        closes_sorted = closes.iloc[order]
        return (str(dates_sorted.iloc[0].date()), str(dates_sorted.iloc[-1].date()),
                float(closes_sorted.iloc[0]), float(closes_sorted.iloc[-1]))
    close_col = None
    for cand in ("close", "Close"):
        if cand in work.columns:
            close_col = cand
            break
    if close_col is None:
        raise ValueError(f"No close column found; columns={list(work.columns)}")
    order = dates.argsort()
    dates_sorted = dates.iloc[order]
    closes_sorted = work[close_col].iloc[order]
    return (str(dates_sorted.iloc[0].date()), str(dates_sorted.iloc[-1].date()),
            float(closes_sorted.iloc[0]), float(closes_sorted.iloc[-1]))


def main():
    args = parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    if args.start_date:
        start = date.fromisoformat(args.start_date)
    else:
        start = end - timedelta(days=int(args.years * 365.25))
    os.makedirs(args.output_dir, exist_ok=True)

    summary = {
        "tool": "openbb",
        "provider": args.provider,
        "start_date": str(start),
        "end_date": str(end),
        "tickers": {},
    }

    try:
        from openbb import obb
        import openbb as openbb_pkg
        summary["openbb_version"] = getattr(openbb_pkg, "__version__", "unknown")
    except Exception as e:
        summary["import_error"] = f"{type(e).__name__}: {e}"
        summary["coverage_verdict"] = {
            t: "ERROR: openbb failed to import" for t in tickers
        }
        with open(os.path.join(args.output_dir, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        return 0

    # Provider slugs to try, in order: the requested one first, then the
    # actual Yahoo Finance slug in OpenBB v4 ("yfinance").
    provider_chain = [args.provider]
    for slug in ("yfinance", "yahoo"):
        if slug not in provider_chain:
            provider_chain.append(slug)
    summary["provider_requested"] = args.provider
    summary["provider_chain_tried"] = provider_chain

    for ticker in tickers:
        entry = {"symbol": ticker, "provider_requested": args.provider,
                 "attempts": []}
        entry["rows"] = 0
        entry["verdict"] = "ERROR: no provider attempt succeeded"
        for slug in provider_chain:
            attempt = {"provider": slug}
            try:
                result = obb.equity.price.historical(
                    symbol=ticker,
                    provider=slug,
                    start_date=str(start),
                    end_date=str(end),
                )
                df = to_dataframe(result)
                attempt["rows"] = int(len(df))
                if len(df) == 0:
                    attempt["error"] = "provider returned an empty dataframe"
                    entry["attempts"].append(attempt)
                    continue
                first_date, last_date, first_close, last_close = extract_series(df)
                entry["provider_used"] = slug
                entry["rows"] = int(len(df))
                entry["first_date"] = first_date
                entry["last_date"] = last_date
                entry["first_close"] = first_close
                entry["last_close"] = last_close
                entry["verdict"] = "OK: provider returned data for this ticker"
                csv_path = os.path.join(args.output_dir, f"{safe_name(ticker)}.csv")
                df.to_csv(csv_path)
                entry["csv"] = os.path.basename(csv_path)
                entry["attempts"].append(attempt)
                break
            except Exception as e:
                attempt["error"] = f"{type(e).__name__}: {e}"
                attempt["traceback_tail"] = traceback.format_exc().splitlines()[-5:]
                entry["attempts"].append(attempt)
        if entry["rows"] == 0 and entry["attempts"]:
            entry["error"] = entry["attempts"][-1].get("error", "")
        summary["tickers"][ticker] = entry
        print(f"{ticker}: {entry.get('verdict')} rows={entry.get('rows')} "
              f"provider_used={entry.get('provider_used', '')} "
              f"error={entry.get('error', '')}")

    # Headline coverage question for the pilot: does yahoo reach POUL.CA?
    coverage = {}
    for t, e in summary["tickers"].items():
        if e.get("rows", 0) and e.get("rows", 0) > 0:
            coverage[t] = f"YES — {e['rows']} rows, {e.get('first_date')} to {e.get('last_date')}"
        else:
            coverage[t] = f"NO — {e.get('error') or e.get('verdict')}"
    summary["coverage_verdict"] = coverage
    if "POUL.CA" in summary["tickers"]:
        poul = summary["tickers"]["POUL.CA"]
        summary["poul_ca_reached_by_yahoo_provider"] = bool(poul.get("rows", 0) > 0)

    with open(os.path.join(args.output_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
