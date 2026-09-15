# SMA 20/50 trend-following execution system (FXPesa / MT5)

Long/short moving-average crossover with next-bar fills, lot sizing, pip stops, and a spread model. Default symbol is **XAUUSD**. Python is the backtester. Live orders on FXPesa go through **MetaTrader 5**.

This is not a magic money bot. SMA crossovers have no reliable edge. Use a **demo** account.

## FXPesa path

FXPesa has no retail REST/FIX API. Automation is MT5:

1. Backtest in Python (this repo).
2. Trade on demo with the Expert Advisor in `mql5/PilbetSMA.mq5`.

### Install the EA

1. Open FXPesa MT5 → **File → Open Data Folder** → `MQL5/Experts/`.
2. Copy `mql5/PilbetSMA.mq5` there.
3. In MetaEditor, compile it (F7).
4. Open a **demo** XAUUSD H1 chart.
5. Drag `PilbetSMA` onto the chart. Enable **Allow Algo Trading**.
6. Turn on the toolbar **AutoTrading** button.

Inputs match the Python defaults: SMA 20/50, 1% risk, 400-pip ($4) stop, 800-pip target, longs and shorts. Gold pip size is $0.01.

### Backtest on FXPesa history

MT5 Python (`--mt5`) only works on **Windows** next to a running terminal. On Linux, export CSV:

1. MT5: View → Symbols → pick `XAUUSD` → Bars → export, or right-click the chart and save history.
2. Then:

```bash
source venv/bin/activate
python main.py --csv path/to/xauusd.csv --symbol XAUUSD --timeframe H1
python -m pytest
```

On Windows, with MT5 logged into FXPesa:

```bash
set MT5_LOGIN=...
set MT5_PASSWORD=...
set MT5_SERVER=...
python main.py --mt5 --symbol XAUUSD --timeframe H1 --bars 2000
```

The server name is on the MT5 login screen (not guessed here). If Market Watch shows `XAUUSDm` or similar, pass that exact name.

## Why these semantics

| Rule | Why |
| --- | --- |
| Trailing SMAs (`center=False`) | A centered window peeks at future closes. |
| Signal on `iloc[-2]` vs `iloc[-1]` | Cross is detected on two *completed* bars. |
| Fill at the **next open** | You cannot trade the same close you just used to compute the SMA. |
| Drop a forming last bar | Live last candles repaint; signals would flicker. |
| Lots = `(equity × 1%) / (stop_pips × pip_value)` | Gold: 1 pip = $1 / lot. A 400-pip ($4) stop with 1% of $10k → 0.25 lots. |
| Cap by free margin and `volume_max` | Leverage is not a sizing strategy. Gold max is 1.0 lot. |
| One position | A reverse closes, then opens the other side on the same open. |
| Intra-bar SL/TP via high/low | Close-only exits miss stops that traded through the bar. |
| If both SL and TP sit in the same bar, **stop wins** | OHLC has no path; TP-first systematically overstates returns. |
| Gap through a stop fills at the **open** | The market never traded at your stop. |
| Spread on buy/sell | FXPesa Standard is spread-based; gold default is 30 pips ($0.30). |

## Layout

```
main.py                 # CLI orchestrator
pilbet/config.py        # BotConfig (XAUUSD / pips / lots)
pilbet/data_pipeline.py # CSV + MT5 history export + trailing SMAs
pilbet/strategy.py      # golden / death cross
pilbet/risk_manager.py  # lot size + pip brackets
pilbet/portfolio.py     # margin PnL, long and short, SL/TP
pilbet/engine.py        # bar loop
pilbet/mt5_client.py    # optional Windows MT5 history pull
mql5/PilbetSMA.mq5      # EA for the FXPesa terminal
tests/                  # lookahead, sizing, shorts, MT5 CSV
```

## Run

```bash
source venv/bin/activate
pip install -r requirements.txt
python main.py
python main.py --csv path/to/ohlcv.csv --symbol XAUUSD
python -m pytest
```

CSV columns: `timestamp,open,high,low,close,volume`, or an MT5 export with `<DATE> <TIME> <OPEN> ... <TICKVOL>`.

Outputs land in `reports/` (`trades.csv`, `equity.csv`, `summary.csv`, `equity_curve.png`) and `logs/pilbet.log`.

## Bar loop

1. Opening print: gap SL/TP, then any queued market order from the previous close (including reverse).
2. Intra-bar: stop if price trades through SL, target if it trades through TP (high/low; shorts inverted).
3. Mark equity at close (balance + floating PnL).
4. Evaluate the 20/50 cross on completed bars; queue for the *next* open.

Default brackets (XAUUSD): 400-pip stop ($4), 800-pip take-profit, 1% equity risk, $10,000 start, 30-pip spread, 1:100 leverage, 100 oz contract.
