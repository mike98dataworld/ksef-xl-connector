"""Źródło z plików JSON — wynik `sql/faktura.sql` zapisany z SSMS (jeden plik = jedna faktura)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any


def wczytaj(sciezka: Path | str) -> dict[str, Any]:
    with open(sciezka, encoding="utf-8-sig") as f:  # SSMS potrafi zapisać BOM
        return json.load(f, parse_float=Decimal)


class FixtureSource:
    def __init__(self, katalog: Path | str):
        self.katalog = Path(katalog)

    def lista(self) -> list[str]:
        return sorted(p.stem for p in self.katalog.glob("*.json"))

    def pobierz(self, nazwa: str) -> dict[str, Any]:
        return wczytaj(self.katalog / f"{nazwa}.json")
