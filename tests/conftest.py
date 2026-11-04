"""Testy zawsze na fikcyjnej konfiguracji firmy (firma.example.toml), nawet gdy obok leży prawdziwy firma.toml."""
import os
from pathlib import Path

os.environ["KSEF_FIRMA"] = str(Path(__file__).resolve().parent.parent / "firma.example.toml")
