"""Przygotowanie do DEMO — bez połączenia z KSeF."""
import json
from pathlib import Path

import pytest

from ksef_connector import config
from ksef_connector.anonimizacja import anonimizuj
from ksef_connector.ksef.klient import KlientKsef
from ksef_connector.pipeline import przygotuj
from ksef_connector.source.fixtures import FixtureSource
from ksef_connector.wysylka import konto, sprawdz_dane_fikcyjne

FIXTURES = FixtureSource(Path(__file__).parent / "fixtures")


def test_wczytaj_env_nie_nadpisuje_istniejacych(tmp_path, monkeypatch):
    plik = tmp_path / ".env"
    plik.write_text("# komentarz\nKSEF_NIP=1111111111\nKSEF_TOKEN_DEMO='abc|def'\nISTNIEJE=z_pliku\n")
    monkeypatch.delenv("KSEF_NIP", raising=False)
    monkeypatch.delenv("KSEF_TOKEN_DEMO", raising=False)
    monkeypatch.setenv("ISTNIEJE", "z_srodowiska")
    config.wczytaj_env(plik)
    import os
    assert os.environ["KSEF_NIP"] == "1111111111"
    assert os.environ["KSEF_TOKEN_DEMO"] == "abc|def"
    assert os.environ["ISTNIEJE"] == "z_srodowiska"


def test_konto_demo_z_env(monkeypatch):
    monkeypatch.setattr(config, "KONFIGURACJA_PRZYKLADOWA", False)
    monkeypatch.setenv("KSEF_NIP", "1111111111")
    monkeypatch.setenv("KSEF_TOKEN_DEMO", "sekret")
    with KlientKsef("demo") as k:
        kt = konto(k)
    assert kt.nip == "1111111111" and kt.sprzedawca == config.SPRZEDAWCA
    assert "sekret" not in repr(kt)


def test_konto_demo_bez_tokenu(monkeypatch):
    monkeypatch.setenv("KSEF_NIP", "1111111111")
    monkeypatch.delenv("KSEF_TOKEN_DEMO", raising=False)
    with KlientKsef("demo") as k, pytest.raises(SystemExit, match="KSEF_TOKEN_DEMO"):
        konto(k)


def test_konto_demo_konfiguracja_przykladowa(monkeypatch):
    """Wzór firma.example.toml (dane fikcyjne) nie może posłużyć do wysyłki na DEMO/PROD."""
    monkeypatch.setenv("KSEF_NIP", "1111111111")
    monkeypatch.setenv("KSEF_TOKEN_DEMO", "sekret")
    with KlientKsef("demo") as k, pytest.raises(SystemExit, match="firma.toml"):
        konto(k)


def test_konto_demo_inny_nip(monkeypatch):
    monkeypatch.setattr(config, "KONFIGURACJA_PRZYKLADOWA", False)
    monkeypatch.setenv("KSEF_NIP", "1234563218")
    monkeypatch.setenv("KSEF_TOKEN_DEMO", "sekret")
    with KlientKsef("demo") as k, pytest.raises(SystemExit, match="różny"):
        konto(k)


def test_demo_tylko_dane_fikcyjne(tmp_path):
    sprawdz_dane_fikcyjne([Path("tests/fixtures/fs_brutto_firma_cf.json")])
    with pytest.raises(SystemExit, match="tylko faktury z"):
        sprawdz_dane_fikcyjne([Path("fixtures_prod/fs_FS_46_10_26_B.json")])
    with pytest.raises(SystemExit):
        sprawdz_dane_fikcyjne([tmp_path / "cokolwiek.json"])


def test_anonimizacja_zachowuje_strukture_i_usuwa_dane():
    raw = FIXTURES.pobierz("fs_netto_wzor_xl")
    raw["nabywca"]["nazwa1"] = "PRAWDZIWA FIRMA SA"
    raw["pozycje"][0]["nazwa"] = "PRAWDZIWY TOWAR"
    anon = anonimizuj(raw, 7)
    tekst = json.dumps(anon, ensure_ascii=False, default=str)
    assert "PRAWDZIWA" not in tekst and "PRAWDZIWY" not in tekst and "1234563218" not in tekst
    bez_opisu = lambda n: {k: v for k, v in n.items() if k != "opis"}
    assert bez_opisu(anon["naglowek"]) == bez_opisu(raw["naglowek"]) and anon["vat"] == raw["vat"]
    assert anon["naglowek"]["opis"] == "Opis testowy 7"  # opis bywa nazwą klienta
    assert [p["netto"] for p in anon["pozycje"]] == [p["netto"] for p in raw["pozycje"]]
    assert len(anon["nabywca"]["nip"]) == 10
    wynik = przygotuj(anon)
    assert wynik.ok, wynik.bledy


def test_anonimizacja_konsument_zostaje_konsumentem():
    anon = anonimizuj(FIXTURES.pobierz("fs_brutto_konsument_roznica_grosza"), 1)
    assert anon["nabywca"]["nip"] == ""
