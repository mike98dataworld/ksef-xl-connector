"""Walidacja XML z oficjalnym XSD FA(3) — offline, bez pobierania schem z crd.gov.pl."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "FA"
XSD_FA3 = SCHEMAS / "schemat_FA(3)_v1-0E.xsd"
BAZOWE = SCHEMAS / "bazowe"


class _LokalneSchemy(etree.Resolver):
    """Podmienia zdalne importy (http://crd.gov.pl/.../X.xsd) na pliki z schemas/FA/bazowe/."""

    def resolve(self, url, pubid, context):
        if url and url.startswith("http"):
            lokalny = BAZOWE / url.rsplit("/", 1)[-1]
            if not lokalny.exists():
                raise FileNotFoundError(f"Brak lokalnej kopii schemy: {url}")
            return self.resolve_filename(str(lokalny), context)
        return None


@lru_cache(maxsize=1)
def schemat_fa3() -> etree.XMLSchema:
    parser = etree.XMLParser(no_network=True)
    parser.resolvers.add(_LokalneSchemy())
    return etree.XMLSchema(etree.parse(str(XSD_FA3), parser))


def bledy_xsd(xml: bytes) -> list[str]:
    """Pusta lista = XML zgodny z FA(3)."""
    schemat = schemat_fa3()
    if schemat.validate(etree.fromstring(xml)):
        return []
    return [f"linia {e.line}: {e.message}" for e in schemat.error_log]
