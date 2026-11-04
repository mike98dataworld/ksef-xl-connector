"""Konfiguracja firmy (z pliku TOML) i ustawienia stałe. Sekretów tu nie ma — tylko w env / .env.

Dane sprzedawcy, rachunki i zasady wysyłki są w `firma.toml` (poza gitem). Kolejność szukania:
1. ścieżka ze zmiennej KSEF_FIRMA,
2. `firma.toml` w bieżącym katalogu,
3. `firma.example.toml` — dane FIKCYJNE; wtedy KONFIGURACJA_PRZYKLADOWA = True i wysyłka poza TEST jest zablokowana.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from ksef_connector.mapping.model import Adres, RachunekBankowy, Sprzedawca

KATALOG_PROJEKTU = Path(__file__).resolve().parent.parent
PLIK_PRZYKLADOWY = KATALOG_PROJEKTU / "firma.example.toml"


def _plik_konfiguracji() -> tuple[Path, bool]:
    if sciezka := os.environ.get("KSEF_FIRMA", "").strip():
        return Path(sciezka), Path(sciezka).resolve() == PLIK_PRZYKLADOWY
    if Path("firma.toml").exists():
        return Path("firma.toml"), False
    return PLIK_PRZYKLADOWY, True


def _rachunek(d: dict[str, Any]) -> RachunekBankowy:
    return RachunekBankowy(nr=str(d["nr"]).replace(" ", ""), swift=d.get("swift"), bank=d.get("bank"),
                           opis=d.get("opis"))


PLIK_FIRMY, KONFIGURACJA_PRZYKLADOWA = _plik_konfiguracji()
_firma = tomllib.loads(PLIK_FIRMY.read_text(encoding="utf-8"))

# Podmiot1
SPRZEDAWCA = Sprzedawca(
    nip=_firma["sprzedawca"]["nip"],
    nazwa=_firma["sprzedawca"]["nazwa"],
    adres=Adres(kod_kraju=_firma["sprzedawca"].get("kod_kraju", "PL"),
                linia1=_firma["sprzedawca"]["adres_linia1"],
                linia2=_firma["sprzedawca"].get("adres_linia2")),
)

# Rachunek do zapłaty na fakturach NIEZAPŁACONYCH (przelew), per waluta. Na zapłaconych rachunku nie podajemy.
RACHUNKI_DO_ZAPLATY = {waluta: _rachunek(r) for waluta, r in _firma["rachunki"]["do_zaplaty"].items()}
RACHUNEK_DO_ZAPLATY = RACHUNKI_DO_ZAPLATY["PLN"]

# Konto proform: na fakturach zaliczkowych (FSL) zawsze, także zapłaconych. Korekty: osobne konto albo to samo.
RACHUNEK_PROFORM = _rachunek(_firma["rachunki"]["proformy"])
RACHUNEK_KOREKT = _rachunek(_firma["rachunki"]["korekty"]) if "korekty" in _firma["rachunki"] else RACHUNEK_PROFORM

# Faktury dla osób prywatnych bez NIP nabywcy — czy wysyłać do KSeF (zasada firmy).
WYSYLAJ_BEZ_NIP = bool(_firma.get("zasady", {}).get("wysylaj_bez_nip", False))

SYSTEM_INFO = "ksef-connector 0.1"

# Na DEMO (i TEST) wolno wysyłać tylko faktury z tych katalogów — dane fikcyjne lub zanonimizowane.
KATALOGI_FIKCYJNE = (Path("tests/fixtures"), Path("fixtures_anon"))


def wczytaj_env(plik: Path | str = ".env") -> None:
    """Prosty loader KEY=VALUE z .env (bez nadpisywania zmiennych już ustawionych w środowisku)."""
    p = Path(plik)
    if not p.exists():
        return
    for linia in p.read_text(encoding="utf-8").splitlines():
        linia = linia.strip()
        if not linia or linia.startswith("#") or "=" not in linia:
            continue
        klucz, wartosc = linia.split("=", 1)
        os.environ.setdefault(klucz.strip(), wartosc.strip().strip('"').strip("'"))
