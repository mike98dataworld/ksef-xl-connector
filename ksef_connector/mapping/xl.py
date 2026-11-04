"""Surowe dane XL (wynik `source/sql/faktura.sql`) -> model Faktura.

Format wejścia (dict, klucze = aliasy z faktura.sql, kwoty jako Decimal):
    naglowek: gid_typ, gid_numer, stan, numer, seria, data_wyst, data_sprz, waluta, flaga_nb, opis
    nabywca:  nip, nip_prefiks, kraj, nazwa1..3, ulica, adres, kodp, miasto
    pozycje:  [lp, kod, nazwa, ilosc, jm, stawka, flaga_vat, netto, brutto]
    vat:      [stawka, flaga_vat, netto, vat, zaliczkowy]
    platnosci: [termin, forma, forma_nr, kwota, pozostaje, rachunek_id, data_rozl]
    wz:       [numer]
    zamowienia: [typ, numer, miesiac, rok, seria]  (ZamNag)

Zasada: wszystko, czego nie umiemy jednoznacznie zmapować, rzuca BladMapowania.
Faktura z błędem nie idzie do KSeF — lepiej wstrzymać, niż wysłać źle.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ksef_connector import config
from ksef_connector.mapping.model import Adres, Faktura, Nabywca, Platnosc, Pozycja, Sprzedawca, SumaVat

CLARION_EPOKA = date(1800, 12, 28)
TYP_FS = 2033
TYP_FSE = 2037  # faktura eksportowa; wysyłamy tylko WDT (TrN_ExpoNorm = 6)
EXPO_WDT = 6  # 7 = sprzedaż do konsumentów UE z obcym VAT (OSS) — XL ich do KSeF nie wysyłał
# TKodyKrajowUE (Grecja = EL, nie GR)
KRAJE_UE = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU", "IE", "IT",
            "LT", "LU", "LV", "MT", "NL", "PT", "RO", "SE", "SI", "SK", "XI"}
KURS = Decimal("0.000001")
STAN_ANULOWANA = 6
# TrN_Stan (ustalone na płatnościach TraPlat): 3 = zatwierdzona, niezapłacona; 4 = niezapłacona/częściowo
# (XL wysyłał je do KSeF); 5 = rozliczona (zapłacona); 6 = anulowana. Bufor (1, 2) nie jest wysyłany.
STANY_DO_WYSYLKI = {3, 4, 5}
GROSZ = Decimal("0.01")

# TrV_FlagaVat / TrE_FlagaVat: 1 = sprzedaż opodatkowana stawką liczbową (potwierdzone na danych produkcyjnych, 23%).
# Pozostałe wartości (zw / np / ...) celowo nieobsługiwane, dopóki nie potwierdzimy znaczenia.
FLAGA_VAT_OPODATKOWANA = 1
STAWKI_LICZBOWE = {"23", "22", "8", "7", "5", "4", "3"}


class BladMapowania(Exception):
    """Faktury nie da się poprawnie zmapować — nie wysyłamy jej."""


class FakturaAnulowana(BladMapowania):
    pass


def data_clarion(dni: int) -> date:
    return CLARION_EPOKA + timedelta(days=int(dni))


def numer_p2(numer_xl: str) -> str:
    """Numer dokładnie jak w XL, z prefiksem "(S)" — tak XL wysyłał do KSeF (np. "(S)FS-59/10/26/B").

    Musi być identyczny, bo KSeF wykrywa duplikaty m.in. po numerze faktury.
    """
    return numer_xl.strip()


def stawka_fa3(stawka: Decimal, flaga_vat: int, wdt: bool = False) -> str:
    if int(flaga_vat) != FLAGA_VAT_OPODATKOWANA:
        raise BladMapowania(f"FlagaVat={flaga_vat} (stawka {stawka}) — znaczenie niepotwierdzone, brak mapowania")
    tekst = format(Decimal(stawka).normalize(), "f")
    if wdt:
        if tekst != "0":
            raise BladMapowania(f"WDT ze stawką {tekst}% — oczekiwane 0% (VAT w walucie obcej poza zakresem)")
        return "0 WDT"
    if tekst == "0":
        raise BladMapowania("Stawka 0% wymaga rozróżnienia 0 KR / 0 WDT / 0 EX — poza zakresem MVP")
    if tekst not in STAWKI_LICZBOWE:
        raise BladMapowania(f"Nieznana stawka VAT: {stawka}")
    return tekst


def _tekst(wartosc: Any) -> str:
    return "" if wartosc is None else str(wartosc).strip()


def _scal(*czesci: Any) -> str:
    return " ".join(t for t in (_tekst(c) for c in czesci) if t)


def nabywca(raw: dict[str, Any], wdt: bool = False) -> Nabywca:
    nazwa = _scal(raw.get("nazwa1"), raw.get("nazwa2"), raw.get("nazwa3"))
    if not nazwa:
        raise BladMapowania("Brak nazwy nabywcy")

    kraj = _tekst(raw.get("kraj")).upper() or "PL"
    prefiks = _tekst(raw.get("nip_prefiks")).upper()
    nip = re.sub(r"\D", "", _tekst(raw.get("nip"))) or None
    kod_ue = None
    if wdt:
        # WDT: KodUE + NrVatUE (litery dozwolone, np. CY „60076113Q”), bez prefiksu kraju w numerze
        kod_ue = {"GR": "EL"}.get(prefiks or kraj, prefiks or kraj)
        if kod_ue not in KRAJE_UE:
            raise BladMapowania(f"WDT: kraj nabywcy {kod_ue!r} spoza UE")
        nip = re.sub(r"[^0-9A-Z]", "", _tekst(raw.get("nip")).upper()).removeprefix(kod_ue) or None
        if not nip:
            raise BladMapowania("WDT bez numeru VAT UE nabywcy")
    elif prefiks not in ("", "PL") or kraj != "PL":
        raise BladMapowania(f"Nabywca zagraniczny (prefiks={prefiks!r}, kraj={kraj!r}) — poza zakresem MVP")
    if not wdt and nip is not None and len(nip) != 10:
        raise BladMapowania(f"Niepoprawny NIP nabywcy: {nip!r}")

    linia1 = _scal(raw.get("ulica"), raw.get("adres"))
    linia2 = _scal(raw.get("kodp"), raw.get("miasto")) or None
    if linia1:
        adres = Adres(kod_kraju=kraj, linia1=linia1, linia2=linia2)
    elif linia2:
        adres = Adres(kod_kraju=kraj, linia1=linia2)
    else:
        adres = None
    return Nabywca(nazwa=nazwa, nip=nip, adres=adres, kod_ue=kod_ue)


def pozycja(raw: dict[str, Any], lp: int, od_brutto: bool, wdt: bool = False, waluta_obca: bool = False) -> Pozycja:
    ilosc = Decimal(raw["ilosc"])
    if ilosc == 0:
        raise BladMapowania(f"Pozycja {lp}: ilość 0")
    if waluta_obca:
        if "wartosc_wal" not in raw:
            raise BladMapowania("Brak wartości w walucie — wyeksportuj fakturę aktualnym faktury_wiele.sql")
        wartosc = Decimal(raw["wartosc_wal"]).quantize(GROSZ)  # TrE_WartoscPoRabacie
        kurs = (Decimal(raw["kurs_l"]) / Decimal(raw["kurs_m"])).quantize(KURS)
    else:
        wartosc = Decimal(raw["brutto"] if od_brutto else raw["netto"]).quantize(GROSZ)
        kurs = None
    # Cena liczona z wartości/ilości. TODO: jeśli XL ma cenę po rabacie w TraElem, brać ją wprost.
    cena = (wartosc / ilosc).quantize(GROSZ, rounding=ROUND_HALF_UP)
    return Pozycja(
        lp=lp,
        nazwa=_tekst(raw.get("nazwa")) or _tekst(raw.get("kod")),
        indeks=_tekst(raw.get("kod")) or None,
        jm=_tekst(raw.get("jm")) or None,
        ilosc=ilosc.normalize(),
        cena=cena,
        wartosc=wartosc,
        stawka=stawka_fa3(raw["stawka"], raw["flaga_vat"], wdt),
        kurs=kurs,
    )


# Nazwy form płatności z XL → TFormaPlatnosci. Tylko pewne dopasowania; reszta jako PlatnoscInna z opisem.
FORMY_PLATNOSCI = (
    (re.compile(r"^przelew(\s+\d+\s*dni)?$", re.I), 6),
    (re.compile(r"^got[oó]wka", re.I), 1),
    (re.compile(r"^karta", re.I), 2),
)
PRZELEW = 6
# Formy terminowe („Przelew 14/21/30 dni”). Pozostałe (Przelew, Blik, Paynow, P24, Karta, PayPo, Pobranie) to
# przedpłata/proforma/sklep: w praktyce zawsze zapłacone, z terminem = data sprzedaży (sprawdzone na danych).
TERMINOWA = re.compile(r"\d+\s*dni", re.I)
# Pobranie (wymóg księgowości): bez „zapłacono” i terminu, forma „Za pobraniem”, Rozliczenie/DoZaplaty = P_15
# (należność jeszcze nieuregulowana). FA(3) nie ma kodu formy dla pobrania, więc idzie jako PlatnoscInna + OpisPlatnosci.
POBRANIE = re.compile(r"^pobranie", re.I)
OPIS_POBRANIA = "Za pobraniem"


def platnosc(raw: list[dict[str, Any]] | None, data_sprzedazy: date | None = None, waluta: str = "PLN") -> Platnosc | None:
    if raw is None:
        raise BladMapowania("Brak danych o płatności — wyeksportuj fakturę aktualnym faktura.sql")
    if not raw:
        return None
    nazwy = {_tekst(p.get("forma")) for p in raw}
    if len(nazwy) != 1:
        raise BladMapowania(f"Kilka form płatności na jednej fakturze: {sorted(nazwy)}")
    nazwa = nazwy.pop()
    if POBRANIE.match(nazwa):
        return Platnosc(terminy=(), forma=None, forma_opis=OPIS_POBRANIA, rachunek=None, nazwa=nazwa,
                        do_zaplaty_calosc=True)
    forma = next((kod for wzor, kod in FORMY_PLATNOSCI if wzor.match(nazwa)), None)

    # Zasady XL (faktury pobrane z KSeF PROD): zapłacona → Zaplacono + DataZaplaty, bez „Kwota do zapłaty”;
    # niezapłacona → „Kwota do zapłaty w terminie …” i rachunek do przelewu.
    kwota_razem = sum((Decimal(p.get("kwota") or 0) for p in raw), Decimal(0))
    pozostaje = sum((Decimal(p.get("pozostaje") or 0) for p in raw), Decimal(0))
    if 0 < pozostaje < kwota_razem:
        raise BladMapowania(f"Faktura częściowo zapłacona (pozostaje {pozostaje} z {kwota_razem}) — poza zakresem MVP")
    zaplacono = kwota_razem > 0 and pozostaje == 0
    data_zaplaty = None
    if zaplacono and data_sprzedazy and not TERMINOWA.search(nazwa):
        # Przedpłata/proforma: data sprzedaży = dzień wpływu środków (wymóg księgowości). Data rozliczenia w XL
        # bywa późniejsza i w KSeF wyglądała jak zapłata po terminie (np. termin 12.10, rozliczenie 16.10).
        data_zaplaty = data_sprzedazy
    elif zaplacono:
        daty = [int(p.get("data_rozl") or 0) for p in raw]
        if not all(0 < d < 999999 for d in daty):  # 999999 = nierozliczona w XL
            raise BladMapowania("Faktura zapłacona, ale bez daty rozliczenia — wyeksportuj aktualnym faktura.sql")
        data_zaplaty = data_clarion(max(daty))

    terminy = tuple(sorted({data_clarion(p["termin"]) for p in raw if p.get("termin")}))
    raty = () if zaplacono else tuple(
        (data_clarion(p["termin"]), Decimal(p["kwota"]).quantize(GROSZ))
        for p in raw if p.get("termin") and Decimal(p.get("pozostaje") or 0) > 0)
    rachunek = None
    if forma == PRZELEW and not zaplacono:
        rachunek = config.RACHUNKI_DO_ZAPLATY.get(waluta)
        if rachunek is None:
            raise BladMapowania(f"Brak konta do zapłaty w walucie {waluta} (config.RACHUNKI_DO_ZAPLATY)")
    return Platnosc(terminy=terminy, forma=forma, forma_opis=None if forma else nazwa, rachunek=rachunek,
                    nazwa=nazwa, raty=raty, zaplacono=zaplacono, data_zaplaty=data_zaplaty)


def _data_sprzedazy(nag: dict[str, Any]) -> date:
    if not nag.get("data_sprz"):
        raise BladMapowania("Brak daty sprzedaży (TrN_Data3) — wyeksportuj fakturę aktualnym faktura.sql")
    return data_clarion(nag["data_sprz"])


# ZaN_ZamTyp → prefiks numeru, jak na wydrukach XL (CDN.NumerDokumentu z typem 960 daje ogólne „Zam-”)
PREFIKSY_ZAMOWIEN = {1280: "ZS"}


def _zamowienia(raw: list[dict[str, Any]] | None, nag: dict[str, Any]) -> tuple[str, ...]:
    """Jak XL: numer obcy zamówienia (TrN_ZamDokumentObcy, np. „Zamówienie PL…”), a gdy pusty — ZS-… z ZamNag."""
    if raw is None or "zam_obcy" not in nag:
        raise BladMapowania("Brak zamówień w eksporcie — wyeksportuj fakturę aktualnym faktura.sql")
    if obcy := _tekst(nag["zam_obcy"]):
        return (obcy,)
    numery = []
    for z in raw:
        prefiks = PREFIKSY_ZAMOWIEN.get(int(z["typ"]))
        if not prefiks:
            continue  # pole nieobowiązkowe: nieznany rodzaj zamówienia pomijamy zamiast zgadywać prefiks
        seria = _tekst(z.get("seria"))
        numer = f"{prefiks}-{int(z['numer'])}/{int(z['miesiac']):02d}/{int(z['rok']) % 100:02d}"
        numery.append(f"{numer}/{seria}" if seria else numer)
    return tuple(sorted(set(numery)))


def _opis(nag: dict[str, Any]) -> str | None:
    if "opis" not in nag:
        raise BladMapowania("Brak opisu dokumentu w eksporcie — wyeksportuj fakturę aktualnym faktura.sql")
    # XL zapisuje końce linii jak Windows (\r\n); w XML zostałyby jako &#13; — XL wysyłał samo \n.
    return _tekst(nag["opis"]).replace("\r\n", "\n").replace("\r", "\n") or None


def _wz(raw: list[dict[str, Any]] | None) -> tuple[str, ...]:
    if raw is None:
        raise BladMapowania("Brak numerów WZ — wyeksportuj fakturę aktualnym faktura.sql")
    return tuple(n for n in (_tekst(w.get("numer")) for w in raw) if n)


def _suma_vat(v: dict[str, Any], wdt: bool, waluta_obca: bool) -> SumaVat:
    stawka = stawka_fa3(v["stawka"], v["flaga_vat"], wdt)
    if waluta_obca:
        # TrV_NettoR/VatR są w PLN; w walucie faktury jest TrV_WartoscWal. Przy 0% WDT VAT = 0, więc wartość = netto.
        if "wartosc_wal" not in v:
            raise BladMapowania("Brak wartości w walucie w TraVat — wyeksportuj fakturę aktualnym faktury_wiele.sql")
        return SumaVat(stawka=stawka, netto=Decimal(v["wartosc_wal"]).quantize(GROSZ), vat=Decimal(0))
    return SumaVat(stawka=stawka, netto=Decimal(v["netto"]).quantize(GROSZ), vat=Decimal(v["vat"]).quantize(GROSZ),
                   zaliczka=int(v.get("zaliczkowy") or 0) == 1)


def faktura(raw: dict[str, Any], sprzedawca: Sprzedawca | None = None) -> Faktura:
    """sprzedawca: nadpisanie Podmiot1 (na TEST fikcyjny NIP zamiast danych z firma.toml)."""
    nag = raw["naglowek"]
    if int(nag["stan"]) == STAN_ANULOWANA:
        raise FakturaAnulowana(f"{nag.get('numer')} (GID {nag['gid_numer']}) jest anulowana — nie wysyłamy")
    if int(nag["stan"]) not in STANY_DO_WYSYLKI:
        raise BladMapowania(f"Stan {nag['stan']} — wysyłamy tylko stany {sorted(STANY_DO_WYSYLKI)}")
    typ = int(nag["gid_typ"])
    if typ not in (TYP_FS, TYP_FSE):
        raise BladMapowania(f"Typ dokumentu {typ} — obsługujemy FS ({TYP_FS}) i FSE ({TYP_FSE})")
    wdt = typ == TYP_FSE
    if wdt and int(nag.get("expo_norm") or 0) != EXPO_WDT:
        raise BladMapowania(f"FSE ({TYP_FSE}) z TrN_ExpoNorm={nag.get('expo_norm')} — tylko WDT ({EXPO_WDT}); sprzedaży "
                            "do konsumentów UE (OSS) XL do KSeF nie wysyłał")

    waluta = _tekst(nag.get("waluta")).upper() or "PLN"
    if waluta != "PLN" and not wdt:
        raise BladMapowania(f"Waluta {waluta} — w walucie obcej obsługujemy tylko WDT (FSE)")
    waluta_obca = waluta != "PLN"

    flaga_nb = _tekst(nag.get("flaga_nb")).upper()
    if flaga_nb not in ("N", "B"):
        raise BladMapowania(f"Nieznana TrN_FlagaNB: {flaga_nb!r}")
    od_brutto = flaga_nb == "B"

    if not raw.get("pozycje"):
        raise BladMapowania("Brak pozycji (brak spiętych WZ?)")
    if not raw.get("vat"):
        raise BladMapowania("Brak wierszy TraVat")
    rozliczane = ()
    if any(int(v.get("zaliczkowy") or 0) == 1 for v in raw["vat"]):
        # Faktura rozliczająca zaliczkę → RodzajFaktury ROZ + odwołanie do FSL (mapping/zaliczki.py)
        if typ != TYP_FS or waluta_obca:
            raise BladMapowania("Rozliczenie zaliczki na fakturze eksportowej / w walucie — poza zakresem")
        from ksef_connector.mapping import zaliczki
        rozliczane = zaliczki.rozliczenie(raw)
    elif any(Decimal(v["netto"]) < 0 for v in raw["vat"]):
        raise BladMapowania("Ujemny wiersz TraVat bez oznaczenia zaliczki — nieznany przypadek")
    sumy_vat = tuple(_suma_vat(v, wdt, waluta_obca) for v in raw["vat"])
    plat = platnosc(raw.get("platnosci"), _data_sprzedazy(nag), waluta)
    if rozliczane and sum((s.netto + s.vat for s in sumy_vat), Decimal(0)) == 0:
        plat = None  # całość zapłacona zaliczką: XL nie podawał płatności

    return Faktura(
        xl_gid_typ=int(nag["gid_typ"]),
        xl_gid_numer=int(nag["gid_numer"]),
        numer=numer_p2(nag["numer"]),
        data_wystawienia=data_clarion(nag["data_wyst"]),
        data_sprzedazy=_data_sprzedazy(nag),
        waluta=waluta,
        od_brutto=od_brutto,
        sprzedawca=sprzedawca or config.SPRZEDAWCA,
        nabywca=nabywca(raw["nabywca"], wdt),
        pozycje=tuple(pozycja(p, i, od_brutto, wdt, waluta_obca) for i, p in enumerate(raw["pozycje"], start=1)),
        platnosc=plat,
        wz=_wz(raw.get("wz")),
        opis=_opis(nag),
        zamowienia=_zamowienia(raw.get("zamowienia"), nag),
        sumy_vat=sumy_vat,
        zaliczki=rozliczane,
    )
