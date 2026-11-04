-- Jedna faktura FS z XL jako JSON (format wejścia dla ksef_connector.mapping.xl).
-- Tylko SELECT — nic nie zapisuje.
--
-- Użycie w SSMS: ustaw @GIDNumer, uruchom, skopiuj komórkę faktura_json i zapisz jako <nazwa>.json (UTF-8).
-- Wynik idzie przez zmienną, bo SSMS dzieli surowy FOR JSON na wiersze po ~2033 znaki.
-- Pliki z produkcji trzymaj w fixtures_prod/ (jest w .gitignore) — to prawdziwe dane klientów.
-- Konektor (XlSqlSource) podmienia wartość @GIDNumer na parametr.

SET NOCOUNT ON;
DECLARE @GIDNumer INT = 100001;

DECLARE @json NVARCHAR(MAX) = (
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
            kna.KnA_Nip         AS nip,
            kna.KnA_NipPrefiks  AS nip_prefiks,
            kna.KnA_Kraj        AS kraj,
            kna.KnA_Nazwa1      AS nazwa1,
            kna.KnA_Nazwa2      AS nazwa2,
            kna.KnA_Nazwa3      AS nazwa3,
            kna.KnA_Ulica       AS ulica,
            kna.KnA_Adres       AS adres,
            kna.KnA_KodP        AS kodp,
            kna.KnA_Miasto      AS miasto
        FROM CDN.KntAdresy kna
        WHERE kna.KnA_GIDNumer = fs.TrN_KnANumer
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),
    -- Pozycje FS nie leżą pod FS, tylko pod spiętymi WZ (typ 2001; FSE: WZE 2005). *_wal = w walucie faktury.
    pozycje = JSON_QUERY((
        SELECT
            e.TrE_GIDLp          AS lp,
            e.TrE_TwrKod         AS kod,
            e.TrE_TwrNazwa       AS nazwa,
            e.TrE_Ilosc          AS ilosc,
            e.TrE_JmZ            AS jm,
            e.TrE_StawkaPod      AS stawka,
            e.TrE_FlagaVat       AS flaga_vat,
            e.TrE_KsiegowaNetto  AS netto,
            e.TrE_KsiegowaBrutto AS brutto,
            e.TrE_WartoscPoRabacie AS wartosc_wal,
            e.TrE_KursM          AS kurs_m,
            e.TrE_KursL          AS kurs_l
        FROM CDN.TraNag wz
        JOIN CDN.TraElem e ON e.TrE_GIDTyp = wz.TrN_GIDTyp AND e.TrE_GIDNumer = wz.TrN_GIDNumer
        WHERE wz.TrN_GIDTyp IN (2001, 2005)
          AND wz.TrN_SpiTyp = fs.TrN_GIDTyp
          AND wz.TrN_SpiNumer = fs.TrN_GIDNumer
        ORDER BY wz.TrN_GIDNumer, e.TrE_GIDLp
        FOR JSON PATH)),
    -- Podsumowanie faktury zawsze z TraVat.
    vat = JSON_QUERY((
        SELECT
            v.TrV_StawkaPod AS stawka,
            v.TrV_FlagaVat  AS flaga_vat,
            v.TrV_NettoR    AS netto,
            v.TrV_VatR      AS vat,
            v.TrV_Zaliczkowy AS zaliczkowy,
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
        FOR JSON PATH), '[]'))
FROM CDN.TraNag fs
WHERE fs.TrN_GIDTyp IN (2033, 2037)
  AND fs.TrN_GIDNumer = @GIDNumer
FOR JSON PATH, WITHOUT_ARRAY_WRAPPER);

SELECT @json AS faktura_json;
