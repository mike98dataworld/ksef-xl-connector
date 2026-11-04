"""Faktury zaliczkowe FSL (1824 → ZAL) i rozliczające zaliczkę (FS z TrV_Zaliczkowy → ROZ), jak u XL
(wzory: faktury zaliczkowe wysłane wcześniej przez XL). Dane fikcyjne."""
import copy
from datetime import date, datetime, timezone

import pytest
from lxml import etree

from ksef_connector.mapping import xl
from ksef_connector.mapping.fa3 import NS
from ksef_connector.pipeline import przygotuj

WYTWORZONO = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
NR_KSEF_FSL = "1111111111-20260422-0A1B2C3D4E5F-CD"


def clarion(d: date) -> int:
    return (d - xl.CLARION_EPOKA).days


NABYWCA = {"nip": "9999999999", "nip_prefiks": "", "kraj": "", "nazwa1": " Parafia Testowa ", "nazwa2": "",
           "nazwa3": "", "ulica": "ul. Kościelna 1", "adres": "", "kodp": "00-001", "miasto": "Testowo"}

FSL = {
    "naglowek": {"gid_typ": 1824, "gid_numer": 900300, "stan": 5, "numer": "FSL-1/04/26/Z", "seria": "Z",
                 "data_wyst": clarion(date(2026, 4, 22)), "data_sprz": clarion(date(2026, 4, 22)), "waluta": "PLN",
                 "flaga_nb": "B", "expo_norm": 0, "zam_obcy": "", "opis": "Płatność w dwóch ratach.\r\nII rata przed dostawą."},
    "nabywca": NABYWCA,
    "pozycje": [],
    "vat": [{"stawka": 23, "flaga_vat": 1, "netto": 8943.09, "vat": 2056.91, "zaliczkowy": 0, "wartosc_wal": 11000}],
    "platnosci": [{"termin": clarion(date(2026, 4, 22)), "forma": "Przelew", "kwota": 11000, "pozostaje": 0,
                   "data_rozl": clarion(date(2026, 4, 23))}],
    "wz": [],
    "zamowienia": [{"typ": 1280, "numer": 1, "miesiac": 4, "rok": 2026, "seria": "INV"}],
    "zamowienie_pozycje": [
        {"lp": 1, "kod": "OBR-1", "nazwa": "OBRAZ TESTOWY 1", "ilosc": 1, "jm": "szt.", "stawka": 23, "flaga_vat": 1,
         "wartosc": 11000, "flaga_nb": "B", "waluta": "PLN"},
        {"lp": 2, "kod": "OBR-2", "nazwa": "OBRAZ TESTOWY 2", "ilosc": 1, "jm": "szt.", "stawka": 23, "flaga_vat": 1,
         "wartosc": 11000, "flaga_nb": "B", "waluta": "PLN"},
    ],
    "zaliczki": [],
}

