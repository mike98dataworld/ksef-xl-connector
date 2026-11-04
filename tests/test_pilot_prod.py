"""Zabezpieczenia pilota na PROD — bez połączenia z KSeF."""
from datetime import date
from pathlib import Path

import pytest

from ksef_connector.log.rejestr import Rejestr
from ksef_connector.pipeline import przygotuj
from ksef_connector.source.fixtures import FixtureSource, wczytaj
from ksef_connector.wysylka import MAKS_PARTIA, Pozycja, _bez_juz_wyslanych, wyslij

FIXTURES = FixtureSource(Path(__file__).parent / "fixtures")


@pytest.fixture
def rejestr(tmp_path):
    r = Rejestr(tmp_path / "r.sqlite")
    yield r
    r.close()


def test_prod_wymaga_potwierdzonych_numerow(rejestr, tmp_path):
    with pytest.raises(SystemExit, match="potwierdzam"):
        wyslij([tmp_path / "x.json"], wczytaj, srodowisko="prod", rejestr=rejestr, out=tmp_path)


def test_prod_limit_pilota(rejestr, tmp_path):
    numery = {f"(S)FS-{i}/10/26/B" for i in range(MAKS_PARTIA + 1)}
    with pytest.raises(SystemExit, match="najwyżej"):
        wyslij([tmp_path / "x.json"], wczytaj, srodowisko="prod", rejestr=rejestr, out=tmp_path, potwierdzone=numery)


@pytest.mark.parametrize("plik", ["tests/fixtures/fs_brutto_firma_cf.json", "fixtures_anon/anon_x.json"])
def test_prod_odrzuca_dane_fikcyjne(rejestr, tmp_path, plik):
    with pytest.raises(SystemExit, match="nie mogą trafić na PROD"):
        wyslij([Path(plik)], wczytaj, srodowisko="prod", rejestr=rejestr, out=tmp_path,
               potwierdzone={"(S)FS-3/10/26/C/F"})


class _KlientKsef:
    """Udaje listę faktur w KSeF dla zakresu dat; liczy zapytania (limit API: 20/h)."""

    def __init__(self, istniejace: set[str]):
        self.istniejace = istniejace
        self.zapytania = 0

    def metadane_faktur(self, access, filtry, offset=0, rozmiar=250):
        self.zapytania += 1
        return {"invoices": [{"ksefNumber": "1111111111-20261020-AAA", "invoiceNumber": numer,
                              "issueDate": "2026-10-20", "buyer": {"identifier": {"type": "None"}},
                              "grossAmount": 1.0, "currency": "PLN", "invoiceType": "Vat",
                              "invoicingDate": "2026-10-20T10:00:00+00:00"} for numer in sorted(self.istniejace)],
                "hasMore": False, "isTruncated": False}


def test_kontrola_przed_wysylka_pomija_faktury_juz_w_ksef(rejestr):
    a = przygotuj(FIXTURES.pobierz("fs_netto_firma_dwie_stawki"))   # (S)FS-1/10/26/B — „już w KSeF”
    b = przygotuj(FIXTURES.pobierz("fs_brutto_firma_cf"))           # (S)FS-3/10/26/C/F — brak w KSeF
    klient = _KlientKsef({a.faktura.numer})
    tokeny = type("T", (), {"access": "x"})()
    komunikaty = []
    zostaja = _bez_juz_wyslanych(klient, tokeny, [Pozycja(Path("a"), a), Pozycja(Path("b"), b)], "prod", rejestr,
                                 komunikaty.append)
    assert [p.wynik.faktura.numer for p in zostaja] == [b.faktura.numer]
    assert klient.zapytania == 1  # jedna kontrola dla całej partii, nie po jednej na fakturę
    assert "1111111111-20261020-AAA" in rejestr.blokada("prod", a.faktura.xl_gid_typ, a.faktura.xl_gid_numer)
    assert any("JUŻ W KSeF" in k for k in komunikaty)


def test_wysylka_pomija_osoby_prywatne(rejestr, tmp_path, monkeypatch):
    import ksef_connector.wysylka as w
    from ksef_connector.mapping.model import Adres, Sprzedawca
    monkeypatch.setattr(w, "konto", lambda klient, wypisz=print: w.Konto(
        "3691025032", "x", Sprzedawca("3691025032", "Test", Adres("PL", "ul. Testowa 1"))))
    komunikaty = []
    wynik = wyslij([Path("tests/fixtures/fs_brutto_konsument_roznica_grosza.json")], wczytaj, srodowisko="test",
                   rejestr=rejestr, out=tmp_path, wypisz=komunikaty.append)
    assert wynik == 0
    assert any("osoba prywatna bez NIP" in k for k in komunikaty)
    assert "Brak faktur do wysłania." in komunikaty


def test_rozdziel_eksport_z_ssms(tmp_path):
    import json, subprocess, sys
    raw = json.dumps(FIXTURES.pobierz("fs_netto_wzor_xl"), default=str)
    plik = tmp_path / "eksport.tsv"
    plik.write_text(f"gid_numer\tfaktura_json\n900005\t{raw}\n\n", encoding="utf-8")
    wynik = subprocess.run([sys.executable, "-m", "ksef_connector", "rozdziel", str(plik), "--out", str(tmp_path / "p")],
                           capture_output=True, text=True)
    assert wynik.returncode == 0, wynik.stderr
    assert [f.name for f in (tmp_path / "p").iterdir()] == ["fs_FS_46_10_26_B.json"]
