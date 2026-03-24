import sqlite3
from werkzeug.security import generate_password_hash

def update_doctors():
    db_path = 'instance/app.sqlite'
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        # 1. Remove specific placeholder/test accounts (Cleaning up just in case)
        print("--- Ensuring Clean State ---")
        placeholders = ['Doctor', 'Doctor1', 'V']
        for name in placeholders:
            cur.execute("SELECT id FROM users WHERE name=? AND role='doctor'", (name,))
            rows = cur.fetchall()
            for row in rows:
                doc_id = row[0]
                print(f"Removing {name} (ID: {doc_id})")
                cur.execute("DELETE FROM doctor_profiles WHERE user_id=?", (doc_id,))
                cur.execute("DELETE FROM appointments WHERE doctor_id=?", (doc_id,))
                cur.execute("DELETE FROM users WHERE id=?", (doc_id,))
        
        # Remove Dr. Sanjay Gupta (ID 14) if he exists (as per previous request)
        cur.execute("SELECT id FROM users WHERE name='Dr. Sanjay Gupta'")
        sanjay = cur.fetchone()
        if sanjay:
            print(f"Removing Dr. Sanjay Gupta (ID: {sanjay[0]}) as requested")
            cur.execute("DELETE FROM doctor_profiles WHERE user_id=?", (sanjay[0],))
            cur.execute("DELETE FROM appointments WHERE doctor_id=?", (sanjay[0],))
            cur.execute("DELETE FROM users WHERE id=?", (sanjay[0],))

        conn.commit()

        # 2. Add New Doctors
        print("\n--- Adding/Updating Doctors ---")

        # Define all doctors (Existing + 4 New)
        all_doctors = [
            # Existing verified doctors
            {'name': 'Dr. Anita Roy', 'email': 'anita@example.com', 'spec': 'Cardiologist', 'exp': 12, 'qual': 'MBBS, MD (Cardiology)', 'fee': 1200, 'bio': 'Expert in interventional cardiology and heart failure management.'},
            {'name': 'Dr. Anjali Desai', 'email': 'anjali@example.com', 'spec': 'Neurologist', 'exp': 8, 'qual': 'MBBS, DM (Neurology)', 'fee': 1000, 'bio': 'Specializes in stroke, epilepsy, and headache disorders.'},
            {'name': 'Dr. Rajesh Kumar', 'email': 'rajesh@example.com', 'spec': 'Orthopedic Surgeon', 'exp': 15, 'qual': 'MBBS, MS (Orthopedics)', 'fee': 900, 'bio': 'Senior consultant for joint replacement and sports injuries.'},
            {'name': 'Dr. Priya Sharma', 'email': 'priya@example.com', 'spec': 'Pediatrician', 'exp': 10, 'qual': 'MBBS, MD (Pediatrics)', 'fee': 800, 'bio': 'Compassionate care for newborns, children, and adolescents.'},
            {'name': 'Dr. Vikram Singh', 'email': 'vikram@example.com', 'spec': 'General Physician', 'exp': 20, 'qual': 'MBBS, MD (Internal Medicine)', 'fee': 600, 'bio': 'Experienced in managing chronic diseases like diabetes and hypertension.'},
            
            # 4 NEW DOCTORS + 2 MORE
            {'name': 'Dr. Meera Reddy', 'email': 'meera@example.com', 'spec': 'Dermatologist', 'exp': 9, 'qual': 'MBBS, MD (Dermatology)', 'fee': 850, 'bio': 'Specialist in clinical and cosmetic dermatology and skin care.'},
            {'name': 'Dr. Arun Patil', 'email': 'arun@example.com', 'spec': 'ENT Specialist', 'exp': 14, 'qual': 'MBBS, MS (ENT)', 'fee': 750, 'bio': 'Expert in ear, nose, and throat disorders and head/neck surgery.'},
            {'name': 'Dr. Suresh Nair', 'email': 'suresh@example.com', 'spec': 'Psychiatrist', 'exp': 11, 'qual': 'MBBS, MD (Psychiatry)', 'fee': 1100, 'bio': 'Compassionate mental health care specializing in anxiety and depression.'},
            {'name': 'Dr. Kavita Singh', 'email': 'kavita@example.com', 'spec': 'Gynecologist', 'exp': 16, 'qual': 'MBBS, MS (Obs & Gyn)', 'fee': 950, 'bio': 'Comprehensive women\'s health care, pregnancy, and reproductive health.'},
            # 2 ADDITIONAL
            {'name': 'Dr. Neha Gupta', 'email': 'neha@example.com', 'spec': 'Endocrinologist', 'exp': 13, 'qual': 'MBBS, MD, DM (Endocrinology)', 'fee': 900, 'bio': 'Expert in diabetes, thyroid disorders, and hormonal imbalances.'},
            {'name': 'Dr. Arjun Malhotra', 'email': 'arunm@example.com', 'spec': 'Pulmonologist', 'exp': 15, 'qual': 'MBBS, MD, DM (Pulmonology)', 'fee': 1000, 'bio': 'Specializes in asthma, COPD, and respiratory infections.'}
        ]

        # Department mapping
        cur.execute("SELECT id, name FROM departments")
        dept_rows = cur.fetchall()
        dept_map = {row[1]: row[0] for row in dept_rows}
        
        # Ensure we have department IDs for new specs (or map to closest)
        spec_to_dept = {
            'Cardiologist': 'Cardiology',
            'Neurologist': 'Neurology', 
            'Orthopedic Surgeon': 'Orthopedics',
            'Pediatrician': 'Pediatrics',
            'General Physician': 'General Medicine',
            'Dermatologist': 'Dermatology',
            'ENT Specialist': 'ENT',
            'Psychiatrist': 'Psychiatry',
            'Gynecologist': 'Gynecology',
            'Endocrinologist': 'Endocrinology',
            'Pulmonologist': 'Pulmonology'
        }

        # Make sure these departments exist, if not create them (optional, but good for completeness)
        # For this script, we'll map new ones to 'General Medicine' if not found, OR verify they exist.
        # Let's check available departments first.
        print(f"Available Departments: {list(dept_map.keys())}")

        for doc in all_doctors:
            print(f"Processing {doc['name']}...")
            
            # 1. Ensure User Exists
            cur.execute("SELECT id FROM users WHERE email=?", (doc['email'],))
            user_row = cur.fetchone()
            
            if not user_row:
                print(f"  Creating user account for {doc['name']}")
                hashed_pw = generate_password_hash('doctor123') # Default password
                cur.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'doctor')",
                           (doc['name'], doc['email'], hashed_pw))
                user_id = cur.lastrowid
            else:
                user_id = user_row[0]
                # Update name if changed
                cur.execute("UPDATE users SET name=? WHERE id=?", (doc['name'], user_id))

            # 2. Determine Department ID
            target_dept = spec_to_dept.get(doc['spec'])
            dept_id = dept_map.get(target_dept)
            if not dept_id:
                # Fallback or create? Let's map to General Medicine or create a generic one if strictly needed.
                # Since we can't easily modify departments table structure/content blindly, let's map to IDs we know usually exist or 1.
                # Assuming 'General Medicine' exists -> ID 1 typically.
                dept_id = dept_map.get('General Medicine', 1) 
                print(f"  Warning: Department '{target_dept}' not found. Mapped to ID {dept_id}")

            # 3. Update/Create Profile
            cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (user_id,))
            profile = cur.fetchone()

            if profile:
                cur.execute("""
                    UPDATE doctor_profiles 
                    SET specialization=?, experience_years=?, qualifications=?, consultation_fee=?, bio=?, department_id=?
                    WHERE user_id=?
                """, (doc['spec'], doc['exp'], doc['qual'], doc['fee'], doc['bio'], dept_id, user_id))
                print(f"  Updated profile.")
            else:
                cur.execute("""
                    INSERT INTO doctor_profiles (user_id, specialization, experience_years, qualifications, consultation_fee, bio, department_id, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Mon,Tue,Wed,Thu,Fri')
                """, (user_id, doc['spec'], doc['exp'], doc['qual'], doc['fee'], doc['bio'], dept_id))
                print(f"  Created new profile.")

        conn.commit()
        print("\nAll updates completed successfully.")

    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    update_doctors()
