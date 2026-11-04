from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from ksef_connector.mapping import xl
from ksef_connector.mapping.fa3 import NS
from ksef_connector.pipeline import przygotuj
from ksef_connector.source.fixtures import FixtureSource
from ksef_connector.validate.sumy import bledy_sum

FIXTURES = FixtureSource(Path(__file__).parent / "fixtures")
WYTWORZONO = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def zbuduj(nazwa: str):
    wynik = przygotuj(FIXTURES.pobierz(nazwa), WYTWORZONO)
    assert wynik.ok, wynik.bledy
    return wynik, etree.fromstring(wynik.xml)


def pole(xml, sciezka: str) -> str | None:
    return xml.findtext("/".join(f"{{{NS}}}{c}" for c in sciezka.split("/")))


# --- funkcje pomocnicze ---

def test_data_clarion():
    assert xl.data_clarion(4) == date(1801, 1, 1)
    assert xl.data_clarion(82476) == date(2026, 10, 20)


def test_numer_p2_jak_w_xl_z_prefiksem_s():
    assert xl.numer_p2(" (S)FS-46/10/26/B ") == "(S)FS-46/10/26/B"


@pytest.mark.parametrize("stawka,oczekiwana", [(Decimal("23.00"), "23"), (Decimal("8.00"), "8"), (Decimal("5"), "5")])
def test_stawka_liczbowa(stawka, oczekiwana):
    assert xl.stawka_fa3(stawka, 1) == oczekiwana


@pytest.mark.parametrize("stawka,flaga", [(Decimal("0"), 1), (Decimal("23"), 2), (Decimal("23"), 0), (Decimal("19"), 1)])
def test_nieobslugiwane_stawki_wstrzymuja_fakture(stawka, flaga):
    with pytest.raises(xl.BladMapowania):
        xl.stawka_fa3(stawka, flaga)


# --- całe faktury ---

def test_fs_netto_firma_dwie_stawki():
    wynik, x = zbuduj("fs_netto_firma_dwie_stawki")
    assert pole(x, "Fa/P_2") == "(S)FS-1/10/26/B"
    assert pole(x, "Fa/P_1") == "2026-10-20"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/NIP") == "1234563218"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/Nazwa") == "Testowa Firma Wnętrz Sp. z o.o."
    assert pole(x, "Podmiot2/Adres/AdresL1") == "ul. Fikcyjna 7/3"
    assert pole(x, "Fa/P_13_1") == "325.00" and pole(x, "Fa/P_14_1") == "74.75"
    assert pole(x, "Fa/P_13_2") == "50.00" and pole(x, "Fa/P_14_2") == "4.00"
    assert pole(x, "Fa/P_15") == "453.75"
    wiersz2 = x.findall(f"{{{NS}}}Fa/{{{NS}}}FaWiersz")[1]
    assert wiersz2.findtext(f"{{{NS}}}P_8B") == "2.5"
    assert wiersz2.findtext(f"{{{NS}}}P_9A") == "50.00"
    assert wiersz2.findtext(f"{{{NS}}}P_11") == "125.00"
    assert wiersz2.find(f"{{{NS}}}P_11A") is None


def test_fs_brutto_konsument_z_roznica_grosza():
    wynik, x = zbuduj("fs_brutto_konsument_roznica_grosza")
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/BrakID") == "1"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/NIP") is None
    assert pole(x, "Podmiot2/Adres/AdresL1") == "Kwiatowa 5 m. 2"
    assert pole(x, "Podmiot2/Adres/AdresL2") == "30-001 Kraków"
    # Podsumowanie z TraVat (149,97), mimo że pozycje dają 149,98
    assert pole(x, "Fa/P_15") == "149.97"
    wiersz = x.find(f"{{{NS}}}Fa/{{{NS}}}FaWiersz")
    assert wiersz.findtext(f"{{{NS}}}P_9B") == "99.99"
    assert wiersz.findtext(f"{{{NS}}}P_11A") == "99.99"
    assert wiersz.find(f"{{{NS}}}P_11") is None


def test_fs_brutto_firma_cf_nip_z_kreskami():
    wynik, x = zbuduj("fs_brutto_firma_cf")
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/NIP") == "9876543210"
    assert pole(x, "Podmiot2/Adres/KodKraju") == "PL"
    assert pole(x, "Podmiot2/Adres/AdresL1") == "ul. Długa 12"
    assert pole(x, "Fa/P_15") == "450.00"


def test_anulowana_nie_jest_wysylana():
    with pytest.raises(xl.FakturaAnulowana):
        przygotuj(FIXTURES.pobierz("fs_anulowana"))


