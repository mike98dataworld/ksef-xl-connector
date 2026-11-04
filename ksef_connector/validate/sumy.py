"""Kontrola sum: pozycje vs TraVat, per stawka.

Faktura od netto (N): suma netto pozycji musi równać się TrV_NettoR co do grosza.
Faktura od brutto (B): suma brutto pozycji vs TrV_NettoR + TrV_VatR, dopuszczalna różnica 0,01
(XL liczy VAT od sumy brutto, a nie z pozycji).
Korekta: wiersze „przed” liczą się na minus (TraVat korekty to różnica po − przed).
Faktura rozliczająca: pozycje = cała dostawa, więc porównujemy je z TraVat bez wierszy „minus zaliczka”.
Faktura zaliczkowa: pozycji nie ma (kwota zaliczki to nie suma wierszy zamówienia) — nie sprawdzamy.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ksef_connector.mapping.model import Faktura

TOLERANCJA_N = Decimal("0.00")
TOLERANCJA_B = Decimal("0.01")


def bledy_sum(f: Faktura) -> list[str]:
    if f.zamowienie:
        return []
    tolerancja = TOLERANCJA_B if f.od_brutto else TOLERANCJA_N
    z_pozycji: dict[str, Decimal] = defaultdict(Decimal)
    for p in f.pozycje:
        # Korekta: TraVat = różnica, czyli „po” minus „przed”.
        z_pozycji[p.stawka] += -p.wartosc if p.stan_przed else p.wartosc

    z_travat: dict[str, Decimal] = defaultdict(Decimal)
    for s in f.sumy_vat:
        if s.zaliczka:
            continue
        z_travat[s.stawka] += s.netto + s.vat if f.od_brutto else s.netto

    bledy = []
    for stawka in sorted(set(z_pozycji) | set(z_travat)):
        roznica = abs(z_pozycji[stawka] - z_travat[stawka])
        if roznica > tolerancja:
            rodzaj = "brutto" if f.od_brutto else "netto"
            bledy.append(
                f"Stawka {stawka}: suma {rodzaj} pozycji {z_pozycji[stawka]} "
                f"≠ TraVat {z_travat[stawka]} (różnica {roznica}, tolerancja {tolerancja})"
            )
    return bledy
