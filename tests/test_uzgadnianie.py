"""Uzgadnianie XL ↔ KSeF i pobieranie metadanych — bez połączenia z KSeF."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from ksef_connector import uzgadnianie as u
from ksef_connector.ksef.pobieranie import FakturaKsef, faktury_sprzedazy
from ksef_connector.log.rejestr import Rejestr

D = date(2026, 9, 15)


def xl(numer, brutto="123.00", stan=5, nip="1234563218", gid=1, ksef=None, waluta="PLN"):
    return u.DokumentXl(2033, gid, numer, D, stan, Decimal(brutto), waluta, nip, ksef)


def ks(numer, brutto="123.00", nip="1234563218", nr="KSEF-1", waluta="PLN"):
    return FakturaKsef(nr, numer, D, nip, Decimal(brutto), waluta, "Vat", datetime(2026, 9, 15, tzinfo=timezone.utc))


def kategorie(pozycje):
    return {((p.xl or p.ksef).numer, p.kategoria) for p in pozycje}


def test_zgodna_faktura_jest_w_ksef():
    assert kategorie(u.uzgodnij([xl("(S)FS-1/09/26/B")], [ks("(S)FS-1/09/26/B")])) == {("(S)FS-1/09/26/B", u.W_KSEF)}


def test_brak_w_ksef_do_wyslania():
    assert kategorie(u.uzgodnij([xl("(S)FS-2/09/26/B")], [])) == {("(S)FS-2/09/26/B", u.DO_WYSLANIA)}


def test_ten_sam_numer_inna_faktura_to_konflikt():
    """Po odtworzeniu bazy z kopii ten sam numer FS-64 trafił do KSeF i do XL jako dwie różne faktury."""
    p = u.uzgodnij([xl("(S)FS-64/09/26/B", brutto="500.00", nip="1234563218")],
                   [ks("(S)FS-64/09/26/B", brutto="900.95", nip="9876543210")])
    assert [x.kategoria for x in p] == [u.KONFLIKT]
    assert "brutto" in p[0].uwagi and "NIP" in p[0].uwagi


def test_tylko_w_ksef():
    assert kategorie(u.uzgodnij([], [ks("(S)FS-65/09/26/B")])) == {("(S)FS-65/09/26/B", u.TYLKO_W_KSEF)}


def test_anulowana_i_ponownie_wystawiona():
    dok = [xl("(S)FS-260/10/26/C", stan=6, gid=1), xl("(S)FS-260/10/26/C", stan=5, gid=2)]
    p = u.uzgodnij(dok, [])
    assert {(x.xl.gid_numer, x.kategoria) for x in p} == {(1, u.POMINIETA), (2, u.DO_WYSLANIA)}


def test_anulowana_w_xl_ale_w_ksef_to_konflikt():
    p = u.uzgodnij([xl("(S)FS-154/10/26/C", stan=6)], [ks("(S)FS-154/10/26/C")])
    assert [x.kategoria for x in p] == [u.KONFLIKT]


def test_xl_zna_numer_ksef_ktorego_nie_ma_w_ksef():
    p = u.uzgodnij([xl("(S)FS-63/09/26/B", ksef="KSEF-X")], [])
    assert [x.kategoria for x in p] == [u.KONFLIKT]


def test_dwie_faktury_w_ksef_o_tym_samym_numerze():
    p = u.uzgodnij([xl("(S)FS-7/09/26/B")], [ks("(S)FS-7/09/26/B", nr="A"), ks("(S)FS-7/09/26/B", nr="B")])
    assert [x.kategoria for x in p] == [u.KONFLIKT]


def test_waluta_obca_kwoty_nieporownywane():
    p = u.uzgodnij([xl("(S)FSE-1/09/26/B/HU", brutto="5000.00", nip=None, waluta="HUF")],
                   [ks("(S)FSE-1/09/26/B/HU", brutto="400000.00", nip=None, waluta="HUF")])
    assert [x.kategoria for x in p] == [u.W_KSEF]


def test_wczytanie_tsv_z_ssms(tmp_path):
    plik = tmp_path / "lista.tsv"
    plik.write_text(
        "gid_typ\tgid_numer\tnumer\tdata_wyst\tstan\tbrutto\twaluta\tnip_prefiks\tnip_nabywcy\tnumer_ksef_xl\n"
        "2033\t900005\t(S)FS-46/10/26/B\t2026-10-16\t4\t900.95\tPLN\tPL\t987-654-32-10\t1111111111-20261020-0A1B2C3D4E5F-B4\n"
        "2033\t900006\t(S)FS-378/10/26/C\t2026-10-27\t5\t1518.10\tPLN\t\tNULL\tNULL\n"
        "2037\t900007\t(S)FSE-1/10/26/B/HU\t2026-10-23\t5\t4000.00\tHUF\tHU\t12345678\tNULL\n",
        encoding="utf-8-sig")
    a, b, c = u.wczytaj_liste_xl(plik)
    assert a.nip_nabywcy == "9876543210" and a.numer_ksef_xl.endswith("-B4") and a.brutto == Decimal("900.95")
    assert b.nip_nabywcy is None and b.numer_ksef_xl is None
    assert c.nip_nabywcy is None and c.waluta == "HUF"  # zagraniczny NIP nie jest porównywany z NIP-em PL


def test_zapis_uzgodnienia_blokuje_wysylke(tmp_path):
    r = Rejestr(tmp_path / "r.sqlite")
    assert r.zapisz_z_uzgodnienia("test", 2033, 1, "(S)FS-1", "KSEF-1", None)
    assert "KSEF-1" in r.blokada("test", 2033, 1)
    assert r.zapisz_z_uzgodnienia("test", 2033, 1, "(S)FS-1", "KSEF-1", None) is None  # idempotentnie
    assert r.zapisz_z_uzgodnienia("test", 2033, 2, "(S)FS-2", None, "brutto XL 1 ≠ KSeF 2")
    assert "KONFLIKT" in r.blokada("test", 2033, 2)
    r.close()


class _KlientStronicowany:
    """Udaje /invoices/query/metadata: 3 faktury, strony po 2, druga strona ucięta (isTruncated)."""

    def __init__(self):
        self.zapytania = []

    def metadane_faktur(self, access, filtry, offset=0, rozmiar=250):
        self.zapytania.append((filtry["dateRange"]["from"], offset))
        m = lambda n, d: {"ksefNumber": f"K{n}", "invoiceNumber": f"FS-{n}", "issueDate": d,
                          "buyer": {"identifier": {"type": "None"}}, "grossAmount": 10.0, "currency": "PLN",
                          "invoiceType": "Vat", "invoicingDate": f"{d}T10:00:00+00:00"}
        if len(self.zapytania) == 1:
            return {"invoices": [m(1, "2026-09-10"), m(2, "2026-09-11")], "hasMore": True, "isTruncated": True}
        return {"invoices": [m(2, "2026-09-11"), m(3, "2026-09-12")], "hasMore": False, "isTruncated": False}


def test_pobieranie_obsluguje_isTruncated_i_usuwa_duplikaty():
    klient = _KlientStronicowany()
    tokeny = type("T", (), {"access": "x"})()
    faktury = faktury_sprzedazy(klient, tokeny, date(2026, 9, 9), date(2026, 9, 30))
    assert [f.numer_ksef for f in faktury] == ["K1", "K2", "K3"]
    assert klient.zapytania[1][0].startswith("2026-09-11") and klient.zapytania[1][1] == 0


class _KlientDwieStrony:
    """Dwie pełne strony bez ucięcia: druga strona to pageOffset=1 (numer strony), nie 250."""

    def __init__(self):
        self.offsety = []

    def metadane_faktur(self, access, filtry, offset=0, rozmiar=250):
        self.offsety.append(offset)
        m = lambda n: {"ksefNumber": f"K{n}", "invoiceNumber": f"FS-{n}", "issueDate": "2026-09-10",
                       "buyer": {"identifier": {"type": "None"}}, "grossAmount": 1.0, "currency": "PLN",
                       "invoiceType": "Vat", "invoicingDate": "2026-09-10T10:00:00+00:00"}
        if offset == 0:
            return {"invoices": [m(1)], "hasMore": True, "isTruncated": False}
        return {"invoices": [m(2)], "hasMore": False, "isTruncated": False}


def test_pageoffset_to_numer_strony():
    klient = _KlientDwieStrony()
    faktury = faktury_sprzedazy(klient, type("T", (), {"access": "x"})(), date(2026, 9, 9), date(2026, 9, 30))
    assert klient.offsety == [0, 1] and len(faktury) == 2


# --- osoby prywatne bez NIP (seria C) ---

def test_osoba_prywatna_bez_nip_nie_jest_do_wyslania():
    dok = u.DokumentXl(2033, 9, "(S)FS-378/10/26/C", D, 5, Decimal("1518.10"), "PLN", None, None, bez_nip=True)
    assert [p.kategoria for p in u.uzgodnij([dok], [])] == [u.POMINIETA]


def test_osoba_prywatna_ktora_jednak_jest_w_ksef_to_w_ksef():
    dok = u.DokumentXl(2033, 9, "(S)FS-192/09/26/C", D, 5, Decimal("123.00"), "PLN", None, None, bez_nip=True)
    assert [p.kategoria for p in u.uzgodnij([dok], [ks("(S)FS-192/09/26/C", nip=None)])] == [u.W_KSEF]


def test_wczytanie_rozpoznaje_brak_nip(tmp_path):
    plik = tmp_path / "l.tsv"
    plik.write_text(
        "gid_typ\tgid_numer\tnumer\tdata_wyst\tstan\tbrutto\twaluta\tnip_prefiks\tnip_nabywcy\tnumer_ksef_xl\n"
        "2033\t1\t(S)FS-1/10/26/C\t2026-10-01\t5\t10.00\tPLN\t\tNULL\tNULL\n"
        "2037\t2\t(S)FSE-1/10/26/B/HU\t2026-10-01\t5\t10.00\tHUF\tHU\t12345678\tNULL\n", encoding="utf-8")
    konsument, zagraniczny = u.wczytaj_liste_xl(plik)
    assert konsument.bez_nip is True
    assert zagraniczny.bez_nip is False and zagraniczny.nip_nabywcy is None


def test_lista_z_bigquery():
    from ksef_connector.source.bigquery import Dokument
    d = Dokument(gid_typ=2033, gid_numer=1, numer="(S)FS-1/10/26/C", seria="C", data_wyst=D, stan=3, waluta="PLN",
                 nip=None, nabywca="X", brutto_pln=Decimal("10.5"), ksef_w_xl=None)
    [x] = u.z_bigquery([d, ])
    assert (x.brutto, x.bez_nip, x.nip_nabywcy) == (Decimal("10.50"), True, None)
    [y] = u.z_bigquery([Dokument(**{**d.__dict__, "nip": "123-456-32-18", "ksef_w_xl": "K"})])
    assert (y.nip_nabywcy, y.bez_nip, y.numer_ksef_xl) == ("1234563218", False, "K")
