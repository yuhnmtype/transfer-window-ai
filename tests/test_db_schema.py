"""Schema-only checks on the real database. No data values are read.
Skipped when the git-ignored database file is absent (for example on a fresh clone)."""
import sqlite3
from pathlib import Path

import pytest

DB_PATH = Path(__file__).resolve().parent.parent / "transfer_window_ai.db"

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="database file not present")


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def conn():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    yield conn
    conn.close()


def test_players_columns(conn):
    assert {"player_id", "market_value_in_eur", "current_club_id",
            "is_current_squad", "contract_expiration_date"} <= columns(conn, "players")


def test_transfers_columns(conn):
    assert {"player_id", "transfer_date", "from_club_id", "to_club_id",
            "transfer_fee"} <= columns(conn, "transfers")


def test_club_finance_columns(conn):
    assert {"club_id", "wage_bill_gbp_m", "revenue_basis"} <= columns(conn, "club_finance")


def test_meta_keys(conn):
    # Reads key names only, never their values
    keys = {row[0] for row in conn.execute("SELECT key FROM meta")}
    assert {"snapshot_date", "md5_clubs", "md5_players", "md5_transfers",
            "md5_player_valuations"} <= keys
