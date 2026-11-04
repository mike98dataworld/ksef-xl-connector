"""Rejestr wysyłek w BigQuery — bez połączenia z BQ, na kliencie udającym. Dane fikcyjne."""
import base64
import json

import pytest

pytest.importorskip("google.cloud.bigquery")

from ksef_connector.log import rejestr_bq  # noqa: E402


class KlientUdajacy:
    def __init__(self, wyniki=(), bledy_insertu=()):
        self.wyniki, self.bledy_insertu = list(wyniki), list(bledy_insertu)
        self.wstawione, self.zapytania, self.zaladowane = [], [], []

    def insert_rows_json(self, tabela, wiersze):
        self.wstawione += [(tabela, w) for w in wiersze]
        return self.bledy_insertu

    def query(self, sql, job_config=None):
        self.zapytania.append((sql, job_config))
        return self

    def result(self):
        return self.wyniki.pop(0) if self.wyniki else []

    def get_table(self, tabela):
        from google.cloud.bigquery import SchemaField

        class T:
            schema = [SchemaField("id_wysylki", "STRING")]
        return T

    def load_table_from_json(self, wiersze, tabela, job_config=None):
        self.zaladowane.append((tabela, list(wiersze), job_config))
        return self


def test_wysylka_to_zdarzenia_tylko_dopisywane_z_tym_samym_id():
    k = KlientUdajacy()
    r = rejestr_bq.RejestrBQ("panel:test", k)
    i = r.zapisz_wyslanie("prod", 2033, 900001, "(S)FS-1/10/26/B", "hash", "SESJA")
    r.zapisz_ref(i, "REF-1")
    r.zapisz_status(i, 200, "Sukces", ["ok"], "KSEF-1")
    r.zapisz_upo(i, b"<UPO/>")
    zdarzenia = [w for _, w in k.wstawione]
    assert {t for t, _ in k.wstawione} == {rejestr_bq.ZDARZENIA}
    assert [z["zdarzenie"] for z in zdarzenia] == ["wyslano", "ref", "status", "upo"]
    assert {z["id_wysylki"] for z in zdarzenia} == {i} and {z["srodowisko"] for z in zdarzenia} == {"prod"}
    assert zdarzenia[0]["kto"] == "panel:test" and zdarzenia[0]["numer_faktury"] == "(S)FS-1/10/26/B"
    assert json.loads(zdarzenia[2]["szczegoly"]) == ["ok"] and zdarzenia[2]["numer_ksef"] == "KSEF-1"
    assert base64.b64decode(zdarzenia[3]["upo"]) == b"<UPO/>"


def test_nieudany_zapis_przed_wysylka_przerywa():
    r = rejestr_bq.RejestrBQ("laptop:test", KlientUdajacy(bledy_insertu=[{"index": 0, "errors": ["x"]}]))
    with pytest.raises(RuntimeError, match="nie zapisano"):
        r.zapisz_wyslanie("prod", 2033, 900001, "(S)FS-1/10/26/B", "hash", "SESJA")


def test_blokada_po_gid_albo_numerze_ta_sama_logika_co_sqlite():
    k = KlientUdajacy(wyniki=[[{"id": "u1", "numer_ksef": "KSEF-23", "status_kod": 200, "status_opis": "Sukces"}]])
    r = rejestr_bq.RejestrBQ("laptop:test", k)
    assert "KSEF-23" in r.blokada("prod", 2033, 188681, "(S)FS-23/10/26/C/F")
    sql, config = k.zapytania[0]
    assert "OR numer_faktury = @numer" in sql
    assert {p.name: p.value for p in config.query_parameters}["numer"] == "(S)FS-23/10/26/C/F"


def test_uzgodnienie_nie_dopisuje_gdy_juz_zablokowana():
    k = KlientUdajacy(wyniki=[[{"id": "u1", "numer_ksef": None, "status_kod": None, "status_opis": None}]])
    assert rejestr_bq.RejestrBQ("laptop:test", k).zapisz_z_uzgodnienia("prod", 2033, 1, "X", "KSEF", None) is None
    assert not k.wstawione


SQLITE = {"id": 7, "srodowisko": "prod", "xl_gid_typ": 2033, "xl_gid_numer": 900001, "numer_faktury": "(S)FS-1/10/26/B",
          "hash_sha256": "hash", "sesja_ref": "SESJA", "faktura_ref": "REF", "wyslano": "2026-10-30T10:00:00+00:00",
          "status_kod": 200, "status_opis": "Sukces", "szczegoly": None, "numer_ksef": "KSEF-1", "upo": b"<UPO/>",
          "aktualizacja": "2026-10-30T10:01:00+00:00"}


def test_przeniesienie_z_sqlite_pomija_juz_przeniesione():
    k = KlientUdajacy(wyniki=[[{"id_wysylki": "sqlite-7"}]])
    assert rejestr_bq.przenies([SQLITE, {**SQLITE, "id": 8, "faktura_ref": None, "upo": None}], k) == (1, 1)
    [(tabela, zdarzenia, config)] = k.zaladowane
    assert tabela == rejestr_bq.ZDARZENIA and [f.name for f in config.schema] == ["id_wysylki"]
    assert [(z["id_wysylki"], z["zdarzenie"]) for z in zdarzenia] == [("sqlite-8", "wyslano"), ("sqlite-8", "status")]
    assert zdarzenia[1]["czas"] == SQLITE["aktualizacja"] and zdarzenia[1]["numer_ksef"] == "KSEF-1"


def test_zdarzenia_z_sqlite_z_upo():
    zdarzenia = rejestr_bq.zdarzenia_z_sqlite(SQLITE)
    assert [z["zdarzenie"] for z in zdarzenia] == ["wyslano", "ref", "status", "upo"]
    assert base64.b64decode(zdarzenia[3]["upo"]) == b"<UPO/>"


def test_uzgodnienia_zbiorczo_pomija_juz_w_rejestrze_i_zapisuje_konflikt_jako_blokade():
    juz = {"id_wysylki": "sqlite-1", "xl_gid_typ": 2033, "xl_gid_numer": 1, "numer_faktury": "A", "numer_ksef": "K1",
           "status_kod": 200, "status_opis": "Sukces", "kto": "laptop:x", "wyslano": None}
    k = KlientUdajacy(wyniki=[[juz]])
    r = rejestr_bq.RejestrBQ("laptop:test", k)
    assert r.zapisz_uzgodnienia("prod", [(2033, 1, "A", "K1", None), (2033, 2, "B", "K2", None),
                                         (2033, 3, "C", "K3", "inna kwota")]) == 2
    zdarzenia = [w for _, w in k.wstawione]
    assert len(k.zapytania) == 1 and {z["numer_faktury"] for z in zdarzenia if "numer_faktury" in z} == {"B", "C"}
    assert {z.get("kto") for z in zdarzenia if z["zdarzenie"] == "wyslano"} == {"uzgodnienie"}
    statusy = [z for z in zdarzenia if z["zdarzenie"] == "status"]
    assert [(s["status_kod"], s["numer_ksef"]) for s in statusy] == [(200, "K2"), (0, None)]
