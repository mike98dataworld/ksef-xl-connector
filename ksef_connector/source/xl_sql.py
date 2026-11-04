"""Źródło z bazy XL przez pyodbc. Wyłącznie odczyt (zasada nr 1).

Jeszcze nieuruchamiane na prawdziwej bazie — najpierw zweryfikować faktura.sql w SSMS.
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from importlib.resources import files
from typing import Any

SQL_FAKTURA = files("ksef_connector.source").joinpath("sql/faktura.sql").read_text(encoding="utf-8")
_DECLARE = re.compile(r"DECLARE @GIDNumer INT = \d+;")


class XlSqlSource:
    def __init__(self, dsn: str | None = None):
        import pyodbc  # opcjonalna zależność: pip install .[xl]

        dsn = dsn or os.environ["KSEF_XL_DSN"]
        if "applicationintent=readonly" not in dsn.replace(" ", "").lower():
            raise ValueError("Connection string musi zawierać ApplicationIntent=ReadOnly")
        self._conn = pyodbc.connect(dsn, readonly=True, autocommit=True)

    def pobierz(self, gid_numer: int) -> dict[str, Any]:
        sql = _DECLARE.sub("DECLARE @GIDNumer INT = ?;", SQL_FAKTURA)
        kursor = self._conn.cursor()
        kursor.execute(sql, int(gid_numer))
        tekst = "".join(w[0] or "" for w in kursor.fetchall())
        if not tekst:
            raise LookupError(f"Brak FS o GIDNumer={gid_numer}")
        return json.loads(tekst, parse_float=Decimal)
