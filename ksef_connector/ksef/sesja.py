"""Sesja interaktywna: otwarcie, wysyłka zaszyfrowanych faktur, statusy, zamknięcie, UPO."""
from __future__ import annotations

import time
from dataclasses import dataclass

from ksef_connector.ksef import crypto
from ksef_connector.ksef.auth import Tokeny, certyfikat_mf
from ksef_connector.ksef.klient import KlientKsef

# Kody statusu faktury w sesji (open-api.json, SessionInvoiceStatusResponse.status)
FAKTURA_PRZYJETA = 100
FAKTURA_PRZETWARZANIE = 150
FAKTURA_SUKCES = 200
# Kody statusu sesji interaktywnej
SESJA_OTWARTA = 100
SESJA_ZAMKNIETA = 170
SESJA_PRZETWORZONA = 200


@dataclass
class WynikFaktury:
    ref: str
    kod: int
    opis: str
    szczegoly: list[str]
    numer_ksef: str | None
    rozszerzenia: dict | None

    @property
    def sukces(self) -> bool:
        return self.kod == FAKTURA_SUKCES


class SesjaInteraktywna:
    """Użycie:
        with SesjaInteraktywna(klient, tokeny) as s:
            ref = s.wyslij(xml)
            wynik = s.czekaj_na_fakture(ref)
        s.czekaj_na_upo()
    """

    def __init__(self, klient: KlientKsef, tokeny: Tokeny):
        self.klient = klient
        self.tokeny = tokeny
        self.klucz = crypto.KluczSesji.nowy()
        self.ref: str | None = None

    def otworz(self) -> str:
        cert = certyfikat_mf(self.klient, "SymmetricKeyEncryption")
        zaszyfrowany_klucz = crypto.szyfruj_rsa_oaep(cert["certificate"], self.klucz.klucz)
        odp = self.klient.otworz_sesje(
            self.tokeny.access, crypto.b64(zaszyfrowany_klucz), crypto.b64(self.klucz.iv), cert.get("publicKeyId")
        )
        self.ref = odp["referenceNumber"]
        return self.ref

    def wyslij(self, xml: bytes) -> str:
        """Wysyła jedną fakturę. Zwraca numer referencyjny faktury w sesji."""
        szyfrogram = self.klucz.szyfruj(xml)
        odp = self.klient.wyslij_fakture(self.tokeny.access, self.ref, {
            "invoiceHash": crypto.sha256_b64(xml),
            "invoiceSize": len(xml),
            "encryptedInvoiceHash": crypto.sha256_b64(szyfrogram),
            "encryptedInvoiceSize": len(szyfrogram),
            "encryptedInvoiceContent": crypto.b64(szyfrogram),
        })
        return odp["referenceNumber"]

    def status_faktury(self, ref_faktury: str) -> WynikFaktury:
        odp = self.klient.status_faktury(self.tokeny.access, self.ref, ref_faktury)
        st = odp["status"]
        return WynikFaktury(
            ref=ref_faktury,
            kod=st["code"],
            opis=st["description"],
            szczegoly=st.get("details") or [],
            numer_ksef=odp.get("ksefNumber"),
            rozszerzenia=st.get("extensions"),
        )

    def czekaj_na_fakture(self, ref_faktury: str, limit_s: float = 180) -> WynikFaktury:
        koniec = time.monotonic() + limit_s
        while True:
            wynik = self.status_faktury(ref_faktury)
            if wynik.kod not in (FAKTURA_PRZYJETA, FAKTURA_PRZETWARZANIE) or time.monotonic() > koniec:
                return wynik
            time.sleep(2)

    def upo_faktury(self, ref_faktury: str) -> bytes:
        return self.klient.upo_faktury(self.tokeny.access, self.ref, ref_faktury)

    def zamknij(self) -> None:
        self.klient.zamknij_sesje(self.tokeny.access, self.ref)

    def czekaj_na_upo(self, limit_s: float = 300) -> tuple[dict, list[bytes]]:
        """Po zamknięciu: czeka na przetworzenie sesji i pobiera zbiorcze UPO (może mieć kilka stron)."""
        koniec = time.monotonic() + limit_s
        while True:
            st = self.klient.status_sesji(self.tokeny.access, self.ref)
            kod = st["status"]["code"]
            if kod == SESJA_PRZETWORZONA and st.get("upo"):
                strony = [self.klient.upo_sesji(self.tokeny.access, self.ref, p["referenceNumber"])
                          for p in st["upo"]["pages"]]
                return st, strony
            if kod not in (SESJA_OTWARTA, SESJA_ZAMKNIETA, SESJA_PRZETWORZONA) or time.monotonic() > koniec:
                return st, []
            time.sleep(3)

    def __enter__(self) -> "SesjaInteraktywna":
        self.otworz()
        return self

    def __exit__(self, *exc) -> None:
        if self.ref:
            self.zamknij()
