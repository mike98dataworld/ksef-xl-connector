"""Model Faktura -> XML FA(3) (schemat_FA(3)_v1-0E.xsd). Kolejność elementów = kolejność w XSD."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from lxml import etree

from ksef_connector import config
from ksef_connector.mapping.model import Adres, Faktura

NS = "http://crd.gov.pl/wzor/2025/06/25/13775/"

# stawka -> (pole netto, pole VAT), w kolejności z XSD
POLA_SUM: dict[str, tuple[str, str | None]] = {
    "23": ("P_13_1", "P_14_1"),
    "22": ("P_13_1", "P_14_1"),
    "8": ("P_13_2", "P_14_2"),
    "7": ("P_13_2", "P_14_2"),
    "5": ("P_13_3", "P_14_3"),
    "4": ("P_13_4", "P_14_4"),
    "3": ("P_13_4", "P_14_4"),
    "0 KR": ("P_13_6_1", None),
    "0 WDT": ("P_13_6_2", None),
    "zw": ("P_13_7", None),
    "np I": ("P_13_8", None),
    "np II": ("P_13_9", None),
}
KOLEJNOSC_POL = [
    "P_13_1", "P_14_1", "P_13_2", "P_14_2", "P_13_3", "P_14_3", "P_13_4", "P_14_4",
    "P_13_6_1", "P_13_6_2", "P_13_7", "P_13_8", "P_13_9",
]


def kwota(d: Decimal) -> str:
    return format(d.quantize(Decimal("0.01")) + 0, "f")  # +0 zamienia -0.00 na 0.00


def cena(d: Decimal) -> str:
    """Cena jednostkowa: 2 miejsca, chyba że ma ich więcej (wiersz „przed” przepisany z faktury w KSeF)."""
    return kwota(d) if d == d.quantize(Decimal("0.01")) else format(d.normalize(), "f")


def ilosc(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _el(rodzic: etree._Element, nazwa: str, tekst: str | None = None, **atrybuty: str) -> etree._Element:
    e = etree.SubElement(rodzic, f"{{{NS}}}{nazwa}", **atrybuty)
    if tekst is not None:
        e.text = tekst
    return e


def _adres(rodzic: etree._Element, a: Adres) -> None:
    el = _el(rodzic, "Adres")
    _el(el, "KodKraju", a.kod_kraju)
    _el(el, "AdresL1", a.linia1)
    if a.linia2:
        _el(el, "AdresL2", a.linia2)


def zbuduj(f: Faktura, wytworzono: datetime | None = None) -> bytes:
    wytworzono = wytworzono or datetime.now(timezone.utc)
    root = etree.Element(f"{{{NS}}}Faktura", nsmap={None: NS})

    nag = _el(root, "Naglowek")
    _el(nag, "KodFormularza", "FA", kodSystemowy="FA (3)", wersjaSchemy="1-0E")
    _el(nag, "WariantFormularza", "3")
    _el(nag, "DataWytworzeniaFa", wytworzono.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _el(nag, "SystemInfo", config.SYSTEM_INFO)

    p1 = _el(root, "Podmiot1")
    di = _el(p1, "DaneIdentyfikacyjne")
    _el(di, "NIP", f.sprzedawca.nip)
    _el(di, "Nazwa", f.sprzedawca.nazwa)
    _adres(p1, f.sprzedawca.adres)

    p2 = _el(root, "Podmiot2")
    di = _el(p2, "DaneIdentyfikacyjne")
    if f.nabywca.kod_ue:
        _el(di, "KodUE", f.nabywca.kod_ue)
        _el(di, "NrVatUE", f.nabywca.nip)
    elif f.nabywca.nip:
        _el(di, "NIP", f.nabywca.nip)
    else:
        _el(di, "BrakID", "1")
    _el(di, "Nazwa", f.nabywca.nazwa)
    if f.nabywca.adres:
        _adres(p2, f.nabywca.adres)
    _el(p2, "JST", "2")
    _el(p2, "GV", "2")

    fa = _el(root, "Fa")
    _el(fa, "KodWaluty", f.waluta)
    _el(fa, "P_1", f.data_wystawienia.isoformat())
    _el(fa, "P_2", f.numer)
    for numer_wz in f.wz:
        _el(fa, "WZ", numer_wz)
    # Faktura: zawsze, także gdy równa P_1 (XL wtedy pomijał) — prośba księgowości, „wtedy będzie jasne”.
    # Korekta: nigdy — jak korekty XL w KSeF, tylko data wystawienia.
    if not f.korygowana:
        _el(fa, "P_6", f.data_sprzedazy.isoformat())

    sumy: dict[str, Decimal] = {}
    for s in f.sumy_vat:
        pole_netto, pole_vat = POLA_SUM[s.stawka]
        sumy[pole_netto] = sumy.get(pole_netto, Decimal("0")) + s.netto
        if pole_vat:
            sumy[pole_vat] = sumy.get(pole_vat, Decimal("0")) + s.vat
    for pole in KOLEJNOSC_POL:
        # Rozliczająca w całości zapłacona zaliczką: pozostało 0 — XL pomijał P_13/P_14, zostawiał P_15 = 0.00.
        if pole in sumy and not (f.zaliczki and sumy[pole] == 0):
            _el(fa, pole, kwota(sumy[pole]))
    _el(fa, "P_15", kwota(f.brutto))

    adn = _el(fa, "Adnotacje")
    _el(adn, "P_16", "2")  # metoda kasowa: nie
    _el(adn, "P_17", "2")  # samofakturowanie: nie
    _el(adn, "P_18", "2")  # odwrotne obciążenie: nie
    _el(adn, "P_18A", "2")  # MPP: nie (TrN_MPP nie występuje)
    _el(_el(adn, "Zwolnienie"), "P_19N", "1")
    _el(_el(adn, "NoweSrodkiTransportu"), "P_22N", "1")
    _el(adn, "P_23", "2")  # procedura trójstronna: nie
    _el(_el(adn, "PMarzy"), "P_PMarzyN", "1")
    _el(fa, "RodzajFaktury", f.rodzaj)
    if f.korygowana:
        if f.przyczyna_korekty:
            _el(fa, "PrzyczynaKorekty", f.przyczyna_korekty[:256])
        dk = _el(fa, "DaneFaKorygowanej")
        _el(dk, "DataWystFaKorygowanej", f.korygowana.data_wystawienia.isoformat())
        _el(dk, "NrFaKorygowanej", f.korygowana.numer)
        _el(dk, "NrKSeF", "1")
        _el(dk, "NrKSeFFaKorygowanej", f.korygowana.numer_ksef)
    for klucz, wartosc in _dodatkowy_opis(f):
        do = _el(fa, "DodatkowyOpis")
        _el(do, "Klucz", klucz)
        _el(do, "Wartosc", wartosc[:256])
    for z in f.zaliczki:
        fz = _el(fa, "FakturaZaliczkowa")
        if z.numer_ksef:
            _el(fz, "NrKSeFFaZaliczkowej", z.numer_ksef)
        else:  # FSL wystawiona przed KSeF (jak XL na (S)FS-1/06/26/INV)
            _el(fz, "NrKSeFZN", "1")
            _el(fz, "NrFaZaliczkowej", z.numer)

    for p in f.pozycje:
        w = _el(fa, "FaWiersz")
        _el(w, "NrWierszaFa", str(p.lp))
        _el(w, "P_7", p.nazwa)
        if p.indeks:
            _el(w, "Indeks", p.indeks[:50])
        if p.jm:
            _el(w, "P_8A", p.jm)
        _el(w, "P_8B", ilosc(p.ilosc))
        _el(w, "P_9B" if f.od_brutto else "P_9A", cena(p.cena))
        _el(w, "P_11A" if f.od_brutto else "P_11", kwota(p.wartosc))
        _el(w, "P_12", p.stawka)
        if p.kurs:
            _el(w, "KursWaluty", ilosc(p.kurs))
        if p.stan_przed:
            _el(w, "StanPrzed", "1")

    if f.platnosc and f.platnosc.do_zaplaty_calosc:
        _el(_el(fa, "Rozliczenie"), "DoZaplaty", kwota(f.brutto))
    if f.platnosc:
        _platnosc(fa, f.platnosc)
    if f.zamowienia:
        wt = _el(fa, "WarunkiTransakcji")
        for nr in f.zamowienia:
            _el(_el(wt, "Zamowienia"), "NrZamowienia", nr)
    if f.zamowienie:
        zam = _el(fa, "Zamowienie")
        _el(zam, "WartoscZamowienia", kwota(f.zamowienie.wartosc))
        for w in f.zamowienie.wiersze:
            zw = _el(zam, "ZamowienieWiersz")
            _el(zw, "NrWierszaZam", str(w.lp))
            _el(zw, "P_7Z", w.nazwa)
            if w.indeks:
                _el(zw, "IndeksZ", w.indeks[:50])
            if w.jm:
                _el(zw, "P_8AZ", w.jm)
            _el(zw, "P_8BZ", ilosc(w.ilosc))
            _el(zw, "P_9AZ", cena(w.cena_netto))
            _el(zw, "P_11NettoZ", kwota(w.netto))
            _el(zw, "P_11VatZ", kwota(w.vat))
            _el(zw, "P_12Z", w.stawka)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _dodatkowy_opis(f: Faktura) -> list[tuple[str, str]]:
    """Jak u XL: opis dokumentu i „Kwota do zapłaty w terminie RRRR-MM-DD” → „Przelew 14 dni 4096.64 PLN”."""
    wpisy = []
    if f.opis:
        wpisy.append(("Opis", f.opis))
    if f.platnosc:
        for termin, kwota_raty in f.platnosc.raty:
            wpisy.append((f"Kwota do zapłaty w terminie {termin.isoformat()}",
                          f"{f.platnosc.nazwa} {kwota(kwota_raty)} {f.waluta}".strip()))
    return wpisy


def _platnosc(fa: etree._Element, p) -> None:
    pl = _el(fa, "Platnosc")
    if p.zaplacono:
        _el(pl, "Zaplacono", "1")
        _el(pl, "DataZaplaty", p.data_zaplaty.isoformat())
    for termin in p.terminy:
        _el(_el(pl, "TerminPlatnosci"), "Termin", termin.isoformat())
    if p.forma:
        _el(pl, "FormaPlatnosci", str(p.forma))
    elif p.forma_opis:
        _el(pl, "PlatnoscInna", "1")
        _el(pl, "OpisPlatnosci", p.forma_opis)
    if p.rachunek:
        rb = _el(pl, "RachunekBankowy")
        _el(rb, "NrRB", p.rachunek.nr)
        if p.rachunek.swift:
            _el(rb, "SWIFT", p.rachunek.swift)
        if p.rachunek.bank:
            _el(rb, "NazwaBanku", p.rachunek.bank)
        if p.rachunek.opis:
            _el(rb, "OpisRachunku", p.rachunek.opis)
