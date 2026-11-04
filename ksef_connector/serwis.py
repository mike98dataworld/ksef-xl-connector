"""Usługa HTTP dla panelu BOK (Cloud Run, prywatna: woła ją tylko wewnętrzna aplikacja z panelem).

    GET  /zdrowie                    — działa
    GET  /zdrowie/ksef               — logowanie tokenem do KSeF (bez wysyłki)
    GET  /dokumenty?od=RRRR-MM-DD    — lista: do_wyslania / w_ksef / pominieta (bez pytania KSeF)
    GET  /podglad?numer=…            — faktura po zmapowaniu, błędy, XML (korekty/rozliczające: odczyt z KSeF)
    POST /wyslij {numery, kto}       — wysyłka; jedna naraz (blokada w rejestrze to odczyt przed zapisem)

Środowisko KSeF z KSEF_SRODOWISKO (wymagane), token z KSEF_TOKEN_<ŚRODOWISKO> (Secret Manager), KSEF_NIP.
Uruchomienie lokalnie: uvicorn ksef_connector.serwis:app
"""
from __future__ import annotations

import logging
import os
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ksef_connector import panel, wysylka
from ksef_connector.log import rejestr_bq
from ksef_connector.mapping.xl import BladMapowania, numer_p2
from ksef_connector.source import bigquery

log = logging.getLogger("ksef_connector.serwis")
app = FastAPI(title="ksef-connector", docs_url=None, redoc_url=None, openapi_url=None)
_wysylka_trwa = threading.Lock()
_pamiec_xml: dict[str, bytes] = {}  # XML faktur z KSeF (korygowane), limit pobierania 64/h
KATALOG_UPO = Path(os.environ.get("KSEF_KATALOG", "/tmp/ksef"))


@contextmanager
def _bez_wyjscia():
    """Konektor (CLI) zgłasza błędy konfiguracji przez SystemExit — w usłudze to ma być błąd żądania, nie koniec procesu."""
    try:
        yield
    except SystemExit as e:
        raise HTTPException(500, str(e)) from None


def srodowisko() -> str:
    s = os.environ.get("KSEF_SRODOWISKO", "")
    if s not in ("test", "demo", "prod"):
        raise HTTPException(500, "Brak KSEF_SRODOWISKO (test / demo / prod) w konfiguracji usługi")
    return s


class ZrodloKsefLeniwe(panel.ZrodloKsef):
    """Loguje się do KSeF dopiero przy pierwszym potrzebnym odczycie."""

    def __init__(self, klient):
        self._klient, self._tokeny = klient, None
        super().__init__(szukaj=self._szukaj, xml=self._xml, pamiec_xml=_pamiec_xml)

    def _zaloguj(self):
        if self._tokeny is None:
            from ksef_connector.ksef import auth
            konto = wysylka.konto(self._klient, wypisz=log.info)
            self._tokeny = auth.zaloguj_tokenem(self._klient, konto.nip, konto.token)
        return self._tokeny

    def _szukaj(self, numer: str, data_wyst: date) -> list[str]:
        from ksef_connector.ksef.pobieranie import szukaj_po_numerze
        return [f.numer_ksef for f in szukaj_po_numerze(self._klient, self._zaloguj(), numer, data_wyst)]

    def _xml(self, numer_ksef: str) -> bytes:
        return self._klient.faktura_xml(self._zaloguj().access, numer_ksef)


def _znane(srod: str) -> tuple[list, dict[str, str]]:
    wysylki = rejestr_bq.wysylki(srod)
    return wysylki, panel.znane_numery_ksef(bigquery.lista(panel.DATA_OD), wysylki)


@app.get("/zdrowie")
def zdrowie() -> dict[str, Any]:
    return {"ok": True, "srodowisko": os.environ.get("KSEF_SRODOWISKO")}


@app.get("/zdrowie/ksef")
def zdrowie_ksef() -> dict[str, Any]:
    from ksef_connector.ksef import auth
    from ksef_connector.ksef.klient import KlientKsef
    with _bez_wyjscia():
        srod = srodowisko()
        with KlientKsef(srod) as klient:
            konto = wysylka.konto(klient, wypisz=log.info)
            auth.zaloguj_tokenem(klient, konto.nip, konto.token)
        return {"ok": True, "srodowisko": srod, "nip": konto.nip}


@app.get("/dokumenty")
def dokumenty(od: date = Query(panel.DATA_OD)) -> dict[str, Any]:
    with _bez_wyjscia():
        srod = srodowisko()
        pozycje = panel.lista(bigquery.lista(max(od, panel.DATA_OD)), rejestr_bq.wysylki(srod), bigquery.tresci_po_gid)
        return {"srodowisko": srod, "liczby": Counter(p.status for p in pozycje),
                "dokumenty": [panel._jsonowalne(p) for p in pozycje]}


@app.get("/podglad")
def podglad(numer: str) -> dict[str, Any]:
    from ksef_connector.ksef.klient import KlientKsef
    with _bez_wyjscia():
        srod = srodowisko()
        dokumenty, bledy = bigquery.pobierz_po_numerach([numer])
        if bledy:
            raise HTTPException(404, "; ".join(bledy))
        _, znane = _znane(srod)
        with KlientKsef(srod) as klient:
            return panel.podglad(dokumenty[numer], znane, ZrodloKsefLeniwe(klient))


class Zlecenie(BaseModel):
    numery: list[str] = Field(min_length=1, max_length=wysylka.MAKS_PARTIA)
    kto: str = Field(min_length=1, max_length=100)


@app.post("/wyslij")
def wyslij(z: Zlecenie) -> dict[str, Any]:
    from ksef_connector.ksef.klient import KlientKsef
    srod = srodowisko()
    if not _wysylka_trwa.acquire(blocking=False):
        raise HTTPException(409, "Trwa inna wysyłka — spróbuj za chwilę")
    try:
        numery = list(dict.fromkeys(numer_p2(n) for n in z.numery))
        komunikaty: list[str] = []
        dokumenty, bledy = bigquery.pobierz_po_numerach(numery)
        komunikaty += [f"POMINIĘTA  {b}" for b in bledy]
        _, znane = _znane(srod)
        with KlientKsef(srod) as klient:
            ksef = ZrodloKsefLeniwe(klient)
            for numer, raw in list(dokumenty.items()):
                if panel._wymaga_ksef(raw):
                    try:
                        dokumenty[numer] = panel.uzupelnij_z_ksef(raw, znane, ksef)
                    except BladMapowania as e:
                        komunikaty.append(f"POMINIĘTA  {numer}: {e}")
                        del dokumenty[numer]
        problemy = 0
        if dokumenty:
            rejestr = rejestr_bq.RejestrBQ(kto=z.kto)
            problemy = wysylka.wyslij(list(dokumenty), dokumenty.__getitem__, srodowisko=srod, rejestr=rejestr,
                                      out=KATALOG_UPO, wypisz=komunikaty.append, potwierdzone=set(dokumenty))
        po = {w.numer_faktury: w for w in rejestr_bq.wysylki(srod) if w.numer_faktury in numery and w.numer_ksef}
        log.info("wysyłka %s: %s", z.kto, komunikaty)
        return {"komunikaty": komunikaty, "problemy": problemy,
                "wyniki": [{"numer": n, "numer_ksef": po[n].numer_ksef if n in po else None} for n in numery]}
    except SystemExit as e:
        raise HTTPException(500, str(e)) from None
    finally:
        _wysylka_trwa.release()