# Rozliczenie FSL-1/04/26/Z: dostawa 22 000 brutto − zaliczka 11 000 = 11 000 do zapłaty.
ROZ = {
    "naglowek": {"gid_typ": 2033, "gid_numer": 900301, "stan": 4, "numer": "(S)FS-3/10/26/INV", "seria": "INV",
                 "data_wyst": clarion(date(2026, 10, 30)), "data_sprz": clarion(date(2026, 10, 30)), "waluta": "PLN",
                 "flaga_nb": "B", "expo_norm": 0, "zam_obcy": "", "opis": ""},
    "nabywca": NABYWCA,
    "pozycje": [
        {"lp": 1, "kod": "OBR-1", "nazwa": "OBRAZ TESTOWY 1", "ilosc": 1, "jm": "szt.", "stawka": 23, "flaga_vat": 1,
         "netto": 8943.09, "brutto": 11000},
        {"lp": 2, "kod": "OBR-2", "nazwa": "OBRAZ TESTOWY 2", "ilosc": 1, "jm": "szt.", "stawka": 23, "flaga_vat": 1,
         "netto": 8943.09, "brutto": 11000},
    ],
    "vat": [{"stawka": 23, "flaga_vat": 1, "netto": 17886.18, "vat": 4113.82, "zaliczkowy": 0},
            {"stawka": 23, "flaga_vat": 1, "netto": -8943.09, "vat": -2056.91, "zaliczkowy": 1}],
    "platnosci": [{"termin": clarion(date(2026, 11, 13)), "forma": "Przelew 14 dni", "kwota": 11000, "pozostaje": 11000,
                   "data_rozl": 0}],
    "wz": [{"numer": "WZ-3/10/26/INV"}],
    "zamowienia": [{"typ": 1280, "numer": 1, "miesiac": 4, "rok": 2026, "seria": "INV"}],
    "zamowienie_pozycje": [],
    "zaliczki": [{"numer": "FSL-1/04/26/Z", "gid_numer": 900300, "data_wyst": clarion(date(2026, 4, 22)),
                  "netto": 8943.09, "vat": 2056.91}],
    "zaliczki_ksef": {"FSL-1/04/26/Z": NR_KSEF_FSL},
}


def kopia(wzor: dict, **zmiany) -> dict:
    raw = copy.deepcopy(wzor)
    for sekcja, pola in zmiany.items():
        if isinstance(raw.get(sekcja), dict):
            raw[sekcja].update(pola)
        else:
            raw[sekcja] = pola
    return raw


def pole(xml, sciezka: str) -> str | None:
    return xml.findtext("/".join(f"{{{NS}}}{c}" for c in sciezka.split("/")))


def zbuduj(raw: dict):
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.ok, wynik.bledy
    return etree.fromstring(wynik.xml)


# --- FSL → ZAL ---

def test_fsl_jak_xl():
    x = zbuduj(kopia(FSL))
    assert pole(x, "Fa/RodzajFaktury") == "ZAL"
    assert pole(x, "Fa/P_2") == "FSL-1/04/26/Z"
    assert (pole(x, "Fa/P_13_1"), pole(x, "Fa/P_14_1"), pole(x, "Fa/P_15")) == ("8943.09", "2056.91", "11000.00")
    assert pole(x, "Fa/P_6") == "2026-04-22"  # zasada BOK „P_6 zawsze” (XL pomijał, gdy równa P_1)
    assert x.find(f".//{{{NS}}}FaWiersz") is None and pole(x, "Fa/WZ") is None
    assert pole(x, "Fa/WarunkiTransakcji/Zamowienia/NrZamowienia") == "ZS-1/04/26/INV"
    assert pole(x, "Fa/Zamowienie/WartoscZamowienia") == "22000.00"
    q = lambda w, t: w.findtext(f"{{{NS}}}{t}")
    assert [tuple(q(w, t) for t in ("NrWierszaZam", "P_7Z", "IndeksZ", "P_8AZ", "P_8BZ", "P_9AZ", "P_11NettoZ",
                                    "P_11VatZ", "P_12Z"))
            for w in x.iter(f"{{{NS}}}ZamowienieWiersz")] == [
        ("1", "OBRAZ TESTOWY 1", "OBR-1", "szt.", "1", "8943.09", "8943.09", "2056.91", "23"),
        ("2", "OBRAZ TESTOWY 2", "OBR-2", "szt.", "1", "8943.09", "8943.09", "2056.91", "23")]
    assert pole(x, "Fa/DodatkowyOpis/Wartosc") == "Płatność w dwóch ratach.\nII rata przed dostawą."


def test_fsl_zaplacona_z_kontem_proform_jak_xl():
    x = zbuduj(kopia(FSL))
    assert pole(x, "Fa/Platnosc/Zaplacono") == "1" and pole(x, "Fa/Platnosc/DataZaplaty") == "2026-04-22"
    assert pole(x, "Fa/Platnosc/FormaPlatnosci") == "6"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == "45111122220000000000000003"


