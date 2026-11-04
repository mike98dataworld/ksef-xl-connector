"""Widok ERP dla panelu BOK: dbo.v_ksef_dokumenty → synchronizacja ERP → BigQuery.

Jeden wiersz = jeden dokument (FS, FSE, FSL, FSK) od DATA_OD, z kolumną `dokument_json` — tym samym JSON-em co
eksport z SSMS (faktury_wiele.sql / korekty_wiele.sql), więc mapowanie i walidacja zostają bez zmian.
Treść JSON jest składana z tych dwóch plików, żeby widok i eksport ręczny nigdy się nie rozjechały
(test: tests/test_widok.py). Po zmianie w faktury_wiele.sql / korekty_wiele.sql:

    python -m ksef_connector.source.widok > ksef_connector/source/sql/widok_ksef_dokumenty.sql

i ponowne uruchomienie tego pliku w SSMS (CREATE OR ALTER).
"""
from __future__ import annotations

from importlib.resources import files

DATA_OD = "20260901"  # panel: dokumenty od tej daty (jak panel.DATA_OD)
TYPY_FAKTUR = "2033, 2037, 1824"  # FS, FSE, FSL — jak faktury_wiele.sql
TYP_KOREKTY = 2041

# Obiekty CDN, których używa widok. Łańcuch własności dbo → CDN jest przerwany, więc bq_reader potrzebuje
# własnych uprawnień do każdego z nich (konto, którym czyta synchronizacja ERP → BigQuery).
TABELE_CDN = ("TraNag", "TraElem", "TraVat", "TraPlat", "TrNOpisy", "KntAdresy", "ZamNag", "ZamElem", "KSeFDokumenty")
FUNKCJE_CDN = ("NumerDokumentu",)


def _podzapytanie(plik: str, alias: str, powiazanie: str) -> str:
    """Wycina z eksportu SSMS podzapytanie budujące faktura_json i wiąże je z wierszem widoku (alias d)."""
    sql = files("ksef_connector.source").joinpath(f"sql/{plik}").read_text(encoding="utf-8")
    poczatek = sql.index("    faktura_json = (\n") + len("    faktura_json = ")
    koniec = sql.index("\nFROM @GIDy g")
    podzapytanie = sql[poczatek:koniec]
    assert podzapytanie.count(powiazanie) == 1, f"{plik}: nie znaleziono powiązania {powiazanie!r}"
    return podzapytanie.replace(powiazanie, f"{alias}.TrN_GIDNumer = d.TrN_GIDNumer")


def sql_widoku() -> str:
    faktura = _podzapytanie("faktury_wiele.sql", "fs", "fs.TrN_GIDNumer = g.GIDNumer")
    korekta = _podzapytanie("korekty_wiele.sql", "k", "k.TrN_GIDNumer = g.GIDNumer")
    uprawnienia = "\n".join(
        [f"GRANT SELECT ON CDN.{t} TO [bq_reader];" for t in TABELE_CDN]
        + [f"GRANT EXECUTE ON CDN.{f} TO [bq_reader];" for f in FUNKCJE_CDN])
    return f"""-- Panel BOK / KSeF: widok dla synchronizacji ERP → BigQuery. WYGENEROWANY przez ksef_connector/source/widok.py — nie edytować ręcznie.
-- Uruchomić w SSMS (całość, F5). Tworzy/zmienia tylko widok i uprawnienia odczytu, danych w XL nie zmienia.
-- Cofnięcie: DROP VIEW dbo.v_ksef_dokumenty; (uprawnienia odczytu bq_reader mogą zostać).
-- Uruchomić w bazie firmy w XL (np. USE [ERPXL_FIRMA];).

CREATE OR ALTER VIEW dbo.v_ksef_dokumenty AS
SELECT
    d.TrN_GIDTyp   AS gid_typ,
    d.TrN_GIDNumer AS gid_numer,
    CDN.NumerDokumentu(d.TrN_GIDTyp, d.TrN_SpiTyp, d.TrN_TrNTyp, d.TrN_TrNNumer,
                       d.TrN_TrNRok, d.TrN_TrNSeria, d.TrN_TrNMiesiac) AS numer,
    RTRIM(d.TrN_TrNSeria) AS seria,
    CONVERT(date, DATEADD(DAY, d.TrN_Data2, '18001228')) AS data_wyst,
    d.TrN_Stan AS stan,
    ISNULL(NULLIF(RTRIM(d.TrN_Waluta), ''), 'PLN') AS waluta,
    RTRIM(a.KnA_Nip) AS nip,
    LTRIM(RTRIM(a.KnA_Nazwa1)) AS nabywca,
    (SELECT SUM(v.TrV_NettoR + v.TrV_VatR) FROM CDN.TraVat v
     WHERE v.TrV_GIDTyp = d.TrN_GIDTyp AND v.TrV_GIDNumer = d.TrN_GIDNumer) AS brutto_pln,
    -- numer KSeF zapisany przez XL (dokumenty wysłane jeszcze przez XL); wysyłki konektora są w rejestrze panelu
    (SELECT TOP 1 NULLIF(ks.KSF_Numer, '') FROM CDN.KSeFDokumenty ks
     WHERE ks.KSF_DokTyp = d.TrN_GIDTyp AND ks.KSF_DokNumer = d.TrN_GIDNumer AND ks.KSF_TypOperacji = 0) AS ksef_w_xl,
    CASE WHEN d.TrN_GIDTyp = {TYP_KOREKTY} THEN {korekta}
         ELSE {faktura}
    END AS dokument_json
FROM CDN.TraNag d
LEFT JOIN CDN.KntAdresy a ON a.KnA_GIDNumer = d.TrN_KnANumer
WHERE d.TrN_GIDTyp IN ({TYPY_FAKTUR}, {TYP_KOREKTY})
  AND d.TrN_Data2 >= DATEDIFF(DAY, '18001228', '{DATA_OD}');
GO

-- synchronizacja zabiera każdy widok dbo z jawnym GRANT SELECT dla bq_reader
GRANT SELECT ON dbo.v_ksef_dokumenty TO [bq_reader];
{uprawnienia}
GO
"""


if __name__ == "__main__":
    print(sql_widoku(), end="")
