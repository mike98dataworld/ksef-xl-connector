"""Logika panelu BOK: lista dokumentów, podgląd, wysyłka. Bez HTTP (to serwis.py) — testowalne na danych w pamięci.

Lista nie pyta KSeF (limit metadanych: 20 zapytań/h). Stan bierze z rejestru wysyłek (BQ `ksef.wysylki`) i z numeru
KSeF zapisanego w ERP (`ksef_w_xl`). Faktury wysłane przez XL bez numeru w ERP trafiają do rejestru przez
`uzgodnij --z-bq --zapisz` (wpis „uzgodnienie: już w KSeF”).

Korekty i faktury rozliczające zaliczkę potrzebują danych z KSeF (faktura korygowana, numery KSeF zaliczek) —
na liście nie są mapowane, pełne sprawdzenie dopiero w podglądzie i przy wysyłce. Numer KSeF bierzemy najpierw
z rejestru / ERP, a zapytanie do KSeF tylko, gdy dokument jest starszy niż widok (np. FSL z kwietnia).
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Iterable

from ksef_connector import config
from ksef_connector.log.rejestr import powod_blokady
from ksef_connector.mapping.korekta import TYP_FSK
from ksef_connector.mapping.xl import STAN_ANULOWANA, TYP_FS, TYP_FSE, BladMapowania, data_clarion, numer_p2
from ksef_connector.mapping.zaliczki import TYP_FSL
from ksef_connector.pipeline import Wynik, przygotuj
from ksef_connector.source.bigquery import Dokument

DATA_OD = date(2026, 9, 1)  # jak widok ERP (source/widok.py DATA_OD)
TYPY = {TYP_FS: "FS", TYP_FSE: "FSE", TYP_FSK: "FSK", TYP_FSL: "FSL"}

DO_WYSLANIA, W_KSEF, POMINIETA = "do_wyslania", "w_ksef", "pominieta"

# Powód pominięcia / błąd po ludzku dla BOK (bez nazw tabel i kolumn ERP). Kolejność ma znaczenie: pierwszy pasujący.
# Pełny komunikat techniczny zostaje w polu `powod` (panel pokazuje go w szczegółach).
DO_ADMINA = "zgłoś administratorowi konektora"
POWODY_BOK = [
    (r"osoba prywatna bez NIP", "Osoba prywatna bez NIP — do KSeF nie wysyłamy"),
    (r"^anulowana w ERP", "Anulowana w ERP"),
    (r"ExpoNorm=7", "Sprzedaż do konsumenta za granicą (OSS) — do KSeF nie wysyłamy"),
    (r"zablokowana w rejestrze: .*KONFLIKT", "Ten numer jest w KSeF na inną fakturę — do wyjaśnienia, nie wysyłać"),
    (r"zablokowana w rejestrze: .*bez statusu", "Wysyłka bez potwierdzenia z KSeF — sprawdź w KSeF, zanim wyślesz ponownie"),
    (r"^Stan \d+ — wysyłamy tylko", "Dokument niezatwierdzony w ERP (bufor) — zatwierdź w ERP"),
    (r"najpierw (ją )?wyślij", None),  # komunikat już jest po ludzku
    (r"nie ma w KSeF", None),
    (r"częściowo zapłacona", "Faktura częściowo zapłacona — konektor jeszcze tego nie obsługuje, " + DO_ADMINA),
    (r"Korekta na plus", "Korekta na plus — konektor jeszcze tego nie obsługuje, " + DO_ADMINA),
    (r"Kilka form płatności", "Kilka form płatności na jednym dokumencie — " + DO_ADMINA),
    (r"Niepoprawny NIP nabywcy", "Niepoprawny NIP nabywcy — popraw w ERP"),
    (r"Brak nazwy nabywcy", "Brak nazwy nabywcy — uzupełnij w ERP"),
    (r"WDT bez numeru VAT UE", "Brak numeru VAT UE nabywcy — uzupełnij w ERP"),
    (r"spoza UE", "Eksport poza UE — konektor jeszcze tego nie obsługuje, " + DO_ADMINA),
    (r"w walucie obcej obsługujemy tylko WDT", "Faktura krajowa w walucie obcej — " + DO_ADMINA),
    (r"Nabywca korekty .* inny", "Inny nabywca niż na fakturze korygowanej — sprawdź w ERP"),
    (r"^błędy:", "Faktura nie przeszła kontroli (sumy / format KSeF) — " + DO_ADMINA),
    (r"brak treści dokumentu", "Brak danych dokumentu w kopii ERP — spróbuj za godzinę"),
]


# Pominięte słusznie (do KSeF nie idą) — reszta pominiętych to sprawy do wyjaśnienia, panel pokazuje je BOK.
POMINIETE_ZWYKLE = [r"osoba prywatna bez NIP", r"^anulowana w ERP", r"ExpoNorm=7"]


def do_wyjasnienia(powod: str | None) -> bool:
    return not any(re.search(w, powod or "") for w in POMINIETE_ZWYKLE)


def dla_bok(powod: str | None) -> str | None:
    if not powod:
        return powod
    for wzor, tekst in POWODY_BOK:
        if re.search(wzor, powod):
            return tekst or powod
    return "Nietypowy dokument — konektor go nie przygotuje, " + DO_ADMINA


@dataclass
class Pozycja:
    numer: str
    typ: str
    gid_typ: int
    gid_numer: int
    data_wyst: date
    nabywca: str | None
    nip: str | None
    brutto_pln: Decimal | None
    waluta: str
    status: str  # do_wyslania | w_ksef | pominieta
    powod: str | None = None  # pominieta: dlaczego (technicznie); do_wyslania: uwaga (np. sprawdzenie z KSeF w podglądzie)
    powod_bok: str | None = None  # ten sam powód po ludzku (dla_bok)
    do_wyjasnienia: bool = False  # pominieta z powodu, który ktoś musi wyjaśnić (nie: bez NIP, anulowana, OSS)
    rodzaj: str | None = None  # VAT / KOR / ZAL / ROZ, gdy zmapowana
    numer_ksef: str | None = None
    kto: str | None = None  # w_ksef: kto wysłał (login, laptop:…, XL, uzgodnienie)
    wyslano: Any = None


@dataclass
class Wysylka:
    """Wiersz widoku rejestru (srodowisko prod)."""
    id: str
    xl_gid_typ: int | None
    xl_gid_numer: int | None
    numer_faktury: str
    numer_ksef: str | None
    status_kod: int | None
    status_opis: str | None
    kto: str | None
    wyslano: Any

    def __getitem__(self, klucz):  # powod_blokady czyta wpisy jak słowniki
        return getattr(self, klucz)


def znane_numery_ksef(dokumenty: Iterable[Dokument], wysylki: Iterable[Wysylka]) -> dict[str, str]:
    """Numer faktury → numer KSeF ze wszystkiego, co wiemy bez pytania KSeF."""
    znane = {d.numer: d.ksef_w_xl for d in dokumenty if d.ksef_w_xl and d.stan != STAN_ANULOWANA}
    znane.update({w.numer_faktury: w.numer_ksef for w in wysylki if w.numer_ksef})
    return znane


def _wymaga_ksef(raw: dict[str, Any]) -> bool:
    return int(raw["naglowek"]["gid_typ"]) == TYP_FSK or bool(raw.get("zaliczki"))


def _status_rejestru(d: Dokument, wysylki: list[Wysylka]) -> tuple[str | None, Wysylka | None]:
    """Blokada z rejestru (po GID albo numerze) i wpis z numerem KSeF, jeśli jest."""
    wpisy = [w for w in wysylki
             if (w.xl_gid_typ == d.gid_typ and w.xl_gid_numer == d.gid_numer) or w.numer_faktury == d.numer]
    return powod_blokady(wpisy), next((w for w in wpisy if w.numer_ksef), None)


def lista(dokumenty: list[Dokument], wysylki: list[Wysylka],
          tresci: Callable[[list[Dokument]], dict[int, dict[str, Any]]]) -> list[Pozycja]:
    """tresci: GID dokumentów → raw (dokument_json) — pobierane tylko dla kandydatów do wysyłki."""
    pozycje: list[Pozycja] = []
    kandydaci: list[tuple[Pozycja, Dokument]] = []
    for d in dokumenty:
        p = Pozycja(numer=d.numer, typ=TYPY.get(d.gid_typ, str(d.gid_typ)), gid_typ=d.gid_typ, gid_numer=d.gid_numer,
                    data_wyst=d.data_wyst, nabywca=d.nabywca, nip=d.nip, brutto_pln=d.brutto_pln, waluta=d.waluta,
                    status=POMINIETA)
        pozycje.append(p)
        blokada, wysylka = _status_rejestru(d, wysylki)
        if d.stan == STAN_ANULOWANA:
            p.powod = "anulowana w ERP"
        elif wysylka:
            p.status, p.numer_ksef, p.kto, p.wyslano = W_KSEF, wysylka.numer_ksef, wysylka.kto, wysylka.wyslano
        elif d.ksef_w_xl:
            p.status, p.numer_ksef, p.kto = W_KSEF, d.ksef_w_xl, "XL"
        elif blokada:
            p.powod = f"zablokowana w rejestrze: {blokada}"
        elif d.nip is None and d.gid_typ != TYP_FSE and not config.WYSYLAJ_BEZ_NIP:
            p.powod = "osoba prywatna bez NIP — nie wysyłamy do KSeF"
        else:
            kandydaci.append((p, d))

    raw_po_gid = tresci([d for _, d in kandydaci]) if kandydaci else {}
    for p, d in kandydaci:
        raw = raw_po_gid.get(d.gid_numer)
        if raw is None:
            p.powod = "brak treści dokumentu w BigQuery"
        elif _wymaga_ksef(raw):
            p.status, p.rodzaj = DO_WYSLANIA, "KOR" if d.gid_typ == TYP_FSK else "ROZ"
            p.powod = "dane z KSeF (faktura korygowana / zaliczki) sprawdzane w podglądzie i przy wysyłce"
        else:
            _ocen(p, raw)
    for p in pozycje:
        if p.status == POMINIETA:
            p.powod_bok = dla_bok(p.powod)
            p.do_wyjasnienia = do_wyjasnienia(p.powod)
        elif p.status == DO_WYSLANIA and p.powod:
            p.powod_bok = "Dane z KSeF (faktura korygowana / zaliczka) sprawdzimy w podglądzie i przy wysyłce"
    return pozycje


def _ocen(p: Pozycja, raw: dict[str, Any]) -> Wynik | None:
    try:
        wynik = przygotuj(raw)
    except BladMapowania as e:
        p.status, p.powod = POMINIETA, str(e)
        return None
    p.rodzaj = wynik.faktura.rodzaj
    if not wynik.ok:
        p.status, p.powod = POMINIETA, "błędy: " + "; ".join(wynik.bledy)
    elif wynik.faktura.nabywca.nip is None and not config.WYSYLAJ_BEZ_NIP:
        p.status, p.powod = POMINIETA, "osoba prywatna bez NIP — nie wysyłamy do KSeF"
    else:
        p.status, p.powod = DO_WYSLANIA, None
    return wynik


# --- dane z KSeF dla korekt i faktur rozliczających ---

@dataclass
class ZrodloKsef:
    """Dostęp do KSeF tylko do odczytu: wyszukanie po numerze (metadane, 20/h) i pobranie XML (64/h)."""
    szukaj: Callable[[str, date], list[str]]  # numer, data wystawienia → numery KSeF
    xml: Callable[[str], bytes]  # numer KSeF → XML faktury
    pamiec_xml: dict[str, bytes] = field(default_factory=dict)

    def pobierz_xml(self, numer_ksef: str) -> bytes:
        if numer_ksef not in self.pamiec_xml:
            self.pamiec_xml[numer_ksef] = self.xml(numer_ksef)
        return self.pamiec_xml[numer_ksef]


def _numer_ksef(numer: str, data_wyst: date, znane: dict[str, str], ksef: ZrodloKsef) -> str | None:
    if numer in znane:
        return znane[numer]
    znalezione = ksef.szukaj(numer, data_wyst)
    if len(znalezione) > 1:
        raise BladMapowania(f"{numer} jest w KSeF {len(znalezione)}× (potrzebna najwyżej 1)")
    return znalezione[0] if znalezione else None


def uzupelnij_z_ksef(raw: dict[str, Any], znane: dict[str, str], ksef: ZrodloKsef) -> dict[str, Any]:
    """To samo co polecenie `oryginaly`, dla jednego dokumentu: klucze oryginal_ksef / zaliczki_ksef."""
    raw = dict(raw)
    if raw.get("zaliczki"):
        raw["zaliczki_ksef"] = {
            numer_p2(z["numer"]): _numer_ksef(numer_p2(z["numer"]), data_clarion(z["data_wyst"]), znane, ksef)
            for z in raw["zaliczki"]}
    if int(raw["naglowek"]["gid_typ"]) == TYP_FSK:
        kor = raw.get("korygowana") or {}
        if not kor.get("numer"):
            raise BladMapowania("brak faktury korygowanej w ERP")
        nr = _numer_ksef(numer_p2(kor["numer"]), data_clarion(kor["data_wyst"]), znane, ksef)
        if nr is None:
            raise BladMapowania(f"faktury korygowanej {numer_p2(kor['numer'])} nie ma w KSeF — najpierw ją wyślij")
        raw["oryginal_ksef"] = {"numer_ksef": nr, "xml": ksef.pobierz_xml(nr).decode("utf-8")}
    return raw


# --- podgląd ---

def _jsonowalne(x):
    if dataclasses.is_dataclass(x):
        return {k: _jsonowalne(v) for k, v in dataclasses.asdict(x).items()}
    if isinstance(x, dict):
        return {k: _jsonowalne(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonowalne(v) for v in x]
    if isinstance(x, (Decimal, date)):
        return str(x)
    return x


def podglad(raw: dict[str, Any], znane: dict[str, str], ksef: ZrodloKsef | None) -> dict[str, Any]:
    """Faktura po zmapowaniu (pola jak w FA(3), do wyświetlenia), błędy i XML — nic nie wysyła."""
    try:
        if _wymaga_ksef(raw):
            if ksef is None:
                raise BladMapowania("potrzebne dane z KSeF")
            raw = uzupelnij_z_ksef(raw, znane, ksef)
        wynik = przygotuj(raw)
    except BladMapowania as e:
        return {"ok": False, "bledy": [str(e)], "bledy_bok": [dla_bok(str(e))], "faktura": None, "xml": None}
    bledy = list(wynik.bledy)
    if wynik.faktura.nabywca.nip is None and not config.WYSYLAJ_BEZ_NIP:
        bledy.append("osoba prywatna bez NIP — nie wysyłamy do KSeF")
    f = _jsonowalne(wynik.faktura)
    f.pop("sprzedawca", None)
    f["rodzaj"] = wynik.faktura.rodzaj
    f["brutto"] = str(wynik.faktura.brutto)
    bledy_bok = list(dict.fromkeys(dla_bok("błędy: " + b if b in wynik.bledy else b) for b in bledy))
    return {"ok": not bledy, "bledy": bledy, "bledy_bok": bledy_bok, "faktura": f, "xml": wynik.xml.decode("utf-8")}
