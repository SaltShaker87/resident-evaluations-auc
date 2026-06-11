"""
AUC — Last-resort password reset.

Use this only if you lost BOTH your password and your recovery key.

How to use (see SECURITY.md for details):
  1. Stop the app:    systemctl --user stop auc
  2. Run this script: python3 reset_password.py   (from the backend folder)
  3. Start the app:   systemctl --user start auc
  4. Open the app in your browser — it will ask you to create a new password.

Anyone with access to this machine can run this script, so the real
protection for your data is controlling who can log into the computer.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "auc.db"


def main():
    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH} — nothing to reset.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("DELETE FROM auth_config")
        conn.execute("DELETE FROM sessions")
        conn.commit()
    except sqlite3.OperationalError:
        print("Auth tables not found — the app has never been set up. Nothing to reset.")
        return
    finally:
        conn.close()

    print("Password cleared and all sessions logged out.")
    print("Start the app and open it in your browser to create a new password.")


if __name__ == "__main__":
    main()
