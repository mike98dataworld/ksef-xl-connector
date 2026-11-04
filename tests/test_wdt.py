"""Faktury eksportowe FSE (2037): WDT 0% do firm z UE w walucie, jak u XL (wzory: faktury wysłane wcześniej przez XL). Dane fikcyjne."""
import copy
from datetime import datetime, timezone

import pytest
from lxml import etree

from ksef_connector.mapping import xl
from ksef_connector.mapping.fa3 import NS
from ksef_connector.pipeline import przygotuj

WYTWORZONO = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

WDT_EUR = {
    "naglowek": {"gid_typ": 2037, "gid_numer": 900200, "stan": 5, "numer": "(S)FSE-1/10/26/B/LV", "seria": "B/LV",
                 "data_wyst": 82475, "data_sprz": 82475, "waluta": "EUR", "flaga_nb": "N", "expo_norm": 6,
                 "zam_obcy": "", "opis": "Delivery address: Testowa iela 1, Riga, Latvia"},
    "nabywca": {"nip": "40000000001", "nip_prefiks": "LV", "kraj": "LV", "nazwa1": "TESTA FIRMA SIA ", "nazwa2": "",
                "nazwa3": "", "ulica": "Testowa iela 1 ", "adres": "", "kodp": "LV-1000", "miasto": "Riga"},
    "pozycje": [
        {"lp": 1, "kod": "TST-1", "nazwa": "PRODUKT TESTOWY", "ilosc": 30, "jm": "szt.", "stawka": 0, "flaga_vat": 1,
         "netto": 1052.94, "brutto": 1052.94, "wartosc_wal": 243.90, "kurs_m": 1, "kurs_l": 4.3171},
        {"lp": 2, "kod": "TRANSPORT", "nazwa": "TRANSPORT", "ilosc": 1, "jm": "szt.", "stawka": 0, "flaga_vat": 1,
         "netto": 215.86, "brutto": 215.86, "wartosc_wal": 50.00, "kurs_m": 1, "kurs_l": 4.3171},
    ],
    "vat": [{"stawka": 0, "flaga_vat": 1, "netto": 1268.80, "vat": 0, "zaliczkowy": 0, "wartosc_wal": 293.90}],
    "platnosci": [{"termin": 82475, "forma": "Przelew", "kwota": 293.90, "pozostaje": 0, "data_rozl": 82476}],
    "wz": [{"numer": "WZE-1/10/26/B/LV"}],
    "zamowienia": [{"typ": 1280, "numer": 1, "miesiac": 10, "rok": 2026, "seria": "B/LV"}],
}


def wdt(**zmiany) -> dict:
    raw = copy.deepcopy(WDT_EUR)
    for sekcja, pola in zmiany.items():
        raw[sekcja].update(pola)
    return raw


def pole(xml, sciezka: str) -> str | None:
    return xml.findtext("/".join(f"{{{NS}}}{c}" for c in sciezka.split("/")))


def zbuduj(raw: dict):
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.ok, wynik.bledy
    return etree.fromstring(wynik.xml)


def test_wdt_w_euro_jak_xl():
    x = zbuduj(wdt())
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/KodUE") == "LV"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/NrVatUE") == "40000000001"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/NIP") is None
    assert pole(x, "Podmiot2/Adres/KodKraju") == "LV"
    assert pole(x, "Fa/KodWaluty") == "EUR"
    assert pole(x, "Fa/WZ") == "WZE-1/10/26/B/LV"
    assert pole(x, "Fa/P_13_6_2") == "293.90" and pole(x, "Fa/P_15") == "293.90"
    assert pole(x, "Fa/P_13_1") is None and pole(x, "Fa/P_14_1") is None
    q = lambda w, t: w.findtext(f"{{{NS}}}{t}")
    assert [(q(w, "P_8B"), q(w, "P_9A"), q(w, "P_11"), q(w, "P_12"), q(w, "KursWaluty"))
            for w in x.iter(f"{{{NS}}}FaWiersz")] == [("30", "8.13", "243.90", "0 WDT", "4.3171"),
                                                     ("1", "50.00", "50.00", "0 WDT", "4.3171")]
    assert pole(x, "Fa/WarunkiTransakcji/Zamowienia/NrZamowienia") == "ZS-1/10/26/B/LV"


def test_wdt_zaplacona_przelewem_jak_proforma_bez_konta():
    x = zbuduj(wdt())
    assert pole(x, "Fa/Platnosc/Zaplacono") == "1" and pole(x, "Fa/Platnosc/DataZaplaty") == "2026-10-19"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") is None


