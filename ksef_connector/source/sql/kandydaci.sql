-- Lista FS do wyboru próbek dla faktura.sql. Tylko SELECT.
-- Kolumny pomocnicze: liczba stawek VAT, liczba spiętych WZ, czy XL już wysłał fakturę do KSeF.

SET NOCOUNT ON;
DECLARE @Od DATE = '2026-10-01';

SELECT TOP 200
    fs.TrN_GIDNumer AS gid_numer,
    CDN.NumerDokumentu(fs.TrN_GIDTyp, fs.TrN_SpiTyp, fs.TrN_TrNTyp, fs.TrN_TrNNumer,
                       fs.TrN_TrNRok, fs.TrN_TrNSeria, fs.TrN_TrNMiesiac) AS numer,
    fs.TrN_TrNSeria AS seria,
    fs.TrN_FlagaNB  AS flaga_nb,
    DATEADD(DAY, fs.TrN_Data2, '1800-12-28') AS data_wyst,
    (SELECT COUNT(*) FROM CDN.TraVat v
      WHERE v.TrV_GIDTyp = fs.TrN_GIDTyp AND v.TrV_GIDNumer = fs.TrN_GIDNumer) AS liczba_stawek,
    (SELECT COUNT(*) FROM CDN.TraNag wz
      WHERE wz.TrN_GIDTyp = 2001 AND wz.TrN_SpiTyp = fs.TrN_GIDTyp AND wz.TrN_SpiNumer = fs.TrN_GIDNumer) AS liczba_wz,
    (SELECT MAX(NULLIF(k.KSF_Numer, '')) FROM CDN.KSeFDokumenty k
      WHERE k.KSF_DokTyp = fs.TrN_GIDTyp AND k.KSF_DokNumer = fs.TrN_GIDNumer AND k.KSF_TypOperacji = 0) AS ksef_numer_xl
FROM CDN.TraNag fs
WHERE fs.TrN_GIDTyp = 2033
  AND fs.TrN_Stan <> 6
  AND fs.TrN_Data2 >= DATEDIFF(DAY, '1800-12-28', @Od)
ORDER BY liczba_stawek DESC, fs.TrN_GIDNumer DESC;
