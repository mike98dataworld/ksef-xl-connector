-- Sprawdzenie widoku dbo.v_ksef_dokumenty po utworzeniu (widok_ksef_dokumenty.sql). Tylko SELECT.
-- Uruchomić w bazie firmy w XL (np. USE [ERPXL_FIRMA];).
SET NOCOUNT ON;

-- 1) Jako bq_reader (tak czyta synchronizacja ERP → BigQuery): błąd uprawnień wyjdzie tutaj, a nie dopiero w nocnym logu synchronizacji.
EXECUTE AS USER = 'bq_reader';
SELECT gid_typ, COUNT(*) AS dokumentow, MIN(data_wyst) AS od, MAX(data_wyst) AS do_,
       SUM(CASE WHEN dokument_json IS NULL THEN 1 ELSE 0 END) AS bez_json,
       MAX(LEN(dokument_json)) AS najdluzszy_json
FROM dbo.v_ksef_dokumenty
GROUP BY gid_typ
ORDER BY gid_typ;
REVERT;

-- 2) Kilka znanych dokumentów (faktura, FSL, korekta, WDT) — do porównania z eksportem ręcznym (wpisz swoje numery)
SELECT gid_typ, gid_numer, numer, data_wyst, stan, waluta, nip, nabywca, brutto_pln, ksef_w_xl,
       LEN(dokument_json) AS dlugosc_json
FROM dbo.v_ksef_dokumenty
WHERE numer IN ('(S)FS-1/10/26/B', 'FSL-1/10/26/Z', '(S)FSK-1/10/26/KB', '(S)FSE-1/10/26/B')
ORDER BY gid_typ, numer;
