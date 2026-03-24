#!/usr/bin/env python3
import sqlite3
import os
DB = os.path.join('instance', 'app.sqlite')

print("Opening DB:", DB)
conn = sqlite3.connect(DB)
cur = conn.cursor()

# Safety checks
cur.execute("PRAGMA foreign_keys;")
print("Foreign keys pragma:", cur.fetchone())

# Check if column already exists
cur.execute("PRAGMA table_info(pharmacy_orders);")
cols = [r[1] for r in cur.fetchall()]
print("Existing columns in pharmacy_orders:", cols)

if 'pharmacy_id' in cols:
    print("Column 'pharmacy_id' already exists — nothing to do.")
else:
    print("Adding column 'pharmacy_id' to pharmacy_orders...")
    # SQLite supports ADD COLUMN; FOREIGN KEY constraint cannot be added this way,
    # but the column will be present and can store the shop id (NULL default).
    cur.execute("ALTER TABLE pharmacy_orders ADD COLUMN pharmacy_id INTEGER;")
    conn.commit()
    print("Column added.")

# Optional: show new schema
cur.execute("PRAGMA table_info(pharmacy_orders);")
print("New schema:", cur.fetchall())

conn.close()
print("Done.")

