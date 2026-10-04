from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retries wait no time in tests; the waits asked for are recorded."""
    asked: list[float] = []
    monkeypatch.setattr("ng_flood.client.sleep", asked.append)
    return asked