def test_waluta_i_typ_poza_mvp():
    raw = FIXTURES.pobierz("fs_netto_firma_dwie_stawki")
    raw["naglowek"]["waluta"] = "EUR"  # FS krajowa w walucie — nie
    with pytest.raises(xl.BladMapowania, match="tylko WDT"):
        przygotuj(raw)
    raw["naglowek"].update(waluta="", gid_typ=2037)  # FSE bez TrN_ExpoNorm = 6 (np. OSS) — nie
    with pytest.raises(xl.BladMapowania, match="2037"):
        przygotuj(raw)


def test_nabywca_zagraniczny_poza_mvp():
    raw = FIXTURES.pobierz("fs_netto_firma_dwie_stawki")
    raw["nabywca"]["nip_prefiks"] = "HU"
    with pytest.raises(xl.BladMapowania, match="zagraniczny"):
        przygotuj(raw)


# --- walidacja ---

def test_kontrola_sum_wykrywa_rozbieznosc_netto():
    raw = FIXTURES.pobierz("fs_netto_firma_dwie_stawki")
    raw["vat"][0]["netto"] = Decimal("325.01")  # od netto tolerancja = 0
    bledy = bledy_sum(xl.faktura(raw))
    assert len(bledy) == 1 and "Stawka 23" in bledy[0]


def test_kontrola_sum_brutto_powyzej_grosza():
    raw = FIXTURES.pobierz("fs_brutto_konsument_roznica_grosza")
    raw["vat"][0]["vat"] = Decimal("28.03")  # różnica 0,02
    assert bledy_sum(xl.faktura(raw))


def test_xsd_odrzuca_zepsuty_xml():
    wynik = przygotuj(FIXTURES.pobierz("fs_netto_firma_dwie_stawki"), WYTWORZONO)
    zepsuty = wynik.xml.replace(b"<P_2>(S)FS-1/10/26/B</P_2>", b"")
    from ksef_connector.validate.xsd import bledy_xsd
    assert bledy_xsd(zepsuty)


def test_wzor_z_xl():
    """Struktura jak w prawdziwym eksporcie FS-46/10/26/B (nabywca zanonimizowany)."""
    wynik, x = zbuduj("fs_netto_wzor_xl")
    assert pole(x, "Fa/P_2") == "(S)FS-46/10/26/B"
    assert pole(x, "Fa/P_1") == "2026-10-16"
    assert pole(x, "Podmiot2/DaneIdentyfikacyjne/Nazwa") == "FIRMA TESTOWA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ"
    assert pole(x, "Podmiot2/Adres/AdresL1") == "ul. Testowa 1"
    assert pole(x, "Fa/P_15") == "900.95"
    wiersz = x.find(f"{{{NS}}}Fa/{{{NS}}}FaWiersz")
    assert wiersz.findtext(f"{{{NS}}}P_8A") == "szt."
    assert wiersz.findtext(f"{{{NS}}}P_8B") == "21"
    assert wiersz.findtext(f"{{{NS}}}P_9A") == "34.88"


def test_p6_gdy_data_sprzedazy_inna_niz_wystawienia():
    _, x = zbuduj("fs_netto_firma_dwie_stawki")
    assert pole(x, "Fa/P_1") == "2026-10-20"
    assert pole(x, "Fa/P_6") == "2026-10-14"
    _, x = zbuduj("fs_netto_wzor_xl")
    assert pole(x, "Fa/P_6") == "2026-10-07"


def test_p6_takze_gdy_daty_rowne():
    _, x = zbuduj("fs_brutto_firma_cf")
    assert pole(x, "Fa/P_6") == pole(x, "Fa/P_1")


def test_brak_daty_sprzedazy_wstrzymuje_fakture():
    raw = FIXTURES.pobierz("fs_brutto_firma_cf")
    del raw["naglowek"]["data_sprz"]
    with pytest.raises(xl.BladMapowania, match="daty sprzedaży"):
        przygotuj(raw)


@pytest.mark.parametrize("stan", [1, 2])
def test_stany_spoza_3_4_5_wstrzymane(stan):
    raw = FIXTURES.pobierz("fs_brutto_firma_cf")
    raw["naglowek"]["stan"] = stan
    with pytest.raises(xl.BladMapowania, match="Stan"):
        przygotuj(raw)


def test_stan_3_zatwierdzona_niezaplacona_jest_wysylana():
    raw = FIXTURES.pobierz("fs_brutto_firma_cf")
    raw["naglowek"]["stan"] = 3
    assert przygotuj(raw).ok


@pytest.mark.parametrize("wiersz", [
    {"stawka": Decimal("23"), "flaga_vat": 1, "netto": Decimal("-365.85"), "vat": Decimal("-84.15"), "zaliczkowy": 1},
    {"stawka": Decimal("23"), "flaga_vat": 1, "netto": Decimal("-365.85"), "vat": Decimal("-84.15")},  # eksport bez kolumny
])
def test_rozliczajaca_bez_danych_fsl_albo_bez_oznaczenia_wstrzymana(wiersz):
    raw = FIXTURES.pobierz("fs_brutto_firma_cf")
    raw["vat"].append(wiersz)
    with pytest.raises(xl.BladMapowania, match="zaliczk"):
        przygotuj(raw)


