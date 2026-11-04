"""Surowe dane XL -> zwalidowany XML FA(3). Jedyne miejsce, które składa mapowanie i walidację."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ksef_connector.mapping import fa3, korekta, xl, zaliczki
from ksef_connector.mapping.model import Faktura, Sprzedawca
from ksef_connector.validate.sumy import bledy_sum
from ksef_connector.validate.xsd import bledy_xsd


@dataclass
class Wynik:
    faktura: Faktura
    xml: bytes
    bledy: list[str]

    @property
    def ok(self) -> bool:
        return not self.bledy


def przygotuj(raw: dict[str, Any], wytworzono: datetime | None = None,
              sprzedawca: Sprzedawca | None = None) -> Wynik:
    """Rzuca xl.BladMapowania, jeśli faktury nie da się zmapować. Błędy walidacji zwraca w Wynik.bledy."""
    if int(raw["naglowek"]["gid_typ"]) == korekta.TYP_FSK:
        faktura = korekta.faktura(raw, sprzedawca)
    elif int(raw["naglowek"]["gid_typ"]) == zaliczki.TYP_FSL:
        faktura = zaliczki.faktura(raw, sprzedawca)
    else:
        faktura = xl.faktura(raw, sprzedawca)
    xml = fa3.zbuduj(faktura, wytworzono)
    return Wynik(faktura=faktura, xml=xml, bledy=bledy_sum(faktura) + bledy_xsd(xml))
