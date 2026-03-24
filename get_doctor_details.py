import sqlite3

def get_doctor_details():
    try:
        conn = sqlite3.connect('instance/app.sqlite')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        with open('doctors_full.txt', 'w', encoding='utf-8') as f:
            f.write("--- Users (Role: doctor) ---\n")
            cursor.execute("SELECT id, name, email FROM users WHERE role='doctor'")
            doctors = cursor.fetchall()
            
            for doc in doctors:
                f.write(f"User ID: {doc['id']}, Name: {doc['name']}, Email: {doc['email']}\n")
                
                # Check profile
                cursor.execute("SELECT * FROM doctor_profiles WHERE user_id=?", (doc['id'],))
                profile = cursor.fetchone()
                if profile:
                    f.write(f"  Profile ID: {profile['id']}\n")
                    f.write(f"  Specialization: {profile['specialization']}\n")
                    f.write(f"  Experience: {profile['experience_years']}\n")
                    f.write(f"  Qualifications: {profile['qualifications']}\n")
                    f.write(f"  Fee: {profile['consultation_fee']}\n")
                else:
                    f.write("  Profile: NONE\n")
                f.write("-" * 20 + "\n")
            
    except Exception as e:
        with open('doctors_full.txt', 'a', encoding='utf-8') as f:
            f.write(f"Error: {e}\n")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    get_doctor_details()