@pytest.mark.parametrize("waluta, konto", [("EUR", "72111122220000000000000002"), ("HUF", "123456781234567812345678")])
def test_wdt_niezaplacona_konto_walutowe_i_kwota_w_walucie(waluta, konto):
    raw = wdt(naglowek={"waluta": waluta})
    raw["platnosci"][0].update(forma="Przelew 14 dni", pozostaje=293.90, termin=82489)
    x = zbuduj(raw)
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == konto
    opisy = {d.findtext(f"{{{NS}}}Klucz"): d.findtext(f"{{{NS}}}Wartosc") for d in x.iter(f"{{{NS}}}DodatkowyOpis")}
    assert opisy["Kwota do zapłaty w terminie 2026-11-02"] == f"Przelew 14 dni 293.90 {waluta}"


def test_wdt_numer_vat_ue_z_literami_i_prefiksem():
    x = zbuduj(wdt(nabywca={"nip": "CY 60000000Q", "nip_prefiks": "CY", "kraj": "CY"}))
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/KodUE") == "CY"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/NrVatUE") == "60000000Q"


def test_wdt_grecja_kod_ue_el():
    x = zbuduj(wdt(nabywca={"nip_prefiks": "GR", "kraj": "GR"}))
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/KodUE") == "EL"


@pytest.mark.parametrize("zmiany, komunikat", [
    ({"naglowek": {"expo_norm": 7}}, "OSS"),                       # konsument UE z obcym VAT — XL nie wysyłał
    ({"nabywca": {"nip": ""}}, "bez numeru VAT UE"),
    ({"nabywca": {"nip_prefiks": "NO", "kraj": "NO"}}, "spoza UE"),
])
def test_wdt_wstrzymane(zmiany, komunikat):
    with pytest.raises(xl.BladMapowania, match=komunikat):
        przygotuj(wdt(**zmiany), WYTWORZONO)


def test_wdt_stawka_inna_niz_zero_wstrzymana():
    raw = wdt()
    raw["vat"][0]["stawka"] = 23
    with pytest.raises(xl.BladMapowania, match="WDT ze stawką 23"):
        przygotuj(raw, WYTWORZONO)


def test_wdt_suma_w_walucie_kontrolowana():
    raw = wdt()
    raw["vat"][0]["wartosc_wal"] = 293.80
    assert przygotuj(raw, WYTWORZONO).bledy


def test_stary_eksport_bez_kwot_w_walucie_wstrzymany():
    raw = wdt()
    del raw["pozycje"][0]["wartosc_wal"]
    with pytest.raises(xl.BladMapowania, match="aktualnym"):
        przygotuj(raw, WYTWORZONO)


def test_opis_z_koncami_linii_windows_jak_xl():
    x = zbuduj(wdt(naglowek={"opis": "Delivery address: Testowa 1\r\ntest@example.com"}))
    assert pole(x, "Fa/DodatkowyOpis/Wartosc") == "Delivery address: Testowa 1\ntest@example.com"


# --- ręczny kurs (--kurs), gdy XL ma nieaktualny kurs na wystawionej fakturze ---

def test_reczny_kurs_zastepuje_kurs_z_xl():
    from ksef_connector import kursy
    raw = kursy.zastosuj(wdt(), kursy.parsuj(["(S)FSE-1/10/26/B/LV=4,3889"]))
    x = zbuduj(raw)
    assert [w.findtext(f"{{{NS}}}KursWaluty") for w in x.iter(f"{{{NS}}}FaWiersz")] == ["4.3889", "4.3889"]
    assert pole(x, "Fa/P_13_6_2") == "293.90"  # kwoty w walucie bez zmian


def test_reczny_kurs_innej_faktury_nic_nie_zmienia():
    from ksef_connector import kursy
    x = zbuduj(kursy.zastosuj(wdt(), kursy.parsuj(["(S)FSE-9/10/26/B/LV=4,40"])))
    assert {w.findtext(f"{{{NS}}}KursWaluty") for w in x.iter(f"{{{NS}}}FaWiersz")} == {"4.3171"}


def test_reczny_kurs_dla_faktury_w_pln_wstrzymany():
    from ksef_connector import kursy
    raw = wdt(naglowek={"waluta": "PLN"})
    with pytest.raises(xl.BladMapowania, match="w PLN"):
        kursy.zastosuj(raw, kursy.parsuj(["(S)FSE-1/10/26/B/LV=4,40"]))


@pytest.mark.parametrize("wpis", ["(S)FSE-1/10/26/B/LV=abc", "(S)FSE-1/10/26/B/LV=0", "=4,40"])
def test_reczny_kurs_zly_format(wpis):
    from ksef_connector import kursy
    with pytest.raises(SystemExit):
        kursy.parsuj([wpis])


def test_reczny_kurs_nieznany_numer_zatrzymuje(tmp_path):
    import json
    from ksef_connector import kursy
    from ksef_connector.source.fixtures import wczytaj
    plik = tmp_path / "f.json"
    plik.write_text(json.dumps(WDT_EUR), encoding="utf-8")
    with pytest.raises(SystemExit, match="brak tych faktur"):
        kursy.wczytaj_z_kursami(wczytaj, [plik], kursy.parsuj(["(S)FSE-1/10/26/B/LT=4,38"]))
