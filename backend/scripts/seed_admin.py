from pathlib import Path
from app.db import get_conn
from app.auth import hash_password

BASE_DIR = Path(__file__).resolve().parents[1]
MIGRATION = BASE_DIR / "migrations" / "001_init.sql"

EMAIL = "admin@example.com"
PASSWORD = "ChangeMeNow!"
ROLE = "admin"


def ensure_schema():
    sql = MIGRATION.read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)


def seed_admin():
    email = EMAIL.strip().lower()

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if existing:
            print("ℹ️ Admin user already exists — nothing to do.")
            return

        conn.execute(
            "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
            (email, hash_password(PASSWORD), ROLE),
        )
        conn.commit()

    print(f"✅ Admin user created: {email} / {PASSWORD}")


if __name__ == "__main__":
    ensure_schema()
    seed_admin()
