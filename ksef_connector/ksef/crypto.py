"""Kryptografia KSeF 2.0: RSA-OAEP kluczem MF, AES-256-CBC faktur, skróty, XAdES i certyfikat testowy."""
from __future__ import annotations

import base64
import hashlib
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import NameOID
from lxml import etree


def b64(dane: bytes) -> str:
    return base64.b64encode(dane).decode("ascii")


def sha256_b64(dane: bytes) -> str:
    return b64(hashlib.sha256(dane).digest())


def szyfruj_rsa_oaep(certyfikat_mf_b64: str, dane: bytes) -> bytes:
    """RSAES-OAEP, SHA-256 + MGF1(SHA-256), kluczem publicznym z certyfikatu MF (DER w base64)."""
    cert = x509.load_der_x509_certificate(base64.b64decode(certyfikat_mf_b64))
    return cert.public_key().encrypt(
        dane,
        asym_padding.OAEP(mgf=asym_padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )


@dataclass(frozen=True)
class KluczSesji:
    """Klucz AES-256 + IV jednej sesji interaktywnej."""
    klucz: bytes
    iv: bytes

    @classmethod
    def nowy(cls) -> "KluczSesji":
        return cls(klucz=os.urandom(32), iv=os.urandom(16))

    def szyfruj(self, dane: bytes) -> bytes:
        dopelniacz = padding.PKCS7(128).padder()
        jawne = dopelniacz.update(dane) + dopelniacz.finalize()
        szyfr = Cipher(algorithms.AES(self.klucz), modes.CBC(self.iv)).encryptor()
        return szyfr.update(jawne) + szyfr.finalize()

    def odszyfruj(self, dane: bytes) -> bytes:
        deszyfr = Cipher(algorithms.AES(self.klucz), modes.CBC(self.iv)).decryptor()
        jawne = deszyfr.update(dane) + deszyfr.finalize()
        usuwacz = padding.PKCS7(128).unpadder()
        return usuwacz.update(jawne) + usuwacz.finalize()


# --- tylko środowisko TEST ---

WAGI_NIP = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def losowy_nip() -> str:
    """Losowy, poprawny formalnie (suma kontrolna) NIP do środowiska TEST."""
    while True:
        cyfry = [random.randint(1, 9)] + [random.randint(0, 9) for _ in range(8)]
        kontrolna = sum(c * w for c, w in zip(cyfry, WAGI_NIP)) % 11
        if kontrolna != 10:
            nip = "".join(map(str, cyfry)) + str(kontrolna)
            if nip[1:3] != "00":  # wzorzec TNrNIP: druga lub trzecia cyfra niezerowa
                return nip


def certyfikat_testowy(nip: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Samopodpisany certyfikat „osoby fizycznej” z NIP w serialNumber (TINPL-<NIP>). Działa tylko na TEST."""
    klucz = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    podmiot = x509.Name([
        x509.NameAttribute(NameOID.GIVEN_NAME, "Konektor"),
        x509.NameAttribute(NameOID.SURNAME, "Testowy"),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"TINPL-{nip}"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Konektor Testowy"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
    ])
    teraz = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(podmiot)
        .issuer_name(podmiot)
        .public_key(klucz.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(teraz - timedelta(hours=1))
        .not_valid_after(teraz + timedelta(days=365))
        .sign(klucz, hashes.SHA256())
    )
    return klucz, cert


def pem_klucza(klucz: rsa.RSAPrivateKey) -> bytes:
    return klucz.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def pem_certyfikatu(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


AUTH_NS = "http://ksef.mf.gov.pl/auth/token/2.0"


def auth_token_request(challenge: str, nip: str) -> bytes:
    """Niepodpisany AuthTokenRequest (schemat auth v2.0), kontekst NIP, podmiot z certyfikatu."""
    root = etree.Element(f"{{{AUTH_NS}}}AuthTokenRequest", nsmap={None: AUTH_NS})
    etree.SubElement(root, f"{{{AUTH_NS}}}Challenge").text = challenge
    ctx = etree.SubElement(root, f"{{{AUTH_NS}}}ContextIdentifier")
    etree.SubElement(ctx, f"{{{AUTH_NS}}}Nip").text = nip
    etree.SubElement(root, f"{{{AUTH_NS}}}SubjectIdentifierType").text = "certificateSubject"
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def podpisz_xades(xml: bytes, klucz: rsa.RSAPrivateKey, cert: x509.Certificate) -> bytes:
    """Podpis XAdES-BES otaczany (enveloped), RSA-SHA256, skróty SHA-256, kanonikalizacja C14N 1.1.

    Nie używać exc-c14n: signxml 5.x liczy wtedy błędny skrót SignedProperties.
    """
    from signxml import SignatureMethod, DigestAlgorithm
    from signxml.xades import XAdESSigner

    podpisujacy = XAdESSigner(
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
    )
    podpisany = podpisujacy.sign(etree.fromstring(xml), key=klucz, cert=[cert])
    return etree.tostring(podpisany, xml_declaration=True, encoding="utf-8")
