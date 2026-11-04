"""Wysyłka faktur do KSeF: przygotowanie -> rejestr -> sesja interaktywna -> statusy -> UPO.

TEST: konto testowe z fikcyjnym NIP. DEMO: NIP i token z .env, wyłącznie faktury fikcyjne
(KATALOGI_FIKCYJNE). PROD: tylko prawdziwe eksporty, tylko jawnie potwierdzone numery (max MAKS_PARTIA),
a tuż przed wysyłką kontrola w KSeF, czy faktury o tym numerze już tam nie ma.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ksef_connector import config
from ksef_connector.ksef import auth, crypto
from ksef_connector.ksef.klient import BladKsef, KlientKsef
from ksef_connector.ksef.pobieranie import faktury_sprzedazy
from ksef_connector.ksef.sesja import SesjaInteraktywna
from ksef_connector.log.rejestr import DUPLIKAT, Rejestr
from ksef_connector.mapping.model import Adres, Sprzedawca
from ksef_connector.mapping.xl import BladMapowania
from ksef_connector.pipeline import Wynik, przygotuj

KATALOG_TEST = Path(".ksef_test")  # w .gitignore
MAKS_PARTIA = 150  # ile faktur maksymalnie w jednym uruchomieniu na PROD (limit API: 180 wysyłek/h)
ODSTEP_WYSYLKI_S = 2.1  # limit API: 30 wysyłek/min


# --- konto (kontekst NIP + token) ---

@dataclass
class Konto:
    nip: str
    token: str
    sprzedawca: Sprzedawca

    def __repr__(self) -> str:  # nie wypisuj tokenu
        return f"Konto(nip={self.nip})"


def konto(klient: KlientKsef, wypisz: Callable[[str], None] = print) -> Konto:
    """TEST: konto testowe (zakładane automatycznie). DEMO/PROD: KSEF_NIP i KSEF_TOKEN_<ŚRODOWISKO> z .env."""
    if klient.srodowisko == "test":
        k = konto_testowe(klient, wypisz=wypisz)
        return Konto(k.nip, k.token, k.sprzedawca)
    nip = os.environ.get("KSEF_NIP", "").strip()
    zmienna = f"KSEF_TOKEN_{klient.srodowisko.upper()}"
    token = os.environ.get(zmienna, "").strip()
    if not nip or not token:
        raise SystemExit(f"Brak KSEF_NIP lub {zmienna} w .env (zob. .env.example)")
    if config.KONFIGURACJA_PRZYKLADOWA:
        raise SystemExit(f"Konfiguracja firmy to wzór z danymi fikcyjnymi ({config.PLIK_FIRMY}) — "
                         "utwórz firma.toml albo ustaw KSEF_FIRMA (zob. firma.example.toml)")
    if nip != config.SPRZEDAWCA.nip:
        raise SystemExit(f"KSEF_NIP={nip} różny od NIP sprzedawcy w config ({config.SPRZEDAWCA.nip})")
    return Konto(nip, token, config.SPRZEDAWCA)


def sprawdz_dane_fikcyjne(pliki: list[Path]) -> None:
    """Poza TEST wysyłamy tylko faktury z katalogów z danymi fikcyjnymi (zasada nr 3, wymóg MF dla DEMO)."""
    dozwolone = [k.resolve() for k in config.KATALOGI_FIKCYJNE]
    for plik in pliki:
        if not any(plik.resolve().is_relative_to(k) for k in dozwolone):
            raise SystemExit(f"{plik}: na DEMO tylko faktury z {', '.join(map(str, config.KATALOGI_FIKCYJNE))}")


def sprawdz_dane_produkcyjne(pliki: list[Path]) -> None:
    """Na PROD nigdy faktur fikcyjnych ani zanonimizowanych. Dokumenty z BigQuery (nie pliki) są z ERP."""
    fikcyjne = [k.resolve() for k in config.KATALOGI_FIKCYJNE]
    for plik in pliki:
        if isinstance(plik, Path) and any(plik.resolve().is_relative_to(k) for k in fikcyjne):
            raise SystemExit(f"{plik}: dane fikcyjne/zanonimizowane nie mogą trafić na PROD")


# --- konto na środowisku TEST ---

@dataclass
class KontoTestowe:
    nip: str
    token: str

    @property
    def sprzedawca(self) -> Sprzedawca:
        return Sprzedawca(
            nip=self.nip,
            nazwa="Konektor Testowy (KSeF TEST)",
            adres=Adres(kod_kraju="PL", linia1="ul. Testowa 1", linia2="00-001 Warszawa"),
        )


def konto_testowe(klient: KlientKsef, katalog: Path = KATALOG_TEST, wypisz: Callable[[str], None] = print
                  ) -> KontoTestowe:
    """Wczytuje konto TEST z dysku albo zakłada nowe: losowy NIP, certyfikat, logowanie XAdES, token KSeF."""
    plik = katalog / "konto.json"
    if plik.exists():
        dane = json.loads(plik.read_text())
        return KontoTestowe(nip=dane["nip"], token=dane["token"])

    katalog.mkdir(exist_ok=True)
    nip = crypto.losowy_nip()
    klucz, cert = crypto.certyfikat_testowy(nip)
    (katalog / "cert.pem").write_bytes(crypto.pem_certyfikatu(cert))
    (katalog / "klucz.pem").write_bytes(crypto.pem_klucza(klucz))
    (katalog / "klucz.pem").chmod(0o600)
    wypisz(f"Nowe konto TEST: NIP {nip}. Logowanie certyfikatem (XAdES)...")
    tokeny = auth.zaloguj_xades(klient, nip, klucz, cert)
    token = auth.nowy_token_ksef(klient, tokeny, ["InvoiceWrite", "InvoiceRead"], "ksef-connector TEST")
    plik.write_text(json.dumps({"nip": nip, "token": token,
                                "utworzono": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    plik.chmod(0o600)
    wypisz(f"Token KSeF (InvoiceWrite, InvoiceRead) zapisany w {plik}")
    return KontoTestowe(nip=nip, token=token)


# --- wysyłka ---

def _bez_juz_wyslanych(klient: KlientKsef, tokeny, gotowe: list["Pozycja"], srodowisko: str, rejestr: Rejestr,
                       wypisz: Callable[[str], None]) -> list["Pozycja"]:
    """Ostatnia kontrola przed wysyłką: czy w KSeF nie ma już faktury o tym numerze (np. wysłanej przez XL).

    Jedno zapytanie o listę faktur z zakresu dat całej partii (limit API: 20 zapytań/h), nie po jednym na fakturę.
    """
    daty = [p.wynik.faktura.data_wystawienia for p in gotowe]
    w_ksef_po_numerze = defaultdict(list)
    for k in faktury_sprzedazy(klient, tokeny, min(daty) - timedelta(days=3), max(daty) + timedelta(days=3)):
        w_ksef_po_numerze[k.numer].append(k)
    zostaja = []
    for p in gotowe:
        f = p.wynik.faktura
        w_ksef = w_ksef_po_numerze.get(f.numer, [])
        if w_ksef:
            numery = ", ".join(k.numer_ksef for k in w_ksef)
            rejestr.zapisz_z_uzgodnienia(srodowisko, f.xl_gid_typ, f.xl_gid_numer, f.numer, w_ksef[0].numer_ksef,
                                         None if len(w_ksef) == 1 else f"w KSeF kilka faktur: {numery}")
            wypisz(f"JUŻ W KSeF {f.numer}: {numery} — nie wysyłam, zapisane w rejestrze")
        else:
            wypisz(f"KONTROLA   {f.numer}: brak w KSeF — można wysłać")
            zostaja.append(p)
    return zostaja


def _nazwa(plik: Path | str) -> str:
    return plik.name if isinstance(plik, Path) else str(plik)


@dataclass
class Pozycja:
    plik: Path | str
    wynik: Wynik
    id_rejestru: int | str | None = None
    ref: str | None = None


def wyslij(pliki: list[Path] | list[str], wczytaj: Callable[[Any], dict[str, Any]], *, srodowisko: str,
           rejestr: Rejestr, out: Path, wypisz: Callable[[str], None] = print,
           potwierdzone: set[str] | None = None) -> int:
    """Zwraca liczbę faktur, które nie dostały numeru KSeF (0 = wszystko OK).

    pliki: ścieżki JSON albo numery dokumentów (panel: treść z BigQuery, wczytaj = słownik numer → raw).
    potwierdzone: na PROD wymagane — numery faktur (P_2, z "(S)"), które człowiek świadomie zatwierdził do wysyłki.
    """
    if srodowisko == "prod":
        if not potwierdzone:
            raise SystemExit("PROD: podaj numery faktur do wysłania (--potwierdzam \"(S)FS-64/10/26/B\")")
        if len(potwierdzone) > MAKS_PARTIA:
            raise SystemExit(f"PROD: najwyżej {MAKS_PARTIA} faktur w jednym uruchomieniu")
        sprawdz_dane_produkcyjne(pliki)
    elif srodowisko != "test":
        sprawdz_dane_fikcyjne(pliki)

    problemy = 0
    with KlientKsef(srodowisko) as klient:
        konto_ = konto(klient, wypisz=wypisz)

        gotowe: list[Pozycja] = []
        for plik in pliki:
            try:
                wynik = przygotuj(wczytaj(plik), sprzedawca=konto_.sprzedawca)
            except BladMapowania as e:
                wypisz(f"POMINIĘTA  {_nazwa(plik)}: {e}")
                continue
            if not wynik.ok:
                problemy += 1
                wypisz(f"BŁĘDY      {_nazwa(plik)}: " + "; ".join(wynik.bledy))
                continue
            f = wynik.faktura
            if f.nabywca.nip is None and not config.WYSYLAJ_BEZ_NIP:
                wypisz(f"POMINIĘTA  {_nazwa(plik)} ({f.numer}): osoba prywatna bez NIP — nie wysyłamy do KSeF")
                continue
            if srodowisko == "prod" and f.numer not in potwierdzone:
                wypisz(f"POMINIĘTA  {_nazwa(plik)} ({f.numer}): numer nie został potwierdzony (--potwierdzam)")
                continue
            powod = rejestr.blokada(srodowisko, f.xl_gid_typ, f.xl_gid_numer, f.numer)
            if powod:
                wypisz(f"ZABLOKOWANA {_nazwa(plik)} ({f.numer}): {powod}")
                continue
            gotowe.append(Pozycja(plik, wynik))

        if not gotowe:
            wypisz("Brak faktur do wysłania.")
            return problemy

        tokeny = auth.zaloguj_tokenem(klient, konto_.nip, konto_.token)
        if srodowisko != "test":
            gotowe = _bez_juz_wyslanych(klient, tokeny, gotowe, srodowisko, rejestr, wypisz)
            if not gotowe:
                wypisz("Brak faktur do wysłania.")
                return problemy
        upo_dir = out / "upo"
        upo_dir.mkdir(parents=True, exist_ok=True)

        sesja = SesjaInteraktywna(klient, tokeny)
        with sesja:
            wypisz(f"Sesja {sesja.ref} otwarta ({srodowisko}, NIP {konto_.nip})")
            for p in gotowe:
                f, xml = p.wynik.faktura, p.wynik.xml
                p.id_rejestru = rejestr.zapisz_wyslanie(
                    srodowisko, f.xl_gid_typ, f.xl_gid_numer, f.numer, crypto.sha256_b64(xml), sesja.ref)
                try:
                    if p is not gotowe[0]:
                        time.sleep(ODSTEP_WYSYLKI_S)
                    p.ref = sesja.wyslij(xml)
                    rejestr.zapisz_ref(p.id_rejestru, p.ref)
                except BladKsef as e:  # KSeF odrzucił żądanie -> faktura nie została przyjęta
                    rejestr.zapisz_status(p.id_rejestru, e.status, f"HTTP {e.status}", [e.tresc[:2000]], None)
                    problemy += 1
                    wypisz(f"ODRZUCONA  {f.numer}: {e}")

            for p in gotowe:
                if not p.ref:
                    continue
                f = p.wynik.faktura
                w = sesja.czekaj_na_fakture(p.ref)
                numer_ksef = w.numer_ksef
                if w.kod == DUPLIKAT and w.rozszerzenia:
                    numer_ksef = w.rozszerzenia.get("originalKsefNumber")
                rejestr.zapisz_status(p.id_rejestru, w.kod, w.opis, w.szczegoly, numer_ksef)
                if w.sukces:
                    upo = sesja.upo_faktury(p.ref)
                    rejestr.zapisz_upo(p.id_rejestru, upo)
                    (upo_dir / f"{w.numer_ksef}.xml").write_bytes(upo)
                    wypisz(f"OK         {f.numer} -> {w.numer_ksef}")
                else:
                    problemy += 1
                    wypisz(f"STATUS {w.kod}  {f.numer}: {w.opis} {'; '.join(w.szczegoly)}")

        status, strony = sesja.czekaj_na_upo()
        for i, strona in enumerate(strony, start=1):
            (upo_dir / f"sesja_{sesja.ref}_{i}.xml").write_bytes(strona)
        wypisz(f"Sesja zamknięta: {status['status']['code']} {status['status']['description']}, "
               f"faktur OK {status.get('successfulInvoiceCount') or 0}, błędnych {status.get('failedInvoiceCount') or 0}, "
               f"UPO zbiorcze: {len(strony)} str.")
    return problemy
