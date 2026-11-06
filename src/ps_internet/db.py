"""SQLite state for students, excuses, firewall-rule scope, and parent overrides."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS students (
  student_id TEXT PRIMARY KEY,
  first_name TEXT NOT NULL,
  full_name TEXT,
  public_token TEXT NOT NULL UNIQUE,
  public_enabled INTEGER NOT NULL DEFAULT 0,
  rule_prefix TEXT NOT NULL DEFAULT '',
  threshold_percent TEXT NOT NULL DEFAULT '80',
  grace_days INTEGER NOT NULL DEFAULT 0,
  courses_json TEXT NOT NULL DEFAULT '[]',
  sync_at TEXT,
  sync_error TEXT NOT NULL DEFAULT '',
  rules_error TEXT NOT NULL DEFAULT '',
  rules_refreshed_at TEXT,
  uptime_scoped INTEGER NOT NULL DEFAULT 0,
  present INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
  student_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
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
  updated_at TEXT NOT NULL,
  PRIMARY KEY (student_id, assignment_id)
);
CREATE TABLE IF NOT EXISTS excuses (
  student_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
  excused INTEGER NOT NULL,
  until_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (student_id, assignment_id)
);
CREATE TABLE IF NOT EXISTS rules (
  student_id TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '',
  enabled INTEGER,
  in_scope INTEGER NOT NULL DEFAULT 0,
  use_rules INTEGER NOT NULL DEFAULT 0,
  use_override INTEGER NOT NULL DEFAULT 0,
  when_internet TEXT NOT NULL DEFAULT 'enable',
  present INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (student_id, policy_id)
);
CREATE TABLE IF NOT EXISTS override (
  student_id TEXT PRIMARY KEY,
  active INTEGER NOT NULL DEFAULT 0,
  internet_on INTEGER,
  until_at TEXT,
  restore_json TEXT,
  created_at TEXT
);
"""

LEGACY_STUDENT_ID = "legacy"


def db_path() -> Path:
    return Path(os.environ.get("APP_DB", "data/app.db"))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _migrate_legacy(conn)
    conn.executescript(SCHEMA)
    _migrate_rule_uses(conn)
    _migrate_excuse_until(conn)
    _migrate_grace_days(conn)
    conn.commit()
    return conn


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if found is None:
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _meta_value(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    if "meta" not in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}:
        return default
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """Move the original one-student tables onto a single student row."""
    assignment_cols = _cols(conn, "assignments")
    if assignment_cols and "student_id" not in assignment_cols:
        _migrate_table(conn, "assignments", _assignment_insert)
    rule_cols = _cols(conn, "rules")
    if rule_cols and "student_id" not in rule_cols:
        _migrate_rule_uses(conn)
        _migrate_table(conn, "rules", _rule_insert)
    excuse_cols = _cols(conn, "excuses")
    if excuse_cols and "student_id" not in excuse_cols:
        _migrate_table(conn, "excuses", _excuse_insert)
    override_cols = _cols(conn, "override")
    if override_cols and "student_id" not in override_cols:
        _migrate_override(conn)


def _ensure_legacy_student(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
          student_id TEXT PRIMARY KEY,
          first_name TEXT NOT NULL,
          full_name TEXT,
          public_token TEXT NOT NULL UNIQUE,
          public_enabled INTEGER NOT NULL DEFAULT 0,
          rule_prefix TEXT NOT NULL DEFAULT '',
          threshold_percent TEXT NOT NULL DEFAULT '80',
          grace_days INTEGER NOT NULL DEFAULT 0,
          courses_json TEXT NOT NULL DEFAULT '[]',
          sync_at TEXT,
          sync_error TEXT NOT NULL DEFAULT '',
          rules_error TEXT NOT NULL DEFAULT '',
          rules_refreshed_at TEXT,
          uptime_scoped INTEGER NOT NULL DEFAULT 0,
          present INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL
        )
        """
    )
    if conn.execute("SELECT 1 FROM students WHERE student_id = ?", (LEGACY_STUDENT_ID,)).fetchone():
        return
    name = _meta_value(conn, "student_name", "") or os.environ.get("PS_STUDENT", "") or "Student"
    conn.execute(
        """
        INSERT INTO students (
          student_id, first_name, full_name, public_token, public_enabled, rule_prefix,
          threshold_percent, courses_json, sync_at, sync_error, rules_error, rules_refreshed_at,
          uptime_scoped, present, updated_at
        ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            LEGACY_STUDENT_ID,
            name,
            name,
            secrets.token_urlsafe(18),
            (os.environ.get("UNIFI_RULE_MATCH", "") or "").strip(),
            _meta_value(conn, "threshold_percent", "80") or "80",
            _meta_value(conn, "courses_json", "[]") or "[]",
            _meta_value(conn, "sync_at", "") or None,
            _meta_value(conn, "sync_error", ""),
            _meta_value(conn, "rules_error", ""),
            _meta_value(conn, "rules_refreshed_at", "") or None,
            1 if _meta_value(conn, "uptime_scoped", "") == "1" else 0,
            now(),
        ),
    )


