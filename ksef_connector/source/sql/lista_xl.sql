-- Lista dokumentów sprzedaży z XL do uzgodnienia z KSeF (python -m ksef_connector uzgodnij). Tylko SELECT.
-- W SSMS: ustaw @Od, F5, kliknij w wyniki, Ctrl+A, prawy przycisk → „Copy with Headers” (Ctrl+Shift+C),
-- wklej do pliku tekstowego i zapisz jako UTF-8, np. fixtures_prod/lista_xl.tsv (poza gitem).

SET NOCOUNT ON;
DECLARE @Od DATE = '2026-09-09';

SELECT
    n.TrN_GIDTyp   AS gid_typ,
    n.TrN_GIDNumer AS gid_numer,
    CDN.NumerDokumentu(n.TrN_GIDTyp, n.TrN_SpiTyp, n.TrN_TrNTyp, n.TrN_TrNNumer,
                       n.TrN_TrNRok, n.TrN_TrNSeria, n.TrN_TrNMiesiac) AS numer,
    CONVERT(CHAR(10), DATEADD(DAY, n.TrN_Data2, '1800-12-28'), 23) AS data_wyst,
    n.TrN_Stan     AS stan,
    CAST(ISNULL((SELECT SUM(v.TrV_NettoR + v.TrV_VatR) FROM CDN.TraVat v
                 WHERE v.TrV_GIDTyp = n.TrN_GIDTyp AND v.TrV_GIDNumer = n.TrN_GIDNumer), 0) AS DECIMAL(15, 2)) AS brutto,
    ISNULL(NULLIF(n.TrN_Waluta, ''), 'PLN') AS waluta,
    kna.KnA_NipPrefiks AS nip_prefiks,
    NULLIF(kna.KnA_Nip, '') AS nip_nabywcy,
    (SELECT MAX(NULLIF(k.KSF_Numer, '')) FROM CDN.KSeFDokumenty k
      WHERE k.KSF_DokTyp = n.TrN_GIDTyp AND k.KSF_DokNumer = n.TrN_GIDNumer
        AND k.KSF_TypOperacji = 0) AS numer_ksef_xl
FROM CDN.TraNag n
LEFT JOIN CDN.KntAdresy kna ON kna.KnA_GIDNumer = n.TrN_KnANumer
WHERE n.TrN_GIDTyp IN (2033, 2037, 2041, 2045, 1824)
  AND n.TrN_Data2 >= DATEDIFF(DAY, '1800-12-28', @Od)
ORDER BY n.TrN_Data2, n.TrN_GIDNumer;
