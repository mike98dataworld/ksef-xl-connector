-- Wiele faktur FS / FSE / FSL naraz: jeden wiersz = jedna faktura (gid_numer, faktura_json). Tylko SELECT.
-- Ten sam JSON co faktura.sql. W SSMS: wpisz GID-y, F5, zaznacz wyniki (Ctrl+A), skopiuj (Ctrl+C).
-- Pojedyncza komórka z FOR JSON w podzapytaniu nie jest dzielona na kawałki, w przeciwieństwie do surowego FOR JSON.

SET NOCOUNT ON;
DECLARE @GIDy TABLE (GIDNumer INT PRIMARY KEY);
-- Partia po numerach zamiast GID (np. FS-65…193/10/26/B bez kilku wybranych): zamiast VALUES poniżej
-- INSERT INTO @GIDy (GIDNumer) SELECT TrN_GIDNumer FROM CDN.TraNag WHERE TrN_GIDTyp = 2033 AND TrN_Stan <> 6
--   AND RTRIM(TrN_TrNSeria) = 'B' AND TrN_TrNMiesiac = 10 AND TrN_TrNRok IN (26, 2026)
--   AND TrN_TrNNumer BETWEEN 65 AND 193 AND TrN_TrNNumer NOT IN (69, 70, 103);
INSERT INTO @GIDy (GIDNumer) VALUES (100001), (100002), (100003);

