"""Cienki klient HTTP API KSeF 2.0 — jedno wywołanie = jedna metoda, bez logiki biznesowej.

Tokeny (accessToken, refreshToken, token KSeF) nigdy nie trafiają do logów ani komunikatów błędów.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

SRODOWISKA = {
    "test": "https://api-test.ksef.mf.gov.pl/v2",
    "demo": "https://api-demo.ksef.mf.gov.pl/v2",
    "prod": "https://api.ksef.mf.gov.pl/v2",
}
MAKS_PONOWIEN_429 = 5


class BladKsef(Exception):
    def __init__(self, metoda: str, sciezka: str, status: int, tresc: str):
        super().__init__(f"{metoda} {sciezka} -> HTTP {status}: {tresc[:2000]}")
        self.status = status
        self.tresc = tresc


class KlientKsef:
    def __init__(self, srodowisko: str = "test", timeout: float = 60.0):
        if srodowisko not in SRODOWISKA:
            raise ValueError(f"Nieznane środowisko {srodowisko!r}")
        self.srodowisko = srodowisko
        self._http = httpx.Client(
            base_url=SRODOWISKA[srodowisko],
            timeout=timeout,
            headers={"Accept": "application/json", "X-Error-Format": "problem-details"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "KlientKsef":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _wywolaj(self, metoda: str, sciezka: str, token: str | None = None, **kwargs) -> httpx.Response:
        naglowki = kwargs.pop("headers", {})
        if token:
            naglowki["Authorization"] = f"Bearer {token}"
        for proba in range(MAKS_PONOWIEN_429 + 1):
            odp = self._http.request(metoda, sciezka, headers=naglowki, **kwargs)
            # 429 = żądanie odrzucone przez limit, nic nie zostało przyjęte -> można bezpiecznie powtórzyć.
            if odp.status_code == 429 and proba < MAKS_PONOWIEN_429:
                time.sleep(min(int(odp.headers.get("Retry-After", "5")), 60))
                continue
            if odp.status_code >= 400:
                raise BladKsef(metoda, sciezka, odp.status_code, odp.text)
            return odp
        raise AssertionError("nieosiągalne")

    def _json(self, metoda: str, sciezka: str, token: str | None = None, **kwargs) -> Any:
        odp = self._wywolaj(metoda, sciezka, token, **kwargs)
        return odp.json() if odp.content else None

    # --- bezpieczeństwo / uwierzytelnienie ---

    def certyfikaty_mf(self) -> list[dict]:
        return self._json("GET", "/security/public-key-certificates")

    def challenge(self) -> dict:
        return self._json("POST", "/auth/challenge")

    def auth_xades(self, podpisany_xml: bytes) -> dict:
        return self._json(
            "POST", "/auth/xades-signature",
            content=podpisany_xml,
            headers={"Content-Type": "application/xml"},
            params={"verifyCertificateChain": "false"},
        )

    def auth_token_ksef(self, challenge: str, nip: str, zaszyfrowany_token_b64: str) -> dict:
        return self._json("POST", "/auth/ksef-token", json={
            "challenge": challenge,
            "contextIdentifier": {"type": "Nip", "value": nip},
            "encryptedToken": zaszyfrowany_token_b64,
        })

    def auth_status(self, ref: str, token_operacji: str) -> dict:
        return self._json("GET", f"/auth/{ref}", token_operacji)

    def auth_redeem(self, token_operacji: str) -> dict:
        return self._json("POST", "/auth/token/redeem", token_operacji)

    def auth_refresh(self, refresh_token: str) -> dict:
        return self._json("POST", "/auth/token/refresh", refresh_token)

    # --- tokeny KSeF ---

    def wygeneruj_token(self, access: str, uprawnienia: list[str], opis: str) -> dict:
        return self._json("POST", "/tokens", access, json={"permissions": uprawnienia, "description": opis})

    def status_tokenu(self, access: str, ref: str) -> dict:
        return self._json("GET", f"/tokens/{ref}", access)

    # --- pobieranie faktur (limit API: 20 zapytań/h) ---

    def metadane_faktur(self, access: str, filtry: dict, offset: int = 0, rozmiar: int = 250) -> dict:
        """offset = numer strony (pageOffset), rozmiar = liczba rekordów na stronie (10–250)."""
        return self._json("POST", "/invoices/query/metadata", access, json=filtry,
                          params={"pageOffset": offset, "pageSize": rozmiar, "sortOrder": "Asc"})

    def faktura_xml(self, access: str, numer_ksef: str) -> bytes:
        """Pełny XML faktury po numerze KSeF (limit API: 64/h)."""
        return self._wywolaj("GET", f"/invoices/ksef/{numer_ksef}", access,
                             headers={"Accept": "application/xml"}).content

    # --- sesja interaktywna ---

    def otworz_sesje(self, access: str, klucz_b64: str, iv_b64: str, public_key_id: str | None = None) -> dict:
        szyfrowanie = {"encryptedSymmetricKey": klucz_b64, "initializationVector": iv_b64}
        if public_key_id:
            szyfrowanie["publicKeyId"] = public_key_id
        return self._json("POST", "/sessions/online", access, json={
            "formCode": {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"},
            "encryption": szyfrowanie,
        })

    def wyslij_fakture(self, access: str, sesja: str, tresc: dict) -> dict:
        return self._json("POST", f"/sessions/online/{sesja}/invoices", access, json=tresc)

    def zamknij_sesje(self, access: str, sesja: str) -> None:
        self._wywolaj("POST", f"/sessions/online/{sesja}/close", access)

    def status_sesji(self, access: str, sesja: str) -> dict:
        return self._json("GET", f"/sessions/{sesja}", access)

    def status_faktury(self, access: str, sesja: str, faktura: str) -> dict:
        return self._json("GET", f"/sessions/{sesja}/invoices/{faktura}", access)

    def upo_faktury(self, access: str, sesja: str, faktura: str) -> bytes:
        return self._wywolaj("GET", f"/sessions/{sesja}/invoices/{faktura}/upo", access,
                             headers={"Accept": "application/xml"}).content

    def upo_sesji(self, access: str, sesja: str, upo_ref: str) -> bytes:
        return self._wywolaj("GET", f"/sessions/{sesja}/upo/{upo_ref}", access,
                             headers={"Accept": "application/xml"}).content
