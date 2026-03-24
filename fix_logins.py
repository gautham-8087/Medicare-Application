import sqlite3
import os
from werkzeug.security import generate_password_hash

db_path = 'instance/app.sqlite'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    users_to_add = [
        ('doctor@gmail.com', 'Doctor', 'Doctor@123', 'doctor'),
        ('labtech@gmail.com', 'Labtech', 'labtech@123', 'lab'),
        ('medical@gmail.com', 'Medical Shop', 'medical@123', 'pharmacy')
    ]
    
    for email, name, pwd, role in users_to_add:
        # Check if exists
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        if user:
            print(f"Updating password for {email}...")
            cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pwd), user[0]))
        else:
            print(f"Adding user {email}...")
            cursor.execute("INSERT INTO users (email, name, password_hash, role) VALUES (?, ?, ?, ?)",
                           (email, name, generate_password_hash(pwd), role))
    
    conn.commit()
    conn.close()
    print("Database updated successfully.")
else:
    print(f"Database not found at {db_path}")
