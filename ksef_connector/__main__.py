"""CLI konektora.

    python -m ksef_connector zbuduj plik.json [...] [--out out]    # mapuje + waliduje + zapisuje XML, nic nie wysyła
    python -m ksef_connector wyslij plik.json [...] [--srodowisko test]
    python -m ksef_connector rejestr [--srodowisko test] [--rejestr sqlite]
    python -m ksef_connector rejestr-do-bq        # tworzy rejestr w BigQuery i przenosi wysyłki z rejestr.sqlite
    python -m ksef_connector uzgodnij --xl lista_xl.tsv --od 2026-09-09 [--do RRRR-MM-DD] [--zapisz]
    python -m ksef_connector uzgodnij --z-bq --od 2026-09-01 --srodowisko prod [--zapisz]
    python -m ksef_connector sprawdz [--srodowisko demo]                # token: logowanie + uprawnienia, bez wysyłki
    python -m ksef_connector anonimizuj fixtures_prod/*.json [--out fixtures_anon]
    python -m ksef_connector rozdziel eksport.tsv --out fixtures_prod/partia_X   # wynik faktury_wiele.sql → pliki JSON
    python -m ksef_connector pobierz --numery-plik numery.txt --out fixtures_prod/partia_X  # to samo z BigQuery
    python -m ksef_connector oryginaly fixtures_prod/korekty_X/*.json --srodowisko prod  # korekty / rozliczające: dane z KSeF
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from ksef_connector import config
from ksef_connector.log.rejestr import Rejestr
from ksef_connector.mapping.xl import BladMapowania
from ksef_connector.pipeline import przygotuj
from ksef_connector.source.fixtures import wczytaj

REJESTR = Path("rejestr.sqlite")  # *.sqlite w .gitignore


def otworz_rejestr(args):
    """Domyślnie rejestr w BigQuery (wspólny z panelem BOK); --rejestr sqlite = lokalny plik, awaryjnie."""
    if args.rejestr == "sqlite":
        return Rejestr(REJESTR)
    from ksef_connector.log.rejestr_bq import RejestrBQ, kto_laptop
    return RejestrBQ(kto_laptop())


def zbuduj(args) -> int:
    from ksef_connector import kursy

    args.out.mkdir(parents=True, exist_ok=True)
    wczytaj_ = kursy.wczytaj_z_kursami(wczytaj, args.pliki, kursy.parsuj(args.kurs))
    problemy = 0
    for plik in args.pliki:
        try:
            wynik = przygotuj(wczytaj_(plik))
        except BladMapowania as e:
            print(f"POMINIĘTA  {plik.name}: {e}")
            problemy += 1
            continue
        cel = args.out / f"{plik.stem}.xml"
        cel.write_bytes(wynik.xml)
        if wynik.ok:
            rodzaj = "" if wynik.faktura.rodzaj == "VAT" else f"{wynik.faktura.rodzaj}, "
            print(f"OK         {plik.name} -> {cel}  ({wynik.faktura.numer}, {rodzaj}brutto {wynik.faktura.brutto})")
        else:
            problemy += 1
            print(f"BŁĘDY      {plik.name} -> {cel}")
            for b in wynik.bledy:
                print(f"           - {b}")
    return 1 if problemy else 0


def wyslij(args) -> int:
    from ksef_connector import kursy
    from ksef_connector.wysylka import wyslij as wyslij_pliki

    wczytaj_ = kursy.wczytaj_z_kursami(wczytaj, args.pliki, kursy.parsuj(args.kurs))
    rejestr = otworz_rejestr(args)
    try:
        potwierdzone = set(args.potwierdzam or [])
        if args.potwierdzam_plik:
            potwierdzone |= set(wczytaj_numery(args.potwierdzam_plik))
        problemy = wyslij_pliki(args.pliki, wczytaj_, srodowisko=args.srodowisko, rejestr=rejestr, out=args.out,
                                potwierdzone=potwierdzone)
    finally:
        rejestr.close()
    return 1 if problemy else 0


def oryginaly(args) -> int:
    """Z KSeF tylko czyta. Korekty: dopisuje do JSON fakturę korygowaną (klucz oryginal_ksef). Faktury rozliczające
    zaliczkę: dopisuje numery KSeF rozliczanych FSL (klucz zaliczki_ksef; null = FSL w KSeF nie ma)."""
    from collections import defaultdict
    from datetime import timedelta

    from ksef_connector.ksef import auth
    from ksef_connector.ksef.klient import KlientKsef
    from ksef_connector.ksef.pobieranie import faktury_sprzedazy, szukaj_po_numerze
    from ksef_connector.mapping.korekta import TYP_FSK
    from ksef_connector.mapping.xl import data_clarion, numer_p2
    from ksef_connector.wysylka import konto as konto_

    korekty, rozliczajace = [], []
    for plik in args.pliki:
        raw = wczytaj(plik)
        kor = raw.get("korygowana") or {}
        if raw.get("zaliczki"):
            rozliczajace.append((plik, raw))
        elif int(raw["naglowek"]["gid_typ"]) != TYP_FSK:
            print(f"POMINIĘTY  {plik.name}: to nie korekta ani faktura rozliczająca zaliczkę")
        elif not kor.get("numer"):
            print(f"BŁĄD       {plik.name}: brak faktury korygowanej w XL — wyeksportuj korekty_wiele.sql")
        else:
            korekty.append((plik, raw, numer_p2(kor["numer"]), data_clarion(kor["data_wyst"])))
    if not korekty and not rozliczajace:
        return 1

    bledy = 0
    with KlientKsef(args.srodowisko) as klient:
        konto = konto_(klient)
        tokeny = auth.zaloguj_tokenem(klient, konto.nip, konto.token)
        # FSL bywa sprzed kilku miesięcy, więc szukamy po numerze i dacie (jedno zapytanie na FSL), bez „(S)”.
        for plik, raw in rozliczajace:
            w_ksef = {}
            for z in raw["zaliczki"]:
                nr = numer_p2(z["numer"])
                znalezione = szukaj_po_numerze(klient, tokeny, nr, data_clarion(z["data_wyst"]))
                if len(znalezione) > 1:
                    print(f"BŁĄD       {plik.name}: {nr} w KSeF {len(znalezione)}× (potrzebna najwyżej 1)")
                    bledy += 1
                    break
                w_ksef[nr] = znalezione[0].numer_ksef if znalezione else None
                print(f"OK         {plik.name}: zaliczka {nr} -> {w_ksef[nr] or 'brak w KSeF'}")
            else:
                raw["zaliczki_ksef"] = w_ksef
                plik.write_text(json.dumps(raw, ensure_ascii=False, default=str), encoding="utf-8")
        if not korekty:
            return 1 if bledy else 0
        # Jedno wyszukanie na wszystkie korekty (limit API: 20/h). Margines, bo data w XL mogła się różnić od tej w KSeF.
        daty = [d for *_, d in korekty]
        w_ksef = defaultdict(list)
        for f in faktury_sprzedazy(klient, tokeny, min(daty) - timedelta(days=14), max(daty) + timedelta(days=14)):
            w_ksef[f.numer].append(f)
        for plik, raw, numer, _ in korekty:
            znalezione = w_ksef.get(numer, [])
            if len(znalezione) != 1:
                print(f"BŁĄD       {plik.name}: {numer} w KSeF {len(znalezione)}× (potrzebna dokładnie 1)")
                bledy += 1
                continue
            nr = znalezione[0].numer_ksef
            raw["oryginal_ksef"] = {"numer_ksef": nr, "xml": klient.faktura_xml(tokeny.access, nr).decode("utf-8")}
            plik.write_text(json.dumps(raw, ensure_ascii=False, default=str), encoding="utf-8")
            print(f"OK         {plik.name}: {numer} -> {nr}")
    return 1 if bledy else 0


def uzgodnij(args) -> int:
    from ksef_connector import uzgadnianie as u
    from ksef_connector.ksef import auth
    from ksef_connector.ksef.klient import KlientKsef
    from ksef_connector.ksef.pobieranie import faktury_sprzedazy
    from ksef_connector.wysylka import konto as konto_

    if args.z_bq:
        from ksef_connector.source import bigquery
        dokumenty = [d for d in u.z_bigquery(bigquery.lista(args.od)) if d.data_wyst <= args.do]
    elif args.xl:
        dokumenty = u.wczytaj_liste_xl(args.xl)
    else:
        raise SystemExit("Podaj --xl lista.tsv albo --z-bq")
    with KlientKsef(args.srodowisko) as klient:
        konto = konto_(klient)
        tokeny = auth.zaloguj_tokenem(klient, konto.nip, konto.token)
        faktury = faktury_sprzedazy(klient, tokeny, args.od, args.do)
    print(f"XL: {len(dokumenty)} dokumentów, KSeF ({args.srodowisko}, NIP {konto.nip}): {len(faktury)} faktur, "
          f"{args.od} – {args.do}")

    pozycje = u.uzgodnij(dokumenty, faktury)
    for kat, ile in sorted(Counter(p.kategoria for p in pozycje).items()):
        print(f"  {kat:<13} {ile}")
    for p in pozycje:
        if p.kategoria in (u.KONFLIKT, u.TYLKO_W_KSEF):
            print(f"  ! {p.kategoria:<12} {(p.xl or p.ksef).numer:<24} {p.uwagi}")

    raport = args.out / f"uzgodnienie_{args.srodowisko}_{args.od}_{args.do}.csv"
    u.zapisz_raport(pozycje, raport)
    print(f"Raport: {raport}")

    if args.zapisz:
        rejestr = otworz_rejestr(args)
        do_zapisu = [(p.xl.gid_typ, p.xl.gid_numer, p.xl.numer, p.ksef.numer_ksef if p.ksef else None,
                      p.uwagi if p.kategoria == u.KONFLIKT else None)
                     for p in pozycje if p.xl and p.kategoria in (u.W_KSEF, u.KONFLIKT)]
        if hasattr(rejestr, "zapisz_uzgodnienia"):  # BigQuery: zbiorczo
            zapisane = rejestr.zapisz_uzgodnienia(args.srodowisko, do_zapisu)
        else:
            zapisane = sum(1 for poz in do_zapisu if rejestr.zapisz_z_uzgodnienia(args.srodowisko, *poz))
        rejestr.close()
        print(f"Rejestr: zapisano {zapisane} wpisów (W_KSEF z numerem, KONFLIKT jako blokada)")
    return 0


def sprawdz(args) -> int:
    """Sprawdza token bez wysyłania faktur: logowanie, odczyt listy faktur, otwarcie i zamknięcie pustej sesji."""
    from datetime import timedelta
    from ksef_connector.ksef import auth
    from ksef_connector.ksef.klient import BladKsef, KlientKsef
    from ksef_connector.ksef.pobieranie import faktury_sprzedazy
    from ksef_connector.ksef.sesja import SesjaInteraktywna
    from ksef_connector.wysylka import konto as konto_

    ok = True
    with KlientKsef(args.srodowisko) as klient:
        konto = konto_(klient)
        print(f"Środowisko {args.srodowisko}, NIP {konto.nip}")
        try:
            tokeny = auth.zaloguj_tokenem(klient, konto.nip, konto.token)
        except (BladKsef, auth.BladUwierzytelnienia) as e:
            print(f"  logowanie tokenem   BŁĄD  {e}")
            return 1
        print(f"  logowanie tokenem   OK    (access ważny do {tokeny.access_wazny_do:%H:%M} UTC)")
        try:
            dzis = date.today()
            n = len(faktury_sprzedazy(klient, tokeny, dzis - timedelta(days=30), dzis))
            print(f"  przeglądanie faktur OK    ({n} faktur wystawionych w ostatnich 30 dniach)")
        except BladKsef as e:
            ok = False
            print(f"  przeglądanie faktur BRAK  (HTTP {e.status}) — token bez uprawnienia InvoiceRead?")
        if args.srodowisko == "prod":
            print("  wystawianie faktur  —     (na PROD nie otwieramy pustej sesji; sprawdzi się przy pierwszej wysyłce)")
            return 0 if ok else 1
        try:
            sesja = SesjaInteraktywna(klient, tokeny)
            sesja.otworz()
            sesja.zamknij()  # pusta sesja: KSeF oznaczy ją jako anulowaną (nie przesłano faktur) — nic nie trafia do KSeF
            print(f"  wystawianie faktur  OK    (otwarto i zamknięto pustą sesję {sesja.ref})")
        except BladKsef as e:
            ok = False
            print(f"  wystawianie faktur  BRAK  (HTTP {e.status}) — token bez uprawnienia InvoiceWrite?")
    return 0 if ok else 1


def plik_dokumentu(katalog: Path, numer: str) -> Path:
    return katalog / ("fs_" + re.sub(r"[^0-9A-Za-z]+", "_", numer.replace("(S)", "")).strip("_") + ".json")


def wczytaj_numery(plik: Path) -> list[str]:
    """Plik z numerami faktur, jeden na linię (# = komentarz) — ten sam co --potwierdzam-plik."""
    return [l.strip() for l in plik.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def pobierz(args) -> int:
    """Dokumenty z BigQuery (widok ERP, opóźnienie do 1 h) → pliki JSON jak z `rozdziel`."""
    from ksef_connector.source import bigquery

    numery = (args.numer or []) + (wczytaj_numery(args.numery_plik) if args.numery_plik else [])
    if not numery:
        raise SystemExit("Podaj --numer albo --numery-plik")
    dokumenty, bledy = bigquery.pobierz_po_numerach(numery)
    args.out.mkdir(parents=True, exist_ok=True)
    for numer, raw in dokumenty.items():
        cel = plik_dokumentu(args.out, numer)
        cel.write_text(json.dumps(raw, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"OK         {numer} -> {cel}")
    for b in bledy:
        print(f"BŁĄD       {b}")
    print(f"Zapisano {len(dokumenty)} z {len(numery)} w {args.out}")
    return 1 if bledy else 0


def rozdziel(args) -> int:
    """Wynik faktury_wiele.sql skopiowany z SSMS (gid_numer TAB faktura_json, z nagłówkiem lub bez) → pliki JSON."""
    args.out.mkdir(parents=True, exist_ok=True)
    zapisane = 0
    for linia in args.plik.read_text(encoding="utf-8-sig").splitlines():
        if "\t" not in linia or not linia.split("\t", 1)[1].lstrip().startswith("{"):
            continue  # nagłówek albo pusta linia
        js = linia.split("\t", 1)[1].strip()
        numer = json.loads(js)["naglowek"]["numer"]
        cel = plik_dokumentu(args.out, numer)
        cel.write_text(js, encoding="utf-8")
        zapisane += 1
    print(f"Zapisano {zapisane} faktur w {args.out}")
    return 0 if zapisane else 1


def anonimizuj(args) -> int:
    from ksef_connector.anonimizacja import anonimizuj as anon

    args.out.mkdir(parents=True, exist_ok=True)
    for nr, plik in enumerate(args.pliki, start=1):
        cel = args.out / f"anon_{plik.stem}.json"
        cel.write_text(json.dumps(anon(wczytaj(plik), nr), ensure_ascii=False, default=str), encoding="utf-8")
        print(f"{plik.name} -> {cel}")
    return 0


def pokaz_rejestr(args) -> int:
    rejestr = otworz_rejestr(args)
    for w in rejestr.wiersze(args.srodowisko):
        print(f"#{str(w['id'])[:13]:<13} {w['srodowisko']:<5} GID {w['xl_gid_numer']:<8} {w['numer_faktury']:<20} "
              f"{w['status_kod'] or '-':<4} {w['numer_ksef'] or '':<36} {w['wyslano']}")
    rejestr.close()
    return 0


def rejestr_do_bq(args) -> int:
    from ksef_connector.log import rejestr_bq

    rejestr_bq.utworz()
    print(f"BigQuery: {rejestr_bq.ZDARZENIA} i widok {rejestr_bq.WIDOK} gotowe")
    lokalny = Rejestr(REJESTR)
    try:
        wiersze = lokalny.wszystko()
    finally:
        lokalny.close()
    przeniesione, pominiete = rejestr_bq.przenies(wiersze)
    print(f"Z {REJESTR}: przeniesiono {przeniesione} wysyłek, pominięto {pominiete} (już w BigQuery)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="ksef_connector")
    sub = ap.add_subparsers(dest="polecenie", required=True)

    p = sub.add_parser("zbuduj", help="mapuje i waliduje, zapisuje XML (bez wysyłki)")
    p.add_argument("pliki", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--kurs", action="append", metavar="NUMER=KURS",
                   help="kurs waluty za 1 jednostkę zamiast kursu z XL, np. \"(S)FSE-1/10/26/B/Z=4,3706\" (można powtórzyć)")
    p.set_defaults(fn=zbuduj)

    p = sub.add_parser("wyslij", help="wysyła faktury do KSeF (na razie tylko TEST)")
    p.add_argument("pliki", nargs="+", type=Path)
    p.add_argument("--srodowisko", choices=["test", "demo", "prod"], default="test")
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--potwierdzam", action="append", metavar="NUMER",
                   help="PROD: numer faktury zatwierdzonej do wysyłki, np. \"(S)FS-64/10/26/B\" (można powtórzyć)")
    p.add_argument("--potwierdzam-plik", type=Path, metavar="PLIK",
                   help="PROD: plik z numerami zatwierdzonych faktur, jeden na linię (# = komentarz)")
    p.add_argument("--kurs", action="append", metavar="NUMER=KURS",
                   help="kurs waluty za 1 jednostkę zamiast kursu z XL, np. \"(S)FSE-1/10/26/B/Z=4,3706\" (można powtórzyć)")
    p.add_argument("--rejestr", choices=["bq", "sqlite"], default="bq",
                   help="rejestr wysyłek: BigQuery (domyślnie, wspólny z panelem) albo lokalny rejestr.sqlite")
    p.set_defaults(fn=wyslij)

    p = sub.add_parser("rejestr", help="pokazuje rejestr wysyłek")
    p.add_argument("--srodowisko", choices=["test", "demo", "prod"])
    p.add_argument("--rejestr", choices=["bq", "sqlite"], default="bq",
                   help="rejestr wysyłek: BigQuery (domyślnie, wspólny z panelem) albo lokalny rejestr.sqlite")
    p.set_defaults(fn=pokaz_rejestr)

    p = sub.add_parser("rejestr-do-bq", help="tworzy rejestr w BigQuery i przenosi do niego wysyłki z rejestr.sqlite")
    p.set_defaults(fn=rejestr_do_bq)

    p = sub.add_parser("uzgodnij", help="porównuje listę dokumentów XL z fakturami w KSeF")
    p.add_argument("--xl", type=Path, help="wynik sql/lista_xl.sql (TSV z nagłówkami)")
    p.add_argument("--z-bq", action="store_true", help="lista dokumentów z widoku ERP w BigQuery zamiast --xl")
    p.add_argument("--od", type=date.fromisoformat, required=True)
    p.add_argument("--do", type=date.fromisoformat, default=date.today())
    p.add_argument("--srodowisko", choices=["test", "demo", "prod"], default="test")
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--zapisz", action="store_true", help="zapisz W_KSEF i KONFLIKT w rejestrze (blokada wysyłki)")
    p.add_argument("--rejestr", choices=["bq", "sqlite"], default="bq",
                   help="rejestr wysyłek: BigQuery (domyślnie, wspólny z panelem) albo lokalny rejestr.sqlite")
    p.set_defaults(fn=uzgodnij)

    p = sub.add_parser("sprawdz", help="sprawdza token (logowanie i uprawnienia) bez wysyłania faktur")
    p.add_argument("--srodowisko", choices=["test", "demo", "prod"], default="demo")
    p.set_defaults(fn=sprawdz)

    p = sub.add_parser("oryginaly", help="korekty i faktury rozliczające: dopisuje do JSON faktury korygowane / "
                                         "numery KSeF zaliczek z KSeF")
    p.add_argument("pliki", nargs="+", type=Path)
    p.add_argument("--srodowisko", choices=["test", "demo", "prod"], default="test")
    p.set_defaults(fn=oryginaly)

    p = sub.add_parser("rozdziel", help="dzieli wynik faktury_wiele.sql / korekty_wiele.sql z SSMS na pliki JSON")
    p.add_argument("plik", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(fn=rozdziel)

    p = sub.add_parser("pobierz", help="pobiera dokumenty z BigQuery (widok ERP) do plików JSON, jak rozdziel")
    p.add_argument("--numer", action="append", metavar="NUMER", help="numer z „(S)”, np. \"(S)FS-212/10/26/B\"")
    p.add_argument("--numery-plik", type=Path, metavar="PLIK", help="numery, jeden na linię (# = komentarz)")
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(fn=pobierz)

    p = sub.add_parser("anonimizuj", help="anonimizuje eksporty z XL do testów na TEST/DEMO")
    p.add_argument("pliki", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path("fixtures_anon"))
    p.set_defaults(fn=anonimizuj)

    config.wczytaj_env()
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
