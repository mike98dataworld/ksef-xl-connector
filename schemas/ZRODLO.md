Pliki pobrane 2026-10-27 z https://github.com/CIRFMF/ksef-api (gałąź main, dawniej CIRFMF/ksef-docs):

- `FA/schemat_FA(3)_v1-0E.xsd` ← `faktury/schemy/FA/schemat_FA(3)_v1-0E.xsd`
- `FA/bazowe/*.xsd` ← `faktury/schemy/FA/bazowe/`
- `upo/upo-v4-3.xsd` ← `faktury/upo/schemy/upo-v4-3.xsd`
- `open-api.json` ← `open-api.json` (API KSeF 2.0)

Główny XSD importuje `StrukturyDanych_v10-0E.xsd` z adresu crd.gov.pl. Walidator
(`ksef_connector/validate/xsd.py`) podmienia ten adres na lokalną kopię z `FA/bazowe/`.
