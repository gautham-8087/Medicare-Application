import sqlite3


def check_doctors():
    conn = sqlite3.connect('instance/app.sqlite')
    cursor = conn.cursor()
    
    with open('doctors_list.txt', 'w', encoding='utf-8') as f:
        f.write("--- Users (Role: doctor) ---\n")
        try:
            cursor.execute("SELECT id, name, email FROM users WHERE role='doctor'")
            doctors = cursor.fetchall()
            for doc in doctors:
                f.write(f"ID: {doc[0]}, Name: {doc[1]}, Email: {doc[2]}\n")
                
                # Check profile
                cursor.execute("SELECT * FROM doctor_profiles WHERE user_id=?", (doc[0],))
                profile = cursor.fetchone()
                if profile:
                    f.write(f"  Profile: {profile}\n")
                else:
                    f.write("  Profile: NONE\n")
        except Exception as e:
            f.write(f"Error: {e}\n")
        finally:
            conn.close()

if __name__ == "__main__":
    check_doctors()
