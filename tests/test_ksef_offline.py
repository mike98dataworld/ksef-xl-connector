"""Kryptografia i rejestr — bez połączenia z KSeF."""
import base64

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree
from signxml.xades import XAdESVerifier

from ksef_connector.ksef import crypto
from ksef_connector.log.rejestr import Rejestr


def test_aes_cbc_pkcs7_tam_i_z_powrotem():
    k = crypto.KluczSesji.nowy()
    dane = "<Faktura>zażółć gęślą jaźń</Faktura>".encode()
    szyfrogram = k.szyfruj(dane)
    assert len(szyfrogram) % 16 == 0 and szyfrogram != dane
    assert k.odszyfruj(szyfrogram) == dane


def test_rsa_oaep_sha256_da_sie_odszyfrowac():
    klucz, cert = crypto.certyfikat_testowy("5254466791")
    cert_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    szyfrogram = crypto.szyfruj_rsa_oaep(cert_b64, b"token|1730000000000")
    jawne = klucz.decrypt(szyfrogram, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    assert jawne == b"token|1730000000000"


def test_losowy_nip_ma_poprawna_sume_kontrolna():
    for _ in range(50):
        nip = crypto.losowy_nip()
        cyfry = list(map(int, nip))
        assert len(nip) == 10 and nip[0] != "0"
        assert sum(c * w for c, w in zip(cyfry, crypto.WAGI_NIP)) % 11 == cyfry[9]


def test_certyfikat_testowy_ma_nip_w_serial_number():
    _, cert = crypto.certyfikat_testowy("5254466791")
    sn = cert.subject.get_attributes_for_oid(x509.oid.NameOID.SERIAL_NUMBER)[0].value
    assert sn == "TINPL-5254466791"


def test_podpis_xades_weryfikuje_sie():
    klucz, cert = crypto.certyfikat_testowy("5254466791")
    xml = crypto.auth_token_request("20250604-CR-461EA5B000-537A6BA15D-D7", "5254466791")
    podpisany = crypto.podpisz_xades(xml, klucz, cert)
    XAdESVerifier().verify(podpisany, x509_cert=crypto.pem_certyfikatu(cert).decode(), expect_references=3)
    root = etree.fromstring(podpisany)
    assert root.findtext("{http://ksef.mf.gov.pl/auth/token/2.0}ContextIdentifier/{http://ksef.mf.gov.pl/auth/token/2.0}Nip") == "5254466791"


@pytest.fixture
def rejestr(tmp_path):
    r = Rejestr(tmp_path / "r.sqlite")
    yield r
    r.close()


def test_rejestr_blokuje_po_sukcesie(rejestr):
    assert rejestr.blokada("test", 2033, 1) is None
    i = rejestr.zapisz_wyslanie("test", 2033, 1, "FS-1", "hash", "SESJA")
    rejestr.zapisz_status(i, 200, "Sukces", None, "KSEF-1")
    assert "KSEF-1" in rejestr.blokada("test", 2033, 1)
    assert rejestr.blokada("demo", 2033, 1) is None  # inne środowisko


def test_rejestr_blokuje_przy_nieznanym_statusie(rejestr):
    rejestr.zapisz_wyslanie("test", 2033, 2, "FS-2", "hash", "SESJA")  # proces padł przed statusem
    assert "bez statusu" in rejestr.blokada("test", 2033, 2)


def test_rejestr_pozwala_ponowic_po_odrzuceniu(rejestr):
    i = rejestr.zapisz_wyslanie("test", 2033, 3, "FS-3", "hash", "SESJA")
    rejestr.zapisz_status(i, 450, "Błąd semantyki", ["..."], None)
    assert rejestr.blokada("test", 2033, 3) is None


def test_rejestr_blokuje_duplikat_z_numerem_oryginalu(rejestr):
    i = rejestr.zapisz_wyslanie("test", 2033, 4, "FS-4", "hash", "SESJA")
    rejestr.zapisz_status(i, 440, "Duplikat faktury", None, "KSEF-ORYG")
    assert "KSEF-ORYG" in rejestr.blokada("test", 2033, 4)


def test_rejestr_blokuje_po_numerze_gdy_ponownie_wystawiona_z_nowym_gid(rejestr):
    """Jak (S)FS-23/10/26/C/F: anulowana w ERP i wystawiona ponownie z tym samym numerem, inny GID."""
    i = rejestr.zapisz_wyslanie("prod", 2033, 900010, "(S)FS-23/10/26/C/F", "hash", "SESJA")
    rejestr.zapisz_status(i, 200, "Sukces", None, "KSEF-23")
    assert "KSEF-23" in rejestr.blokada("prod", 2033, 188681, "(S)FS-23/10/26/C/F")
    assert rejestr.blokada("prod", 2033, 188681, "(S)FS-24/10/26/C/F") is None
