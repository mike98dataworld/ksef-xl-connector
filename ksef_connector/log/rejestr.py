"""Rejestr wysyłek (SQLite). Zasada nr 4: każda wysyłka zapisana, zero „cichych” ponowień.

Faktura (środowisko + GID z XL albo numer) jest zablokowana do ponownej wysyłki, jeśli:
- ma numer KSeF (sukces albo duplikat 440 z numerem oryginału),
- została wysłana, ale nie znamy statusu końcowego (np. przerwany proces) — wymaga ręcznego sprawdzenia.
Ponowna wysyłka jest dozwolona tylko po jednoznacznym odrzuceniu (kod >= 400 inny niż 440).
Po numerze, bo anulowanie i ponowne wystawienie w ERP daje nowy GID z tym samym numerem (np. (S)FS-23/10/26/C/F).

Ta sama logika w rejestrze BigQuery (log/rejestr_bq.py) — funkcja powod_blokady.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DUPLIKAT = 440

SCHEMAT = """
CREATE TABLE IF NOT EXISTS wysylki (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    srodowisko    TEXT NOT NULL,
    xl_gid_typ    INTEGER NOT NULL,
    xl_gid_numer  INTEGER NOT NULL,
    numer_faktury TEXT NOT NULL,
    hash_sha256   TEXT NOT NULL,
    sesja_ref     TEXT,
    faktura_ref   TEXT,
    wyslano       TEXT NOT NULL,
    status_kod    INTEGER,
    status_opis   TEXT,
    szczegoly     TEXT,
    numer_ksef    TEXT,
    upo           BLOB,
    aktualizacja  TEXT
);
CREATE INDEX IF NOT EXISTS ix_wysylki_gid ON wysylki (srodowisko, xl_gid_typ, xl_gid_numer);
CREATE INDEX IF NOT EXISTS ix_wysylki_numer ON wysylki (srodowisko, numer_faktury);
"""


def powod_blokady(wpisy) -> str | None:
    """wpisy: wcześniejsze wysyłki tej faktury (od najstarszej), z kluczami id, numer_ksef, status_kod, status_opis."""
    for w in wpisy:
        if w["numer_ksef"]:
            return f"ma już numer KSeF {w['numer_ksef']} (wysyłka #{w['id']})"
        if w["status_kod"] is None or w["status_kod"] < 400:
            opis = f", {w['status_opis']}" if w["status_opis"] else ""
            return f"wpis #{w['id']} bez statusu końcowego ({w['status_kod']}{opis}) — sprawdź ręcznie w KSeF"
    return None


def _teraz() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Rejestr:
    def __init__(self, sciezka: Path | str):
        Path(sciezka).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(sciezka))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMAT)

    def close(self) -> None:
        self._db.close()

    def blokada(self, srodowisko: str, gid_typ: int, gid_numer: int, numer: str | None = None) -> str | None:
        """Powód, dla którego NIE wolno wysłać faktury, albo None."""
        return powod_blokady(self._db.execute(
            "SELECT * FROM wysylki WHERE srodowisko=? AND ((xl_gid_typ=? AND xl_gid_numer=?) OR numer_faktury=?)"
            " ORDER BY id",
            (srodowisko, gid_typ, gid_numer, numer),
        ))

    def zapisz_wyslanie(self, srodowisko: str, gid_typ: int, gid_numer: int, numer: str, hash_b64: str,
                        sesja_ref: str | None) -> int:
        """Zapis PRZED wywołaniem API — jeśli proces padnie w trakcie, rekord zablokuje ponowienie."""
        kur = self._db.execute(
            "INSERT INTO wysylki (srodowisko, xl_gid_typ, xl_gid_numer, numer_faktury, hash_sha256, sesja_ref, wyslano)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (srodowisko, gid_typ, gid_numer, numer, hash_b64, sesja_ref, _teraz()),
        )
        self._db.commit()
        return kur.lastrowid

    def zapisz_ref(self, id_: int, faktura_ref: str) -> None:
        self._db.execute("UPDATE wysylki SET faktura_ref=?, aktualizacja=? WHERE id=?", (faktura_ref, _teraz(), id_))
        self._db.commit()

    def zapisz_status(self, id_: int, kod: int, opis: str, szczegoly: list | dict | None,
                      numer_ksef: str | None) -> None:
        self._db.execute(
            "UPDATE wysylki SET status_kod=?, status_opis=?, szczegoly=?, numer_ksef=?, aktualizacja=? WHERE id=?",
            (kod, opis, json.dumps(szczegoly, ensure_ascii=False) if szczegoly else None, numer_ksef, _teraz(), id_),
        )
        self._db.commit()

    def zapisz_upo(self, id_: int, upo: bytes) -> None:
        self._db.execute("UPDATE wysylki SET upo=?, aktualizacja=? WHERE id=?", (upo, _teraz(), id_))
        self._db.commit()

    def zapisz_z_uzgodnienia(self, srodowisko: str, gid_typ: int, gid_numer: int, numer: str,
                             numer_ksef: str | None, konflikt: str | None) -> int | None:
        """Wynik uzgodnienia z KSeF: faktura już w KSeF (numer) albo konflikt (blokada do ręcznego wyjaśnienia).

        Zwraca id wpisu albo None, jeśli faktura była już zablokowana.
        """
        if self.blokada(srodowisko, gid_typ, gid_numer, numer):
            return None
        i = self.zapisz_wyslanie(srodowisko, gid_typ, gid_numer, numer, "", None)
        if konflikt:
            self.zapisz_status(i, 0, f"uzgodnienie: KONFLIKT {konflikt}", None, None)
        else:
            self.zapisz_status(i, 200, "uzgodnienie: już w KSeF", None, numer_ksef)
        return i

    def wszystko(self) -> list[dict]:
        """Pełne wiersze (z UPO) — do przeniesienia do BigQuery."""
        return [dict(w) for w in self._db.execute("SELECT * FROM wysylki ORDER BY id")]

    def wiersze(self, srodowisko: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT id, srodowisko, xl_gid_numer, numer_faktury, status_kod, status_opis, numer_ksef, wyslano FROM wysylki"
        if srodowisko:
            return list(self._db.execute(sql + " WHERE srodowisko=? ORDER BY id", (srodowisko,)))
        return list(self._db.execute(sql + " ORDER BY id"))
