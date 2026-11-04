"""Panel BOK: lista, podgląd, dane z KSeF dla korekt i rozliczających — na danych fikcyjnych, bez BQ i KSeF."""
import copy
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ksef_connector import panel
from ksef_connector.mapping.xl import BladMapowania
from ksef_connector.source.bigquery import Dokument
from ksef_connector.source.fixtures import FixtureSource
from test_korekty import NR_KSEF_ORG, korekta_kb
from test_zaliczki import NR_KSEF_FSL, ROZ

FIXTURES = FixtureSource(Path(__file__).parent / "fixtures")


def dok(gid, numer, typ=2033, stan=3, nip="9999999999", ksef_w_xl=None):
    return Dokument(gid_typ=typ, gid_numer=gid, numer=numer, seria="B", data_wyst=date(2026, 10, 30), stan=stan,
                    waluta="PLN", nip=nip, nabywca="FIRMA TESTOWA", brutto_pln=Decimal("100.00"), ksef_w_xl=ksef_w_xl)


def wys(numer, gid=None, numer_ksef="KSEF-X", status_kod=200, kto="laptop:test"):
    return panel.Wysylka(id=f"u-{numer}", xl_gid_typ=2033 if gid else None, xl_gid_numer=gid, numer_faktury=numer,
                         numer_ksef=numer_ksef, status_kod=status_kod, status_opis=None, kto=kto, wyslano=None)


def fs(gid, numer):
    raw = copy.deepcopy(FIXTURES.pobierz("fs_netto_firma_dwie_stawki"))
    raw["naglowek"].update(gid_numer=gid, numer=numer)
    return raw


def test_lista_statusy():
    dokumenty = [
        dok(1, "(S)FS-1/10/26/B"),                                   # do wysłania (mapuje się)
        dok(2, "(S)FS-2/10/26/B"),                                   # wysłana przez konektor
        dok(3, "(S)FS-3/10/26/B", ksef_w_xl="KSEF-XL"),               # wysłana przez XL
        dok(4, "(S)FS-4/10/26/C", nip=None),                         # osoba prywatna
        dok(5, "(S)FS-23/10/26/C/F", stan=6),                         # anulowana (stary GID)
        dok(6, "(S)FS-23/10/26/C/F"),                                # wystawiona ponownie, nowy GID, ten sam numer
        dok(7, "(S)FS-7/10/26/B"),                                   # wysłana, status nieznany
        dok(8, "(S)FSK-1/10/26/KB", typ=2041),                       # korekta: dane z KSeF dopiero w podglądzie
    ]
    wysylki = [wys("(S)FS-2/10/26/B", 2), wys("(S)FS-23/10/26/C/F", 5, numer_ksef="KSEF-23"),
               wys("(S)FS-7/10/26/B", 7, numer_ksef=None, status_kod=None)]
    pobrane = []

    def tresci(kandydaci):
        pobrane.extend(d.gid_numer for d in kandydaci)
        return {1: fs(1, "(S)FS-1/10/26/B"), 8: korekta_kb()}

    p = {(x.gid_numer): x for x in panel.lista(dokumenty, wysylki, tresci)}
    assert sorted(pobrane) == [1, 8]  # treść tylko dla kandydatów
    assert (p[1].status, p[1].rodzaj, p[1].powod) == ("do_wyslania", "VAT", None)
    assert (p[2].status, p[2].numer_ksef, p[2].kto) == ("w_ksef", "KSEF-X", "laptop:test")
    assert (p[3].status, p[3].kto) == ("w_ksef", "XL")
    assert p[4].status == "pominieta" and "bez NIP" in p[4].powod
    assert p[5].status == "pominieta" and p[5].powod == "anulowana w ERP"
    assert (p[6].status, p[6].numer_ksef) == ("w_ksef", "KSEF-23")
    assert p[7].status == "pominieta" and "sprawdź ręcznie" in p[7].powod
    assert (p[8].status, p[8].rodzaj) == ("do_wyslania", "KOR") and "podglądzie" in p[8].powod


def test_lista_blad_mapowania_trafia_do_pominietych_z_powodem():
    raw = fs(1, "(S)FS-1/10/26/B")
    raw["naglowek"]["waluta"] = "XYZ"
    [p] = panel.lista([dok(1, "(S)FS-1/10/26/B")], [], lambda _: {1: raw})
    assert p.status == "pominieta" and p.powod


class KsefUdajacy(panel.ZrodloKsef):
    def __init__(self, w_ksef: dict[str, str], xml: dict[str, bytes]):
        self.szukane, self.pobrane = [], []

        def szukaj(numer, data):
            self.szukane.append(numer)
            return [w_ksef[numer]] if numer in w_ksef else []

        def pobierz(nr):
            self.pobrane.append(nr)
            return xml[nr]

        super().__init__(szukaj=szukaj, xml=pobierz)


