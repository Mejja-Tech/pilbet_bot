from __future__ import annotations

import pytest

from pilbet.mt5_client import MT5Client, MT5Unavailable


def test_mt5_connect_explains_linux_limitation() -> None:
    client = MT5Client()
    with pytest.raises(MT5Unavailable):
        client.connect()
