"""Korekty FSK (2041) → FA(3) KOR, jak u XL (wzory: korekty wysłane wcześniej przez XL). Dane fikcyjne."""
import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from lxml import etree

from ksef_connector.mapping import xl
from ksef_connector.mapping.fa3 import NS
from ksef_connector.pipeline import przygotuj
from ksef_connector.source.fixtures import FixtureSource

FIXTURES = FixtureSource(Path(__file__).parent / "fixtures")
WYTWORZONO = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
NR_KSEF_ORG = "1111111111-20261016-0A1B2C3D4E5F-AB"


def _w_ksef(nazwa: str) -> dict:
    """Faktura z fixtures zbudowana naszym fa3 — udaje fakturę korygowaną pobraną z KSeF."""
    wynik = przygotuj(FIXTURES.pobierz(nazwa), WYTWORZONO)
    assert wynik.ok, wynik.bledy
    return {"numer_ksef": NR_KSEF_ORG, "xml": wynik.xml.decode()}


def korekta_kb() -> dict:
    """FS-46/10/26/B (netto): 21 szt. PNL-01 po 34,88 → zwrot 4 szt."""
    org = FIXTURES.pobierz("fs_netto_wzor_xl")
    return {
        "naglowek": {"gid_typ": 2041, "gid_numer": 900100, "stan": 3, "numer": "(S)FSK-1/10/26/KB", "seria": "KB",
                     "data_wyst": 82483, "data_sprz": 82483, "waluta": "PLN", "flaga_nb": "N",
                     "przyczyna": "Rezygnacja z części zamówienia.", "opis": ""},
        "korygowana": {"gid_typ": 2033, "gid_numer": 900005, "numer": "(S)FS-46/10/26/B", "data_wyst": 82472},
        "nabywca": org["nabywca"],
        "pozycje": [{"lp": 1, "kod": "PNL-01", "nazwa": "PANEL ŚCIENNY BIAŁY MAT", "ilosc": -4, "jm": "szt.",
                     "stawka": 23, "flaga_vat": 1, "netto": -139.52, "brutto": -171.61}],
        "vat": [{"stawka": 23, "flaga_vat": 1, "netto": -139.52, "vat": -32.09, "zaliczkowy": 0}],
        "platnosci": [{"termin": 82497, "forma": "Przelew 14 dni", "kwota": 171.61, "pozostaje": 171.61,
                       "data_rozl": 999999}],
        "wz": [{"numer": "WZK-1/10/26/KB"}],
        "oryginal_ksef": _w_ksef("fs_netto_wzor_xl"),
    }


def korekta_kcf() -> dict:
    """FS-3/10/26/C/F (brutto): 3 szt. PNL-003 po 150 brutto → zwrot całości."""
    org = FIXTURES.pobierz("fs_brutto_firma_cf")
    return {
        "naglowek": {"gid_typ": 2041, "gid_numer": 900101, "stan": 3, "numer": "(S)FSK-1/10/26/KC/F",
                     "seria": "KC/F", "data_wyst": 82483, "data_sprz": 82483, "waluta": "PLN", "flaga_nb": "B",
                     "przyczyna": "Rezygnacja z całego zamówienia", "opis": ""},
        "korygowana": {"gid_typ": 2033, "gid_numer": 900003, "numer": "(S)FS-3/10/26/C/F", "data_wyst": 82476},
        "nabywca": org["nabywca"],
        "pozycje": [{"lp": 1, "kod": "PNL-003", "nazwa": "Panel 3D", "ilosc": -3, "jm": "szt", "stawka": 23,
                     "flaga_vat": 1, "netto": -365.85, "brutto": -450.00}],
        "vat": [{"stawka": 23, "flaga_vat": 1, "netto": -365.85, "vat": -84.15, "zaliczkowy": 0}],
        "platnosci": [{"termin": 82497, "forma": "Przelew 14 dni", "kwota": 450.00, "pozostaje": 450.00,
                       "data_rozl": 999999}],
        "wz": [{"numer": "WZK-509/10/26"}],
        "oryginal_ksef": _w_ksef("fs_brutto_firma_cf"),
    }


def pole(xml, sciezka: str) -> str | None:
    return xml.findtext("/".join(f"{{{NS}}}{c}" for c in sciezka.split("/")))


def wiersze(xml) -> list[tuple]:
    q = lambda w, t: w.findtext(f"{{{NS}}}{t}")
    return [(q(w, "NrWierszaFa"), q(w, "Indeks"), q(w, "P_8B"), q(w, "P_9A") or q(w, "P_9B"),
             q(w, "P_11") or q(w, "P_11A"), q(w, "StanPrzed")) for w in xml.iter(f"{{{NS}}}FaWiersz")]


def zbuduj(raw: dict):
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.ok, wynik.bledy
    return wynik, etree.fromstring(wynik.xml)


