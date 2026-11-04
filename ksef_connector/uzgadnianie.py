"""Uzgodnienie dokumentów XL z fakturami w KSeF przed wysyłką zaległości (ryzyko dubli).

Dopasowanie po numerze faktury (P_2, z prefiksem "(S)"), potem kontrola kwoty brutto, NIP nabywcy
i numeru KSeF zapamiętanego w XL. Sam numer nie wystarcza: po odtworzeniu bazy z kopii zapasowej
numeracja mogła się powtórzyć — faktura o tym samym numerze w KSeF i inna w XL.

Kategorie:
    W_KSEF        — jest w KSeF i wszystko się zgadza → nie wysyłać, zapisać numer KSeF w rejestrze
    DO_WYSLANIA   — brak w KSeF → kandydat do wysyłki
    KONFLIKT      — ten sam numer, ale inna kwota / nabywca / numer KSeF albo duplikat → ręcznie
    TYLKO_W_KSEF  — jest w KSeF, brak w XL (np. wystawiona na starym serwerze po backupie) → ręcznie
    POMINIETA     — anulowana w XL albo faktura dla osoby prywatnej bez NIP, której nie ma w KSeF → nic nie robimy
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ksef_connector import config
from ksef_connector.ksef.pobieranie import FakturaKsef

STAN_ANULOWANA = 6
W_KSEF, DO_WYSLANIA, KONFLIKT, TYLKO_W_KSEF, POMINIETA = "W_KSEF", "DO_WYSLANIA", "KONFLIKT", "TYLKO_W_KSEF", "POMINIETA"


@dataclass(frozen=True)
class DokumentXl:
    gid_typ: int
    gid_numer: int
    numer: str
    data_wyst: date
    stan: int
    brutto: Decimal
    waluta: str
    nip_nabywcy: str | None
    numer_ksef_xl: str | None
    bez_nip: bool = False  # nabywca bez żadnego identyfikatora (osoba prywatna)


@dataclass(frozen=True)
class Pozycja:
    kategoria: str
    xl: DokumentXl | None
    ksef: FakturaKsef | None
    uwagi: str = ""


def _roznice(x: DokumentXl, k: FakturaKsef) -> list[str]:
    r = []
    if x.waluta == "PLN" and k.waluta == "PLN" and x.brutto != k.brutto:
        r.append(f"brutto XL {x.brutto} ≠ KSeF {k.brutto}")
    if x.nip_nabywcy and k.nip_nabywcy and x.nip_nabywcy != k.nip_nabywcy:
        r.append(f"NIP nabywcy XL {x.nip_nabywcy} ≠ KSeF {k.nip_nabywcy}")
    if bool(x.nip_nabywcy) != bool(k.nip_nabywcy):
        r.append("nabywca z NIP tylko po jednej stronie")
    if x.numer_ksef_xl and x.numer_ksef_xl != k.numer_ksef:
        r.append(f"XL zna inny numer KSeF ({x.numer_ksef_xl})")
    return r


def uzgodnij(dokumenty_xl: list[DokumentXl], faktury_ksef: list[FakturaKsef]) -> list[Pozycja]:
    ksef_po_numerze: dict[str, list[FakturaKsef]] = defaultdict(list)
    for k in faktury_ksef:
        ksef_po_numerze[k.numer].append(k)
    xl_po_numerze: dict[str, list[DokumentXl]] = defaultdict(list)
    for x in dokumenty_xl:
        xl_po_numerze[x.numer].append(x)

    wynik: list[Pozycja] = []
    for numer, dokumenty in xl_po_numerze.items():
        w_ksef = ksef_po_numerze.get(numer, [])
        aktywne = [x for x in dokumenty if x.stan != STAN_ANULOWANA]
        anulowane = [x for x in dokumenty if x.stan == STAN_ANULOWANA]

        for x in anulowane:
            if w_ksef and not aktywne:
                wynik.append(Pozycja(KONFLIKT, x, w_ksef[0], "anulowana w XL, ale jest w KSeF — potrzebna korekta"))
            else:
                wynik.append(Pozycja(POMINIETA, x, None, "anulowana"))

        if len(aktywne) > 1:
            for x in aktywne:
                wynik.append(Pozycja(KONFLIKT, x, w_ksef[0] if w_ksef else None, "kilka aktywnych dokumentów XL o tym numerze"))
            continue
        if not aktywne:
            continue
        x = aktywne[0]
        if not w_ksef:
            if x.numer_ksef_xl:
                wynik.append(Pozycja(KONFLIKT, x, None,
                                     f"XL zna numer KSeF {x.numer_ksef_xl}, ale brak go w KSeF w tym zakresie dat"))
            elif x.bez_nip and not config.WYSYLAJ_BEZ_NIP:
                wynik.append(Pozycja(POMINIETA, x, None, "osoba prywatna bez NIP — nie wysyłamy"))
            else:
                wynik.append(Pozycja(DO_WYSLANIA, x, None))
        elif len(w_ksef) > 1:
            numery = ", ".join(k.numer_ksef for k in w_ksef)
            wynik.append(Pozycja(KONFLIKT, x, w_ksef[0], f"w KSeF {len(w_ksef)} faktury o tym numerze: {numery}"))
        else:
            roznice = _roznice(x, w_ksef[0])
            wynik.append(Pozycja(KONFLIKT if roznice else W_KSEF, x, w_ksef[0], "; ".join(roznice)))

    for numer, w_ksef in ksef_po_numerze.items():
        if numer not in xl_po_numerze:
            for k in w_ksef:
                wynik.append(Pozycja(TYLKO_W_KSEF, None, k, "brak w XL (np. wystawiona na starym serwerze po backupie)"))
    return wynik


# --- wejście z XL: wynik sql/lista_xl.sql skopiowany z SSMS z nagłówkami (TSV) ---

def _tekst(v: str) -> str | None:
    v = (v or "").strip()
    return None if v in ("", "NULL") else v


def _nip_pl(prefiks: str | None, nip: str | None) -> str | None:
    cyfry = re.sub(r"\D", "", nip or "")
    return cyfry if (prefiks or "PL").upper() == "PL" and len(cyfry) == 10 else None


def wczytaj_liste_xl(sciezka: Path | str) -> list[DokumentXl]:
    with open(sciezka, encoding="utf-8-sig", newline="") as f:
        wiersze = list(csv.DictReader(f, delimiter="\t"))
    wynik = []
    for i, w in enumerate(wiersze, start=2):
        try:
            wynik.append(DokumentXl(
                gid_typ=int(w["gid_typ"]),
                gid_numer=int(w["gid_numer"]),
                numer=w["numer"].strip(),
                data_wyst=date.fromisoformat(w["data_wyst"].strip()[:10]),
                stan=int(w["stan"]),
                brutto=Decimal(w["brutto"].strip()).quantize(Decimal("0.01")),
                waluta=_tekst(w.get("waluta", "")) or "PLN",
                nip_nabywcy=_nip_pl(_tekst(w.get("nip_prefiks", "")), _tekst(w.get("nip_nabywcy", ""))),
                bez_nip=not re.sub(r"\W", "", _tekst(w.get("nip_nabywcy", "")) or ""),
                numer_ksef_xl=_tekst(w.get("numer_ksef_xl", "")),
            ))
        except (KeyError, ValueError, InvalidOperation) as e:
            raise ValueError(f"{sciezka}: wiersz {i}: {e!r}") from e
    return wynik


def z_bigquery(dokumenty) -> list[DokumentXl]:
    """Lista z widoku ERP w BigQuery (source.bigquery.lista) zamiast TSV z SSMS."""
    return [DokumentXl(gid_typ=d.gid_typ, gid_numer=d.gid_numer, numer=d.numer, data_wyst=d.data_wyst, stan=d.stan,
                       brutto=(d.brutto_pln or Decimal(0)).quantize(Decimal("0.01")), waluta=d.waluta,
                       nip_nabywcy=_nip_pl(None, d.nip), bez_nip=d.nip is None, numer_ksef_xl=d.ksef_w_xl)
            for d in dokumenty]


def zapisz_raport(pozycje: list[Pozycja], sciezka: Path) -> None:
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    with open(sciezka, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["kategoria", "numer", "data_wyst", "gid_typ", "gid_numer", "stan", "brutto_xl",
                    "numer_ksef", "brutto_ksef", "uwagi"])
        for p in sorted(pozycje, key=lambda p: (p.kategoria, (p.xl or p.ksef).numer)):
            x, k = p.xl, p.ksef
            w.writerow([p.kategoria, (x or k).numer, (x or k).data_wyst, x and x.gid_typ, x and x.gid_numer,
                        x and x.stan, x and x.brutto, k and k.numer_ksef, k and k.brutto, p.uwagi])
