"""Model faktury niezależny od XL i od formatu FA(3).

Kwoty zawsze jako Decimal. Stawki VAT w notacji FA(3) (`TStawkaPodatku`): "23", "8", "5", "0 KR", "zw", "np I", ...
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Adres:
    kod_kraju: str
    linia1: str
    linia2: str | None = None


@dataclass(frozen=True)
class Sprzedawca:
    nip: str
    nazwa: str
    adres: Adres


@dataclass(frozen=True)
class Nabywca:
    nazwa: str
    nip: str | None  # None = konsument -> BrakID; przy kod_ue = numer VAT UE bez prefiksu kraju
    adres: Adres | None
    kod_ue: str | None = None  # WDT: KodUE (np. LV, EL) → Podmiot2 KodUE + NrVatUE zamiast NIP


@dataclass(frozen=True)
class Pozycja:
    lp: int
    nazwa: str
    indeks: str | None
    jm: str | None
    ilosc: Decimal
    cena: Decimal  # netto albo brutto, zależnie od Faktura.od_brutto
    wartosc: Decimal  # j.w.
    stawka: str
    stan_przed: bool = False  # korekta: wiersz „przed korektą” (StanPrzed = 1); lp = NrWierszaFa oryginału
    kurs: Decimal | None = None  # faktura w walucie obcej: KursWaluty wiersza (TrE_KursL / TrE_KursM)


@dataclass(frozen=True)
class SumaVat:
    """Jeden wiersz TraVat: suma dla stawki."""
    stawka: str
    netto: Decimal
    vat: Decimal
    zaliczka: bool = False  # faktura rozliczająca: wiersz „minus zaliczka” (TrV_Zaliczkowy = 1), ujemny


@dataclass(frozen=True)
class RachunekBankowy:
    nr: str  # NrRB, bez spacji
    swift: str | None = None
    bank: str | None = None
    opis: str | None = None


@dataclass(frozen=True)
class Platnosc:
    terminy: tuple[date, ...]
    forma: int | None  # TFormaPlatnosci: 1 gotówka, 2 karta, 6 przelew, ...; None = PlatnoscInna
    forma_opis: str | None  # OpisPlatnosci, gdy forma = None
    rachunek: RachunekBankowy | None
    nazwa: str = ""  # nazwa formy z XL, np. "Przelew 14 dni" (do opisu „Kwota do zapłaty…”)
    raty: tuple[tuple[date, Decimal], ...] = ()  # (termin, kwota do zapłaty) — tylko gdy niezapłacona
    zaplacono: bool = False  # cała kwota rozliczona (TrP_Pozostaje = 0)
    data_zaplaty: date | None = None  # TrP_DataRozliczenia
    do_zaplaty_calosc: bool = False  # Rozliczenie/DoZaplaty = P_15 (pobranie: należność jeszcze nieuregulowana)


@dataclass(frozen=True)
class FakturaKorygowana:
    """DaneFaKorygowanej — z faktury korygowanej w KSeF (XL nie trzyma jej daty ani numeru KSeF w korekcie)."""
    data_wystawienia: date
    numer: str  # z „(S)”, jak w KSeF
    numer_ksef: str


@dataclass(frozen=True)
class PozycjaZamowienia:
    """ZamowienieWiersz faktury zaliczkowej — pozycja zamówienia (ZamElem), zawsze netto + VAT."""
    lp: int
    nazwa: str
    indeks: str | None
    jm: str | None
    ilosc: Decimal
    cena_netto: Decimal
    netto: Decimal
    vat: Decimal
    stawka: str


@dataclass(frozen=True)
class Zamowienie:
    """Sekcja Zamowienie faktury zaliczkowej (ZAL): wartość brutto całego zamówienia i jego pozycje."""
    wartosc: Decimal
    wiersze: tuple[PozycjaZamowienia, ...]


@dataclass(frozen=True)
class Zaliczka:
    """FakturaZaliczkowa faktury rozliczającej (ROZ)."""
    numer: str  # numer FSL jak w KSeF (bez „(S)”)
    numer_ksef: str | None  # None = FSL wystawiona przed KSeF → NrKSeFZN + NrFaZaliczkowej


@dataclass(frozen=True)
class Faktura:
    xl_gid_typ: int
    xl_gid_numer: int
    numer: str  # P_2
    data_wystawienia: date  # P_1
    data_sprzedazy: date  # P_6 (TrN_Data3), zawsze
    waluta: str
    od_brutto: bool  # TrN_FlagaNB = 'B'
    sprzedawca: Sprzedawca
    nabywca: Nabywca
    pozycje: tuple[Pozycja, ...]
    sumy_vat: tuple[SumaVat, ...]
    platnosc: Platnosc | None = None
    wz: tuple[str, ...] = ()  # numery spiętych WZ
    opis: str | None = None  # opis dokumentu z XL (TrNOpisy)
    zamowienia: tuple[str, ...] = ()  # numery zamówień, np. ZS-58/10/26/B
    korygowana: FakturaKorygowana | None = None  # ustawione → RodzajFaktury KOR
    przyczyna_korekty: str | None = None
    zamowienie: Zamowienie | None = None  # ustawione → RodzajFaktury ZAL
    zaliczki: tuple[Zaliczka, ...] = ()  # niepuste → RodzajFaktury ROZ

    @property
    def rodzaj(self) -> str:
        if self.korygowana:
            return "KOR"
        if self.zamowienie:
            return "ZAL"
        return "ROZ" if self.zaliczki else "VAT"

    @property
    def brutto(self) -> Decimal:
        """P_15 — zawsze z TraVat."""
        return sum((s.netto + s.vat for s in self.sumy_vat), Decimal("0"))
