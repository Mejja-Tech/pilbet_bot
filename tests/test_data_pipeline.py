from __future__ import annotations

import pandas as pd
import pytest

from pilbet.config import BotConfig
from pilbet.data_pipeline import DataPipeline
from pilbet.synthetic import generate_synthetic_ohlcv


def test_trailing_sma_matches_manual_mean() -> None:
    pipeline = DataPipeline(BotConfig(short_window=3, long_window=5))
    raw = generate_synthetic_ohlcv(n_bars=80, seed=7)
    out = pipeline.compute_indicators(raw)
    expected_short = raw["close"].rolling(3, min_periods=3).mean()
    expected_long = raw["close"].rolling(5, min_periods=5).mean()
    pd.testing.assert_series_equal(out["sma_short"], expected_short, check_names=False)
    pd.testing.assert_series_equal(out["sma_long"], expected_long, check_names=False)


def test_sma_prefix_equals_full_series() -> None:
    pipeline = DataPipeline()
    raw = generate_synthetic_ohlcv(n_bars=90, seed=3)
    full = pipeline.compute_indicators(raw)
    for end in range(50, len(raw) + 1):
        prefix = pipeline.compute_indicators(raw.iloc[:end])
        assert prefix["sma_short"].iloc[-1] == pytest.approx(
            full["sma_short"].iloc[end - 1]
        )
        assert prefix["sma_long"].iloc[-1] == pytest.approx(
            full["sma_long"].iloc[end - 1]
        )


def test_mutating_future_closes_does_not_change_past_sma() -> None:
    pipeline = DataPipeline()
    raw = generate_synthetic_ohlcv(n_bars=100, seed=11)
    split = 70
    mutated = raw.copy()
    mutated.loc[split:, "close"] *= 3.0
    original = pipeline.compute_indicators(raw)
    changed = pipeline.compute_indicators(mutated)
    pd.testing.assert_series_equal(
        original.loc[: split - 1, "sma_short"],
        changed.loc[: split - 1, "sma_short"],
    )
    pd.testing.assert_series_equal(
        original.loc[: split - 1, "sma_long"],
        changed.loc[: split - 1, "sma_long"],
    )


def test_incomplete_last_bar_is_dropped() -> None:
    raw = generate_synthetic_ohlcv(n_bars=80, seed=1)
    raw.loc[raw.index[-1], "is_closed"] = False
    pipeline = DataPipeline(BotConfig(drop_incomplete_last_bar=True, last_bar_closed=True))
    prepared = pipeline.prepare(raw)
    assert len(prepared) == len(raw) - 1
    assert bool(prepared.iloc[-1].get("is_closed", True))


def test_invalid_ohlc_raises() -> None:
    pipeline = DataPipeline()
    raw = generate_synthetic_ohlcv(n_bars=80, seed=2)
    raw.loc[10, "high"] = raw.loc[10, "low"] - 1.0
    with pytest.raises(ValueError, match="high/low"):
        pipeline.validate(raw)


def test_mt5_history_export_csv(tmp_path) -> None:
    from datetime import datetime, timedelta

    path = tmp_path / "eurusd.csv"
    start = datetime(2024, 1, 2, 0, 0)
    rows = [
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
    ]
    for i in range(80):
        ts = start + timedelta(hours=i)
        if i == 0:
            ohlc = "1.10450\t1.10520\t1.10380\t1.10490\t1234"
        else:
            ohlc = "1.10500\t1.10580\t1.10420\t1.10510\t1000"
        rows.append(
            f"{ts.strftime('%Y.%m.%d')}\t{ts.strftime('%H:%M')}\t{ohlc}\t0\t10"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    pipeline = DataPipeline(BotConfig(symbol="EURUSD", last_bar_closed=True))
    out = pipeline.load_csv(path)
    assert "sma_long" in out.columns
    assert out["open"].iloc[0] == pytest.approx(1.10450)
    assert out["volume"].iloc[0] == 1234
    assert len(out) == 80