def test_fsl_niezaplacona_kwota_do_zaplaty_i_konto_proform():
    raw = kopia(FSL)
    raw["platnosci"][0].update(pozostaje=11000, data_rozl=0)
    x = zbuduj(raw)
    assert pole(x, "Fa/Platnosc/Zaplacono") is None
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == "45111122220000000000000003"
    opisy = {d.findtext(f"{{{NS}}}Klucz"): d.findtext(f"{{{NS}}}Wartosc") for d in x.iter(f"{{{NS}}}DodatkowyOpis")}
    assert opisy["Kwota do zapłaty w terminie 2026-04-22"] == "Przelew 11000.00 PLN"


def test_fsl_zamowienie_netto():
    raw = kopia(FSL)
    raw["zamowienie_pozycje"] = [{"lp": 1, "kod": "GRS", "nazwa": "PŁYTKA", "ilosc": 33, "jm": "szt.", "stawka": 23,
                                  "flaga_vat": 1, "wartosc": 1207.47, "flaga_nb": "N", "waluta": "PLN"}]
    raw["vat"][0].update(netto=1207.47, vat=277.72)
    raw["platnosci"][0]["kwota"] = 1485.19
    x = zbuduj(raw)
    assert pole(x, "Fa/Zamowienie/WartoscZamowienia") == "1485.19"
    assert (pole(x, "Fa/Zamowienie/ZamowienieWiersz/P_9AZ"), pole(x, "Fa/Zamowienie/ZamowienieWiersz/P_11VatZ")) == \
        ("36.59", "277.72")


def test_fsl_bez_nip_mapuje_sie_jako_konsument():
    """Wysyłka i tak ją pominie (osoba prywatna bez NIP) — jak serię C."""
    x = zbuduj(kopia(FSL, nabywca={"nip": ""}))
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/BrakID") == "1"


@pytest.mark.parametrize("zmiany, komunikat", [
    ({"zamowienie_pozycje": []}, "bez pozycji zamówienia"),
    ({"naglowek": {"waluta": "EUR"}}, "tylko PLN"),
    ({"naglowek": {"stan": 6}}, "anulowana"),
])
def test_fsl_wstrzymana(zmiany, komunikat):
    with pytest.raises(xl.BladMapowania, match=komunikat):
        przygotuj(kopia(FSL, **zmiany), WYTWORZONO)


def test_fsl_zaliczka_wieksza_niz_zamowienie_wstrzymana():
    raw = kopia(FSL)
    raw["zamowienie_pozycje"] = raw["zamowienie_pozycje"][:1]
    raw["vat"][0].update(netto=9000, vat=2070)
    with pytest.raises(xl.BladMapowania, match="większa niż wartość zamówienia"):
        przygotuj(raw, WYTWORZONO)


def test_stary_eksport_fsl_bez_pozycji_zamowienia_wstrzymany():
    raw = kopia(FSL)
    del raw["zamowienie_pozycje"]
    with pytest.raises(xl.BladMapowania, match="aktualnym"):
        przygotuj(raw, WYTWORZONO)


# --- FS rozliczająca zaliczkę → ROZ ---

def test_rozliczajaca_z_doplata():
    x = zbuduj(kopia(ROZ))
    assert pole(x, "Fa/RodzajFaktury") == "ROZ"
    assert (pole(x, "Fa/P_13_1"), pole(x, "Fa/P_14_1"), pole(x, "Fa/P_15")) == ("8943.09", "2056.91", "11000.00")
    assert pole(x, "Fa/FakturaZaliczkowa/NrKSeFFaZaliczkowej") == NR_KSEF_FSL
    assert [w.findtext(f"{{{NS}}}P_11A") for w in x.iter(f"{{{NS}}}FaWiersz")] == ["11000.00", "11000.00"]
    assert pole(x, "Fa/WZ") == "WZ-3/10/26/INV"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == "02111122220000000000000001"
    opisy = {d.findtext(f"{{{NS}}}Klucz"): d.findtext(f"{{{NS}}}Wartosc") for d in x.iter(f"{{{NS}}}DodatkowyOpis")}
    assert opisy["Kwota do zapłaty w terminie 2026-11-13"] == "Przelew 14 dni 11000.00 PLN"


