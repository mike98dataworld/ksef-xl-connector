-- Korekty FSK (2041): jeden wiersz = jedna korekta (gid_numer, faktura_json). Tylko SELECT.
-- Format jak faktury_wiele.sql (ten sam „rozdziel”), plus dane faktury korygowanej. Pozycje = różnice ze spiętych WZK (2009).
-- Wiersze „przed korektą” i numer KSeF oryginału bierze konektor z faktury korygowanej w KSeF (polecenie `oryginaly`),
-- bo TrN_KSeFNumerOrg bywa pusty, a TrN_DataWystOrg to data korekty.

SET NOCOUNT ON;
DECLARE @GIDy TABLE (GIDNumer INT PRIMARY KEY);
-- Korekty po numerach: zamiast VALUES poniżej np.
-- INSERT INTO @GIDy (GIDNumer) SELECT TrN_GIDNumer FROM CDN.TraNag WHERE TrN_GIDTyp = 2041 AND TrN_Stan <> 6
--   AND RTRIM(TrN_TrNSeria) IN ('KB', 'KC/F') AND TrN_TrNMiesiac = 10 AND TrN_TrNRok IN (26, 2026);
INSERT INTO @GIDy (GIDNumer) VALUES (100101), (100102), (100103);

SELECT
    g.GIDNumer AS gid_numer,
    faktura_json = (
        SELECT
            naglowek = JSON_QUERY((
                SELECT
                    k.TrN_GIDTyp   AS gid_typ,
                    k.TrN_GIDNumer AS gid_numer,
                    k.TrN_Stan     AS stan,
                    CDN.NumerDokumentu(k.TrN_GIDTyp, k.TrN_SpiTyp, k.TrN_TrNTyp, k.TrN_TrNNumer,
                                       k.TrN_TrNRok, k.TrN_TrNSeria, k.TrN_TrNMiesiac) AS numer,
                    k.TrN_TrNSeria AS seria,
                    k.TrN_Data2    AS data_wyst,
                    k.TrN_Data3    AS data_sprz,
                    k.TrN_Waluta   AS waluta,
                    k.TrN_FlagaNB  AS flaga_nb,
                    ISNULL(RTRIM(k.TrN_PrzyczynaKorekty), '') AS przyczyna,
                    ISNULL((SELECT STRING_AGG(o.TnO_Opis, '') WITHIN GROUP (ORDER BY o.TnO_TrnLp)
                            FROM CDN.TrNOpisy o
                            WHERE o.TnO_TrnTyp = k.TrN_GIDTyp AND o.TnO_TrnNumer = k.TrN_GIDNumer AND o.TnO_Typ = 0), '') AS opis
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),
            -- Faktura korygowana (TrN_ZwrTyp/ZwrNumer): numer z „(S)” i data wystawienia z XL (do wyszukania w KSeF)
            korygowana = JSON_QUERY((
                SELECT
                    o.TrN_GIDTyp   AS gid_typ,
                    o.TrN_GIDNumer AS gid_numer,
                    CDN.NumerDokumentu(o.TrN_GIDTyp, o.TrN_SpiTyp, o.TrN_TrNTyp, o.TrN_TrNNumer,
                                       o.TrN_TrNRok, o.TrN_TrNSeria, o.TrN_TrNMiesiac) AS numer,
                    o.TrN_Data2    AS data_wyst
                FROM CDN.TraNag o
                WHERE o.TrN_GIDTyp = k.TrN_ZwrTyp AND o.TrN_GIDNumer = k.TrN_ZwrNumer
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),
            nabywca = JSON_QUERY((
                SELECT
                    kna.KnA_Nip AS nip, kna.KnA_NipPrefiks AS nip_prefiks, kna.KnA_Kraj AS kraj,
                    kna.KnA_Nazwa1 AS nazwa1, kna.KnA_Nazwa2 AS nazwa2, kna.KnA_Nazwa3 AS nazwa3,
                    kna.KnA_Ulica AS ulica, kna.KnA_Adres AS adres, kna.KnA_KodP AS kodp, kna.KnA_Miasto AS miasto
                FROM CDN.KntAdresy kna
                WHERE kna.KnA_GIDNumer = k.TrN_KnANumer
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)),
            -- Różnice (ujemne ilości i wartości) ze spiętych WZK
            pozycje = JSON_QUERY((
                SELECT
                    e.TrE_GIDLp AS lp, e.TrE_TwrKod AS kod, e.TrE_TwrNazwa AS nazwa,
                    e.TrE_Ilosc AS ilosc, e.TrE_JmZ AS jm,
                    e.TrE_StawkaPod AS stawka, e.TrE_FlagaVat AS flaga_vat,
                    e.TrE_KsiegowaNetto AS netto, e.TrE_KsiegowaBrutto AS brutto
                FROM CDN.TraNag wz
                JOIN CDN.TraElem e ON e.TrE_GIDTyp = wz.TrN_GIDTyp AND e.TrE_GIDNumer = wz.TrN_GIDNumer
                WHERE wz.TrN_GIDTyp = 2009
                  AND wz.TrN_SpiTyp = k.TrN_GIDTyp
                  AND wz.TrN_SpiNumer = k.TrN_GIDNumer
                ORDER BY wz.TrN_GIDNumer, e.TrE_GIDLp
                FOR JSON PATH)),
            vat = JSON_QUERY((
                SELECT v.TrV_StawkaPod AS stawka, v.TrV_FlagaVat AS flaga_vat,
                       v.TrV_NettoR AS netto, v.TrV_VatR AS vat, v.TrV_Zaliczkowy AS zaliczkowy
                FROM CDN.TraVat v
                WHERE v.TrV_GIDTyp = k.TrN_GIDTyp AND v.TrV_GIDNumer = k.TrN_GIDNumer
                FOR JSON PATH)),
            platnosci = JSON_QUERY(ISNULL((
                SELECT p.TrP_Termin AS termin, RTRIM(p.TrP_FormaNazwa) AS forma, p.TrP_FormaNr AS forma_nr,
                       p.TrP_Kwota AS kwota, p.TrP_Pozostaje AS pozostaje, p.TrP_KARNumer AS rachunek_id,
                       p.TrP_DataRozliczenia AS data_rozl
                FROM CDN.TraPlat p
                WHERE p.TrP_GIDTyp = k.TrN_GIDTyp AND p.TrP_GIDNumer = k.TrN_GIDNumer
                ORDER BY p.TrP_GIDLp
                FOR JSON PATH), '[]')),
            wz = JSON_QUERY(ISNULL((
                SELECT CDN.NumerDokumentu(wz.TrN_GIDTyp, wz.TrN_SpiTyp, wz.TrN_TrNTyp, wz.TrN_TrNNumer,
                                          wz.TrN_TrNRok, wz.TrN_TrNSeria, wz.TrN_TrNMiesiac) AS numer
                FROM CDN.TraNag wz
                WHERE wz.TrN_GIDTyp = 2009 AND wz.TrN_SpiTyp = k.TrN_GIDTyp AND wz.TrN_SpiNumer = k.TrN_GIDNumer
                ORDER BY wz.TrN_GIDNumer
                FOR JSON PATH), '[]'))
        FROM CDN.TraNag k
        WHERE k.TrN_GIDTyp = 2041
          AND k.TrN_GIDNumer = g.GIDNumer
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
FROM @GIDy g
ORDER BY g.GIDNumer;
