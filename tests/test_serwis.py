"""Usługa HTTP panelu: bez BQ i KSeF (podmienione funkcje). Dane fikcyjne."""
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from ksef_connector import serwis  # noqa: E402
from test_panel import dok, fs, wys  # noqa: E402


@pytest.fixture
def klient(monkeypatch):
    monkeypatch.setenv("KSEF_SRODOWISKO", "prod")
    monkeypatch.setattr(serwis.bigquery, "lista", lambda od: [dok(1, "(S)FS-1/10/26/B"), dok(2, "(S)FS-2/10/26/B")])
    monkeypatch.setattr(serwis.bigquery, "tresci_po_gid", lambda d: {1: fs(1, "(S)FS-1/10/26/B")})
    monkeypatch.setattr(serwis.rejestr_bq, "wysylki", lambda s: [wys("(S)FS-2/10/26/B", 2)])
    return TestClient(serwis.app)


def test_bez_srodowiska_blad(klient, monkeypatch):
    monkeypatch.delenv("KSEF_SRODOWISKO")
    assert klient.get("/dokumenty").status_code == 500


def test_dokumenty(klient):
    odp = klient.get("/dokumenty").json()
    assert odp["liczby"] == {"do_wyslania": 1, "w_ksef": 1}
    assert [(d["numer"], d["status"]) for d in odp["dokumenty"]] == [("(S)FS-1/10/26/B", "do_wyslania"),
                                                                   ("(S)FS-2/10/26/B", "w_ksef")]


def test_podglad_bez_logowania_do_ksef_dla_zwyklej_faktury(klient, monkeypatch):
    monkeypatch.setattr(serwis.bigquery, "pobierz_po_numerach", lambda n: ({n[0]: fs(1, n[0])}, []))
    odp = klient.get("/podglad", params={"numer": "(S)FS-1/10/26/B"}).json()
    assert odp["ok"] and odp["faktura"]["numer"] == "(S)FS-1/10/26/B" and "<Faktura" in odp["xml"]


def test_podglad_nieznany_numer_404(klient, monkeypatch):
    monkeypatch.setattr(serwis.bigquery, "pobierz_po_numerach", lambda n: ({}, [f"{n[0]}: brak w BigQuery"]))
    assert klient.get("/podglad", params={"numer": "X"}).status_code == 404


def test_wyslij_przekazuje_potwierdzone_numery_i_kto(klient, monkeypatch):
    wywolanie = {}

    def wyslij(pliki, wczytaj, *, srodowisko, rejestr, out, wypisz, potwierdzone):
        wywolanie.update(pliki=pliki, srodowisko=srodowisko, kto=rejestr.kto, potwierdzone=potwierdzone,
                         raw=wczytaj(pliki[0]))
        wypisz("OK         (S)FS-1/10/26/B -> KSEF-1")
        return 0

    monkeypatch.setattr(serwis.bigquery, "pobierz_po_numerach",
                        lambda n: ({x: fs(1, x) for x in n if x != "BRAK"}, ["BRAK: brak w BigQuery"]))
    monkeypatch.setattr(serwis.wysylka, "wyslij", wyslij)
    monkeypatch.setattr(serwis.rejestr_bq, "RejestrBQ", lambda kto: type("R", (), {"kto": kto})())
    odp = klient.post("/wyslij", json={"numery": ["(S)FS-1/10/26/B ", "BRAK"], "kto": "jan.kowalski"}).json()
    assert wywolanie["pliki"] == ["(S)FS-1/10/26/B"] and wywolanie["potwierdzone"] == {"(S)FS-1/10/26/B"}
    assert (wywolanie["srodowisko"], wywolanie["kto"]) == ("prod", "jan.kowalski")
    assert wywolanie["raw"]["naglowek"]["numer"] == "(S)FS-1/10/26/B"
    assert "POMINIĘTA  BRAK: brak w BigQuery" in odp["komunikaty"]


def test_druga_wysylka_naraz_odrzucona(klient):
    assert serwis._wysylka_trwa.acquire(blocking=False)
    try:
        assert klient.post("/wyslij", json={"numery": ["A"], "kto": "jan.kowalski"}).status_code == 409
    finally:
        serwis._wysylka_trwa.release()


def test_bez_kto_odrzucona(klient):
    assert klient.post("/wyslij", json={"numery": ["A"], "kto": ""}).status_code == 422


def test_blad_konfiguracji_to_500_a_nie_koniec_procesu(klient, monkeypatch):
    def brak_tokenu(*a, **k):
        raise SystemExit("Brak KSEF_NIP lub KSEF_TOKEN_PROD")
    monkeypatch.setattr(serwis.bigquery, "pobierz_po_numerach", lambda n: ({n[0]: fs(1, n[0])}, []))
    monkeypatch.setattr(serwis.wysylka, "wyslij", brak_tokenu)
    monkeypatch.setattr(serwis.rejestr_bq, "RejestrBQ", lambda kto: None)
    odp = klient.post("/wyslij", json={"numery": ["(S)FS-1/10/26/B"], "kto": "jan.kowalski"})
    assert odp.status_code == 500 and "KSEF_TOKEN_PROD" in odp.json()["detail"]
    assert serwis._wysylka_trwa.acquire(blocking=False)  # blokada zwolniona
    serwis._wysylka_trwa.release()
