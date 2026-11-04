"""Faktury zaliczkowe FSL (XL 1824 → RodzajFaktury ZAL) i faktury rozliczające zaliczkę (FS z TrV_Zaliczkowy → ROZ).

Wzory XL w KSeF (faktury zaliczkowe wysłane wcześniej przez XL):
- FSL: numer bez „(S)”, bez FaWiersz i WZ (FSL nie ma pozycji w TraElem); P_13/P_14/P_15 = zaliczka (TraVat FSL);
  sekcja Zamowienie = WartoscZamowienia (brutto całego zamówienia) + ZamowienieWiersz z pozycji zamówienia (ZamElem:
  cena i wartość netto, VAT); NrZamowienia; opis; płatność z terminem i kontem proform — także gdy zapłacona.
- Rozliczająca: zwykła FS z dwoma wierszami TraVat — pełna wartość i minus zaliczka (TrV_Zaliczkowy = 1). W KSeF:
  FaWiersz = cała dostawa (WZ), P_13/P_14/P_15 = pozostało do zapłaty, FakturaZaliczkowa = numer KSeF FSL; FSL sprzed
  KSeF: NrKSeFZN = 1 + numer FSL. Całość zapłacona zaliczką: P_15 = 0, bez P_13/P_14 i bez Platnosc.
FSL rozliczanej faktury szukamy po zamówieniu (SQL), a jej numer KSeF dopisuje polecenie `oryginaly` (klucz
raw["zaliczki_ksef"]: numer FSL → numer KSeF albo null, gdy FSL w KSeF nie ma).
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ksef_connector import config
from ksef_connector.mapping import xl
from ksef_connector.mapping.model import Faktura, PozycjaZamowienia, Sprzedawca, SumaVat, Zaliczka, Zamowienie
from ksef_connector.mapping.xl import GROSZ, BladMapowania

TYP_FSL = 1824
# Obowiązkowy KSeF od 1.04.2026: FSL sprzed tej daty może nie być w KSeF (wtedy rozliczenie z NrKSeFZN). FSL młodsza niż ta data musi być w KSeF, zanim wyślemy fakturę, która ją rozlicza.
KSEF_OD = date(2026, 4, 1)


def _wiersz_zamowienia(raw: dict[str, Any], lp: int) -> PozycjaZamowienia:
    if (waluta := xl._tekst(raw.get("waluta")).upper()) not in ("", "PLN"):
        raise BladMapowania(f"Pozycja zamówienia {lp} w walucie {waluta} — FSL obsługujemy tylko w PLN")
    ilosc = Decimal(raw["ilosc"])
    if ilosc <= 0:
        raise BladMapowania(f"Pozycja zamówienia {lp}: ilość {ilosc}")
    stawka = xl.stawka_fa3(raw["stawka"], raw["flaga_vat"])
    procent = Decimal(stawka)
    wartosc = Decimal(raw["wartosc"]).quantize(GROSZ)
    flaga_nb = xl._tekst(raw.get("flaga_nb")).upper()
    if flaga_nb == "B":
        netto = (wartosc * 100 / (100 + procent)).quantize(GROSZ, rounding=ROUND_HALF_UP)
        vat = wartosc - netto
    elif flaga_nb == "N":
        netto = wartosc
        vat = (netto * procent / 100).quantize(GROSZ, rounding=ROUND_HALF_UP)
    else:
        raise BladMapowania(f"Pozycja zamówienia {lp}: nieznana ZaE_FlagaNB {flaga_nb!r}")
    return PozycjaZamowienia(
        lp=lp,
        nazwa=xl._tekst(raw.get("nazwa")) or xl._tekst(raw.get("kod")),
        indeks=xl._tekst(raw.get("kod")) or None,
        jm=xl._tekst(raw.get("jm")) or None,
        ilosc=ilosc.normalize(),
        cena_netto=(netto / ilosc).quantize(GROSZ, rounding=ROUND_HALF_UP),
        netto=netto,
        vat=vat,
        stawka=stawka,
    )


def zamowienie(raw: list[dict[str, Any]] | None) -> Zamowienie:
    if raw is None:
        raise BladMapowania("Brak pozycji zamówienia w eksporcie — wyeksportuj FSL aktualnym faktury_wiele.sql")
    if not raw:
        raise BladMapowania("FSL bez pozycji zamówienia (brak zamówienia w XL?)")
    wiersze = tuple(_wiersz_zamowienia(p, i) for i, p in enumerate(raw, start=1))
    return Zamowienie(wartosc=sum((w.netto + w.vat for w in wiersze), Decimal(0)), wiersze=wiersze)


def faktura(raw: dict[str, Any], sprzedawca: Sprzedawca | None = None) -> Faktura:
    """FSL → faktura zaliczkowa (ZAL)."""
    nag = raw["naglowek"]
    if int(nag["stan"]) == xl.STAN_ANULOWANA:
        raise xl.FakturaAnulowana(f"{nag.get('numer')} (GID {nag['gid_numer']}) jest anulowana — nie wysyłamy")
    if int(nag["stan"]) not in xl.STANY_DO_WYSYLKI:
        raise BladMapowania(f"Stan {nag['stan']} — wysyłamy tylko stany {sorted(xl.STANY_DO_WYSYLKI)}")
    if int(nag["gid_typ"]) != TYP_FSL:
        raise BladMapowania(f"Typ dokumentu {nag['gid_typ']} — to nie FSL ({TYP_FSL})")
    if (waluta := xl._tekst(nag.get("waluta")).upper() or "PLN") != "PLN":
        raise BladMapowania(f"FSL w walucie {waluta} — obsługujemy tylko PLN")
    flaga_nb = xl._tekst(nag.get("flaga_nb")).upper()
    if flaga_nb not in ("N", "B"):
        raise BladMapowania(f"Nieznana TrN_FlagaNB: {flaga_nb!r}")
    if raw.get("pozycje"):
        raise BladMapowania("FSL z pozycjami towarowymi (spięta WZ?) — nieznany przypadek")
    if not raw.get("vat"):
        raise BladMapowania("Brak wierszy TraVat")
    if any(int(v.get("zaliczkowy") or 0) == 1 or Decimal(v["netto"]) <= 0 for v in raw["vat"]):
        raise BladMapowania("FSL z ujemnym lub zaliczkowym wierszem TraVat — nieznany przypadek")

    zam = zamowienie(raw.get("zamowienie_pozycje"))
    sumy = tuple(xl._suma_vat(v, False, False) for v in raw["vat"])
    zaliczka = sum((s.netto + s.vat for s in sumy), Decimal(0))
    if zaliczka > zam.wartosc:
        raise BladMapowania(f"Zaliczka {zaliczka} większa niż wartość zamówienia {zam.wartosc}")
    data_sprz = xl._data_sprzedazy(nag)
    plat = xl.platnosc(raw.get("platnosci"), data_sprz)
    if plat:
        plat = replace(plat, rachunek=config.RACHUNEK_PROFORM)  # jak XL: konto proform, także na zapłaconej

    return Faktura(
        xl_gid_typ=TYP_FSL,
        xl_gid_numer=int(nag["gid_numer"]),
        numer=xl.numer_p2(nag["numer"]),
        data_wystawienia=xl.data_clarion(nag["data_wyst"]),
        data_sprzedazy=data_sprz,
        waluta="PLN",
        od_brutto=flaga_nb == "B",
        sprzedawca=sprzedawca or config.SPRZEDAWCA,
        nabywca=xl.nabywca(raw["nabywca"]),
        pozycje=(),
        sumy_vat=sumy,
        platnosc=plat,
        opis=xl._opis(nag),
        zamowienia=xl._zamowienia(raw.get("zamowienia"), nag),
        zamowienie=zam,
    )


def rozliczenie(raw: dict[str, Any]) -> tuple[Zaliczka, ...]:
    """Faktura rozliczająca: FSL z XL, sprawdzone z wierszami zaliczkowymi TraVat i odszukane w KSeF."""
    numer = raw["naglowek"].get("numer")
    zal_netto = -sum((Decimal(v["netto"]) for v in raw["vat"] if int(v.get("zaliczkowy") or 0) == 1), Decimal(0))
    zal_vat = -sum((Decimal(v["vat"]) for v in raw["vat"] if int(v.get("zaliczkowy") or 0) == 1), Decimal(0))
    if zal_netto <= 0:
        raise BladMapowania(f"{numer}: wiersz zaliczkowy TraVat nie jest ujemny — nieznany przypadek")
    if any(Decimal(v["netto"]) < 0 for v in raw["vat"] if int(v.get("zaliczkowy") or 0) != 1):
        raise BladMapowania(f"{numer}: ujemny wiersz TraVat bez oznaczenia zaliczki — nieznany przypadek")
    if sum((Decimal(v["netto"]) + Decimal(v["vat"]) for v in raw["vat"]), Decimal(0)) < 0:
        raise BladMapowania(f"{numer}: zaliczka większa niż faktura — nieznany przypadek")

    fsl = raw.get("zaliczki")
    if fsl is None:
        raise BladMapowania(f"{numer}: brak faktur zaliczkowych w eksporcie — wyeksportuj aktualnym faktury_wiele.sql")
    if not fsl:
        raise BladMapowania(f"{numer}: rozlicza zaliczkę, ale w XL nie ma FSL do tego zamówienia")
    fsl_netto = sum((Decimal(z["netto"]) for z in fsl), Decimal(0))
    fsl_vat = sum((Decimal(z["vat"]) for z in fsl), Decimal(0))
    if (fsl_netto, fsl_vat) != (zal_netto, zal_vat):
        numery = ", ".join(xl._tekst(z["numer"]) for z in fsl)
        raise BladMapowania(f"{numer}: zaliczki na fakturze {zal_netto}/{zal_vat} ≠ FSL zamówienia ({numery}) "
                            f"{fsl_netto}/{fsl_vat} — sprawdź w XL, które FSL rozlicza")

    w_ksef = raw.get("zaliczki_ksef")
    if w_ksef is None:
        raise BladMapowania(f"{numer}: brak numerów KSeF faktur zaliczkowych — uruchom: "
                            "python -m ksef_connector oryginaly <pliki> --srodowisko prod")
    # Nabywca bez NIP: jego FSL nie idzie do KSeF (jak seria C), więc w odwołaniu jest jako faktura spoza KSeF;
    # samą rozliczającą i tak pominie wysyłka (osoba prywatna bez NIP).
    bez_nip = not re.sub(r"\D", "", xl._tekst(raw["nabywca"].get("nip")))
    wynik = []
    for z in fsl:
        nr = xl.numer_p2(z["numer"])
        if nr not in w_ksef:
            raise BladMapowania(f"{numer}: {nr} nie była szukana w KSeF — uruchom ponownie oryginaly")
        if w_ksef[nr] is None and xl.data_clarion(z["data_wyst"]) >= KSEF_OD and not bez_nip:
            raise BladMapowania(f"{numer}: faktury zaliczkowej {nr} nie ma w KSeF — najpierw wyślij FSL")
        wynik.append(Zaliczka(numer=nr, numer_ksef=w_ksef[nr]))
    return tuple(wynik)