def test_korekta_netto_jak_xl():
    wynik, x = zbuduj(korekta_kb())
    assert wynik.faktura.rodzaj == "KOR"
    assert pole(x, "Fa/RodzajFaktury") == "KOR"
    assert pole(x, "Fa/P_2") == "(S)FSK-1/10/26/KB"
    assert pole(x, "Fa/PrzyczynaKorekty") == "Rezygnacja z części zamówienia."
    assert pole(x, "Fa/DaneFaKorygowanej/DataWystFaKorygowanej") == "2026-10-16"  # z KSeF, nie z XL
    assert pole(x, "Fa/DaneFaKorygowanej/NrFaKorygowanej") == "(S)FS-46/10/26/B"
    assert pole(x, "Fa/DaneFaKorygowanej/NrKSeF") == "1"
    assert pole(x, "Fa/DaneFaKorygowanej/NrKSeFFaKorygowanej") == NR_KSEF_ORG
    assert pole(x, "Fa/WZ") == "WZK-1/10/26/KB"
    assert pole(x, "Fa/P_6") is None  # korekta: tylko data wystawienia, jak u XL (BOK)
    assert (pole(x, "Fa/P_13_1"), pole(x, "Fa/P_14_1"), pole(x, "Fa/P_15")) == ("-139.52", "-32.09", "-171.61")
    assert wiersze(x) == [("1", "PNL-01", "21", "34.88", "732.48", "1"),
                          ("1", "PNL-01", "17", "34.88", "592.96", None)]


def test_korekta_na_minus_bez_do_rozliczenia_i_kwoty_do_zaplaty_z_kontem_proform():
    _, x = zbuduj(korekta_kb())
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}Rozliczenie") is None  # BOK: kwota korekty tylko w P_15 i podsumowaniu
    assert pole(x, "Fa/Rozliczenie/DoZaplaty") is None
    assert pole(x, "Fa/Platnosc/TerminPlatnosci/Termin") == "2026-11-10"
    assert pole(x, "Fa/Platnosc/FormaPlatnosci") == "6"
    assert pole(x, "Fa/Platnosc/Zaplacono") is None
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == "45111122220000000000000003"  # konto proform (BOK)
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}DodatkowyOpis") is None


def test_korekta_brutto_do_zera():
    _, x = zbuduj(korekta_kcf())
    assert wiersze(x) == [("1", "PNL-003", "3", "150.00", "450.00", "1"),
                          ("1", "PNL-003", "0", "150.00", "0.00", None)]
    assert pole(x, "Fa/P_15") == "-450.00" and x.find(f"{{{NS}}}Fa/{{{NS}}}Rozliczenie") is None


def test_faktura_vat_bez_danych_korekty():
    _, x = zbuduj(FIXTURES.pobierz("fs_netto_wzor_xl") | {})
    assert pole(x, "Fa/RodzajFaktury") == "VAT"
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}DaneFaKorygowanej") is None
    assert all(w[5] is None for w in wiersze(x))


def _wstrzymana(raw: dict, komunikat: str) -> None:
    with pytest.raises(xl.BladMapowania, match=komunikat):
        przygotuj(raw, WYTWORZONO)


def test_bez_faktury_korygowanej_z_ksef_wstrzymana():
    raw = korekta_kb()
    del raw["oryginal_ksef"]
    _wstrzymana(raw, "oryginaly")


def test_inny_nabywca_niz_na_fakturze_korygowanej_wstrzymana():
    raw = korekta_kb()
    raw["nabywca"] = dict(raw["nabywca"], nip="9999999999")
    _wstrzymana(raw, "Nabywca korekty")


def test_inny_numer_faktury_korygowanej_wstrzymana():
    raw = korekta_kb()
    raw["korygowana"]["numer"] = "(S)FS-47/10/26/B"
    _wstrzymana(raw, "numer")


def test_towaru_nie_ma_na_fakturze_korygowanej_wstrzymana():
    raw = korekta_kb()
    raw["pozycje"][0]["kod"] = "INNY"
    _wstrzymana(raw, "w 0 wierszach")


def test_korekta_wieksza_niz_faktura_wstrzymana():
    raw = korekta_kb()
    raw["pozycje"][0].update(ilosc=-30, netto=-1046.40, brutto=-1287.07)
    _wstrzymana(raw, "mniej niż zero")


def test_korekta_na_plus_wstrzymana():
    raw = korekta_kb()
    for p in raw["pozycje"] + raw["vat"]:
        for k in ("ilosc", "netto", "vat", "brutto"):
            if k in p:
                p[k] = -p[k]
    _wstrzymana(raw, "na plus")


def test_sumy_korekty_kontrolowane():
    raw = korekta_kb()
    raw["vat"][0]["netto"] = -139.51  # TraVat ≠ różnica pozycji
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.bledy and "netto" in wynik.bledy[0]


def test_konto_proform_na_kazdej_korekcie_takze_bez_przelewu():
    raw = korekta_kb()
    raw["platnosci"][0]["forma"] = "Blik"
    _, x = zbuduj(raw)
    assert pole(x, "Fa/Platnosc/OpisPlatnosci") == "Blik"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == "45111122220000000000000003"


def test_korekta_bez_nip_pomijana_jak_seria_c():
    raw = korekta_kb()
    raw["nabywca"] = dict(raw["nabywca"], nip="")
    raw["oryginal_ksef"] = copy.deepcopy(raw["oryginal_ksef"])
    raw["oryginal_ksef"]["xml"] = raw["oryginal_ksef"]["xml"].replace(
        "<NIP>1234563218</NIP>", "<BrakID>1</BrakID>")
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.faktura.nabywca.nip is None  # wysylka.py pomija dokumenty bez NIP (WYSYLAJ_BEZ_NIP = False)
