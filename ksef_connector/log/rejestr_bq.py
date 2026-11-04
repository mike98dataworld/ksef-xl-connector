"""Rejestr wysyłek w BigQuery — wspólny dla laptopa i panelu BOK (ta sama blokada co w SQLite: rejestr.powod_blokady).

Tabela tylko do dopisywania: każda zmiana (wysłanie, numer referencyjny, status, UPO) to nowy wiersz zdarzenia
z identyfikatorem wysyłki. Stan wysyłki = widok `wysylki` (ostatnie zdarzenie każdego rodzaju).
Bez UPDATE, bo wiersze wstawione strumieniowo przez ok. 30 min nie dają się zmieniać, a insert jest szybki (~0,1 s).

Zbiór `ksef` osobno od zbioru z danymi ERP (ten nadpisuje synchronizacja ERP → BigQuery), w tym samym regionie.
Logowanie jak w source/bigquery.py (ADC na laptopie, konto usługi w Cloud Run).
"""
from __future__ import annotations

import base64
import getpass
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ksef_connector.log.rejestr import DUPLIKAT, powod_blokady  # noqa: F401 (DUPLIKAT jak w rejestr.py)
from ksef_connector.source.bigquery import LOKALIZACJA, PROJEKT, _klient, _zapytanie

ZBIOR = f"{PROJEKT}.ksef"
ZDARZENIA = f"{ZBIOR}.wysylki_zdarzenia"
WIDOK = f"{ZBIOR}.wysylki"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS `{ZBIOR}` OPTIONS (location = '{LOKALIZACJA}',
  description = 'Konektor KSeF: rejestr wysyłek (laptop i panel BOK)');

CREATE TABLE IF NOT EXISTS `{ZDARZENIA}` (
  id_wysylki    STRING    NOT NULL OPTIONS (description = 'uuid; przeniesione z SQLite: sqlite-<id>'),
  zdarzenie     STRING    NOT NULL OPTIONS (description = 'wyslano | ref | status | upo'),
  czas          TIMESTAMP NOT NULL,
  srodowisko    STRING,
  xl_gid_typ    INT64,
  xl_gid_numer  INT64,
  numer_faktury STRING    OPTIONS (description = 'P_2, z (S)'),
  hash_sha256   STRING,
  sesja_ref     STRING,
  kto           STRING    OPTIONS (description = 'kto wysłał: login panelu albo laptop:<użytkownik>'),
  faktura_ref   STRING,
  status_kod    INT64,
  status_opis   STRING,
  szczegoly     STRING    OPTIONS (description = 'JSON'),
  numer_ksef    STRING,
  upo           BYTES
)
PARTITION BY DATE(czas)
CLUSTER BY srodowisko, numer_faktury
OPTIONS (description = 'Rejestr wysyłek do KSeF, tylko dopisywanie (ksef_connector/log/rejestr_bq.py)');

CREATE OR REPLACE VIEW `{WIDOK}`
OPTIONS (description = 'Stan wysyłek: wiersz = wysyłka (ostatni status, numer KSeF, kto, kiedy)') AS
SELECT
  id_wysylki,
  ANY_VALUE(IF(zdarzenie = 'wyslano', srodowisko, NULL))    AS srodowisko,
  ANY_VALUE(IF(zdarzenie = 'wyslano', xl_gid_typ, NULL))    AS xl_gid_typ,
  ANY_VALUE(IF(zdarzenie = 'wyslano', xl_gid_numer, NULL))  AS xl_gid_numer,
  ANY_VALUE(IF(zdarzenie = 'wyslano', numer_faktury, NULL)) AS numer_faktury,
  ANY_VALUE(IF(zdarzenie = 'wyslano', hash_sha256, NULL))   AS hash_sha256,
  ANY_VALUE(IF(zdarzenie = 'wyslano', sesja_ref, NULL))     AS sesja_ref,
  ANY_VALUE(IF(zdarzenie = 'wyslano', kto, NULL))           AS kto,
  MIN(IF(zdarzenie = 'wyslano', czas, NULL))                AS wyslano,
  ARRAY_AGG(IF(zdarzenie = 'ref', faktura_ref, NULL) IGNORE NULLS ORDER BY czas DESC LIMIT 1)[SAFE_OFFSET(0)]
                                                            AS faktura_ref,
  ARRAY_AGG(IF(zdarzenie = 'status', STRUCT(status_kod, status_opis, szczegoly, numer_ksef), NULL)
            IGNORE NULLS ORDER BY czas DESC LIMIT 1)[SAFE_OFFSET(0)].*,
  LOGICAL_OR(zdarzenie = 'upo')                             AS ma_upo,
  MAX(czas)                                                 AS aktualizacja