def test_korekta_bierze_numer_oryginalu_z_rejestru_bez_pytania_ksef():
    raw = korekta_kb()
    org = raw.pop("oryginal_ksef")
    ksef = KsefUdajacy({}, {NR_KSEF_ORG: org["xml"].encode()})
    wynik = panel.podglad(raw, {"(S)FS-46/10/26/B": NR_KSEF_ORG}, ksef)
    assert wynik["ok"], wynik["bledy"]
    assert wynik["faktura"]["rodzaj"] == "KOR" and NR_KSEF_ORG in wynik["xml"]
    assert ksef.szukane == [] and ksef.pobrane == [NR_KSEF_ORG]
    panel.podglad(raw, {"(S)FS-46/10/26/B": NR_KSEF_ORG}, ksef)
    assert ksef.pobrane == [NR_KSEF_ORG]  # XML z pamięci (limit 64/h)


def test_korekta_bez_oryginalu_w_ksef_wstrzymana():
    raw = korekta_kb()
    raw.pop("oryginal_ksef")
    wynik = panel.podglad(raw, {}, KsefUdajacy({}, {}))
    assert not wynik["ok"] and "najpierw ją wyślij" in wynik["bledy"][0]


def test_rozliczajaca_szuka_starej_zaliczki_w_ksef():
    """FSL z kwietnia nie ma w widoku (od 1.09) ani w rejestrze → jedno zapytanie do KSeF."""
    raw = copy.deepcopy(ROZ)
    raw.pop("zaliczki_ksef")
    ksef = KsefUdajacy({"FSL-1/04/26/Z": NR_KSEF_FSL}, {})
    wynik = panel.podglad(raw, {}, ksef)
    assert wynik["ok"], wynik["bledy"]
    assert wynik["faktura"]["rodzaj"] == "ROZ" and ksef.szukane == ["FSL-1/04/26/Z"]


def test_dwie_faktury_o_tym_numerze_w_ksef_to_blad():
    ksef = panel.ZrodloKsef(szukaj=lambda n, d: ["A", "B"], xml=lambda n: b"")
    with pytest.raises(BladMapowania, match="2×"):
        panel.uzupelnij_z_ksef({**copy.deepcopy(ROZ), "zaliczki_ksef": None}, {}, ksef)


def test_znane_numery_z_erp_i_rejestru_bez_anulowanych():
    znane = panel.znane_numery_ksef(
        [dok(1, "A", ksef_w_xl="K1"), dok(2, "B", stan=6, ksef_w_xl="K2")], [wys("C", numer_ksef="K3"), wys("D", numer_ksef=None)])
    assert znane == {"A": "K1", "C": "K3"}


@pytest.mark.parametrize("techniczny, po_ludzku", [
    ("osoba prywatna bez NIP — nie wysyłamy do KSeF", "Osoba prywatna bez NIP"),
    ("FSE (2037) z TrN_ExpoNorm=7 — tylko WDT (6); sprzedaży …", "(OSS)"),
    ("zablokowana w rejestrze: wpis #u1 bez statusu końcowego (0, uzgodnienie: KONFLIKT brutto XL …)", "inną fakturę"),
    ("zablokowana w rejestrze: wpis #u1 bez statusu końcowego (None) — sprawdź ręcznie w KSeF", "bez potwierdzenia"),
    ("Stan 1 — wysyłamy tylko stany [3, 4, 5]", "bufor"),
    ("faktury korygowanej (S)FS-1/10/26/B nie ma w KSeF — najpierw ją wyślij", "najpierw ją wyślij"),
    ("Ujemny wiersz TraVat bez oznaczenia zaliczki — nieznany przypadek", "Nietypowy dokument"),
])
def test_powody_po_ludzku_bez_nazw_z_erp(techniczny, po_ludzku):
    tekst = panel.dla_bok(techniczny)
    assert po_ludzku in tekst
    assert not re.search(r"Tr[NVE]_|TraVat|ExpoNorm|#u1", tekst)


def test_lista_podaje_powod_po_ludzku():
    [p] = panel.lista([dok(4, "(S)FS-4/10/26/C", nip=None)], [], lambda _: {})
    assert p.powod_bok == "Osoba prywatna bez NIP — do KSeF nie wysyłamy"


def test_do_wyjasnienia_tylko_nietypowe_pominiete():
    pozycje = panel.lista([dok(1, "(S)FS-1/10/26/C", nip=None), dok(2, "(S)FS-2/10/26/B", stan=6),
                           dok(3, "(S)FS-3/10/26/B")], [], lambda _: {})
    assert [p.do_wyjasnienia for p in pozycje] == [False, False, True]  # 3: brak treści w kopii ERP
    assert not panel.do_wyjasnienia("FSE (2037) z TrN_ExpoNorm=7 — tylko WDT (6)")
    assert panel.do_wyjasnienia("zablokowana w rejestrze: wpis #u1 bez statusu końcowego (0, uzgodnienie: KONFLIKT)")
