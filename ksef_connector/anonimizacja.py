"""Anonimizacja eksportów z XL do testów na TEST/DEMO: zostaje struktura (numery, daty, kwoty, ilości, stawki,
liczba pozycji, typ nabywcy), znikają dane kontrahentów i nazwy towarów. Wynik → fixtures_anon/ (poza gitem)."""
from __future__ import annotations

import copy
from typing import Any

from ksef_connector.ksef.crypto import losowy_nip


def anonimizuj(raw: dict[str, Any], nr: int) -> dict[str, Any]:
    wynik = copy.deepcopy(raw)
    if (wynik.get("naglowek") or {}).get("opis"):
        wynik["naglowek"]["opis"] = f"Opis testowy {nr}"  # opis bywa nazwą klienta
    nabywca = raw.get("nabywca") or {}
    firma = bool(str(nabywca.get("nip") or "").strip())
    wynik["nabywca"] = {
        "nip": losowy_nip() if firma else "",
        "nip_prefiks": nabywca.get("nip_prefiks", ""),
        "kraj": nabywca.get("kraj") or "PL",
        "nazwa1": f"Firma Testowa {nr} Sp. z o.o." if firma else f"Klient Testowy {nr}",
        "nazwa2": "", "nazwa3": "",
        "ulica": f"ul. Testowa {nr}", "adres": "", "kodp": "00-001", "miasto": "Warszawa",
    }
    for j, poz in enumerate(wynik.get("pozycje") or [], start=1):
        poz["nazwa"] = f"Produkt testowy {nr}.{j}"
        poz["kod"] = f"TEST-{nr}-{j}"
    return wynik