# --- płatność i WZ ---

def test_przelew_z_terminem_i_rachunkiem_firmy():
    _, x = zbuduj("fs_netto_wzor_xl")
    assert pole(x, "Fa/WZ") == "WZ-45/10/26/B"
    assert pole(x, "Fa/Platnosc/TerminPlatnosci/Termin") == "2026-10-30"
    assert pole(x, "Fa/Platnosc/FormaPlatnosci") == "6"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") == "02111122220000000000000001"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/SWIFT") == "TESTPLPW"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NazwaBanku") == "Bank Przykładowy S.A."


def test_zaplacona_inna_forma_jak_u_xl():
    """Jak FS-12/10/26/C/F z XL: Zaplacono + DataZaplaty, PlatnoscInna, bez rachunku i bez „Kwota do zapłaty”."""
    _, x = zbuduj("fs_brutto_konsument_roznica_grosza")
    assert pole(x, "Fa/Platnosc/Zaplacono") == "1"
    assert pole(x, "Fa/Platnosc/DataZaplaty") == "2026-10-20"
    assert pole(x, "Fa/Platnosc/PlatnoscInna") == "1"
    assert pole(x, "Fa/Platnosc/OpisPlatnosci") == "Przelewy24"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") is None
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}DodatkowyOpis") is None


def test_brak_wierszy_platnosci_brak_sekcji():
    _, x = zbuduj("fs_brutto_firma_cf")
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}Platnosc") is None


@pytest.mark.parametrize("klucz", ["platnosci", "wz"])
def test_stary_eksport_bez_platnosci_lub_wz_wstrzymany(klucz):
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    del raw[klucz]
    with pytest.raises(xl.BladMapowania, match="aktualnym faktura.sql"):
        przygotuj(raw)


def test_zaplacona_przelewem_bez_rachunku_i_bez_kwoty_do_zaplaty():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["platnosci"][0].update(pozostaje=Decimal("0"), data_rozl=82472)
    x = etree.fromstring(przygotuj(raw, WYTWORZONO).xml)
    assert pole(x, "Fa/Platnosc/Zaplacono") == "1" and pole(x, "Fa/Platnosc/DataZaplaty") == "2026-10-16"
    assert pole(x, "Fa/Platnosc/FormaPlatnosci") == "6"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") is None
    klucze = [d.findtext(f"{{{NS}}}Klucz") for d in x.iter(f"{{{NS}}}DodatkowyOpis")]
    assert klucze == ["Opis"]


def test_zaplacona_bez_daty_rozliczenia_wstrzymana():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["platnosci"][0].update(pozostaje=Decimal("0"), data_rozl=999999)
    with pytest.raises(xl.BladMapowania, match="daty rozliczenia"):
        przygotuj(raw)


def test_czesciowo_zaplacona_wstrzymana():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["platnosci"][0]["pozostaje"] = Decimal("100.00")
    with pytest.raises(xl.BladMapowania, match="częściowo"):
        przygotuj(raw)


def test_zamowienie_obce_ma_pierwszenstwo_przed_zs():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["naglowek"]["zam_obcy"] = "Zamówienie PL999"
    x = etree.fromstring(przygotuj(raw, WYTWORZONO).xml)
    assert pole(x, "Fa/WarunkiTransakcji/Zamowienia/NrZamowienia") == "Zamówienie PL999"


def test_stary_eksport_bez_zam_obcy_wstrzymany():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    del raw["naglowek"]["zam_obcy"]
    with pytest.raises(xl.BladMapowania, match="zamówień"):
        przygotuj(raw)


@pytest.mark.parametrize("pozostaje", [Decimal("0"), Decimal("900.95")])
def test_pobranie_tylko_forma_za_pobraniem(pozostaje):
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["platnosci"][0].update(forma="Pobranie", pozostaje=pozostaje, data_rozl=82472)
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.ok, wynik.bledy
    x = etree.fromstring(wynik.xml)
    assert pole(x, "Fa/Platnosc/Zaplacono") is None and pole(x, "Fa/Platnosc/DataZaplaty") is None
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}Platnosc/{{{NS}}}TerminPlatnosci") is None
    assert pole(x, "Fa/Platnosc/PlatnoscInna") == "1" and pole(x, "Fa/Platnosc/OpisPlatnosci") == "Za pobraniem"
    assert pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") is None
    assert pole(x, "Fa/Rozliczenie/DoZaplaty") == pole(x, "Fa/P_15") == "900.95"
    klucze = [d.findtext(f"{{{NS}}}Klucz") for d in x.iter(f"{{{NS}}}DodatkowyOpis")]
    assert klucze == ["Opis"]