def test_rozliczajaca_w_calosci_zaplacona_zaliczka_jak_xl():
    raw = kopia(ROZ)
    raw["pozycje"] = raw["pozycje"][:1]
    raw["vat"] = [{"stawka": 23, "flaga_vat": 1, "netto": -8943.09, "vat": -2056.91, "zaliczkowy": 1},
                  {"stawka": 23, "flaga_vat": 1, "netto": 8943.09, "vat": 2056.91, "zaliczkowy": 0}]
    raw["platnosci"] = []
    x = zbuduj(raw)
    assert pole(x, "Fa/P_15") == "0.00"
    assert pole(x, "Fa/P_13_1") is None and pole(x, "Fa/P_14_1") is None
    assert x.find(f".//{{{NS}}}Platnosc") is None


def test_rozliczajaca_zaliczka_sprzed_ksef():
    raw = kopia(ROZ, zaliczki_ksef={"FSL-1/04/26/Z": None})
    raw["zaliczki"][0]["data_wyst"] = clarion(date(2026, 2, 5))
    x = zbuduj(raw)
    assert pole(x, "Fa/FakturaZaliczkowa/NrKSeFZN") == "1"
    assert pole(x, "Fa/FakturaZaliczkowa/NrFaZaliczkowej") == "FSL-1/04/26/Z"


@pytest.mark.parametrize("zmiany, komunikat", [
    ({"zaliczki_ksef": {"FSL-1/04/26/Z": None}}, "najpierw wyślij FSL"),
    ({"zaliczki": []}, "nie ma FSL"),
])
def test_rozliczajaca_wstrzymana(zmiany, komunikat):
    with pytest.raises(xl.BladMapowania, match=komunikat):
        przygotuj(kopia(ROZ, **zmiany), WYTWORZONO)


def test_rozliczajaca_fsl_nieszukana_w_ksef_wstrzymana():
    raw = kopia(ROZ)
    raw["zaliczki_ksef"] = {}
    with pytest.raises(xl.BladMapowania, match="nie była szukana"):
        przygotuj(raw, WYTWORZONO)


def test_rozliczajaca_bez_numerow_ksef_kaze_uruchomic_oryginaly():
    raw = kopia(ROZ)
    del raw["zaliczki_ksef"]
    with pytest.raises(xl.BladMapowania, match="oryginaly"):
        przygotuj(raw, WYTWORZONO)


def test_rozliczajaca_inna_kwota_fsl_niz_zaliczka_na_fakturze_wstrzymana():
    raw = kopia(ROZ)
    raw["zaliczki"][0]["netto"] = 8000
    with pytest.raises(xl.BladMapowania, match="sprawdź w XL"):
        przygotuj(raw, WYTWORZONO)


def test_rozliczajaca_sumy_pozycji_kontrolowane_bez_wiersza_zaliczki():
    raw = kopia(ROZ)
    raw["pozycje"][1]["brutto"] = 10000
    assert przygotuj(raw, WYTWORZONO).bledy


def test_rozliczajaca_dla_osoby_bez_nip_z_fsl_spoza_ksef_mapuje_sie():
    """FSL bez NIP nie idzie do KSeF — rozliczająca odwołuje się do niej jak do faktury spoza KSeF (i też jest pomijana)."""
    raw = kopia(ROZ, nabywca={"nip": ""}, zaliczki_ksef={"FSL-1/04/26/Z": None})
    x = zbuduj(raw)
    assert pole(x, "Fa/FakturaZaliczkowa/NrKSeFZN") == "1"