def _migrate_table(conn: sqlite3.Connection, table: str, insert) -> None:
    _ensure_legacy_student(conn)
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
    conn.executescript(SCHEMA)
    for row in rows:
        insert(conn, row)
    conn.execute(f"DROP TABLE {table}_legacy")


def _assignment_insert(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    keys = row.keys()
    conn.execute(
        """
        INSERT INTO assignments (
          student_id, assignment_id, course, name, due_date, store_code, percent, points,
          max_points, missing, exempt, letter, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            LEGACY_STUDENT_ID,
            row["assignment_id"],
            row["course"],
            row["name"],
            row["due_date"],
            row["store_code"],
            row["percent"],
            row["points"],
            row["max_points"],
            row["missing"],
            row["exempt"] if "exempt" in keys else 0,
            row["letter"],
            row["updated_at"],
        ),
    )


def _excuse_insert(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    keys = row.keys()
    conn.execute(
        """
        INSERT INTO excuses (student_id, assignment_id, excused, until_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            LEGACY_STUDENT_ID,
            row["assignment_id"],
            row["excused"],
            row["until_at"] if "until_at" in keys else None,
            row["updated_at"],
        ),
    )


def _migrate_excuse_until(conn: sqlite3.Connection) -> None:
    """A blank end time is the original excuse: it stays until a parent clears it."""
    cols = _cols(conn, "excuses")
    if cols and "until_at" not in cols:
        conn.execute("ALTER TABLE excuses ADD COLUMN until_at TEXT")


def _migrate_grace_days(conn: sqlite3.Connection) -> None:
    cols = _cols(conn, "students")
    if cols and "grace_days" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN grace_days INTEGER NOT NULL DEFAULT 0")


def _rule_insert(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    keys = row.keys()
    conn.execute(
        """
        INSERT INTO rules (
          student_id, policy_id, name, description, action, enabled, in_scope,
          use_rules, use_override, when_internet, present, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            LEGACY_STUDENT_ID,
            row["policy_id"],
            row["name"],
            row["description"] if "description" in keys else "",
            row["action"] if "action" in keys else "",
            row["enabled"],
            row["in_scope"] if "in_scope" in keys else 0,
            row["use_rules"] if "use_rules" in keys else row["in_scope"] if "in_scope" in keys else 0,
            row["use_override"] if "use_override" in keys else row["in_scope"] if "in_scope" in keys else 0,
            row["when_internet"] if "when_internet" in keys else "enable",
            row["present"] if "present" in keys else 1,
            row["updated_at"],
        ),
    )


def _migrate_override(conn: sqlite3.Connection) -> None:
    _ensure_legacy_student(conn)
    row = conn.execute("SELECT * FROM override WHERE id = 1").fetchone()
    conn.execute("ALTER TABLE override RENAME TO override_legacy")
    conn.executescript(SCHEMA)
    if row is not None:
        conn.execute(
            """
            INSERT INTO override (student_id, active, internet_on, until_at, restore_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                LEGACY_STUDENT_ID,
                row["active"],
                row["internet_on"],
                row["until_at"],
                row["restore_json"],
                row["created_at"],
            ),
        )
    conn.execute("DROP TABLE override_legacy")


def _migrate_rule_uses(conn: sqlite3.Connection) -> None:
    """Older databases had one in-scope flag. That flag meant both uses."""
    cols = _cols(conn, "rules")
    if not cols or "student_id" not in cols:
        return
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


def display_timezone() -> str:
    from ps_internet.status import DEFAULT_TIMEZONE, TIMEZONES

    allowed = {key for key, _label in TIMEZONES}
    raw = (get_meta("display_timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE).strip()
    return raw if raw in allowed else DEFAULT_TIMEZONE


def _student(row: sqlite3.Row) -> dict:
    return {
        "student_id": row["student_id"],
        "first_name": row["first_name"],
        "full_name": row["full_name"] or "",
        "public_token": row["public_token"],
        "public_enabled": bool(row["public_enabled"]),
        "rule_prefix": row["rule_prefix"] or "",
        "threshold_percent": row["threshold_percent"] or "80",
        "grace_days": int(row["grace_days"] or 0),
        "courses_json": row["courses_json"] or "[]",
        "sync_at": row["sync_at"],
        "sync_error": row["sync_error"] or "",
        "rules_error": row["rules_error"] or "",
        "rules_refreshed_at": row["rules_refreshed_at"],
        "uptime_scoped": bool(row["uptime_scoped"]),
        "present": bool(row["present"]),
    }


def list_students(present_only: bool = False) -> list[dict]:
    with connect() as conn:
        if present_only:
            rows = conn.execute(
                "SELECT * FROM students WHERE present = 1 ORDER BY first_name, student_id"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM students ORDER BY first_name, student_id").fetchall()
    return [_student(row) for row in rows]


def get_student(student_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
    return _student(row) if row else None


def student_by_token(token: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM students WHERE public_token = ?", (token,)).fetchone()
    return _student(row) if row else None


def ensure_student(student_id: str, first_name: str, full_name: str | None = None) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT student_id FROM students WHERE student_id = ?", (student_id,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO students (
                  student_id, first_name, full_name, public_token, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (student_id, first_name, full_name or "", secrets.token_urlsafe(18), now()),
            )
        else:
            conn.execute(
                """
                UPDATE students
                SET first_name = ?, full_name = COALESCE(NULLIF(?, ''), full_name), present = 1, updated_at = ?
                WHERE student_id = ?
                """,
                (first_name, full_name or "", now(), student_id),
            )
    return get_student(student_id) or {}


def rekey_student(old_id: str, new_id: str) -> None:
    if old_id == new_id:
        return
    with connect() as conn:
        if conn.execute("SELECT 1 FROM students WHERE student_id = ?", (new_id,)).fetchone():
            return
        if conn.execute("SELECT 1 FROM students WHERE student_id = ?", (old_id,)).fetchone() is None:
            return
        for table in ("assignments", "excuses", "rules", "override"):
            conn.execute(
                f"UPDATE {table} SET student_id = ? WHERE student_id = ?",
                (new_id, old_id),
            )
        conn.execute(
            "UPDATE students SET student_id = ?, updated_at = ? WHERE student_id = ?",
            (new_id, now(), old_id),
        )


def mark_students_present(student_ids: set[str]) -> None:
    with connect() as conn:
        conn.execute("UPDATE students SET present = 0")
        conn.executemany(
            "UPDATE students SET present = 1, updated_at = ? WHERE student_id = ?",
            [(now(), student_id) for student_id in student_ids],
        )


def update_student(student_id: str, **fields) -> None:
    allowed = {
        "first_name",
        "full_name",
        "public_enabled",
        "rule_prefix",
        "threshold_percent",
        "grace_days",
        "courses_json",
        "sync_at",
        "sync_error",
        "rules_error",
        "rules_refreshed_at",
        "uptime_scoped",
        "present",
    }
    chosen = {key: value for key, value in fields.items() if key in allowed}
    if not chosen:
        return
    if "public_enabled" in chosen:
        chosen["public_enabled"] = 1 if chosen["public_enabled"] else 0
    if "uptime_scoped" in chosen:
        chosen["uptime_scoped"] = 1 if chosen["uptime_scoped"] else 0
    if "present" in chosen:
        chosen["present"] = 1 if chosen["present"] else 0
    if "grace_days" in chosen:
        chosen["grace_days"] = max(0, int(chosen["grace_days"]))
    assignments = ", ".join(f"{key} = ?" for key in chosen)
    with connect() as conn:
        conn.execute(
            f"UPDATE students SET {assignments}, updated_at = ? WHERE student_id = ?",
            (*chosen.values(), now(), student_id),
        )


def threshold(student_id: str) -> float:
    student = get_student(student_id)
    raw = (student or {}).get("threshold_percent") or "80"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 80.0
    if value <= 0 or value > 100:
        return 80.0
    return value


def replace_assignments(student_id: str, rows: list[dict]) -> None:
    stamped = now()
    with connect() as conn:
        conn.execute("DELETE FROM assignments WHERE student_id = ?", (student_id,))
        conn.executemany(
            """
            INSERT INTO assignments (
              student_id, assignment_id, course, name, due_date, store_code, percent, points,
              max_points, missing, exempt, letter, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    student_id,
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


def list_assignments(student_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM assignments
            WHERE student_id = ?
            ORDER BY due_date, course, name
            """,
            (student_id,),
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


def set_excuse(student_id: str, assignment_id: str, excused: bool, *, days: int | None = None) -> str | None:
    """Hold an assignment out. No day count keeps the original excuse, with no end."""
    until = None
    if excused and days is not None:
        if days < 1 or days > 5:
            raise ValueError("a temporary excuse is 1 to 5 days")
        until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO excuses (student_id, assignment_id, excused, until_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, assignment_id) DO UPDATE SET
              excused = excluded.excused,
              until_at = excluded.until_at,
              updated_at = excluded.updated_at
            """,
            (student_id, assignment_id, 1 if excused else 0, until if excused else None, now()),
        )
    return until


def active_excuses(student_id: str) -> dict[str, str | None]:
    """Assignment ids that are excused right now. A missing end time does not expire."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT assignment_id, until_at FROM excuses
            WHERE student_id = ? AND excused = 1
              AND (until_at IS NULL OR until_at = '' OR until_at > ?)
            """,
            (student_id, now()),
        ).fetchall()
    return {row["assignment_id"]: row["until_at"] or None for row in rows}


def excused_ids(student_id: str) -> set[str]:
    return set(active_excuses(student_id))


def clear_expired_excuses() -> list[str]:
    """Drop temporary excuses whose time is up. Returns the students that changed."""
    stamped = now()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT student_id FROM excuses
            WHERE excused = 1 AND until_at IS NOT NULL AND until_at != '' AND until_at <= ?
            """,
            (stamped,),
        ).fetchall()
        student_ids = [row["student_id"] for row in rows]
        if student_ids:
            conn.execute(
                """
                UPDATE excuses SET excused = 0, until_at = NULL, updated_at = ?
                WHERE excused = 1 AND until_at IS NOT NULL AND until_at != '' AND until_at <= ?
                """,
                (stamped, stamped),
            )
    return student_ids


def observe_rules(student_id: str, rules: list[dict]) -> None:
    stamped = now()
    with connect() as conn:
        conn.execute("UPDATE rules SET present = 0 WHERE student_id = ?", (student_id,))
        for rule in rules:
            conn.execute(
                """
                INSERT INTO rules (
                  student_id, policy_id, name, description, action, enabled, in_scope,
                  when_internet, present, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 'enable', 1, ?)
                ON CONFLICT(student_id, policy_id) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  action = excluded.action,
                  enabled = excluded.enabled,
                  present = 1,
                  updated_at = excluded.updated_at
                """,
                (
                    student_id,
                    rule["id"],
                    rule["name"],
                    rule.get("description") or "",
                    rule.get("action") or "",
                    1 if rule.get("enabled") else 0,
                    stamped,
                ),
            )


def scope_uptime_rules_once(student_id: str) -> None:
    """Fast windows start as both uses. Do this once so a later uncheck stays off."""
    student = get_student(student_id)
    if student is None or student["uptime_scoped"]:
        return
    with connect() as conn:
        conn.execute(
            """
            UPDATE rules
            SET use_rules = 1, use_override = 1
            WHERE student_id = ? AND instr(lower(name), 'uptime') > 0
            """,
            (student_id,),
        )
    update_student(student_id, uptime_scoped=True)


def list_rules(student_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rules WHERE student_id = ? ORDER BY name, policy_id",
            (student_id,),
        ).fetchall()
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


def set_rule_scope(student_id: str, policy_id: str, use_rules: bool, use_override: bool, when_internet: str) -> None:
    if when_internet not in {"enable", "disable"}:
        raise ValueError("when_internet must be enable or disable")
    with connect() as conn:
        found = conn.execute(
            "SELECT policy_id FROM rules WHERE student_id = ? AND policy_id = ?",
            (student_id, policy_id),
        ).fetchone()
        if found is None:
            raise KeyError(policy_id)
        conn.execute(
            """
            UPDATE rules
            SET use_rules = ?, use_override = ?, when_internet = ?, updated_at = ?
            WHERE student_id = ? AND policy_id = ?
            """,
            (1 if use_rules else 0, 1 if use_override else 0, when_internet, now(), student_id, policy_id),
        )


def set_rule_enabled(student_id: str, policy_id: str, enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE rules SET enabled = ?, updated_at = ?
            WHERE student_id = ? AND policy_id = ?
            """,
            (1 if enabled else 0, now(), student_id, policy_id),
        )


def get_override(student_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM override WHERE student_id = ?", (student_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO override (student_id, active) VALUES (?, 0)", (student_id,))
            row = conn.execute("SELECT * FROM override WHERE student_id = ?", (student_id,)).fetchone()
    return {
        "active": bool(row["active"]),
        "internet_on": None if row["internet_on"] is None else bool(row["internet_on"]),
        "until_at": row["until_at"],
        "restore": json.loads(row["restore_json"]) if row["restore_json"] else {},
        "created_at": row["created_at"],
    }


def set_override(student_id: str, internet_on: bool, until_at: str, restore: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO override (student_id, active, internet_on, until_at, restore_json, created_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET
              active = 1,
              internet_on = excluded.internet_on,
              until_at = excluded.until_at,
              restore_json = excluded.restore_json,
              created_at = excluded.created_at
            """,
            (student_id, 1 if internet_on else 0, until_at, json.dumps(restore), now()),
        )


def clear_override(student_id: str) -> dict:
    current = get_override(student_id)
    with connect() as conn:
        conn.execute(
            """
            UPDATE override
            SET active = 0, internet_on = NULL, until_at = NULL, restore_json = NULL
            WHERE student_id = ?
            """,
            (student_id,),
        )
    return current