def test_barter_z_kwota_do_zaplaty_bez_konta():
    """Seria RB (potwierdzone z księgowością) — jak każda niezapłacona: termin i „Kwota do zapłaty”, bez konta."""
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["platnosci"][0].update(forma="Barter", pozostaje=raw["platnosci"][0]["kwota"], data_rozl=999999)
    wynik = przygotuj(raw, WYTWORZONO)
    assert wynik.ok, wynik.bledy
    x = etree.fromstring(wynik.xml)
    assert pole(x, "Fa/Platnosc/PlatnoscInna") == "1" and pole(x, "Fa/Platnosc/OpisPlatnosci") == "Barter"
    assert pole(x, "Fa/Platnosc/Zaplacono") is None and pole(x, "Fa/Platnosc/RachunekBankowy/NrRB") is None
    opisy = [d.findtext(f"{{{NS}}}Wartosc") for d in x.iter(f"{{{NS}}}DodatkowyOpis")]
    assert any(o.startswith("Barter ") and o.endswith(" PLN") for o in opisy)


def test_bez_pobrania_brak_rozliczenia():
    _, x = zbuduj("fs_netto_wzor_xl")
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}Rozliczenie") is None


def test_kilka_form_platnosci_wstrzymane():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["platnosci"].append(dict(raw["platnosci"][0], forma="Gotówka"))
    with pytest.raises(xl.BladMapowania, match="Kilka form"):
        przygotuj(raw)


# --- dodatkowe informacje (jak u XL) ---

def test_dodatkowy_opis_opis_i_kwota_do_zaplaty():
    _, x = zbuduj("fs_netto_wzor_xl")
    wpisy = [(d.findtext(f"{{{NS}}}Klucz"), d.findtext(f"{{{NS}}}Wartosc"))
             for d in x.iter(f"{{{NS}}}DodatkowyOpis")]
    assert wpisy == [("Opis", "Opis testowy"),
                     ("Kwota do zapłaty w terminie 2026-10-30", "Przelew 14 dni 900.95 PLN")]


def test_bez_opisu_i_bez_platnosci_brak_dodatkowego_opisu():
    _, x = zbuduj("fs_brutto_firma_cf")
    assert x.find(f"{{{NS}}}Fa/{{{NS}}}DodatkowyOpis") is None


def test_stary_eksport_bez_opisu_wstrzymany():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    del raw["naglowek"]["opis"]
    with pytest.raises(xl.BladMapowania, match="opisu"):
        przygotuj(raw)


def test_numer_zamowienia_ze_z_prefiksem_zs():
    _, x = zbuduj("fs_netto_wzor_xl")
    assert pole(x, "Fa/WarunkiTransakcji/Zamowienia/NrZamowienia") == "ZS-49/10/26/B"


def test_zamowienie_nieznanego_typu_pominiete_i_jednocyfrowy_miesiac():
    assert xl._zamowienia([{"typ": 9999, "numer": 1, "miesiac": 1, "rok": 2026, "seria": "B"},
                           {"typ": 1280, "numer": 7, "miesiac": 3, "rok": 2026, "seria": ""}],
                          {"zam_obcy": ""}) == ("ZS-7/03/26",)


def test_stary_eksport_bez_zamowien_wstrzymany():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    del raw["zamowienia"]
    with pytest.raises(xl.BladMapowania, match="zamówień"):
        przygotuj(raw)


def test_przedplata_data_zaplaty_to_data_sprzedazy():
    """FS-69: forma „Przelew” (bez dni) = proforma → zapłacono w dniu sprzedaży, nie w dniu rozliczenia w XL."""
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")  # sprzedaż 07.10, wystawienie 16.10
    raw["platnosci"][0].update(forma="Przelew", termin=82463, pozostaje=Decimal("0"), data_rozl=82472)
    x = etree.fromstring(przygotuj(raw, WYTWORZONO).xml)
    assert pole(x, "Fa/Platnosc/Zaplacono") == "1"
    assert pole(x, "Fa/Platnosc/DataZaplaty") == "2026-10-07"
    assert pole(x, "Fa/Platnosc/TerminPlatnosci/Termin") == "2026-10-07"


def test_terminowa_zaplacona_data_rozliczenia():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")  # „Przelew 14 dni”
    raw["platnosci"][0].update(pozostaje=Decimal("0"), data_rozl=82480)
    x = etree.fromstring(przygotuj(raw, WYTWORZONO).xml)
    assert pole(x, "Fa/Platnosc/DataZaplaty") == "2026-10-24"
