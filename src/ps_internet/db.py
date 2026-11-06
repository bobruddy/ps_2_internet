"""SQLite state for excuses, firewall-rule scope, and the parent override."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
  assignment_id TEXT PRIMARY KEY,
  course TEXT NOT NULL,
  name TEXT NOT NULL,
  due_date TEXT,
  store_code TEXT,
  percent REAL,
  points REAL,
  max_points REAL,
  missing INTEGER NOT NULL,
  exempt INTEGER NOT NULL DEFAULT 0,
  letter TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS excuses (
  assignment_id TEXT PRIMARY KEY,
  excused INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
  policy_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '',
  enabled INTEGER,
  in_scope INTEGER NOT NULL DEFAULT 0,
  use_rules INTEGER NOT NULL DEFAULT 0,
  use_override INTEGER NOT NULL DEFAULT 0,
  when_internet TEXT NOT NULL DEFAULT 'enable',
  present INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS override (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  active INTEGER NOT NULL DEFAULT 0,
  internet_on INTEGER,
  until_at TEXT,
  restore_json TEXT,
  created_at TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("APP_DB", "data/app.db"))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_rule_uses(conn)
    conn.execute("INSERT OR IGNORE INTO override (id, active) VALUES (1, 0)")
    conn.commit()
    return conn


def _migrate_rule_uses(conn: sqlite3.Connection) -> None:
    """Older databases had one in-scope flag. That flag meant both uses."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(rules)")}
    if "use_rules" not in cols:
        conn.execute("ALTER TABLE rules ADD COLUMN use_rules INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE rules SET use_rules = in_scope")
    if "use_override" not in cols:
        conn.execute("ALTER TABLE rules ADD COLUMN use_override INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE rules SET use_override = in_scope")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_meta(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def threshold() -> float:
    raw = get_meta("threshold_percent", "80") or "80"
    try:
        value = float(raw)
    except ValueError:
        return 80.0
    if value <= 0 or value > 100:
        return 80.0
    return value


def replace_assignments(rows: list[dict]) -> None:
    stamped = now()
    with connect() as conn:
        conn.execute("DELETE FROM assignments")
        conn.executemany(
            """
            INSERT INTO assignments (
              assignment_id, course, name, due_date, store_code, percent, points,
              max_points, missing, exempt, letter, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["assignment_id"],
                    row["course"],
                    row["name"],
                    row.get("due_date"),
                    row.get("store_code"),
                    row.get("percent"),
                    row.get("points"),
                    row.get("max_points"),
                    1 if row.get("missing") else 0,
                    1 if row.get("exempt") else 0,
                    row.get("letter"),
                    stamped,
                )
                for row in rows
            ],
        )


def list_assignments() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM assignments ORDER BY due_date, course, name"
        ).fetchall()
    return [_assignment(row) for row in rows]


def _assignment(row: sqlite3.Row) -> dict:
    return {
        "assignment_id": row["assignment_id"],
        "course": row["course"],
        "name": row["name"],
        "due_date": row["due_date"],
        "store_code": row["store_code"],
        "percent": row["percent"],
        "points": row["points"],
        "max_points": row["max_points"],
        "missing": bool(row["missing"]),
        "exempt": bool(row["exempt"]),
        "letter": row["letter"],
    }


def set_excuse(assignment_id: str, excused: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO excuses (assignment_id, excused, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(assignment_id) DO UPDATE SET excused = excluded.excused, updated_at = excluded.updated_at
            """,
            (assignment_id, 1 if excused else 0, now()),
        )


def excused_ids() -> set[str]:
    with connect() as conn:
        rows = conn.execute("SELECT assignment_id FROM excuses WHERE excused = 1").fetchall()
    return {row["assignment_id"] for row in rows}


def observe_rules(rules: list[dict]) -> None:
    stamped = now()
    with connect() as conn:
        conn.execute("UPDATE rules SET present = 0")
        for rule in rules:
            conn.execute(
                """
                INSERT INTO rules (
                  policy_id, name, description, action, enabled, in_scope, when_internet, present, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, 'enable', 1, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  action = excluded.action,
                  enabled = excluded.enabled,
                  present = 1,
                  updated_at = excluded.updated_at
                """,
                (
                    rule["id"],
                    rule["name"],
                    rule.get("description") or "",
                    rule.get("action") or "",
                    1 if rule.get("enabled") else 0,
                    stamped,
                ),
            )


def scope_uptime_rules_once() -> None:
    """Fast windows start as both uses. Do this once so a later uncheck stays off."""
    if get_meta("uptime_scoped") == "1":
        return
    with connect() as conn:
        conn.execute(
            """
            UPDATE rules
            SET use_rules = 1, use_override = 1
            WHERE instr(lower(name), 'uptime') > 0
            """
        )
    set_meta("uptime_scoped", "1")


def list_rules() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM rules ORDER BY name, policy_id").fetchall()
    return [
        {
            "policy_id": row["policy_id"],
            "name": row["name"],
            "description": row["description"],
            "action": row["action"],
            "enabled": None if row["enabled"] is None else bool(row["enabled"]),
            "use_rules": bool(row["use_rules"]),
            "use_override": bool(row["use_override"]),
            "when_internet": row["when_internet"],
            "present": bool(row["present"]),
        }
        for row in rows
    ]


def set_rule_scope(policy_id: str, use_rules: bool, use_override: bool, when_internet: str) -> None:
    if when_internet not in {"enable", "disable"}:
        raise ValueError("when_internet must be enable or disable")
    with connect() as conn:
        found = conn.execute("SELECT policy_id FROM rules WHERE policy_id = ?", (policy_id,)).fetchone()
        if found is None:
            raise KeyError(policy_id)
        conn.execute(
            """
            UPDATE rules
            SET use_rules = ?, use_override = ?, when_internet = ?, updated_at = ?
            WHERE policy_id = ?
            """,
            (1 if use_rules else 0, 1 if use_override else 0, when_internet, now(), policy_id),
        )


def set_rule_enabled(policy_id: str, enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE rules SET enabled = ?, updated_at = ? WHERE policy_id = ?",
            (1 if enabled else 0, now(), policy_id),
        )


def get_override() -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM override WHERE id = 1").fetchone()
    return {
        "active": bool(row["active"]),
        "internet_on": None if row["internet_on"] is None else bool(row["internet_on"]),
        "until_at": row["until_at"],
        "restore": json.loads(row["restore_json"]) if row["restore_json"] else {},
        "created_at": row["created_at"],
    }


def set_override(internet_on: bool, until_at: str, restore: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE override
            SET active = 1, internet_on = ?, until_at = ?, restore_json = ?, created_at = ?
            WHERE id = 1
            """,
            (1 if internet_on else 0, until_at, json.dumps(restore), now()),
        )


def clear_override() -> dict:
    current = get_override()
    with connect() as conn:
        conn.execute(
            "UPDATE override SET active = 0, internet_on = NULL, until_at = NULL, restore_json = NULL WHERE id = 1"
        )
    return current
