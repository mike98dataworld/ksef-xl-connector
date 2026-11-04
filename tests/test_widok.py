"""Widok dla panelu BOK (dbo.v_ksef_dokumenty) daje ten sam JSON co eksport z SSMS."""
from importlib.resources import files

from ksef_connector.source.widok import sql_widoku


def test_plik_widoku_aktualny():
    """Po zmianie faktury_wiele.sql / korekty_wiele.sql: python -m ksef_connector.source.widok > …/widok_ksef_dokumenty.sql"""
    zapisany = files("ksef_connector.source").joinpath("sql/widok_ksef_dokumenty.sql").read_text(encoding="utf-8")
    assert zapisany == sql_widoku()


def test_widok_wiaze_json_z_wierszem_i_nie_uzywa_zmiennych():
    sql = sql_widoku()
    assert "g.GIDNumer" not in sql and "@GIDy" not in sql
    assert sql.count("fs.TrN_GIDNumer = d.TrN_GIDNumer") == 1 and sql.count("k.TrN_GIDNumer = d.TrN_GIDNumer") == 1
    assert "GRANT SELECT ON dbo.v_ksef_dokumenty TO [bq_reader];" in sql
