"""Uwierzytelnienie: XAdES (TEST, samopodpisany certyfikat) albo token KSeF (DEMO/PROD) -> accessToken."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ksef_connector.ksef import crypto
from ksef_connector.ksef.klient import KlientKsef

STATUS_W_TOKU = 100
STATUS_SUKCES = 200


class BladUwierzytelnienia(Exception):
    pass


@dataclass
class Tokeny:
    access: str
    access_wazny_do: datetime
    refresh: str

    def __repr__(self) -> str:  # nie wypisuj tokenów
        return f"Tokeny(access_wazny_do={self.access_wazny_do.isoformat()})"


def certyfikat_mf(klient: KlientKsef, uzycie: str) -> dict:
    """Aktualny certyfikat MF do danego celu: 'KsefTokenEncryption' albo 'SymmetricKeyEncryption'."""
    teraz = datetime.now(timezone.utc)
    pasujace = [
        c for c in klient.certyfikaty_mf()
        if uzycie in c["usage"]
        and datetime.fromisoformat(c["validFrom"]) <= teraz <= datetime.fromisoformat(c["validTo"])
    ]
    if not pasujace:
        raise BladUwierzytelnienia(f"Brak ważnego certyfikatu MF do {uzycie}")
    return max(pasujace, key=lambda c: c["validFrom"])


def _dokoncz(klient: KlientKsef, start: dict, limit_s: float = 120) -> Tokeny:
    ref = start["referenceNumber"]
    token_operacji = start["authenticationToken"]["token"]
    koniec = time.monotonic() + limit_s
    while True:
        status = klient.auth_status(ref, token_operacji)["status"]
        if status["code"] == STATUS_SUKCES:
            break
        if status["code"] != STATUS_W_TOKU:
            raise BladUwierzytelnienia(f"{ref}: {status['code']} {status['description']} {status.get('details') or ''}")
        if time.monotonic() > koniec:
            raise BladUwierzytelnienia(f"{ref}: uwierzytelnianie trwa dłużej niż {limit_s}s")
        time.sleep(1)
    para = klient.auth_redeem(token_operacji)
    return Tokeny(
        access=para["accessToken"]["token"],
        access_wazny_do=datetime.fromisoformat(para["accessToken"]["validUntil"]),
        refresh=para["refreshToken"]["token"],
    )


def zaloguj_xades(klient: KlientKsef, nip: str, klucz, cert) -> Tokeny:
    if klient.srodowisko != "test":
        raise BladUwierzytelnienia("Samopodpisany certyfikat działa tylko na środowisku TEST")
    ch = klient.challenge()
    podpisany = crypto.podpisz_xades(crypto.auth_token_request(ch["challenge"], nip), klucz, cert)
    return _dokoncz(klient, klient.auth_xades(podpisany))


def zaloguj_tokenem(klient: KlientKsef, nip: str, token_ksef: str) -> Tokeny:
    ch = klient.challenge()
    cert = certyfikat_mf(klient, "KsefTokenEncryption")
    jawny = f"{token_ksef}|{ch['timestampMs']}".encode("utf-8")
    zaszyfrowany = crypto.b64(crypto.szyfruj_rsa_oaep(cert["certificate"], jawny))
    return _dokoncz(klient, klient.auth_token_ksef(ch["challenge"], nip, zaszyfrowany))


def nowy_token_ksef(klient: KlientKsef, tokeny: Tokeny, uprawnienia: list[str], opis: str,
                    limit_s: float = 120) -> str:
    """Generuje token KSeF i czeka, aż stanie się aktywny. Zwraca sam token (sekret!)."""
    wynik = klient.wygeneruj_token(tokeny.access, uprawnienia, opis)
    koniec = time.monotonic() + limit_s
    while True:
        status = klient.status_tokenu(tokeny.access, wynik["referenceNumber"])["status"]
        if status == "Active":
            return wynik["token"]
        if status not in ("Pending",):
            raise BladUwierzytelnienia(f"Token {wynik['referenceNumber']} ma status {status}")
        if time.monotonic() > koniec:
            raise BladUwierzytelnienia("Token KSeF nie stał się aktywny w wyznaczonym czasie")
        time.sleep(2)
