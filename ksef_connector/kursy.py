"""Ręczny kurs waluty dla wybranych faktur (--kurs "NUMER=KURS").

Powód: faktury eksportowe wystawione przy nieaktualnej tabeli kursów w XL mają zły kurs, a poprawa tabeli nie zmienia
już wystawionych dokumentów. Księgowość podaje prawidłowe kursy, a kwoty w walucie się nie zmieniają —
w KSeF kurs jest tylko w KursWaluty wierszy. Kurs = za 1 jednostkę waluty (HUF: 1,1918 za 100 → 0.011918).
Kwoty w PLN w XL (rejestr VAT) trzeba poprawić osobno.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from ksef_connector.mapping.xl import BladMapowania, numer_p2


def parsuj(wpisy: list[str] | None) -> dict[str, Decimal]:
    kursy = {}
    for wpis in wpisy or []:
        numer, _, kurs = wpis.rpartition("=")
        try:
            wartosc = Decimal(kurs.strip().replace(",", "."))
        except InvalidOperation:
            raise SystemExit(f"--kurs {wpis!r}: zły format kursu, oczekiwane NUMER=KURS, np. \"(S)FSE-1/10/26/B/Z=4,3706\"")
        if not numer.strip() or wartosc <= 0:
            raise SystemExit(f"--kurs {wpis!r}: oczekiwane NUMER=KURS z kursem > 0")
        kursy[numer_p2(numer)] = wartosc
    return kursy


def zastosuj(raw: dict[str, Any], kursy: dict[str, Decimal]) -> dict[str, Any]:
    numer = numer_p2(raw["naglowek"]["numer"])
    if numer not in kursy:
        return raw
    waluta = (raw["naglowek"].get("waluta") or "PLN").strip().upper()
    if waluta == "PLN":
        raise BladMapowania(f"--kurs dla {numer}: faktura w PLN nie ma kursu")
    for p in raw["pozycje"]:
        p["kurs_l"], p["kurs_m"] = str(kursy[numer]), 1
    return raw


def wczytaj_z_kursami(wczytaj: Callable[[Path], dict[str, Any]], pliki: list[Path],
                      kursy: dict[str, Decimal]) -> Callable[[Path], dict[str, Any]]:
    """Jak `wczytaj`, ale z ręcznym kursem. Każdy podany numer musi być wśród plików (literówka = stop)."""
    if kursy:
        numery = {numer_p2(wczytaj(p)["naglowek"]["numer"]) for p in pliki}
        if brak := sorted(set(kursy) - numery):
            raise SystemExit(f"--kurs: brak tych faktur wśród plików: {', '.join(brak)}")
        for numer, kurs in sorted(kursy.items()):
            print(f"KURS       {numer}: {kurs} (ręcznie, zamiast kursu z XL)")
    return lambda plik: zastosuj(wczytaj(plik), kursy)
