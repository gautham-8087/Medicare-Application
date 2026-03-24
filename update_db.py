import sqlite3
import os

DB_PATH = os.path.join('instance', 'app.sqlite')

def update_db():
    if not os.path.exists(DB_PATH):
        print("Database not found. Nothing to update.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check and add missing columns
    columns_to_add = {
        "risk_score": "REAL DEFAULT NULL",
        "risk_label": "TEXT DEFAULT NULL",
        "risk_message": "TEXT DEFAULT NULL"
    }

    cursor.execute("PRAGMA table_info(reports)")
    existing_columns = [col[1] for col in cursor.fetchall()]

    for col, col_type in columns_to_add.items():
        if col not in existing_columns:
            cursor.execute(f"ALTER TABLE reports ADD COLUMN {col} {col_type}")
            print(f"Added column: {col}")
        else:
            print(f"Column already exists: {col}")

    # Remove John Doe from patients
    cursor.execute("DELETE FROM patients WHERE name = 'John Doe'")
    deleted_count = cursor.rowcount
    if deleted_count > 0:
        print(f"Removed {deleted_count} record(s) of John Doe from patients table.")
    else:
        print("No John Doe record found in patients table.")

    conn.commit()
    conn.close()
    print("Database update completed.")

if __name__ == "__main__":
    update_db()
