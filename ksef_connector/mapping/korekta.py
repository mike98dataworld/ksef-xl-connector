"""Korekta FSK z XL (TrN_GIDTyp 2041) → model Faktura z RodzajFaktury KOR.

Ustalone na korektach wysłanych wcześniej przez XL (porównanie z KSeF):
- XL trzyma w korekcie tylko różnice (pozycje spiętej WZK, ujemne ilości i wartości); TrE_*PrzedKorekta są puste,
  TrN_KSeFNumerOrg pusty, a TrN_DataWystOrg to data korekty, nie oryginału.
- W KSeF XL pokazywał parę wierszy na korygowaną pozycję: „przed” = wiersz faktury korygowanej (ten sam NrWierszaFa,
  ilość, cena, wartość, StanPrzed = 1) i „po” = przed + różnica, z tą samą ceną. Tylko korygowane pozycje.
- Dlatego wiersze „przed”, datę wystawienia i numer KSeF bierzemy z faktury korygowanej pobranej z KSeF
  (raw["oryginal_ksef"], dopisuje je polecenie `oryginaly`) — to ona jest korygowana, a nie jej kopia w XL (po odtworzeniu bazy z kopii mogła się różnić).
Płatność: forma i termin jak na wydruku XL, bez „Kwota do zapłaty”; zawsze konto proform
(config.RACHUNEK_KOREKT, wymóg księgowości). Bez Rozliczenie/DoRozliczenia — kwota korekty jest już w P_15
i w podsumowaniu stawek; trzeci raz ta sama kwota wprowadzała w błąd (uwaga księgowości po pilocie).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from lxml import etree

from ksef_connector import config
from ksef_connector.mapping import xl
from ksef_connector.mapping.fa3 import NS
from ksef_connector.mapping.model import Faktura, FakturaKorygowana, Platnosc, Pozycja, Sprzedawca, SumaVat
from ksef_connector.mapping.xl import BladMapowania, GROSZ

TYP_FSK = 2041


@dataclass(frozen=True)
class Oryginal:
    """Faktura korygowana, tak jak jest w KSeF."""
    numer_ksef: str
    numer: str
    data_wystawienia: date
    nip_nabywcy: str | None
    od_brutto: bool
    wiersze: tuple[Pozycja, ...]


def oryginal(numer_ksef: str, xml: str | bytes) -> Oryginal:
    root = etree.fromstring(xml.encode() if isinstance(xml, str) else xml)
    q = lambda e, sciezka: e.findtext("/".join(f"{{{NS}}}{c}" for c in sciezka.split("/")))
    fa = root.find(f"{{{NS}}}Fa")
    if fa is None:
        raise BladMapowania(f"Faktura korygowana {numer_ksef}: brak sekcji Fa (inny schemat niż FA(3)?)")
    wiersze = []
    od_brutto = None
    for w in fa.findall(f"{{{NS}}}FaWiersz"):
        brutto = q(w, "P_11A") is not None
        if od_brutto is not None and brutto != od_brutto:
            raise BladMapowania(f"Faktura korygowana {numer_ksef}: wiersze netto i brutto naraz")
        od_brutto = brutto
        wiersze.append(Pozycja(
            lp=int(q(w, "NrWierszaFa")),
            nazwa=q(w, "P_7") or "",
            indeks=q(w, "Indeks"),
            jm=q(w, "P_8A"),
            ilosc=Decimal(q(w, "P_8B") or "0"),
            cena=Decimal((q(w, "P_9B") if brutto else q(w, "P_9A")) or "0"),
            wartosc=Decimal(q(w, "P_11A") if brutto else q(w, "P_11")),
            stawka=q(w, "P_12") or "",
        ))
    return Oryginal(
        numer_ksef=numer_ksef,
        numer=q(fa, "P_2") or "",
        data_wystawienia=date.fromisoformat(q(fa, "P_1")),
        nip_nabywcy=q(root, "Podmiot2/DaneIdentyfikacyjne/NIP"),
        od_brutto=bool(od_brutto),
        wiersze=tuple(wiersze),
    )


def _roznice(raw: list[dict[str, Any]], od_brutto: bool) -> dict[str, tuple[Decimal, Decimal, str]]:
    """Pozycje WZK zsumowane po kodzie towaru: kod → (ilość, wartość, stawka FA(3))."""
    wynik: dict[str, tuple[Decimal, Decimal, str]] = {}
    for p in raw:
        kod = xl._tekst(p.get("kod"))
        if not kod:
            raise BladMapowania("Pozycja korekty bez kodu towaru — nie da się dopasować do faktury korygowanej")
        stawka = xl.stawka_fa3(p["stawka"], p["flaga_vat"])
        il, wart, st = wynik.get(kod, (Decimal(0), Decimal(0), stawka))
        if st != stawka:
            raise BladMapowania(f"Pozycja korekty {kod}: różne stawki VAT")
        wynik[kod] = (il + Decimal(p["ilosc"]),
                      wart + Decimal(p["brutto"] if od_brutto else p["netto"]).quantize(GROSZ), stawka)
    return wynik


def _wiersze(org: Oryginal, roznice: dict[str, tuple[Decimal, Decimal, str]]) -> tuple[Pozycja, ...]:
    wiersze = []
    for kod, (d_ilosc, d_wartosc, stawka) in roznice.items():
        pasujace = [w for w in org.wiersze if w.indeks == kod]
        if len(pasujace) != 1:
            raise BladMapowania(f"Towar {kod}: na fakturze korygowanej {org.numer} jest w {len(pasujace)} wierszach "
                                "(potrzebny dokładnie 1)")
        przed = pasujace[0]
        if przed.stawka != stawka:
            raise BladMapowania(f"Towar {kod}: stawka {stawka} w korekcie, {przed.stawka} na fakturze korygowanej")
        il_po, wart_po = przed.ilosc + d_ilosc, przed.wartosc + d_wartosc
        if il_po < 0 or wart_po < 0:
            raise BladMapowania(f"Towar {kod}: po korekcie ilość {il_po}, wartość {wart_po} — mniej niż zero")
        if d_ilosc == 0 and d_wartosc == 0:
            continue
        # Cena bez zmian, chyba że korekta zmienia wartość przy tej samej ilości (korekta ceny).
        cena_po = przed.cena
        if il_po and abs(przed.cena * il_po - wart_po) > GROSZ:
            cena_po = (wart_po / il_po).quantize(GROSZ, rounding=ROUND_HALF_UP)
        wiersze.append((przed.lp, replace(przed, stan_przed=True),
                        Pozycja(lp=przed.lp, nazwa=przed.nazwa, indeks=przed.indeks, jm=przed.jm, ilosc=il_po,
                                cena=cena_po, wartosc=wart_po, stawka=stawka)))
    return tuple(p for _, przed, po in sorted(wiersze, key=lambda t: t[0]) for p in (przed, po))


def _platnosc(raw: list[dict[str, Any]] | None) -> Platnosc | None:
    """Jak na wydruku korekty z XL: forma, termin i konto (proform). Bez „Kwota do zapłaty” — to my oddajemy pieniądze."""
    if raw is None:
        raise BladMapowania("Brak danych o płatności — wyeksportuj korektę aktualnym korekty_wiele.sql")
    if not raw:
        return None
    nazwy = {xl._tekst(p.get("forma")) for p in raw}
    if len(nazwy) != 1:
        raise BladMapowania(f"Kilka form płatności na jednej korekcie: {sorted(nazwy)}")
    nazwa = nazwy.pop()
    forma = next((kod for wzor, kod in xl.FORMY_PLATNOSCI if wzor.match(nazwa)), None)
    terminy = tuple(sorted({xl.data_clarion(p["termin"]) for p in raw if p.get("termin")}))
    # Konto proform na każdej korekcie, niezależnie od formy — „jak dane firmy” (wymóg księgowości).
    return Platnosc(terminy=terminy, forma=forma, forma_opis=None if forma else nazwa,
                    rachunek=config.RACHUNEK_KOREKT, nazwa=nazwa)


def faktura(raw: dict[str, Any], sprzedawca: Sprzedawca | None = None) -> Faktura:
    nag = raw["naglowek"]
    if int(nag["gid_typ"]) != TYP_FSK:
        raise BladMapowania(f"Typ dokumentu {nag['gid_typ']} — to nie korekta FSK ({TYP_FSK})")
    if int(nag["stan"]) == xl.STAN_ANULOWANA:
        raise xl.FakturaAnulowana(f"{nag.get('numer')} (GID {nag['gid_numer']}) jest anulowana — nie wysyłamy")
    if int(nag["stan"]) not in xl.STANY_DO_WYSYLKI:
        raise BladMapowania(f"Stan {nag['stan']} — wysyłamy tylko stany {sorted(xl.STANY_DO_WYSYLKI)}")
    waluta = xl._tekst(nag.get("waluta")).upper() or "PLN"
    if waluta != "PLN":
        raise BladMapowania(f"Waluta {waluta} — korekty obsługujemy tylko w PLN")
    flaga_nb = xl._tekst(nag.get("flaga_nb")).upper()
    if flaga_nb not in ("N", "B"):
        raise BladMapowania(f"Nieznana TrN_FlagaNB: {flaga_nb!r}")
    od_brutto = flaga_nb == "B"
    if not raw.get("pozycje"):
        raise BladMapowania("Brak pozycji korekty (brak spiętej WZK?)")
    if not raw.get("vat"):
        raise BladMapowania("Brak wierszy TraVat")

    korygowana = raw.get("korygowana") or {}
    if not korygowana.get("numer"):
        raise BladMapowania("Brak faktury korygowanej w XL (TrN_ZwrTyp/ZwrNumer)")
    ok = raw.get("oryginal_ksef")
    if not ok:
        raise BladMapowania("Brak faktury korygowanej z KSeF — uruchom: python -m ksef_connector oryginaly "
                            "<pliki korekt> --srodowisko prod")
    org = oryginal(ok["numer_ksef"], ok["xml"])

    nab = xl.nabywca(raw["nabywca"])
    numer_org = xl.numer_p2(korygowana["numer"])
    if org.numer != numer_org:
        raise BladMapowania(f"Faktura korygowana w KSeF ma numer {org.numer}, w XL {numer_org}")
    if (org.nip_nabywcy or None) != nab.nip:
        raise BladMapowania(f"Nabywca korekty (NIP {nab.nip}) inny niż na fakturze korygowanej w KSeF "
                            f"(NIP {org.nip_nabywcy})")
    if org.od_brutto != od_brutto:
        raise BladMapowania("Korekta i faktura korygowana liczone inaczej (od netto / od brutto)")

    sumy = tuple(SumaVat(stawka=xl.stawka_fa3(v["stawka"], v["flaga_vat"]),
                         netto=Decimal(v["netto"]).quantize(GROSZ), vat=Decimal(v["vat"]).quantize(GROSZ))
                 for v in raw["vat"])
    if sum((s.netto + s.vat for s in sumy), Decimal(0)) >= 0:
        raise BladMapowania("Korekta na plus albo zerowa — na razie obsługujemy tylko korekty zmniejszające")

    przyczyna = xl._tekst(nag.get("przyczyna")) or None
    if przyczyna:
        przyczyna = re.sub(r"\s+", " ", przyczyna)
    return Faktura(
        xl_gid_typ=TYP_FSK,
        xl_gid_numer=int(nag["gid_numer"]),
        numer=xl.numer_p2(nag["numer"]),
        data_wystawienia=xl.data_clarion(nag["data_wyst"]),
        data_sprzedazy=xl._data_sprzedazy(nag),
        waluta=waluta,
        od_brutto=od_brutto,
        sprzedawca=sprzedawca or config.SPRZEDAWCA,
        nabywca=nab,
        pozycje=_wiersze(org, _roznice(raw["pozycje"], od_brutto)),
        sumy_vat=sumy,
        platnosc=_platnosc(raw.get("platnosci")),
        wz=xl._wz(raw.get("wz")),
        opis=xl._opis(nag),
        korygowana=FakturaKorygowana(data_wystawienia=org.data_wystawienia, numer=org.numer,
                                     numer_ksef=org.numer_ksef),
        przyczyna_korekty=przyczyna,
    )