SELECT
    g.GIDNumer AS gid_numer,
    faktura_json = (
        SELECT
            naglowek = JSON_QUERY((
                SELECT
                    fs.TrN_GIDTyp   AS gid_typ,
                    fs.TrN_GIDNumer AS gid_numer,
                    fs.TrN_Stan     AS stan,
                    CDN.NumerDokumentu(fs.TrN_GIDTyp, fs.TrN_SpiTyp, fs.TrN_TrNTyp, fs.TrN_TrNNumer,
                                       fs.TrN_TrNRok, fs.TrN_TrNSeria, fs.TrN_TrNMiesiac) AS numer,
                    fs.TrN_TrNSeria AS seria,
                    fs.TrN_Data2    AS data_wyst,
                    fs.TrN_Data3    AS data_sprz,
                    fs.TrN_Waluta   AS waluta,
                    fs.TrN_FlagaNB  AS flaga_nb,
                    fs.TrN_ExpoNorm AS expo_norm,     -- FSE: 6 = WDT, 7 = OSS/konsument UE
                    ISNULL(RTRIM(fs.TrN_ZamDokumentObcy), '') AS zam_obcy,  -- XL wysyła go zamiast ZS-…, jeśli jest
                    -- Opis dokumentu (pole „Opis” w XL; TnO_Typ = 0), sklejony po TnO_TrnLp. ISNULL → zawsze obecny klucz.
                    ISNULL((SELECT STRING_AGG(o.TnO_Opis, '') WITHIN GROUP (ORDER BY o.TnO_TrnLp)
                            FROM CDN.TrNOpisy o
                            WHERE o.TnO_TrnTyp = fs.TrN_GIDTyp AND o.TnO_TrnNumer = fs.TrN_GIDNumer AND o.TnO_Typ = 0), '') AS opis
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),
            nabywca = JSON_QUERY((
                SELECT
                    kna.KnA_Nip AS nip, kna.KnA_NipPrefiks AS nip_prefiks, kna.KnA_Kraj AS kraj,
                    kna.KnA_Nazwa1 AS nazwa1, kna.KnA_Nazwa2 AS nazwa2, kna.KnA_Nazwa3 AS nazwa3,
                    kna.KnA_Ulica AS ulica, kna.KnA_Adres AS adres, kna.KnA_KodP AS kodp, kna.KnA_Miasto AS miasto
                FROM CDN.KntAdresy kna
                WHERE kna.KnA_GIDNumer = fs.TrN_KnANumer
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),
            pozycje = JSON_QUERY((
                SELECT
                    e.TrE_GIDLp AS lp, e.TrE_TwrKod AS kod, e.TrE_TwrNazwa AS nazwa,
                    e.TrE_Ilosc AS ilosc, e.TrE_JmZ AS jm,
                    e.TrE_StawkaPod AS stawka, e.TrE_FlagaVat AS flaga_vat,
                    e.TrE_KsiegowaNetto AS netto, e.TrE_KsiegowaBrutto AS brutto,
                    e.TrE_WartoscPoRabacie AS wartosc_wal, e.TrE_KursM AS kurs_m, e.TrE_KursL AS kurs_l  -- FSE: w walucie
                FROM CDN.TraNag wz
                JOIN CDN.TraElem e ON e.TrE_GIDTyp = wz.TrN_GIDTyp AND e.TrE_GIDNumer = wz.TrN_GIDNumer
                WHERE wz.TrN_GIDTyp IN (2001, 2005)  -- WZ; FSE: WZE
                  AND wz.TrN_SpiTyp = fs.TrN_GIDTyp
                  AND wz.TrN_SpiNumer = fs.TrN_GIDNumer
                ORDER BY wz.TrN_GIDNumer, e.TrE_GIDLp
                FOR JSON PATH)),
            vat = JSON_QUERY((
                SELECT v.TrV_StawkaPod AS stawka, v.TrV_FlagaVat AS flaga_vat,
                       v.TrV_NettoR AS netto, v.TrV_VatR AS vat, v.TrV_Zaliczkowy AS zaliczkowy,
                       v.TrV_WartoscWal AS wartosc_wal
                FROM CDN.TraVat v
                WHERE v.TrV_GIDTyp = fs.TrN_GIDTyp AND v.TrV_GIDNumer = fs.TrN_GIDNumer
                FOR JSON PATH)),
            -- Płatności (termin, forma, rachunek firmy: TrP_KARNumer). ISNULL(…,'[]'), żeby brak wierszy dawał [] zamiast braku klucza.
            platnosci = JSON_QUERY(ISNULL((
                SELECT p.TrP_Termin AS termin, RTRIM(p.TrP_FormaNazwa) AS forma, p.TrP_FormaNr AS forma_nr,
                       p.TrP_Kwota AS kwota, p.TrP_Pozostaje AS pozostaje, p.TrP_KARNumer AS rachunek_id,
                       p.TrP_DataRozliczenia AS data_rozl
                FROM CDN.TraPlat p
                WHERE p.TrP_GIDTyp = fs.TrN_GIDTyp AND p.TrP_GIDNumer = fs.TrN_GIDNumer
                ORDER BY p.TrP_GIDLp
                FOR JSON PATH), '[]')),
            -- Numery spiętych WZ (pole WZ w FA(3), XL też je wysyłał)
            wz = JSON_QUERY(ISNULL((
                SELECT CDN.NumerDokumentu(wz.TrN_GIDTyp, wz.TrN_SpiTyp, wz.TrN_TrNTyp, wz.TrN_TrNNumer,
                                          wz.TrN_TrNRok, wz.TrN_TrNSeria, wz.TrN_TrNMiesiac) AS numer
                FROM CDN.TraNag wz
                WHERE wz.TrN_GIDTyp IN (2001, 2005) AND wz.TrN_SpiTyp = fs.TrN_GIDTyp AND wz.TrN_SpiNumer = fs.TrN_GIDNumer
                ORDER BY wz.TrN_GIDNumer
                FOR JSON PATH), '[]')),
            -- Zamówienia: powiązanie jest na WZ (TrN_ZaNTyp/TrN_ZaNNumer), czasem na samej FS. Numer składa mapowanie (ZS-…).
            zamowienia = JSON_QUERY(ISNULL((
                SELECT DISTINCT z.ZaN_ZamTyp AS typ, z.ZaN_ZamNumer AS numer, z.ZaN_ZamMiesiac AS miesiac,
                       z.ZaN_ZamRok AS rok, RTRIM(z.ZaN_ZamSeria) AS seria
                FROM (SELECT d.TrN_ZaNTyp AS typ, d.TrN_ZaNNumer AS nr FROM CDN.TraNag d
                      WHERE d.TrN_GIDTyp IN (2001, 2005) AND d.TrN_SpiTyp = fs.TrN_GIDTyp AND d.TrN_SpiNumer = fs.TrN_GIDNumer
                      UNION SELECT fs.TrN_ZaNTyp, fs.TrN_ZaNNumer) l
                JOIN CDN.ZamNag z ON z.ZaN_GIDTyp = l.typ AND z.ZaN_GIDNumer = l.nr
                FOR JSON PATH), '[]')),
            -- FSL (1824): pozycji nie ma (TraElem puste) — w KSeF idą wiersze zamówienia (Zamowienie/ZamowienieWiersz).
            -- ZaE_FlagaNB B: ZaE_WartoscPoRabacie = brutto. Dla FS/FSE pusta lista.
            zamowienie_pozycje = JSON_QUERY(ISNULL((
                SELECT ze.ZaE_GIDLp AS lp, ze.ZaE_TwrKod AS kod, ze.ZaE_TwrNazwa AS nazwa, ze.ZaE_Ilosc AS ilosc,
                       ze.ZaE_JmZ AS jm, ze.ZaE_StawkaPod AS stawka, ze.ZaE_FlagaVAT AS flaga_vat,
                       ze.ZaE_WartoscPoRabacie AS wartosc, ze.ZaE_FlagaNB AS flaga_nb, ze.ZaE_Waluta AS waluta
                FROM CDN.ZamElem ze
                WHERE fs.TrN_GIDTyp = 1824 AND ze.ZaE_GIDTyp = fs.TrN_ZaNTyp AND ze.ZaE_GIDNumer = fs.TrN_ZaNNumer
                ORDER BY ze.ZaE_GIDLp
                FOR JSON PATH), '[]')),
            -- Faktura rozliczająca (FS z wierszem TraVat TrV_Zaliczkowy = 1): FSL tych samych zamówień (przez WZ lub FS).
            -- Mapowanie sprawdza, że suma tych FSL = wiersze zaliczkowe TraVat. Numer KSeF FSL dopisuje `oryginaly`.
            zaliczki = JSON_QUERY(ISNULL((
                SELECT CDN.NumerDokumentu(fsl.TrN_GIDTyp, fsl.TrN_SpiTyp, fsl.TrN_TrNTyp, fsl.TrN_TrNNumer,
                                          fsl.TrN_TrNRok, fsl.TrN_TrNSeria, fsl.TrN_TrNMiesiac) AS numer,
                       fsl.TrN_GIDNumer AS gid_numer, fsl.TrN_Data2 AS data_wyst,
                       (SELECT SUM(v.TrV_NettoR) FROM CDN.TraVat v
                        WHERE v.TrV_GIDTyp = fsl.TrN_GIDTyp AND v.TrV_GIDNumer = fsl.TrN_GIDNumer) AS netto,
                       (SELECT SUM(v.TrV_VatR) FROM CDN.TraVat v
                        WHERE v.TrV_GIDTyp = fsl.TrN_GIDTyp AND v.TrV_GIDNumer = fsl.TrN_GIDNumer) AS vat
                FROM CDN.TraNag fsl
                WHERE fsl.TrN_GIDTyp = 1824 AND fsl.TrN_Stan <> 6
                  AND fs.TrN_GIDTyp <> 1824
                  AND EXISTS (SELECT 1 FROM CDN.TraVat v WHERE v.TrV_GIDTyp = fs.TrN_GIDTyp
                              AND v.TrV_GIDNumer = fs.TrN_GIDNumer AND v.TrV_Zaliczkowy = 1)
                  AND EXISTS (SELECT 1 FROM (SELECT d.TrN_ZaNTyp AS typ, d.TrN_ZaNNumer AS nr FROM CDN.TraNag d
                                             WHERE d.TrN_GIDTyp IN (2001, 2005) AND d.TrN_SpiTyp = fs.TrN_GIDTyp
                                               AND d.TrN_SpiNumer = fs.TrN_GIDNumer
                                             UNION SELECT fs.TrN_ZaNTyp, fs.TrN_ZaNNumer) l
                              WHERE l.nr <> 0 AND l.typ = fsl.TrN_ZaNTyp AND l.nr = fsl.TrN_ZaNNumer)
                ORDER BY fsl.TrN_Data2, fsl.TrN_GIDNumer
                FOR JSON PATH), '[]'))
        FROM CDN.TraNag fs
        WHERE fs.TrN_GIDTyp IN (2033, 2037, 1824)  -- FS, FSE, FSL
          AND fs.TrN_GIDNumer = g.GIDNumer
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
FROM @GIDy g
ORDER BY g.GIDNumer;
