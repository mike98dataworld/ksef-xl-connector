"""Źródło z BigQuery (widok ERP) — bez połączenia z BQ, na kliencie udającym. Dane fikcyjne."""
import json
from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("google.cloud.bigquery")

from ksef_connector.source import bigquery  # noqa: E402

JS = lambda gid, stan: json.dumps({"naglowek": {"gid_numer": gid, "stan": stan}, "vat": [{"netto": 100.10}]})


class KlientUdajacy:
    def __init__(self, wiersze):
        self.wiersze, self.zapytania = wiersze, []

    def query(self, sql, job_config=None):
        self.zapytania.append((sql, job_config))
        return self

    def result(self):
        return self.wiersze


def test_anulowana_i_wystawiona_ponownie_z_tym_samym_numerem_bierze_nowa():
    """Jak (S)FS-23/10/26/C/F: anulowana w ERP i wystawiona ponownie z tym samym numerem."""
    wybrane, bledy = bigquery.wybierz_po_numerze(
        [("(S)FS-23/10/26/C/F", 900001, 6, "stara"), ("(S)FS-23/10/26/C/F", 900002, 5, "nowa")], ["(S)FS-23/10/26/C/F"])
    assert wybrane == {"(S)FS-23/10/26/C/F": "nowa"} and not bledy


def test_tylko_anulowana_zostaje_wybrana_mapowanie_ja_wstrzyma():
    wybrane, bledy = bigquery.wybierz_po_numerze([("(S)FS-1/10/26/B", 900001, 6, "anul")], ["(S)FS-1/10/26/B"])
    assert wybrane == {"(S)FS-1/10/26/B": "anul"} and not bledy


def test_dwie_nieanulowane_o_tym_samym_numerze_to_blad():
    wybrane, bledy = bigquery.wybierz_po_numerze(
        [("(S)FS-1/10/26/B", 900001, 5, "a"), ("(S)FS-1/10/26/B", 900002, 3, "b")], ["(S)FS-1/10/26/B"])
    assert not wybrane and "wyjaśnij w ERP" in bledy[0]


def test_brak_numeru_w_bq_to_blad():
    wybrane, bledy = bigquery.wybierz_po_numerze([], ["(S)FS-9/10/26/B"])
    assert not wybrane and "brak w BigQuery" in bledy[0]


def test_pobierz_po_numerach_parsuje_kwoty_jako_decimal_i_przekazuje_parametry():
    klient = KlientUdajacy([{"numer": "(S)FS-1/10/26/B", "gid_numer": 900001, "stan": 5, "dokument_json": JS(900001, 5)}])
    dokumenty, bledy = bigquery.pobierz_po_numerach(["(S)FS-1/10/26/B "], klient)
    assert not bledy
    assert dokumenty["(S)FS-1/10/26/B "]["vat"][0]["netto"] == Decimal("100.10")
    sql, config = klient.zapytania[0]
    assert "UNNEST(@numery)" in sql and config.query_parameters[0].values == ["(S)FS-1/10/26/B"]


def test_lista():
    klient = KlientUdajacy([{"gid_typ": 2033, "gid_numer": 900001, "numer": "(S)FS-1/10/26/B", "seria": "B",
                             "data_wyst": date(2026, 10, 30), "stan": 5, "waluta": "PLN", "nip": "9999999999 ",
                             "nabywca": "FIRMA TESTOWA", "brutto_pln": 123.45, "ksef_w_xl": None}])
    [d] = bigquery.lista(date(2026, 9, 1), klient)
    assert (d.numer, d.nip, d.brutto_pln) == ("(S)FS-1/10/26/B", "9999999999", Decimal("123.45"))
