# openbb-runner

Runs [OpenBB](https://openbb.co/) analyses on GitHub Actions, where the
commands to execute live in a repository **Actions variable** — not in code.

## The tool

OpenBB is an open data platform for financial research: a Python SDK
(`from openbb import obb`) that gives unified access to many data
providers (equity prices, fundamentals, economics, and more) behind one
interface, e.g. `obb.equity.price.historical(symbol=..., provider="yfinance")`
(the Yahoo Finance provider — see the packaging notes in the pilot section).

OpenBB is **open-core**: the platform / SDK itself is free and open
source, while the **Workspace UI** (the hosted web/desktop front end)
is a paid product. This runner uses only the free SDK.

## The pattern (one repo per tool)

1. **Edit the commands**: repo *Settings → Secrets and variables → Actions →
   Variables* — set the `COMMANDS` variable to whatever shell commands this
   run should execute (multi-line works). The same can be done through the
   GitHub API (`PUT /repos/{owner}/{repo}/actions/variables/COMMANDS`).
2. **Trigger manually**: *Actions* tab → "Run analysis" → *Run workflow*
   (or `POST /repos/{owner}/{repo}/actions/workflows/run.yml/dispatches`).
   The workflow has **no other triggers** — it runs only when dispatched.
3. **Collect results**: the workflow uploads everything under `results/` as
   the `results` build artifact (`if: always()`, so partial results survive
   a failing step). Download it from the run page or via the API.

The workflow itself is generic: checkout → Python 3.11 →
`pip install -r requirements.txt` → run `${{ vars.COMMANDS }}` → upload
`results/`. To run a different analysis, change the variable — not the workflow.

## Multi-market by design

Nothing in the workflow or the runner pattern is tied to one exchange.
Scripts take the **ticker symbol and analysis parameters as inputs** (CLI
args and/or the `COMMANDS` variable), so the same repo can analyse an EGX
stock today and a US stock (e.g. markets available through Nsave or Alpaca)
tomorrow — only the inputs change.

## Pilot: does OpenBB's yahoo provider reach POUL.CA?

Pilot script `scripts/fetch.py` (defaults overridable via
`--tickers`, `--years`, `--start-date`, `--end-date`, `--provider`):

- Tickers: `AAPL`, `POUL.CA`
- Call per ticker: `obb.equity.price.historical(symbol=ticker, provider=<slug>)`
  for ~2 years. The script tries the requested slug (`yahoo`) first and
  then the actual OpenBB v4 Yahoo slug (`yfinance`), recording every
  attempt and its exact error in `summary.json`.
- Per ticker it records: rows, first/last date, first/last close — or the
  exact error — saves a CSV per ticker, and writes `results/summary.json`
  with the coverage verdict per ticker.

Pilot `COMMANDS` value:

```bash
mkdir -p results
python scripts/fetch.py 2>&1 | tee results/run.log
```

Note: the `openbb` install is heavy — the Install dependencies step can
take a long while on a fresh runner.

**Packaging notes (learned from the pilot's first run, 2026-11-10):**
there is no `openbb-yahoo` package on PyPI (`No matching distribution
found for openbb-yahoo` — the first run failed on exactly that). The
Yahoo Finance provider package is `openbb-yfinance`, its provider slug
is `yfinance`, and OpenBB 5.0.0 removed it (it needs openbb-core 1.x) —
so `requirements.txt` pins `openbb<5` and `openbb-yfinance<2`.