FROM `{ZDARZENIA}`
GROUP BY id_wysylki;
"""


def _teraz() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kto_laptop() -> str:
    return f"laptop:{getpass.getuser()}"


class RejestrBQ:
    """Ten sam interfejs co log.rejestr.Rejestr; id wysyłki to uuid (str)."""

    def __init__(self, kto: str, klient=None):
        self.kto = kto
        self._bq = _klient(klient)
        self._srodowisko: dict[str, str] = {}  # id -> środowisko, do wierszy zdarzeń

    def close(self) -> None:
        pass

    def _dopisz(self, wiersze: list[dict[str, Any]]) -> None:
        bledy = self._bq.insert_rows_json(ZDARZENIA, wiersze)
        if bledy:  # zapis PRZED wysyłką musi się udać — inaczej nie wysyłamy
            raise RuntimeError(f"Rejestr BQ: nie zapisano {len(bledy)} wierszy: {bledy}")

    def blokada(self, srodowisko: str, gid_typ: int, gid_numer: int, numer: str | None = None) -> str | None:
        from google.cloud import bigquery
        wiersze = _zapytanie(self._bq, f"""
            SELECT id_wysylki AS id, numer_ksef, status_kod, status_opis FROM `{WIDOK}`
            WHERE srodowisko = @srodowisko
              AND ((xl_gid_typ = @gid_typ AND xl_gid_numer = @gid_numer) OR numer_faktury = @numer)
            ORDER BY wyslano, id_wysylki""", [
            bigquery.ScalarQueryParameter("srodowisko", "STRING", srodowisko),
            bigquery.ScalarQueryParameter("gid_typ", "INT64", gid_typ),
            bigquery.ScalarQueryParameter("gid_numer", "INT64", gid_numer),
            bigquery.ScalarQueryParameter("numer", "STRING", numer),
        ])
        return powod_blokady(wiersze)

    def zapisz_wyslanie(self, srodowisko: str, gid_typ: int, gid_numer: int, numer: str, hash_b64: str,
                        sesja_ref: str | None) -> str:
        """Zapis PRZED wywołaniem API — jeśli proces padnie w trakcie, wpis zablokuje ponowienie."""
        id_ = str(uuid.uuid4())
        self._dopisz([{"id_wysylki": id_, "zdarzenie": "wyslano", "czas": _teraz(), "srodowisko": srodowisko,
                       "xl_gid_typ": gid_typ, "xl_gid_numer": gid_numer, "numer_faktury": numer,
                       "hash_sha256": hash_b64, "sesja_ref": sesja_ref, "kto": self.kto}])
        self._srodowisko[id_] = srodowisko
        return id_

    def _zdarzenie(self, id_: str, zdarzenie: str, **pola) -> None:
        self._dopisz([{"id_wysylki": id_, "zdarzenie": zdarzenie, "czas": _teraz(),
                       "srodowisko": self._srodowisko.get(id_), **pola}])

    def zapisz_ref(self, id_: str, faktura_ref: str) -> None:
        self._zdarzenie(id_, "ref", faktura_ref=faktura_ref)

    def zapisz_status(self, id_: str, kod: int, opis: str, szczegoly: list | dict | None,
                      numer_ksef: str | None) -> None:
        self._zdarzenie(id_, "status", status_kod=kod, status_opis=opis, numer_ksef=numer_ksef,
                        szczegoly=json.dumps(szczegoly, ensure_ascii=False) if szczegoly else None)

    def zapisz_upo(self, id_: str, upo: bytes) -> None:
        self._zdarzenie(id_, "upo", upo=base64.b64encode(upo).decode("ascii"))

    def zapisz_z_uzgodnienia(self, srodowisko: str, gid_typ: int, gid_numer: int, numer: str,
                             numer_ksef: str | None, konflikt: str | None) -> str | None:
        if self.blokada(srodowisko, gid_typ, gid_numer, numer):
            return None
        i = self.zapisz_wyslanie(srodowisko, gid_typ, gid_numer, numer, "", None)
        if konflikt:
            self.zapisz_status(i, 0, f"uzgodnienie: KONFLIKT {konflikt}", None, None)
        else:
            self.zapisz_status(i, 200, "uzgodnienie: już w KSeF", None, numer_ksef)
        return i

    def zapisz_uzgodnienia(self, srodowisko: str, pozycje: list[tuple[int, int, str, str | None, str | None]]) -> int:
        """Wiele wyników uzgodnienia naraz: (gid_typ, gid_numer, numer, numer_ksef, konflikt). Jedno zapytanie
        o rejestr + wstawianie paczkami (po jednym ~1 s na fakturę). kto = „uzgodnienie” (wysłał XL / stary serwer)."""
        wpisy = wysylki(srodowisko, self._bq)
        teraz, wiersze, zapisane = _teraz(), [], 0
        for gid_typ, gid_numer, numer, numer_ksef, konflikt in pozycje:
            if powod_blokady([w for w in wpisy if (w.xl_gid_typ == gid_typ and w.xl_gid_numer == gid_numer)
                              or w.numer_faktury == numer]):
                continue
            id_ = str(uuid.uuid4())
            wspolne = {"id_wysylki": id_, "czas": teraz, "srodowisko": srodowisko}
            wiersze.append({**wspolne, "zdarzenie": "wyslano", "xl_gid_typ": gid_typ, "xl_gid_numer": gid_numer,
                            "numer_faktury": numer, "hash_sha256": "", "kto": "uzgodnienie"})
            wiersze.append({**wspolne, "zdarzenie": "status", "numer_ksef": None if konflikt else numer_ksef,
                            "status_kod": 0 if konflikt else 200,
                            "status_opis": f"uzgodnienie: KONFLIKT {konflikt}" if konflikt else "uzgodnienie: już w KSeF"})
            zapisane += 1
        for i in range(0, len(wiersze), 500):
            self._dopisz(wiersze[i:i + 500])
        return zapisane

    def wiersze(self, srodowisko: str | None = None) -> list[dict[str, Any]]:
        from google.cloud import bigquery
        wiersze = _zapytanie(self._bq, f"""
            SELECT id_wysylki AS id, srodowisko, xl_gid_numer, numer_faktury, status_kod, status_opis, numer_ksef,
                   wyslano, kto
            FROM `{WIDOK}` WHERE @srodowisko IS NULL OR srodowisko = @srodowisko
            ORDER BY wyslano, id_wysylki""", [bigquery.ScalarQueryParameter("srodowisko", "STRING", srodowisko)])
        return [dict(w.items()) for w in wiersze]


def wysylki(srodowisko: str, klient=None) -> list:
    """Stan wszystkich wysyłek środowiska (bez UPO) — do listy w panelu."""
    from google.cloud import bigquery

    from ksef_connector.panel import Wysylka
    wiersze = _zapytanie(_klient(klient), f"""
        SELECT id_wysylki, xl_gid_typ, xl_gid_numer, numer_faktury, numer_ksef, status_kod, status_opis, kto, wyslano
        FROM `{WIDOK}` WHERE srodowisko = @srodowisko ORDER BY wyslano, id_wysylki""",
                         [bigquery.ScalarQueryParameter("srodowisko", "STRING", srodowisko)])
    return [Wysylka(id=w["id_wysylki"], xl_gid_typ=w["xl_gid_typ"], xl_gid_numer=w["xl_gid_numer"],
                    numer_faktury=w["numer_faktury"], numer_ksef=w["numer_ksef"], status_kod=w["status_kod"],
                    status_opis=w["status_opis"], kto=w["kto"], wyslano=w["wyslano"]) for w in wiersze]


def utworz(klient=None) -> None:
    """Zbiór, tabela i widok (IF NOT EXISTS / OR REPLACE — można uruchamiać wielokrotnie)."""
    _zapytanie(_klient(klient), DDL, [])


def zdarzenia_z_sqlite(wiersz: dict[str, Any]) -> list[dict[str, Any]]:
    """Wiersz rejestru SQLite → zdarzenia (id_wysylki = sqlite-<id>, czasy z SQLite)."""
    id_ = f"sqlite-{wiersz['id']}"
    wspolne = {"id_wysylki": id_, "srodowisko": wiersz["srodowisko"]}
    pozniej = wiersz["aktualizacja"] or wiersz["wyslano"]
    zdarzenia = [{**wspolne, "zdarzenie": "wyslano", "czas": wiersz["wyslano"], "xl_gid_typ": wiersz["xl_gid_typ"],
                  "xl_gid_numer": wiersz["xl_gid_numer"], "numer_faktury": wiersz["numer_faktury"],
                  "hash_sha256": wiersz["hash_sha256"], "sesja_ref": wiersz["sesja_ref"], "kto": kto_laptop()}]
    if wiersz["faktura_ref"]:
        zdarzenia.append({**wspolne, "zdarzenie": "ref", "czas": pozniej, "faktura_ref": wiersz["faktura_ref"]})
    if wiersz["status_kod"] is not None:
        zdarzenia.append({**wspolne, "zdarzenie": "status", "czas": pozniej, "status_kod": wiersz["status_kod"],
                          "status_opis": wiersz["status_opis"], "szczegoly": wiersz["szczegoly"],
                          "numer_ksef": wiersz["numer_ksef"]})
    if wiersz["upo"]:
        zdarzenia.append({**wspolne, "zdarzenie": "upo", "czas": pozniej,
                          "upo": base64.b64encode(wiersz["upo"]).decode("ascii")})
    return zdarzenia


def przenies(wiersze_sqlite: list[dict[str, Any]], klient=None) -> tuple[int, int]:
    """Dopisuje do BQ wysyłki z SQLite, których jeszcze tam nie ma (po id_wysylki). Zwraca (przeniesione, pominięte).

    Zadanie ładowania (nie strumień): darmowe i od razu widoczne w całości.
    """
    from google.cloud import bigquery
    bq = _klient(klient)
    juz = {w["id_wysylki"] for w in _zapytanie(
        bq, f"SELECT DISTINCT id_wysylki FROM `{ZDARZENIA}` WHERE STARTS_WITH(id_wysylki, 'sqlite-')", [])}
    nowe = [w for w in wiersze_sqlite if f"sqlite-{w['id']}" not in juz]
    if nowe:
        zdarzenia = [z for w in nowe for z in zdarzenia_z_sqlite(w)]
        bq.load_table_from_json(zdarzenia, ZDARZENIA, job_config=bigquery.LoadJobConfig(
            schema=bq.get_table(ZDARZENIA).schema, write_disposition="WRITE_APPEND")).result()
    return len(nowe), len(wiersze_sqlite) - len(nowe)
