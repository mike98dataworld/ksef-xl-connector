"""Lista faktur sprzedaży z KSeF (metadane, bez treści) — do uzgodnienia z XL."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterator
from zoneinfo import ZoneInfo

from ksef_connector.ksef.auth import Tokeny
from ksef_connector.ksef.klient import KlientKsef

STREFA = ZoneInfo("Europe/Warsaw")
OKNO_DNI = 90  # ostrożnie: dzielimy długie zakresy na okna
ROZMIAR_STRONY = 250  # maksimum API
GROSZ = Decimal("0.01")


@dataclass(frozen=True)
class FakturaKsef:
    numer_ksef: str
    numer: str  # P_2
    data_wyst: date
    nip_nabywcy: str | None
    brutto: Decimal
    waluta: str
    rodzaj: str  # Vat, Kor, Zal, Roz, ...
    przyjeto: datetime


def _kwota(x) -> Decimal:
    return Decimal(str(x)).quantize(GROSZ)


def _z_metadanych(m: dict) -> FakturaKsef:
    ident = m["buyer"]["identifier"]
    return FakturaKsef(
        numer_ksef=m["ksefNumber"],
        numer=m["invoiceNumber"].strip(),
        data_wyst=date.fromisoformat(m["issueDate"]),
        nip_nabywcy=ident.get("value") if ident.get("type") == "Nip" else None,
        brutto=_kwota(m["grossAmount"]),
        waluta=m["currency"],
        rodzaj=m["invoiceType"],
        przyjeto=datetime.fromisoformat(m["invoicingDate"]),
    )


def _okna(od: date, do: date) -> Iterator[tuple[date, date]]:
    poczatek = od
    while poczatek <= do:
        koniec = min(poczatek + timedelta(days=OKNO_DNI - 1), do)
        yield poczatek, koniec
        poczatek = koniec + timedelta(days=1)


def szukaj_po_numerze(klient: KlientKsef, tokeny: Tokeny, numer: str, data_wyst: date) -> list[FakturaKsef]:
    """Faktury sprzedaży o dokładnie tym numerze (P_2) wystawione w okolicy daty — kontrola tuż przed wysyłką."""
    odp = klient.metadane_faktur(tokeny.access, {
        "subjectType": "Subject1",
        "dateRange": {
            "dateType": "Issue",
            "from": datetime.combine(data_wyst - timedelta(days=3), time.min, STREFA).isoformat(),
            "to": datetime.combine(data_wyst + timedelta(days=4), time.min, STREFA).isoformat(),
        },
        "invoiceNumber": numer,
    }, rozmiar=10)
    return [f for f in map(_z_metadanych, odp["invoices"]) if f.numer == numer.strip()]


def faktury_sprzedazy(klient: KlientKsef, tokeny: Tokeny, od: date, do: date) -> list[FakturaKsef]:
    """Wszystkie faktury, w których kontekst (nasz NIP) jest sprzedawcą, z datą wystawienia w [od, do]."""
    wynik: dict[str, FakturaKsef] = {}
    for okno_od, okno_do in _okna(od, do):
        zakres_od = datetime.combine(okno_od, time.min, STREFA)
        zakres_do = datetime.combine(okno_do + timedelta(days=1), time.min, STREFA)
        offset = 0
        while True:
            odp = klient.metadane_faktur(tokeny.access, {
                "subjectType": "Subject1",
                "dateRange": {"dateType": "Issue", "from": zakres_od.isoformat(), "to": zakres_do.isoformat()},
            }, offset=offset, rozmiar=ROZMIAR_STRONY)
            for m in odp["invoices"]:
                f = _z_metadanych(m)
                if od <= f.data_wyst <= do:
                    wynik[f.numer_ksef] = f
            if not odp["hasMore"]:
                break
            if odp["isTruncated"]:  # limit 10 000 rekordów na zestaw filtrów: przesuwamy początek zakresu
                ostatnia = date.fromisoformat(odp["invoices"][-1]["issueDate"])
                zakres_od = datetime.combine(ostatnia, time.min, STREFA)
                offset = 0
            else:
                offset += 1  # pageOffset to numer strony, nie liczba pominiętych rekordów
    return sorted(wynik.values(), key=lambda f: (f.data_wyst, f.numer))
