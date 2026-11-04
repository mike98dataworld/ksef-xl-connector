"""Źródło z BigQuery: widok ERP dbo.v_ksef_dokumenty, przenoszony okresowo synchronizacją ERP → BigQuery (source/widok.py).

`dokument_json` to ten sam JSON co eksport z SSMS, więc reszta konektora (oryginaly, zbuduj, wyslij) działa bez zmian.
Logowanie: Application Default Credentials (laptop: `gcloud auth application-default login`; Cloud Run: konto usługi).
Dane w BQ mają opóźnienie do godziny względem ERP.

Anulowanie i ponowne wystawienie w ERP daje dwa dokumenty o tym samym numerze (anulowany + nowy, inny GID,
np. (S)FS-23/10/26/C/F) — przy wyborze po numerze bierzemy nieanulowany.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ksef_connector.mapping.xl import STAN_ANULOWANA, numer_p2

PROJEKT = os.environ.get("KSEF_BQ_PROJEKT", "moj-projekt-gcp")
LOKALIZACJA = os.environ.get("KSEF_BQ_LOKALIZACJA", "europe-central2")  # zbiór z danymi ERP i zbiór ksef
ZBIOR_ERP = os.environ.get("KSEF_BQ_ZBIOR_ERP", "erp_xl")
TABELA = f"{PROJEKT}.{ZBIOR_ERP}.v_ksef_dokumenty"
KOLUMNY = "gid_typ, gid_numer, numer, seria, data_wyst, stan, waluta, nip, nabywca, brutto_pln, ksef_w_xl"


@dataclass(frozen=True)
class Dokument:
    """Wiersz listy (bez treści) — do panelu i do wyboru dokumentów."""
    gid_typ: int
    gid_numer: int
    numer: str
    seria: str
    data_wyst: date
    stan: int
    waluta: str
    nip: str | None
    nabywca: str | None
    brutto_pln: Decimal | None
    ksef_w_xl: str | None


def _klient(klient=None):
    if klient is not None:
        return klient
    from google.cloud import bigquery  # zależność opcjonalna: pip install -e ".[bq]"
    return bigquery.Client(project=PROJEKT, location=LOKALIZACJA)


def _zapytanie(klient, sql: str, parametry: list) -> list:
    from google.auth.exceptions import RefreshError
    from google.cloud import bigquery
    try:
        return list(klient.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=parametry)).result())
    except RefreshError:
        raise SystemExit("BigQuery: wygasło logowanie — uruchom: gcloud auth application-default login")


def lista(od: date, klient=None) -> list[Dokument]:
    from google.cloud import bigquery
    wiersze = _zapytanie(_klient(klient), f"SELECT {KOLUMNY} FROM `{TABELA}` WHERE data_wyst >= @od "
                                          "ORDER BY data_wyst, gid_typ, numer",
                         [bigquery.ScalarQueryParameter("od", "DATE", od)])
    return [Dokument(
        gid_typ=int(w["gid_typ"]), gid_numer=int(w["gid_numer"]), numer=numer_p2(w["numer"]),
        seria=w["seria"] or "", data_wyst=w["data_wyst"], stan=int(w["stan"]), waluta=w["waluta"] or "PLN",
        nip=(w["nip"] or "").strip() or None, nabywca=w["nabywca"],
        brutto_pln=Decimal(str(w["brutto_pln"])) if w["brutto_pln"] is not None else None,
        ksef_w_xl=w["ksef_w_xl"]) for w in wiersze]


def _surowy(dokument_json: str) -> dict[str, Any]:
    return json.loads(dokument_json, parse_float=Decimal)


def wybierz_po_numerze(wiersze: list[tuple[str, int, int, str]], numery: list[str]) -> tuple[dict[str, str], list[str]]:
    """(numer, gid_numer, stan, dokument_json) → {numer: dokument_json}, lista błędów.

    Na numer: dokładnie jeden nieanulowany; jeśli są tylko anulowane — ostatni (mapowanie i tak go wstrzyma).
    """
    po_numerze: dict[str, list[tuple[int, int, str]]] = {}
    for numer, gid, stan, js in wiersze:
        po_numerze.setdefault(numer_p2(numer), []).append((int(gid), int(stan), js))
    wynik, bledy = {}, []
    for numer in numery:
        kandydaci = po_numerze.get(numer_p2(numer), [])
        aktywne = [k for k in kandydaci if k[1] != STAN_ANULOWANA]
        if len(aktywne) > 1:
            bledy.append(f"{numer}: {len(aktywne)} nieanulowane dokumenty o tym numerze (GID "
                         f"{', '.join(str(k[0]) for k in aktywne)}) — wyjaśnij w ERP")
        elif aktywne:
            wynik[numer] = aktywne[0][2]
        elif kandydaci:
            wynik[numer] = max(kandydaci)[2]
        else:
            bledy.append(f"{numer}: brak w BigQuery (inny numer albo dokument młodszy niż ostatnia synchronizacja)")
    return wynik, bledy


def pobierz_po_numerach(numery: list[str], klient=None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Treść dokumentów (raw dict jak z eksportu SSMS) po numerach z „(S)”, np. "(S)FS-212/10/26/B"."""
    from google.cloud import bigquery
    wiersze = _zapytanie(_klient(klient), f"SELECT numer, gid_numer, stan, dokument_json FROM `{TABELA}` "
                                          "WHERE numer IN UNNEST(@numery)",
                         [bigquery.ArrayQueryParameter("numery", "STRING", [numer_p2(n) for n in numery])])
    wybrane, bledy = wybierz_po_numerze([(w["numer"], w["gid_numer"], w["stan"], w["dokument_json"]) for w in wiersze],
                                        numery)
    return {n: _surowy(js) for n, js in wybrane.items()}, bledy


def tresci_po_gid(dokumenty: list[Dokument], klient=None) -> dict[int, dict[str, Any]]:
    """Treść wybranych dokumentów (raw jak z eksportu SSMS), kluczem GID — panel pobiera ją tylko dla kandydatów."""
    from google.cloud import bigquery
    gidy = sorted({d.gid_numer for d in dokumenty})
    wiersze = _zapytanie(_klient(klient), f"SELECT gid_numer, dokument_json FROM `{TABELA}` "
                                          "WHERE gid_numer IN UNNEST(@gidy)",
                         [bigquery.ArrayQueryParameter("gidy", "INT64", gidy)])
    return {int(w["gid_numer"]): _surowy(w["dokument_json"]) for w in wiersze if w["dokument_json"]}
