import os
import sqlite3
from functools import wraps
from datetime import datetime, timedelta
import random
import time 

from flask import (
    Flask, g, render_template, request, redirect, url_for, flash,
    session, send_file, abort, jsonify
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
DB_PATH = os.path.join('instance', 'app.sqlite')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('instance', exist_ok=True)

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['DATABASE'] = DB_PATH


import psycopg2
from psycopg2.extras import DictCursor
from psycopg2 import sql
import re

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        # Get connection string from environment
        conn_str = os.environ.get('SUPABASE_DATABASE_URL')
        if not conn_str:
            raise RuntimeError("SUPABASE_DATABASE_URL is not set in environment.")
        
        # Connect to Postgres
        raw_conn = psycopg2.connect(conn_str)
        # Enable autocommit for simpler emulation (if desired, but we keep commit() manual to match existing app)
        
        class SQLiteCursorSimulator:
            def __init__(self, raw_cursor):
                self._cursor = raw_cursor
                self.lastrowid = None

            def execute(self, query, args=None):
                # Replace SQLite ? with Postgres %s, but only if not inside quotes
                # A simple regex for ? -> %s. This is rudimentary but works for most queries in this app
                pg_query = query.replace('?', '%s')
                
                # Check if it's an INSERT to simulate lastrowid
                is_insert = pg_query.strip().upper().startswith('INSERT')
                
                if is_insert and 'RETURNING' not in pg_query.upper():
                    # Only tables with 'id' column support RETURNING id
                    # patients table uses patient_id as PK, not id
                    insert_into_match = re.search(r'INSERT\s+INTO\s+(\w+)', pg_query, re.IGNORECASE)
                    table_name = insert_into_match.group(1).lower() if insert_into_match else ''
                    if table_name != 'patients':
                        pg_query += ' RETURNING id'
                
                if args:
                    self._cursor.execute(pg_query, args)
                else:
                    self._cursor.execute(pg_query)
                
                if is_insert:
                    try:
                        self.lastrowid = self._cursor.fetchone()['id']
                    except Exception:
                        self.lastrowid = None

                return self

            def executescript(self, script):
                # We need to manually translate some SQLite-isms to Postgres
                pg_script = script.replace('AUTOINCREMENT', 'SERIAL')
                pg_script = pg_script.replace('INTEGER PRIMARY KEY SERIAL', 'SERIAL PRIMARY KEY')
                pg_script = pg_script.replace('TEXT CHECK', 'TEXT CHECK') # No change needed
                self._cursor.execute(pg_script)
                return self

            def fetchone(self):
                return self._cursor.fetchone()

            def fetchall(self):
                return self._cursor.fetchall()

            def fetchmany(self, size):
                return self._cursor.fetchmany(size)

            def close(self):
                self._cursor.close()

        class SQLiteConnSimulator:
            def __init__(self, raw_conn):
                self._conn = raw_conn
                # SQLite executes directly on conn sometimes
                self._cursor = self._conn.cursor(cursor_factory=DictCursor)

            def cursor(self):
                return SQLiteCursorSimulator(self._conn.cursor(cursor_factory=DictCursor))

            def execute(self, query, args=None):
                cur = self.cursor()
                return cur.execute(query, args)

            def commit(self):
                self._conn.commit()

            def rollback(self):
                self._conn.rollback()

            def close(self):
                self._conn.close()

        db = g._database = SQLiteConnSimulator(raw_conn)
    return db

def init_db():
    """
    Creates all required tables. Safe to run repeatedly on same DB (IF NOT EXISTS).
    """
    db = get_db()
    cur = db.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        name TEXT,
        password_hash TEXT,
        role TEXT CHECK(role IN ('doctor','lab','pharmacy','admin','receptionist','nurse')) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS patients (
        patient_id TEXT PRIMARY KEY,
        name TEXT,
        email TEXT,
        dob TEXT,
        view_password_hash TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT,
        uploaded_by INTEGER,
        filename TEXT,
        report_type TEXT,
        notes TEXT,
        risk_score REAL DEFAULT NULL,
        risk_label TEXT DEFAULT NULL,
        risk_message TEXT DEFAULT NULL,
        upload_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(uploaded_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS prescriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER NOT NULL,
        patient_id TEXT NOT NULL,
        report_id INTEGER DEFAULT NULL,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(doctor_id) REFERENCES users(id),
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(report_id) REFERENCES reports(id)
    );

    CREATE TABLE IF NOT EXISTS prescription_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prescription_id INTEGER NOT NULL,
        medicine_name TEXT NOT NULL,
        dose TEXT,
        quantity INTEGER DEFAULT 1,
        times_of_day TEXT,
        meal_timing TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(prescription_id) REFERENCES prescriptions(id)
    );

    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER,
        patient_id TEXT,
        report_id INTEGER,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS pharmacies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER DEFAULT NULL,
        name TEXT NOT NULL,
        contact TEXT,
        address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS pharmacy_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prescription_id INTEGER NOT NULL,
        created_by INTEGER NOT NULL,
        pharmacy_id INTEGER DEFAULT NULL,
        shop_info TEXT DEFAULT NULL,
        status TEXT DEFAULT 'sent',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(prescription_id) REFERENCES prescriptions(id),
        FOREIGN KEY(created_by) REFERENCES users(id),
        FOREIGN KEY(pharmacy_id) REFERENCES pharmacies(id)
    );

    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        icon TEXT,
        head_doctor_id INTEGER DEFAULT NULL,
        contact TEXT,
        floor_location TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(head_doctor_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS doctor_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        specialization TEXT,
        qualifications TEXT,
        experience_years INTEGER,
        department_id INTEGER DEFAULT NULL,
        consultation_fee REAL DEFAULT 0,
        bio TEXT,
        available_days TEXT,
        available_time_start TEXT,
        available_time_end TEXT,
        max_patients_per_day INTEGER DEFAULT 20,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(department_id) REFERENCES departments(id)
    );

    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        doctor_id INTEGER NOT NULL,
        appointment_date TEXT NOT NULL,
        appointment_time TEXT NOT NULL,
        status TEXT DEFAULT 'scheduled' CHECK(status IN ('scheduled','confirmed','cancelled','completed','no-show')),
        reason TEXT,
        notes TEXT,
        created_by TEXT DEFAULT 'patient',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(doctor_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS medical_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        doctor_id INTEGER NOT NULL,
        appointment_id INTEGER DEFAULT NULL,
        diagnosis TEXT,
        symptoms TEXT,
        treatment TEXT,
        vital_signs TEXT,
        record_date TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(doctor_id) REFERENCES users(id),
        FOREIGN KEY(appointment_id) REFERENCES appointments(id)
    );

    CREATE TABLE IF NOT EXISTS billing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        appointment_id INTEGER DEFAULT NULL,
        prescription_id INTEGER DEFAULT NULL,
        amount REAL NOT NULL,
        description TEXT,
        payment_status TEXT DEFAULT 'pending' CHECK(payment_status IN ('pending','paid','cancelled')),
        payment_method TEXT,
        paid_at TIMESTAMP DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(appointment_id) REFERENCES appointments(id),
        FOREIGN KEY(prescription_id) REFERENCES prescriptions(id)
    );

    CREATE TABLE IF NOT EXISTS medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT,
        manufacturer TEXT,
        price REAL DEFAULT 0,
        stock_quantity INTEGER DEFAULT 0,
        reorder_level INTEGER DEFAULT 10,
        description TEXT,
        side_effects TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS test_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_name TEXT NOT NULL,
        category TEXT,
        price REAL DEFAULT 0,
        preparation_required TEXT,
        sample_type TEXT,
        report_time TEXT,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS suggested_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER NOT NULL,
        patient_id TEXT NOT NULL,
        test_id INTEGER NOT NULL,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(doctor_id) REFERENCES users(id),
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(test_id) REFERENCES test_catalog(id)
    );

    CREATE TABLE IF NOT EXISTS lab_bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        test_id INTEGER NOT NULL,
        booking_date TEXT NOT NULL,
        booking_time TEXT,
        status TEXT DEFAULT 'scheduled' CHECK(status IN ('scheduled','sample_collected','processing','completed','cancelled')),
        report_id INTEGER DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(test_id) REFERENCES test_catalog(id),
        FOREIGN KEY(report_id) REFERENCES reports(id)
    );

    CREATE TABLE IF NOT EXISTS hospital_info (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT 'MediCare+ Hospital',
        tagline TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        emergency_contact TEXT,
        operating_hours TEXT,
        website TEXT,
        about_text TEXT,
        mission TEXT,
        vision TEXT,
        total_beds INTEGER DEFAULT 0,
        ambulance_count INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS testimonials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_name TEXT NOT NULL,
        patient_id TEXT DEFAULT NULL,
        rating INTEGER CHECK(rating >= 1 AND rating <= 5),
        comment TEXT,
        treatment_type TEXT,
        is_approved INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER DEFAULT NULL,
        patient_id TEXT DEFAULT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        type TEXT DEFAULT 'info',
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
    );

    CREATE TABLE IF NOT EXISTS doctor_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        doctor_id INTEGER NOT NULL,
        appointment_id INTEGER,
        rating INTEGER CHECK(rating >= 1 AND rating <= 5),
        message TEXT,
        medicine_feedback TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY(doctor_id) REFERENCES users(id),
        FOREIGN KEY(appointment_id) REFERENCES appointments(id)
    );
    ''')
    db.commit()

    # Migration: add meal_timing to prescription_items (for existing DBs)
    try:
        cur.execute("ALTER TABLE prescription_items ADD COLUMN meal_timing TEXT")
        db.commit()
    except Exception:
        db.rollback()

    # Migration: create suggested_tests if not exists (Postgres: run CREATE TABLE)
    try:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS suggested_tests (
                id SERIAL PRIMARY KEY,
                doctor_id INTEGER NOT NULL,
                patient_id TEXT NOT NULL,
                test_id INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(doctor_id) REFERENCES users(id),
                FOREIGN KEY(patient_id) REFERENCES patients(patient_id),
                FOREIGN KEY(test_id) REFERENCES test_catalog(id)
            )
        ''')
        db.commit()
    except Exception:
        db.rollback()

    # sample accounts if none exist
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO users (email,name,password_hash,role) VALUES (?,?,?,?)",
                    ('doc@example.com','Dr. Alice', generate_password_hash('docpass'), 'doctor'))
        cur.execute("INSERT INTO users (email,name,password_hash,role) VALUES (?,?,?,?)",
                    ('lab@example.com','Technician Bob', generate_password_hash('labpass'), 'lab'))
        cur.execute("INSERT INTO users (email,name,password_hash,role) VALUES (?,?,?,?)",
                    ('admin@example.com','Admin User', generate_password_hash('adminpass'), 'admin'))
        db.commit()

    # Ensure Dr. Anjali exists
    cur.execute("SELECT id FROM users WHERE email='anjali@example.com'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (email,name,password_hash,role) VALUES (?,?,?,?)",
                    ('anjali@example.com','Dr. Anjali Desai', generate_password_hash('pass123'), 'doctor'))
        db.commit()


    # 1. Rename Dr. Alice to Dr. Anita Roy (if exists)
    cur.execute("UPDATE users SET name='Dr. Anita Roy' WHERE email='doc@example.com' AND name='Dr. Alice'")
    db.commit()

    # Ensure other doctors exist (Total 6)
    new_docs = [
        ('rajesh@example.com', 'Dr. Rajesh Kumar', 'pass123'),
        ('priya@example.com', 'Dr. Priya Sharma', 'pass123'),
        ('vikram@example.com', 'Dr. Vikram Singh', 'pass123'),
        ('sanjay@example.com', 'Dr. Sanjay Gupta', 'pass123')
    ]
    for email, name, pwd in new_docs:
        cur.execute("SELECT id FROM users WHERE email=?", (email,))
        if not cur.fetchone():
            cur.execute("INSERT INTO users (email,name,password_hash,role) VALUES (?,?,?,?)",
                        (email, name, generate_password_hash(pwd), 'doctor'))
    db.commit()

    # Initialize hospital info if not exists
    cur.execute("SELECT COUNT(*) FROM hospital_info")
    if cur.fetchone()[0] == 0:
        cur.execute("""INSERT INTO hospital_info 
            (name, tagline, phone, email, address, emergency_contact, operating_hours, about_text, mission, vision, total_beds, ambulance_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ('MediCare+ Hospital', 
             'Your Health, Our Priority',
             '+91 98765 43210',
             'info@medicareplus.com',
             '123, Apollo Road, Jubilee Hills, Hyderabad, Telangana 500033',
             '108 or +91 98765 43210',
             'Mon-Fri: 8AM-8PM, Sat-Sun: 9AM-5PM, Emergency: 24/7',
             'MediCare+ Hospital is a leading healthcare provider committed to delivering exceptional patient care with compassion and excellence.',
             'To provide world-class healthcare services that are accessible, affordable, and patient-centered.',
             'To be the most trusted healthcare institution, setting new standards in medical excellence and patient satisfaction.',
             250, 5))
        db.commit()

    # Initialize departments if not exists
    cur.execute("SELECT COUNT(*) FROM departments")
    if cur.fetchone()[0] == 0:
        departments_data = [
            ('Cardiology', 'Heart and cardiovascular care', 'fas fa-heartbeat', '+91 98765 43211', 'Floor 3'),
            ('Neurology', 'Brain and nervous system treatment', 'fas fa-brain', '+91 98765 43212', 'Floor 4'),
            ('Orthopedics', 'Bone and joint care', 'fas fa-x-ray', '+91 98765 43213', 'Floor 2'),
            ('Pediatrics', 'Healthcare for children', 'fas fa-baby', '+91 98765 43214', 'Floor 1'),
            ('Emergency', '24/7 emergency medical services', 'fas fa-ambulance', '108', 'Ground Floor'),
            ('Laboratory', 'Diagnostic testing and pathology', 'fas fa-vial', '+91 98765 43215', 'Floor 1'),
            ('Radiology', 'Medical imaging services', 'fas fa-radiation', '+91 98765 43216', 'Floor 1'),
            ('General Medicine', 'General healthcare and checkups', 'fas fa-stethoscope', '+91 98765 43217', 'Floor 2')
        ]
        for dept in departments_data:
            cur.execute("INSERT INTO departments (name, description, icon, contact, floor_location) VALUES (?,?,?,?,?)", dept)
        db.commit()

    # Initialize doctor profiles
    cur.execute("SELECT COUNT(*) FROM doctor_profiles")
    if cur.fetchone()[0] == 0:
        # 1. Update/Add proper profiles for existing Dr. Alice (Cardiology)
        cur.execute("SELECT id FROM users WHERE email='doc@example.com'")
        alice_row = cur.fetchone()
        
        cur.execute("SELECT id FROM departments WHERE name='Cardiology'")
        cardio_row = cur.fetchone()

        if alice_row and cardio_row:
             # Ensure profile uses 'Cardiologist' and updated Bio
             cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (alice_row[0],))
             if not cur.fetchone():
                 cur.execute("""INSERT INTO doctor_profiles
                    (user_id, specialization, qualifications, experience_years, department_id, consultation_fee, bio, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (alice_row[0], 'Cardiologist', 'MBBS, MD (Cardiology)', 12, cardio_row[0], 800, 
                     'Dr. Anita is a leading cardiologist with over a decade of experience in treating complex heart conditions.', 
                     'Mon,Tue,Wed,Thu,Fri'))
             else:
                 # Update bio if still using old one
                 cur.execute("""UPDATE doctor_profiles 
                              SET bio='Dr. Anita is a leading cardiologist with over a decade of experience in treating complex heart conditions.'
                              WHERE user_id=?""", (alice_row[0],))

        # 2. Add Dr. Anjali Desai (Neurology)
        cur.execute("SELECT id FROM users WHERE email='anjali@example.com'")
        anjali_row = cur.fetchone()
        cur.execute("SELECT id FROM departments WHERE name='Neurology'")
        neuro_row = cur.fetchone()

        if anjali_row and neuro_row:
             # Check if profile already exists for Anjali (just in case)
             cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (anjali_row[0],))
             if not cur.fetchone():
                 cur.execute("""INSERT INTO doctor_profiles
                    (user_id, specialization, qualifications, experience_years, department_id, consultation_fee, bio, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (anjali_row[0], 'Neurologist', 'MBBS, DM (Neurology)', 8, neuro_row[0], 700, 
                     'Dr. Anjali handles complex neurological cases and specializes in stroke management.', 
                     'Mon,Wed,Fri'))

        # 3. Add Dr. Rajesh Kumar (Orthopedics)
        cur.execute("SELECT id FROM users WHERE email='rajesh@example.com'")
        rajesh_row = cur.fetchone()
        cur.execute("SELECT id FROM departments WHERE name='Orthopedics'")
        ortho_row = cur.fetchone()
        if rajesh_row and ortho_row:
             cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (rajesh_row[0],))
             if not cur.fetchone():
                 cur.execute("""INSERT INTO doctor_profiles
                    (user_id, specialization, qualifications, experience_years, department_id, consultation_fee, bio, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rajesh_row[0], 'Orthopedic Surgeon', 'MBBS, MS (Orthopedics)', 15, ortho_row[0], 900, 
                     'Dr. Rajesh is an expert in joint replacement surgeries and sports injuries.', 
                     'Tue,Thu,Sat'))

        # 4. Add Dr. Priya Sharma (Pediatrics)
        cur.execute("SELECT id FROM users WHERE email='priya@example.com'")
        priya_row = cur.fetchone()
        cur.execute("SELECT id FROM departments WHERE name='Pediatrics'")
        pedia_row = cur.fetchone()
        if priya_row and pedia_row:
             cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (priya_row[0],))
             if not cur.fetchone():
                 cur.execute("""INSERT INTO doctor_profiles
                    (user_id, specialization, qualifications, experience_years, department_id, consultation_fee, bio, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (priya_row[0], 'Pediatrician', 'MBBS, MD (Pediatrics)', 10, pedia_row[0], 600, 
                     'Dr. Priya loves children and provides comprehensive care from infancy to adolescence.', 
                     'Mon,Tue,Wed,Thu,Fri,Sat'))

        # 5. Add Dr. Vikram Singh (General Medicine)
        cur.execute("SELECT id FROM users WHERE email='vikram@example.com'")
        vikram_row = cur.fetchone()
        cur.execute("SELECT id FROM departments WHERE name='General Medicine'")
        gen_row = cur.fetchone()
        if vikram_row and gen_row:
             cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (vikram_row[0],))
             if not cur.fetchone():
                 cur.execute("""INSERT INTO doctor_profiles
                    (user_id, specialization, qualifications, experience_years, department_id, consultation_fee, bio, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (vikram_row[0], 'General Physician', 'MBBS, MD (Internal Medicine)', 20, gen_row[0], 500, 
                     'Dr. Vikram has 20 years of experience in diagnosing and treating chronic illnesses.', 
                     'Mon,Tue,Wed,Thu,Fri'))

        # 6. Add Dr. Sanjay Gupta (Radiology)
        cur.execute("SELECT id FROM users WHERE email='sanjay@example.com'")
        sanjay_row = cur.fetchone()
        cur.execute("SELECT id FROM departments WHERE name='Radiology'")
        radio_row = cur.fetchone()
        if sanjay_row and radio_row:
             cur.execute("SELECT id FROM doctor_profiles WHERE user_id=?", (sanjay_row[0],))
             if not cur.fetchone():
                 cur.execute("""INSERT INTO doctor_profiles
                    (user_id, specialization, qualifications, experience_years, department_id, consultation_fee, bio, available_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sanjay_row[0], 'Radiologist', 'MBBS, MD (Radiodiagnosis)', 14, radio_row[0], 750, 
                     'Dr. Sanjay specializes in advanced medical imaging and interventional radiology.', 
                     'Mon,Tue,Wed,Sat'))

        db.commit()

    # Initialize sample medicines
    cur.execute("SELECT COUNT(*) FROM medicines")
    if cur.fetchone()[0] == 0:
        medicines_data = [
            ('Paracetamol 500mg', 'Pain Relief', 'PharmaCorp', 2.50, 500, 50, 'Common pain and fever relief', 'Nausea, allergic reactions (rare)'),
            ('Amoxicillin 250mg', 'Antibiotic', 'MediLabs', 5.00, 300, 40, 'Bacterial infection treatment', 'Diarrhea, nausea, rash'),
            ('Metformin 500mg', 'Diabetes', 'DiabCare Inc', 8.00, 200, 30, 'Type 2 diabetes management', 'Stomach upset, diarrhea'),
            ('Lisinopril 10mg', 'Blood Pressure', 'CardioMed', 12.00, 150, 25, 'High blood pressure treatment', 'Dizziness, dry cough'),
            ('Aspirin 75mg', 'Blood Thinner', 'PharmaCorp', 3.00, 400, 50, 'Heart health and blood thinning', 'Stomach irritation, bleeding risk'),
        ]
        for med in medicines_data:
            cur.execute("INSERT INTO medicines (name, category, manufacturer, price, stock_quantity, reorder_level, description, side_effects) VALUES (?,?,?,?,?,?,?,?)", med)
        db.commit()

    # Initialize test catalog
    cur.execute("SELECT COUNT(*) FROM test_catalog")
    if cur.fetchone()[0] == 0:
        tests_data = [
            ('Complete Blood Count (CBC)', 'Hematology', 450.00, 'Fasting not required', 'Blood', '24 hours', 'Comprehensive blood cell analysis'),
            ('Lipid Profile', 'Biochemistry', 850.00, '12 hour fasting required', 'Blood', '24 hours', 'Cholesterol and triglycerides analysis'),
            ('HbA1c Test', 'Diabetes', 600.00, 'Fasting not required', 'Blood', '48 hours', 'Long-term blood sugar control assessment'),
            ('Liver Function Test', 'Biochemistry', 700.00, 'Fasting preferred', 'Blood', '24 hours', 'Liver health assessment'),
            ('Kidney Function Test', 'Biochemistry', 650.00, 'Fasting not required', 'Blood', '24 hours', 'Kidney health markers'),
            ('Thyroid Profile', 'Endocrinology', 550.00, 'Morning sample preferred', 'Blood', '48 hours', 'Thyroid hormone levels'),
            ('X-Ray Chest', 'Radiology', 500.00, 'No preparation needed', 'Imaging', '2 hours', 'Chest and lung imaging'),
            ('Urine Routine', 'Clinical Pathology', 250.00, 'Morning sample preferred', 'Urine', '12 hours', 'Urine analysis for infections and abnormalities'),
            ('Vitamin D (25-OH)', 'Biochemistry', 1200.00, 'Fasting not required', 'Blood', '48 hours', 'Assessment of Vitamin D levels for bone health'),
            ('Vitamin B12', 'Biochemistry', 900.00, 'Overnight fasting preferred', 'Blood', '24 hours', 'Checks for B12 deficiency and nerve health'),
            ('Blood Glucose (Fasting)', 'Diabetes', 150.00, '8-10 hours fasting required', 'Blood', '12 hours', 'Measures blood sugar level after fasting'),
            ('Electrolyte Panel', 'Biochemistry', 500.00, 'Fasting not required', 'Blood', '24 hours', 'Measures sodium, potassium, and chloride levels'),
            ('C-Reactive Protein (CRP)', 'Immunology', 550.00, 'Fasting not required', 'Blood', '12 hours', 'Marker for inflammation in the body'),
            ('Malaria Parasite (MP)', 'Microbiology', 300.00, 'Fasting not required', 'Blood', '6 hours', 'Detection of malaria parasites in blood'),
            ('Widal Test', 'Microbiology', 400.00, 'Fasting not required', 'Blood', '24 hours', 'Traditional test for Typhoid fever'),
            ('Iron Studies', 'Biochemistry', 1500.00, '12 hour fasting required', 'Blood', '24 hours', 'Assessment of iron levels and storage'),
        ]
        for test in tests_data:
            cur.execute("SELECT id FROM test_catalog WHERE test_name=?", (test[0],))
            if not cur.fetchone():
                cur.execute("INSERT INTO test_catalog (test_name, category, price, preparation_required, sample_type, report_time, description) VALUES (?,?,?,?,?,?,?)", test)
        db.commit()

        # Update prices for existing records to ensure they reflect the new professional rates
        updates = [
            (450.00, 'Complete Blood Count (CBC)'),
            (850.00, 'Lipid Profile'),
            (600.00, 'HbA1c Test'),
            (700.00, 'Liver Function Test'),
            (650.00, 'Kidney Function Test'),
            (550.00, 'Thyroid Profile'),
            (500.00, 'X-Ray Chest'),
            (250.00, 'Urine Routine')
        ]
        for price, name in updates:
            cur.execute("UPDATE test_catalog SET price=? WHERE test_name=?", (price, name))
        db.commit()

    # Migration: Add medicine_feedback column to doctor_feedback if not exists
    try:
        db.execute("ALTER TABLE doctor_feedback ADD COLUMN medicine_feedback TEXT")
        db.commit()
    except Exception as e:
        # Catch generic exception and rollback so postgres connection isn't aborted
        db.rollback()


@app.teardown_appcontext
def close_conn(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ---------- Utility ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def roles_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator

# ---------- Sugar Prediction ----------
def predict_risk_from_report(file_path):
    score = round(random.uniform(0, 1), 2)
    if score < 0.3:
        label = "Low Sugar"
        message = "Low sugar: Consult doctor"
    elif score < 0.7:
        label = "Normal Sugar"
        message = "Normal sugar"
    else:
        label = "High Sugar"
        message = "High sugar: Consult doctor"
    return score, label, message

@app.route('/api/hospital-info')
def api_hospital_info():
    db = get_db()
    hospital = db.execute("SELECT * FROM hospital_info LIMIT 1").fetchone()
    departments = db.execute("SELECT * FROM departments LIMIT 6").fetchall()
    testimonials = db.execute("SELECT * FROM testimonials WHERE is_approved = 1 ORDER BY created_at DESC LIMIT 6").fetchall()
    
    return jsonify({
        'hospital': dict(hospital) if hospital else None,
        'departments': [dict(d) for d in departments],
        'testimonials': [dict(t) for t in testimonials]
    })

@app.route('/')
def index():
    db = get_db()
    # Get hospital info
    hospital = db.execute("SELECT * FROM hospital_info LIMIT 1").fetchone()
    # Get departments for display
    departments = db.execute("SELECT * FROM departments LIMIT 6").fetchall()
    # Get approved testimonials
    testimonials = db.execute("SELECT * FROM testimonials WHERE is_approved = 1 ORDER BY created_at DESC LIMIT 6").fetchall()
    
    return render_template('index.html', 
                         hospital=dict(hospital) if hospital else None,
                         departments=[dict(d) for d in departments],
                         testimonials=[dict(t) for t in testimonials])

@app.route('/about')
def about():
    db = get_db()
    hospital = db.execute("SELECT * FROM hospital_info LIMIT 1").fetchone()
    return render_template('about.html', hospital=dict(hospital) if hospital else None)

@app.route('/departments')
def departments_page():
    db = get_db()
    departments = db.execute("""
        SELECT d.*, COUNT(DISTINCT dp.id) as doctor_count
        FROM departments d
        LEFT JOIN doctor_profiles dp ON d.id = dp.department_id
        GROUP BY d.id
        ORDER BY d.name
    """).fetchall()
    return render_template('departments.html', departments=[dict(d) for d in departments])

@app.route('/services')
def services():
    db = get_db()
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    tests = db.execute("SELECT * FROM test_catalog ORDER BY category, test_name").fetchall()
    return render_template('services.html', 
                         departments=[dict(d) for d in departments],
                         tests=[dict(t) for t in tests])

@app.route('/contact')
def contact():
    db = get_db()
    hospital = db.execute("SELECT * FROM hospital_info LIMIT 1").fetchone()
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    return render_template('contact.html', 
                         hospital=dict(hospital) if hospital else None,
                         departments=[dict(d) for d in departments])

@app.route('/help')
def help_page():
    return render_template('help.html')

# ========== NEW API ENDPOINTS ==========

@app.route('/api/departments')
def api_departments():
    db = get_db()
    departments = db.execute("""
        SELECT d.*, COUNT(DISTINCT dp.id) as doctor_count
        FROM departments d
        LEFT JOIN doctor_profiles dp ON d.id = dp.department_id
        GROUP BY d.id
        ORDER BY d.name
    """).fetchall()
    return jsonify([dict(d) for d in departments])

@app.route('/api/tests')
def api_tests():
    db = get_db()
    tests = db.execute("SELECT * FROM test_catalog ORDER BY category, test_name").fetchall()
    return jsonify([dict(t) for t in tests])

# ========== NEW PROFESSIONAL FEATURES ==========

@app.route('/api/doctors')
def api_doctors():
    db = get_db()
    doctors = db.execute("""
        SELECT u.id, u.name, u.email,
               dp.specialization, dp.qualifications, dp.experience_years,
               dp.department_id, dp.consultation_fee, dp.bio,
               dp.available_days, dp.available_time_start, dp.available_time_end,
               d.name as department_name
        FROM users u
        LEFT JOIN doctor_profiles dp ON u.id = dp.user_id
        LEFT JOIN departments d ON dp.department_id = d.id
        WHERE u.role = 'doctor'
        ORDER BY u.name
    """).fetchall()
    return jsonify([dict(row) for row in doctors])

@app.route('/doctors')
def doctor_directory():
    db = get_db()
    # Get all doctors with their profiles
    doctors = db.execute("""
        SELECT u.id, u.name, u.email,
               dp.specialization, dp.qualifications, dp.experience_years,
               dp.department_id, dp.consultation_fee, dp.bio,
               dp.available_days, dp.available_time_start, dp.available_time_end
        FROM users u
        LEFT JOIN doctor_profiles dp ON u.id = dp.user_id
        WHERE u.role = 'doctor'
        ORDER BY u.name
    """).fetchall()
    
    # Get departments for filter
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    
    return render_template('doctor_directory.html',
                         doctors=[dict(d) for d in doctors][:-2] if len(doctors) > 2 else [dict(d) for d in doctors],
                         departments=[dict(d) for d in departments])

@app.route('/doctor/<int:doctor_id>')
def doctor_profile(doctor_id):
    db = get_db()
    
    # Get doctor info
    doctor = db.execute("SELECT * FROM users WHERE id = ? AND role = 'doctor'", (doctor_id,)).fetchone()
    if not doctor:
        flash('Doctor not found.', 'danger')
        return redirect(url_for('doctor_directory'))
    
    # Get doctor profile
    profile = db.execute("SELECT * FROM doctor_profiles WHERE user_id = ?", (doctor_id,)).fetchone()
    
    # Get department info if exists
    department = None
    if profile and profile['department_id']:
        department = db.execute("SELECT * FROM departments WHERE id = ?", (profile['department_id'],)).fetchone()
    
    return render_template('doctor_detail.html',
                         doctor=dict(doctor),
                         profile=dict(profile) if profile else {},
                         department=dict(department) if department else None)

@app.route('/api/health-packages')
def api_health_packages():
    """Health checkup packages API"""
    packages = [
        {
            'id': 1,
            'name': 'Basic Health Checkup',
            'price': 99,
            'tests': ['Complete Blood Count', 'Blood Sugar', 'Blood Pressure', 'Basic Urine Test'],
            'duration': '2 hours',
            'description': 'Essential health screening for routine checkup'
        },
        {
            'id': 2,
            'name': 'Comprehensive Health Package',
            'price': 299,
            'tests': ['CBC', 'Lipid Profile', 'Liver Function', 'Kidney Function', 'Thyroid Profile', 'HbA1c', 'ECG'],
            'duration': '4 hours',
            'description': 'Complete health assessment with detailed analysis'
        },
        {
            'id': 3,
            'name': 'Senior Citizen Package',
            'price': 399,
            'tests': ['All Comprehensive tests', 'Bone Density', 'Vitamin D', 'B12', 'Chest X-Ray', 'Cardio Consultation'],
            'duration': '5 hours',
            'description': 'Specially designed for seniors aged 60+'
        },
        {
            'id': 4,
            'name': 'Diabetes Care Package',
            'price': 199,
            'tests': ['HbA1c', 'Fasting Sugar', 'PP Sugar', 'Lipid Profile', 'Kidney Function', 'Eye Examination'],
            'duration': '3 hours',
            'description': 'Comprehensive diabetes monitoring and management'
        },
        {
            'id': 5,
            'name': 'Women Wellness Package',
            'price': 349,
            'tests': ['Pap Smear', 'Mammogram', 'Thyroid', 'Iron Studies', 'Vitamin D', 'Bone Density', 'Gynecology Consultation'],
            'duration': '4 hours',
            'description': 'Complete health checkup for women'
        },
        {
            'id': 6,
            'name': 'Heart Health Package',
            'price': 279,
            'tests': ['ECG', '2D Echo', 'Lipid Profile', 'Cardiac Enzymes', 'Stress Test', 'Cardiology Consultation'],
            'duration': '3 hours',
            'description': 'Advanced cardiac assessment'
        }
    ]
    return jsonify(packages)

@app.route('/health-packages')
def health_packages():
    """Health checkup packages page"""
    packages = [
        {
            'id': 1,
            'name': 'Basic Health Checkup',
            'price': 99,
            'tests': ['Complete Blood Count', 'Blood Sugar', 'Blood Pressure', 'Basic Urine Test'],
            'duration': '2 hours',
            'description': 'Essential health screening for routine checkup'
        },
        {
            'id': 2,
            'name': 'Comprehensive Health Package',
            'price': 299,
            'tests': ['CBC', 'Lipid Profile', 'Liver Function', 'Kidney Function', 'Thyroid Profile', 'HbA1c', 'ECG'],
            'duration': '4 hours',
            'description': 'Complete health assessment with detailed analysis'
        },
        {
            'id': 3,
            'name': 'Senior Citizen Package',
            'price': 399,
            'tests': ['All Comprehensive tests', 'Bone Density', 'Vitamin D', 'B12', 'Chest X-Ray', 'Cardio Consultation'],
            'duration': '5 hours',
            'description': 'Specially designed for seniors aged 60+'
        },
        {
            'id': 4,
            'name': 'Diabetes Care Package',
            'price': 49,
            'tests': ['Complete Blood Count', 'Blood Sugar (Fasting)', 'Cholesterol', 'Urine Analysis', 'BP Check'],
            'duration': '2 hours',
            'description': 'Essential health screening for all ages'
        },
        {
            'id': 2,
            'name': 'Comprehensive Body Check',
            'price': 99,
            'tests': ['CBC', 'Liver Function Test', 'Kidney Function Test', 'Lipid Profile', 'Thyroid Profile', 'ECG', 'Chest X-Ray'],
            'duration': '4 hours',
            'description': 'Head-to-toe evaluation of your health'
        },
        {
            'id': 3,
            'name': 'Heart Care Package',
            'price': 149,
            'tests': ['ECG', 'Echocardiogram', 'Lipid Profile', 'Cardiac Markers', 'Cardiologist Consultation'],
            'duration': '3 hours',
            'description': 'Specialized checkup for cardiac health'
        },
        {
            'id': 4,
            'name': 'Diabetes Management',
            'price': 79,
            'tests': ['HbA1c', 'Blood Sugar (F/PP)', 'Lipid Profile', 'Kidney Function', 'Eye Checkup', 'Dietician Consultation'],
            'duration': '3 hours',
            'description': 'Monitoring package for diabetic patients'
        },
        {
             'id': 5,
             'name': 'Women Wellness',
             'price': 129,
             'tests': ['CBC', 'Thyroid Profile', 'Pap Smear', 'Mammogram (if >40)', 'Gynecologist Consultation', 'Bone Density'],
             'duration': '4 hours',
             'description': 'Complete health checkup for women'
        },
        {
            'id': 6,
            'name': 'Heart Health Package',
            'price': 279,
            'tests': ['ECG', '2D Echo', 'Lipid Profile', 'Cardiac Enzymes', 'Stress Test', 'Cardiology Consultation'],
            'duration': '3 hours',
            'description': 'Advanced cardiac assessment'
        }
    ]
    return render_template('health_packages.html', packages=packages)



@app.route('/emergency')
def emergency_services():
    """Emergency services and ambulance booking"""
    db = get_db()
    hospital = db.execute("SELECT * FROM hospital_info LIMIT 1").fetchone()
    return render_template('emergency_services.html', hospital=dict(hospital) if hospital else None)

@app.route('/api/emergency')
def api_emergency():
    db = get_db()
    hospital = db.execute("SELECT * FROM hospital_info LIMIT 1").fetchone()
    return jsonify(dict(hospital) if hospital else {})

@app.route('/api/news')
def api_news():
    """Health news API"""
    news_items = [
        {
            'id': 1,
            'title': 'New Advanced MRI Machine Installed',
            'date': '2026-02-01',
            'category': 'Technology',
            'excerpt': 'MediCare+ Hospital has installed a state-of-the-art 3T MRI machine for better diagnostic accuracy.',
            'image': 'news1.jpg'
        },
        {
            'id': 2,
            'title': '5 Tips for Managing Diabetes',
            'date': '2026-01-28',
            'category': 'Health Tips',
            'excerpt': 'Learn essential tips for maintaining healthy blood sugar levels and preventing complications.',
            'image': 'news2.jpg'
        },
        {
            'id': 3,
            'title': 'Free Health Camp This Weekend',
            'date': '2026-01-25',
            'category': 'Events',
            'excerpt': 'Join us for a free health screening camp on Saturday and Sunday from 9 AM to 5 PM.',
            'image': 'news3.jpg'
        },
        {
            'id': 4,
            'title': 'Heart Health: Prevention is Better than Cure',
            'date': '2026-01-20',
            'category': 'Health Tips',
            'excerpt': 'Understanding cardiovascular health and simple lifestyle changes to protect your heart.',
            'image': 'news4.jpg'
        }
    ]
    return jsonify(news_items)

@app.route('/news')
def news():
    """Health news and tips"""
    news_items = [
        {
            'id': 1,
            'title': 'New Advanced MRI Machine Installed',
            'date': '2026-02-01',
            'category': 'Technology',
            'excerpt': 'MediCare+ Hospital has installed a state-of-the-art 3T MRI machine for better diagnostic accuracy.',
            'image': 'news1.jpg'
        },
        {
            'id': 2,
            'title': '5 Tips for Managing Diabetes',
            'date': '2026-01-28',
            'category': 'Health Tips',
            'excerpt': 'Learn essential tips for maintaining healthy blood sugar levels and preventing complications.',
            'image': 'news2.jpg'
        },
        {
            'id': 3,
            'title': 'Free Health Camp This Weekend',
            'date': '2026-01-25',
            'category': 'Events',
            'excerpt': 'Join us for a free health screening camp on Saturday and Sunday from 9 AM to 5 PM.',
            'image': 'news3.jpg'
        },
        {
            'id': 4,
            'title': 'Heart Health: Prevention is Better than Cure',
            'date': '2026-01-20',
            'category': 'Health Tips',
            'excerpt': 'Understanding cardiovascular health and simple lifestyle changes to protect your heart.',
            'image': 'news4.jpg'
        }
    ]
    return render_template('news.html', news=news_items)

@app.route('/gallery')
def gallery():
    """Hospital photo gallery"""
    gallery_images = [
        {'id': 1, 'title': 'Main Reception', 'category': 'Facility', 'image': 'reception.jpg'},
        {'id': 2, 'title': 'ICU Ward', 'category': 'Facility', 'image': 'icu.jpg'},
        {'id': 3, 'title': 'Operation Theater', 'category': 'Facility', 'image': 'ot.jpg'},
        {'id': 4, 'title': 'Patient Rooms', 'category': 'Facility', 'image': 'room.jpg'},
        {'id': 5, 'title': 'Laboratory', 'category': 'Facility', 'image': 'lab.jpg'},
        {'id': 6, 'title': 'Medical Equipment', 'category': 'Equipment', 'image': 'equipment.jpg'},
    ]
    return render_template('gallery.html', images=gallery_images)

@app.route('/careers')
def careers():
    """Careers and job openings"""
    job_openings = [
        {
            'id': 1,
            'title': 'Senior Cardiologist',
            'department': 'Cardiology',
            'type': 'Full-time',
            'experience': '10+ years',
            'posted': '2026-01-30'
        },
        {
            'id': 2,
            'title': 'Registered Nurse',
            'department': 'General Ward',
            'type': 'Full-time',
            'experience': '2-5 years',
            'posted': '2026-01-28'
        },
        {
            'id': 3,
            'title': 'Lab Technician',
            'department': 'Laboratory',
            'type': 'Full-time',
            'experience': '1-3 years',
            'posted': '2026-01-25'
        },
        {
            'id': 4,
            'title': 'Pharmacist',
            'department': 'Pharmacy',
            'type': 'Full-time',
            'experience': '3+ years',
            'posted': '2026-01-20'
        }
    ]
    return render_template('careers.html', jobs=job_openings)

@app.route('/insurance')
def insurance():
    """Insurance partners information"""
    insurance_partners = [
        {'name': 'Blue Cross Blue Shield', 'logo': 'bcbs.png', 'accepted': True},
        {'name': 'United Healthcare', 'logo': 'uhc.png', 'accepted': True},
        {'name': 'Aetna', 'logo': 'aetna.png', 'accepted': True},
        {'name': 'Cigna', 'logo': 'cigna.png', 'accepted': True},
        {'name': 'Medicare', 'logo': 'medicare.png', 'accepted': True},
        {'name': 'Medicaid', 'logo': 'medicaid.png', 'accepted': True},
    ]
    return render_template('insurance.html', partners=insurance_partners)

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        password = request.form['password']
        role = request.form['role']

        if role not in ('doctor', 'lab', 'pharmacy', 'admin', 'receptionist', 'nurse'):
            flash('Please select a valid role.', 'danger')
            return render_template('signup.html')

        db = get_db()
        try:
            cur = db.cursor()
            cur.execute(
                "INSERT INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
                (email, email.split('@')[0].capitalize(), generate_password_hash(password), role)
            )
            user_id = cur.lastrowid

            if role == 'pharmacy':
                shop_name = request.form.get('shop_name', '').strip() or email.split('@')[0].capitalize()
                contact = request.form.get('contact', '').strip() or None
                address = request.form.get('address', '').strip() or None
                cur.execute(
                    "INSERT INTO pharmacies (user_id, name, contact, address) VALUES (?,?,?,?)",
                    (user_id, shop_name, contact, address)
                )

            db.commit()
            flash('Account created successfully. You can now log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            db.rollback()
            flash('Email already exists.', 'danger')
            return render_template('signup.html')

    return render_template('signup.html')

@app.route('/api/auth/signup', methods=['POST'])
def api_signup():
    data = request.json
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
        
    email = data.get('email', '').lower().strip()
    password = data.get('password')
    role = data.get('role')

    if not email or not password or not role:
        return jsonify({'success': False, 'message': 'Missing required fields'}), 400

    if role not in ('doctor', 'lab', 'pharmacy', 'admin', 'receptionist', 'nurse'):
        return jsonify({'success': False, 'message': 'Invalid role'}), 400

    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
            (email, email.split('@')[0].capitalize(), generate_password_hash(password), role)
        )
        user_id = cur.lastrowid

        if role == 'pharmacy':
            shop_name = data.get('shop_name', '').strip() or email.split('@')[0].capitalize()
            contact = data.get('contact', '').strip() or None
            address = data.get('address', '').strip() or None
            cur.execute(
                "INSERT INTO pharmacies (user_id, name, contact, address) VALUES (?,?,?,?)",
                (user_id, shop_name, contact, address)
            )

        db.commit()
        return jsonify({'success': True, 'message': 'Account created successfully'})
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({'success': False, 'message': 'Email already exists'}), 409
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
        
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'success': False, 'message': 'Email and password required'}), 400
        
    db = get_db()
    
    # Check regular users (email)
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    if user:
        if check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['name'] = user['name']
            session['user_name'] = user['name']
            
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'name': user['name'],
                    'role': user['role']
                }
            })
        else:
            return jsonify({'success': False, 'message': 'Invalid password for user'}), 401
            
    # Check patients (patient_id)
    # email field is used for patient_id in login form if generic
    patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (email.upper(),)).fetchone()
    
    if patient:
        if check_password_hash(patient['view_password_hash'], password):
            session.clear()
            session['user_id'] = patient['patient_id']
            # Important: role must be 'patient' for frontend checks
            session['role'] = 'patient' 
            session['name'] = patient['name']
            session['user_name'] = patient['name']
            
            return jsonify({
                'success': True,
                'user': {
                    'id': patient['patient_id'],
                    'name': patient['name'],
                    'role': 'patient'
                }
            })
        else:
            return jsonify({'success': False, 'message': 'Invalid password for patient'}), 401
            
    return jsonify({'success': False, 'message': 'User not found'}), 404

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/status')
def api_auth_status():
    if 'user_id' in session:
        return jsonify({
            'authenticated': True,
            'user': {
                'id': session['user_id'],
                'name': session.get('name'),
                'role': session.get('role')
            }
        })
    return jsonify({'authenticated': False})

@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        db = get_db()
        error = None
        
        # Check regular users
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        
        if user:
            if check_password_hash(user['password_hash'], password):
                session.clear()
                session['user_id'] = user['id']
                session['role'] = user['role']
                session['name'] = user['name']
                session['user_name'] = user['name']
                
                # Redirect based on role
                if user['role'] == 'admin':
                    return redirect(url_for('admin_dashboard'))
                elif user['role'] == 'doctor':
                    return redirect(url_for('doctor_dashboard'))
                elif user['role'] == 'pharmacy':
                    return redirect(url_for('pharmacy_dashboard'))
                elif user['role'] == 'lab':
                    return redirect(url_for('lab_dashboard'))
                else:
                    return redirect(url_for('index'))
            else:
                flash('Invalid password for existing user.', 'danger')
                
        else:
             # Check patients
            patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (email,)).fetchone()
            if patient:
                if check_password_hash(patient['view_password_hash'], password):
                    session.clear()
                    session['user_id'] = patient['patient_id']
                    session['role'] = 'patient'
                    session['name'] = patient['name']
                    return redirect(url_for('patient_dashboard'))
                else:
                    flash('Invalid password for patient.', 'danger')
            else:
                flash('User not found. Please check your email/Patient ID.', 'danger')
                
    return render_template('login.html')



@app.context_processor
def inject_now():
    return {'datetime': datetime, 'current_year': datetime.utcnow().year}

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

# Profile Management
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('logout'))
    
    # Get pharmacy details if user is pharmacy
    pharmacy = None
    if session.get('role') == 'pharmacy':
        pharmacy = db.execute("SELECT * FROM pharmacies WHERE user_id = ?", (session['user_id'],)).fetchone()
    
    # Get activity stats
    stats = {}
    if session.get('role') == 'doctor':
        # Count patients created by this doctor
        stats['total_patients'] = db.execute(
            "SELECT COUNT(DISTINCT patient_id) FROM prescriptions WHERE doctor_id = ?",
            (session['user_id'],)
        ).fetchone()[0]
        stats['total_prescriptions'] = db.execute(
            "SELECT COUNT(*) FROM prescriptions WHERE doctor_id = ?",
            (session['user_id'],)
        ).fetchone()[0]
    elif session.get('role') == 'lab':
        stats['total_reports'] = db.execute(
            "SELECT COUNT(*) FROM reports WHERE uploaded_by = ?",
            (session['user_id'],)
        ).fetchone()[0]
    elif session.get('role') == 'pharmacy':
        if pharmacy:
            stats['total_orders'] = db.execute(
                "SELECT COUNT(*) FROM pharmacy_orders WHERE pharmacy_id = ?",
                (pharmacy['id'],)
            ).fetchone()[0]
            stats['delivered_orders'] = db.execute(
                "SELECT COUNT(*) FROM pharmacy_orders WHERE pharmacy_id = ? AND status = 'delivered'",
                (pharmacy['id'],)
            ).fetchone()[0]
    
    return render_template('profile.html', user=dict(user), pharmacy=dict(pharmacy) if pharmacy else None, stats=stats)

@app.route('/profile/update', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    
    if not name or not email:
        flash('Name and email are required.', 'danger')
        return redirect(url_for('profile'))
    
    db = get_db()
    try:
        db.execute(
            "UPDATE users SET name = ?, email = ? WHERE id = ?",
            (name, email, session['user_id'])
        )
        db.commit()
        session['user_name'] = name
        flash('Profile updated successfully.', 'success')
    except sqlite3.IntegrityError:
        flash('Email already exists.', 'danger')
    
    return redirect(url_for('profile'))

@app.route('/profile/change-password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_new_password = request.form.get('confirm_new_password', '')
    
    if not current_password or not new_password or not confirm_new_password:
        flash('All password fields are required.', 'danger')
        return redirect(url_for('profile'))
    
    if new_password != confirm_new_password:
        flash('New passwords do not match.', 'danger')
        return redirect(url_for('profile'))
    
    if len(new_password) < 6:
        flash('New password must be at least 6 characters long.', 'danger')
        return redirect(url_for('profile'))
    
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    if not user or not check_password_hash(user['password_hash'], current_password):
        flash('Current password is incorrect.', 'danger')
        return redirect(url_for('profile'))
    
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), session['user_id'])
    )
    db.commit()
    flash('Password changed successfully.', 'success')
    return redirect(url_for('profile'))

# Doctor creates patient
@app.route('/create_patient', methods=['GET', 'POST'])
@roles_required('doctor')
def create_patient():
    db = get_db()
    if request.method == 'POST':
        patient_id = request.form['patient_id'].strip().upper()
        name = request.form['name'].strip()
        email = request.form.get('email', '').strip()
        dob = request.form['dob'].strip()
        password = request.form['password'].strip()

        try:
            db.execute(
                "INSERT INTO patients (patient_id, name, email, dob, view_password_hash) VALUES (?,?,?,?,?)",
                (patient_id, name, email, dob, generate_password_hash(password))
            )
            db.commit()
            flash(f'Patient {patient_id} created successfully. You can now add a prescription.', 'success')
            return redirect(url_for('create_prescription', patient_id=patient_id))
        except sqlite3.IntegrityError:
            flash('Patient ID already exists.', 'danger')
        return redirect(url_for('create_patient'))
    return render_template('create_patient.html')

# Delete patient (doctor only)
@app.route('/delete_patient/<patient_id>', methods=['POST'])
@roles_required('doctor')
def delete_patient(patient_id):
    db = get_db()
    patient_id = patient_id.strip().upper()
    
    # Check if patient exists
    patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
    if not patient:
        flash('Patient not found.', 'danger')
        return redirect(url_for('doctor_dashboard'))
    
    try:
        # Delete all related data (cascade delete)
        # 1. Get all prescription IDs for this patient
        prescription_ids = db.execute(
            "SELECT id FROM prescriptions WHERE patient_id = ?", 
            (patient_id,)
        ).fetchall()
        
        # 2. Delete prescription items for each prescription
        for presc in prescription_ids:
            db.execute("DELETE FROM prescription_items WHERE prescription_id = ?", (presc['id'],))
        
        # 3. Delete pharmacy orders for prescriptions
        for presc in prescription_ids:
            db.execute("DELETE FROM pharmacy_orders WHERE prescription_id = ?", (presc['id'],))
        
        # 4. Delete prescriptions
        db.execute("DELETE FROM prescriptions WHERE patient_id = ?", (patient_id,))
        
        # 5. Delete suggestions
        db.execute("DELETE FROM suggestions WHERE patient_id = ?", (patient_id,))
        
        # 6. Delete reports
        db.execute("DELETE FROM reports WHERE patient_id = ?", (patient_id,))
        
        # 7. Finally, delete the patient
        db.execute("DELETE FROM patients WHERE patient_id = ?", (patient_id,))
        
        db.commit()
        flash(f'Patient {patient_id} and all associated data deleted successfully.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error deleting patient: {str(e)}', 'danger')
    
    return redirect(url_for('doctor_dashboard'))


# Optional pharmacy registration page (alternate)
@app.route('/register_pharmacy', methods=['GET', 'POST'])
def register_pharmacy():
    db = get_db()
    if request.method == 'POST':
        email = request.form['email'].lower().strip()
        password = request.form['password']
        shop_name = request.form['shop_name'].strip()
        contact = request.form.get('contact', '').strip()
        address = request.form.get('address', '').strip()

        try:
            cur = db.cursor()
            cur.execute(
                "INSERT INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
                (email, shop_name, generate_password_hash(password), 'pharmacy')
            )
            user_id = cur.lastrowid
            cur.execute(
                "INSERT INTO pharmacies (user_id, name, contact, address) VALUES (?,?,?,?)",
                (user_id, shop_name, contact, address)
            )
            db.commit()
            flash('Pharmacy account created. You can now log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already exists.', 'danger')

    return render_template('register_pharmacy.html')

# Pharmacy dashboard (active + delivered) with safe dict conversion
@app.route('/api/dashboard/pharmacy')
@roles_required('pharmacy')
def api_pharmacy_dashboard():
    db = get_db()
    pharmacy = db.execute("SELECT * FROM pharmacies WHERE user_id = ?", (session['user_id'],)).fetchone()
    if not pharmacy:
        return jsonify({'error': 'Pharmacy profile not found'}), 404

    active_orders = db.execute('''
        SELECT o.id as order_id, o.created_at, o.status, o.shop_info,
               pr.id as prescription_id, pr.patient_id, p.name as patient_name,
               pr.content as prescription_notes, u.name as doctor_name
        FROM pharmacy_orders o
        JOIN prescriptions pr ON o.prescription_id = pr.id
        JOIN users u ON pr.doctor_id = u.id
        LEFT JOIN patients p ON pr.patient_id = p.patient_id
        WHERE o.pharmacy_id = ? AND o.status != 'delivered'
        ORDER BY o.created_at DESC
    ''', (pharmacy['id'],)).fetchall()

    delivered_orders = db.execute('''
        SELECT o.id as order_id, o.created_at, o.status, o.shop_info,
               pr.id as prescription_id, pr.patient_id, p.name as patient_name,
               pr.content as prescription_notes, u.name as doctor_name
        FROM pharmacy_orders o
        JOIN prescriptions pr ON o.prescription_id = pr.id
        JOIN users u ON pr.doctor_id = u.id
        LEFT JOIN patients p ON pr.patient_id = p.patient_id
        WHERE o.pharmacy_id = ? AND o.status = 'delivered'
        ORDER BY o.created_at DESC
    ''', (pharmacy['id'],)).fetchall()

    def build_order_data(orders):
        data = []
        for o in orders:
            items = db.execute('''
                SELECT medicine_name, dose, quantity, times_of_day, meal_timing
                FROM prescription_items
                WHERE prescription_id = ?
            ''', (o['order_id'] if 'order_id' in o.keys() else o['id'],)).fetchall() # o is row, keys are accessable
            # Actually o is sqlite3.Row, need to be careful. query selects o.id as order_id.
            # prescription_id is available in query result.
            items = db.execute('''
                SELECT medicine_name, dose, quantity, times_of_day, meal_timing
                FROM prescription_items
                WHERE prescription_id = ?
            ''', (o['prescription_id'],)).fetchall()
            
            data.append({
                'order': dict(o),
                'medicine_items': [dict(it) for it in items]
            })
        return data

    return jsonify({
        'pharmacy': dict(pharmacy),
        'active_orders': build_order_data(active_orders),
        'delivered_orders': build_order_data(delivered_orders)
    })

@app.route('/pharmacy_dashboard')
@roles_required('pharmacy')
def pharmacy_dashboard():
    db = get_db()

    # Get pharmacy info for logged-in user
    pharmacy = db.execute(
        "SELECT * FROM pharmacies WHERE user_id = ?",
        (session['user_id'],)
    ).fetchone()

    if not pharmacy:
        flash('No pharmacy profile found for this account.', 'danger')
        return redirect(url_for('index'))

    # Active orders (not delivered)
    active_orders = db.execute('''
        SELECT o.id as order_id, o.created_at, o.status, o.shop_info,
               pr.id as prescription_id, pr.patient_id, p.name as patient_name,
               pr.content as prescription_notes, u.name as doctor_name
        FROM pharmacy_orders o
        JOIN prescriptions pr ON o.prescription_id = pr.id
        JOIN users u ON pr.doctor_id = u.id
        LEFT JOIN patients p ON pr.patient_id = p.patient_id
        WHERE o.pharmacy_id = ? AND o.status != 'delivered'
        ORDER BY o.created_at DESC
    ''', (pharmacy['id'],)).fetchall()

    # Delivered (history)
    delivered_orders = db.execute('''
        SELECT o.id as order_id, o.created_at, o.status, o.shop_info,
               pr.id as prescription_id, pr.patient_id, p.name as patient_name,
               pr.content as prescription_notes, u.name as doctor_name
        FROM pharmacy_orders o
        JOIN prescriptions pr ON o.prescription_id = pr.id
        JOIN users u ON pr.doctor_id = u.id
        LEFT JOIN patients p ON pr.patient_id = p.patient_id
        WHERE o.pharmacy_id = ? AND o.status = 'delivered'
        ORDER BY o.created_at DESC
    ''', (pharmacy['id'],)).fetchall()

    def build_order_data(orders):
        data = []
        for o in orders:
            items = db.execute('''
                SELECT medicine_name, dose, quantity, times_of_day, meal_timing
                FROM prescription_items
                WHERE prescription_id = ?
            ''', (o['prescription_id'],)).fetchall()
            data.append({
                'order': dict(o),
                'medicine_items': [dict(it) for it in items]
            })
        return data

    active_data = build_order_data(active_orders)
    delivered_data = build_order_data(delivered_orders)

    return render_template('pharmacy_dashboard.html', pharmacy=dict(pharmacy), active_orders=active_data, delivered_orders=delivered_data)

# Update order status (pharmacy only)
@app.route('/pharmacy/order/<int:order_id>/status/<status>', methods=['GET','POST'])
@roles_required('pharmacy')
def update_order_status(order_id, status):
    ALLOWED = {'sent', 'accepted', 'delivered', 'cancelled'}
    status = status.lower()
    if status not in ALLOWED:
        flash('Invalid status.', 'danger')
        return redirect(url_for('pharmacy_dashboard'))

    db = get_db()
    pharmacy = db.execute("SELECT * FROM pharmacies WHERE user_id = ?", (session['user_id'],)).fetchone()
    if not pharmacy:
        flash('No pharmacy profile found for this account.', 'danger')
        return redirect(url_for('index'))

    order = db.execute("SELECT * FROM pharmacy_orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        flash('Order not found.', 'danger')
        return redirect(url_for('pharmacy_dashboard'))

    # Ownership check
    if order['pharmacy_id'] is None or int(order['pharmacy_id']) != int(pharmacy['id']):
        flash('You are not authorized to update this order.', 'danger')
        return redirect(url_for('pharmacy_dashboard'))

    db.execute("UPDATE pharmacy_orders SET status = ? WHERE id = ?", (status, order_id))
    db.commit()
    flash(f'Order #{order_id} status updated to {status}.', 'success')
    return redirect(url_for('pharmacy_dashboard'))

# Prescription creation (doctor)
@app.route('/prescription/create', methods=['GET', 'POST'])
@roles_required('doctor')
def create_prescription():
    db = get_db()
    # allow GET or POST for patient_id and optional report_id
    report_id = request.values.get('report_id') or None
    patient_id = request.values.get('patient_id', '').strip().upper()

    if request.method == 'POST':
        patient_id = request.form['patient_id'].strip().upper()
        content = request.form.get('content', '').strip()
        med_names = request.form.getlist('med_name[]')
        doses = request.form.getlist('dose[]')
        quantities = request.form.getlist('quantity[]')
        times = request.form.getlist('times[]')
        meal_timings = request.form.getlist('meal_timing[]')
        send_to_pharmacy = request.form.get('send_to_pharmacy') == 'on'
        pharmacy_id = request.form.get('pharmacy_id') or None
        if pharmacy_id == '':
            pharmacy_id = None
        shop_info = request.form.get('shop_info') or None
        report_id = request.form.get('report_id') or report_id

        # validation
        if not patient_id:
            flash('Patient ID is required.', 'danger')
            return redirect(url_for('create_prescription', patient_id=patient_id))

        patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        if not patient:
            flash('Patient ID not found. Create patient first.', 'danger')
            return redirect(url_for('create_patient'))

        cur = db.cursor()
        # insert prescription (with report link if provided)
        if report_id:
            cur.execute(
                "INSERT INTO prescriptions (doctor_id, patient_id, report_id, content) VALUES (?,?,?,?)",
                (session['user_id'], patient_id, report_id, content)
            )
        else:
            cur.execute(
                "INSERT INTO prescriptions (doctor_id, patient_id, content) VALUES (?,?,?)",
                (session['user_id'], patient_id, content)
            )
        prescription_id = cur.lastrowid

        # insert prescription items
        for i, med in enumerate(med_names):
            med = med.strip()
            if not med:
                continue
            dose = doses[i].strip() if i < len(doses) else ''
            try:
                quantity = int(quantities[i]) if i < len(quantities) and quantities[i].strip() else 1
            except ValueError:
                quantity = 1
            times_of_day = times[i].strip() if i < len(times) else ''
            meal_timing = meal_timings[i].strip() if i < len(meal_timings) else ''
            cur.execute(
                "INSERT INTO prescription_items (prescription_id, medicine_name, dose, quantity, times_of_day, meal_timing) VALUES (?,?,?,?,?,?)",
                (prescription_id, med, dose, quantity, times_of_day, meal_timing)
            )

        # create pharmacy order if requested
        if send_to_pharmacy:
            # If doctor provided shop_info but not selected a shop, try to match by name
            if not pharmacy_id and shop_info:
                match = db.execute("SELECT id FROM pharmacies WHERE LOWER(name)=? LIMIT 1", (shop_info.strip().lower(),)).fetchone()
                if match:
                    pharmacy_id = match['id']

            # fallback to first pharmacy if still not chosen
            if not pharmacy_id:
                first = db.execute("SELECT id FROM pharmacies ORDER BY id LIMIT 1").fetchone()
                pharmacy_id = first['id'] if first else None

            cur.execute(
                "INSERT INTO pharmacy_orders (prescription_id, created_by, pharmacy_id, shop_info, status) VALUES (?,?,?,?,?)",
                (prescription_id, session['user_id'], pharmacy_id, shop_info, 'sent')
            )

        db.commit()

        flash('Prescription saved.' + (' Sent to medical shop.' if send_to_pharmacy else ''), 'success')
        return redirect(url_for('patient_reports', patient_id=patient_id))

    # GET: show form with pharmacy list
    pharmacies = db.execute("SELECT id, name, contact, address FROM pharmacies ORDER BY name").fetchall()
    return render_template('prescription_form.html', patient_id=patient_id, report_id=report_id, pharmacies=pharmacies)

# Suggest tests (doctor)
@app.route('/suggest_test', methods=['GET', 'POST'])
@roles_required('doctor')
def suggest_test():
    db = get_db()
    patient_id = request.values.get('patient_id', '').strip().upper()
    tests = db.execute("SELECT * FROM test_catalog ORDER BY category, test_name").fetchall()

    if request.method == 'POST':
        patient_id = request.form.get('patient_id', '').strip().upper()
        test_ids = request.form.getlist('test_ids[]')
        notes = request.form.get('notes', '').strip()

        if not patient_id:
            flash('Patient ID is required.', 'danger')
            return redirect(url_for('suggest_test'))

        patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        if not patient:
            flash('Patient not found. Create patient first.', 'danger')
            return redirect(url_for('create_patient'))

        if not test_ids or not any(tid and tid.isdigit() for tid in test_ids):
            flash('Select at least one test.', 'danger')
            return redirect(url_for('suggest_test', patient_id=patient_id))

        for tid in test_ids:
            if tid and tid.isdigit():
                db.execute(
                    "INSERT INTO suggested_tests (doctor_id, patient_id, test_id, notes, status) VALUES (?,?,?,?,?)",
                    (session['user_id'], patient_id, int(tid), notes, 'pending')
                )
        db.commit()
        flash(f'Test(s) suggested for {patient_id}. Lab will be notified.', 'success')
        return redirect(url_for('doctor_dashboard'))

    patients = db.execute("SELECT patient_id, name FROM patients ORDER BY patient_id").fetchall()
    return render_template('suggest_test.html', patient_id=patient_id, patients=patients, tests=tests)

# Lab portal - pending tests to perform
@app.route('/lab_dashboard')
@roles_required('lab')
def lab_dashboard():
    db = get_db()
    pending = db.execute('''
        SELECT st.id, st.patient_id, st.test_id, st.status, st.notes, st.created_at,
               tc.test_name, tc.category, tc.sample_type, p.name as patient_name,
               u.name as doctor_name
        FROM suggested_tests st
        JOIN test_catalog tc ON st.test_id = tc.id
        JOIN patients p ON st.patient_id = p.patient_id
        LEFT JOIN users u ON st.doctor_id = u.id
        WHERE st.status = 'pending'
        ORDER BY st.created_at ASC
    ''').fetchall()
    return render_template('lab_dashboard.html', pending_tests=[dict(p) for p in pending])

@app.route('/lab/mark_test_done/<int:suggested_id>', methods=['POST'])
@roles_required('lab')
def lab_mark_test_done(suggested_id):
    db = get_db()
    db.execute("UPDATE suggested_tests SET status = 'completed' WHERE id = ?", (suggested_id,))
    db.commit()
    flash('Test marked as completed.', 'success')
    return redirect(url_for('lab_dashboard'))

# Doctor dashboard
@app.route('/api/dashboard/doctor')
@roles_required('doctor')
def api_doctor_dashboard():
    db = get_db()
    patients_rows = db.execute('''
        SELECT p.patient_id, p.name,
               MAX(r.upload_ts) as last_upload,
               (SELECT r2.risk_message FROM reports r2 WHERE r2.patient_id=p.patient_id ORDER BY r2.upload_ts DESC LIMIT 1) AS risk_message,
               (SELECT MAX(pr.created_at) FROM prescriptions pr WHERE pr.patient_id = p.patient_id) AS last_prescription
        FROM patients p
        LEFT JOIN reports r ON p.patient_id = r.patient_id
        GROUP BY p.patient_id, p.name
        ORDER BY last_upload DESC
    ''').fetchall()
    
    patients = []
    for row in patients_rows:
        p_dict = dict(row)
        # Fetch 3 most recent reports for this patient
        recent_reports = db.execute('''
            SELECT id, filename, report_type, upload_ts 
            FROM reports 
            WHERE patient_id = ? 
            ORDER BY upload_ts DESC 
            LIMIT 3
        ''', (p_dict['patient_id'],)).fetchall()
        p_dict['recent_reports'] = [dict(r) for r in recent_reports]
        patients.append(p_dict)

    # Fetch feedback for this doctor
    feedbacks = db.execute('''
        SELECT f.rating, f.message, f.medicine_feedback, f.created_at, p.name as patient_name
        FROM doctor_feedback f
        JOIN patients p ON f.patient_id = p.patient_id
        WHERE f.doctor_id = ?
        ORDER BY f.created_at DESC
    ''', (session['user_id'],)).fetchall()

    return jsonify({
        'patients': patients,
        'feedbacks': [dict(f) for f in feedbacks]
    })

@app.route('/api/patient-reports/<patient_id>')
def api_patient_reports(patient_id):
    db = get_db()
    patient_id = patient_id.strip().upper()

    reports = db.execute("SELECT * FROM reports WHERE patient_id = ? ORDER BY upload_ts DESC", (patient_id,)).fetchall()

    prescriptions = db.execute(
        "SELECT p.*, u.name as doctor_name FROM prescriptions p LEFT JOIN users u ON p.doctor_id = u.id WHERE p.patient_id = ? ORDER BY p.created_at DESC",
        (patient_id,)
    ).fetchall()

    prescriptions_with_items = []
    for p in prescriptions:
        items = db.execute("SELECT medicine_name, dose, quantity, times_of_day, meal_timing FROM prescription_items WHERE prescription_id = ?", (p['id'],)).fetchall()
        order = db.execute("SELECT * FROM pharmacy_orders WHERE prescription_id = ?", (p['id'],)).fetchone()
        prescriptions_with_items.append({
            'prescription': dict(p),
            'medicine_items': [dict(it) for it in items],
            'order': dict(order) if order else None
        })

    return jsonify({
        'reports': [dict(r) for r in reports],
        'prescriptions': prescriptions_with_items
    })

@app.route('/doctor')
@roles_required('doctor')
def doctor_dashboard():
    db = get_db()
    patients = db.execute('''
        SELECT p.patient_id, p.name,
               MAX(r.upload_ts) as last_upload,
               (SELECT r2.risk_message FROM reports r2 WHERE r2.patient_id=p.patient_id ORDER BY r2.upload_ts DESC LIMIT 1) AS risk_message,
               (SELECT r2.report_type FROM reports r2 WHERE r2.patient_id=p.patient_id ORDER BY r2.upload_ts DESC LIMIT 1) AS last_report_type,
               (SELECT MAX(pr.created_at) FROM prescriptions pr WHERE pr.patient_id = p.patient_id) AS last_prescription
        FROM patients p
        LEFT JOIN reports r ON p.patient_id = r.patient_id
        GROUP BY p.patient_id, p.name
        ORDER BY last_upload DESC
    ''').fetchall()
    # convert to list of dicts for template convenience
    patients_list = [dict(p) for p in patients]

    # Fetch feedback for this doctor
    feedbacks = db.execute('''
        SELECT f.rating, f.message, f.medicine_feedback, f.created_at, p.name as patient_name
        FROM doctor_feedback f
        JOIN patients p ON f.patient_id = p.patient_id
        WHERE f.doctor_id = ?
        ORDER BY f.created_at DESC
    ''', (session['user_id'],)).fetchall()

    return render_template('doctor_dashboard.html', 
                         patients=patients_list, 
                         feedbacks=[dict(f) for f in feedbacks])

@app.route('/api/submit_feedback', methods=['POST'])
def api_submit_feedback():
    if 'user_id' not in session or session.get('role') != 'patient':
        return jsonify({'success': False, 'message': 'Please login as a patient to submit feedback.'}), 401
    
    data = request.json
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
        
    patient_id = session['user_id']
    doctor_id = data.get('doctor_id')
    rating = data.get('rating')
    message = data.get('message')
    medicine_feedback = data.get('medicine_feedback', '')
    
    if not doctor_id or not rating:
        return jsonify({'success': False, 'message': 'Doctor and rating are required.'}), 400
    
    db = get_db()
    try:
        db.execute('''
            INSERT INTO doctor_feedback (patient_id, doctor_id, rating, message, medicine_feedback)
            VALUES (?, ?, ?, ?, ?)
        ''', (patient_id, doctor_id, rating, message, medicine_feedback))
        db.commit()
        return jsonify({'success': True, 'message': 'Thank you for your feedback! It has been sent to the doctor.'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    if 'user_id' not in session or session.get('role') != 'patient':
        flash('Please login as a patient to submit feedback.', 'danger')
        return redirect(url_for('login'))
    
    patient_id = session['user_id']
    doctor_id = request.form.get('doctor_id')
    rating = request.form.get('rating')
    message = request.form.get('message')
    medicine_feedback = request.form.get('medicine_feedback', '')
    
    if not doctor_id or not rating:
        flash('Please provide doctor and rating.', 'danger')
        return redirect(url_for('patient_dashboard', patient_id=patient_id if session.get('role') == 'patient' else ''))
    
    db = get_db()
    db.execute('''
        INSERT INTO doctor_feedback (patient_id, doctor_id, rating, message, medicine_feedback)
        VALUES (?, ?, ?, ?, ?)
    ''', (patient_id, doctor_id, rating, message, medicine_feedback))
    db.commit()
    
    flash('Thank you for your feedback! It has been sent to the doctor.', 'success')
    return redirect(url_for('patient_dashboard', patient_id=patient_id))

# Lab upload report
@app.route('/upload', methods=['GET','POST'])
@roles_required('lab')
def upload_report():
    db = get_db()
    if request.method == 'POST':
        patient_id = request.form['patient_id'].strip().upper()
        report_type = request.form.get('report_type','Unspecified')
        notes = request.form.get('notes','')
        file = request.files.get('file')

        patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        if not patient:
            flash('Patient ID not found. Ask doctor to create it first.', 'danger')
            return redirect(url_for('upload_report'))

        if not file or file.filename == '':
            flash('No file selected', 'danger')
            return redirect(url_for('upload_report'))

        if not allowed_file(file.filename):
            flash('Only PDF files are allowed', 'danger')
            return redirect(url_for('upload_report'))

        filename = secure_filename(f"{patient_id}_{int(datetime.utcnow().timestamp())}_{file.filename}")
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        # Predict sugar risk
        risk_score, risk_label, risk_message = predict_risk_from_report(file_path)

        db.execute(
            "INSERT INTO reports (patient_id, uploaded_by, filename, report_type, notes, risk_score, risk_label, risk_message) VALUES (?,?,?,?,?,?,?,?)",
            (patient_id, session['user_id'], filename, report_type, notes, risk_score, risk_label, risk_message)
        )
        db.commit()
        flash(f'Report uploaded successfully. Risk: {risk_label}', 'success')
        return redirect(url_for('upload_report'))

    prefill_patient = request.args.get('patient_id', '')
    return render_template('upload.html', prefill_patient_id=prefill_patient)

# Patient login (view-only)
@app.route('/patient_login', methods=['GET','POST'])
def patient_login():
    if request.method == 'POST':
        patient_id = request.form.get('patient_id', '').strip().upper()
        password = request.form.get('password', '')
        
        if not patient_id or not password:
            flash('Patient ID and password are required.', 'danger')
            return render_template('patient_login.html')
        
        db = get_db()
        patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        
        if patient and check_password_hash(patient['view_password_hash'], password):
            session['patient_view'] = patient_id
            session['patient_view_expires'] = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
            flash('Access granted for 30 minutes.', 'success')
            return redirect(url_for('patient_reports', patient_id=patient_id))
        
        flash('Invalid Patient ID or password.', 'danger')
    
    return render_template('patient_login.html')

# Patient reports (shows reports + prescriptions)
@app.route('/patient_report/<patient_id>')
def patient_reports(patient_id):
    db = get_db()
    patient_id = patient_id.strip().upper()

    reports = db.execute("SELECT * FROM reports WHERE patient_id = ? ORDER BY upload_ts DESC", (patient_id,)).fetchall()

    # Fetch prescriptions and convert to structured list with items & order
    prescriptions = db.execute(
        "SELECT p.*, u.name as doctor_name FROM prescriptions p LEFT JOIN users u ON p.doctor_id = u.id WHERE p.patient_id = ? ORDER BY p.created_at DESC",
        (patient_id,)
    ).fetchall()

    prescriptions_with_items = []
    for p in prescriptions:
        items = db.execute("SELECT medicine_name, dose, quantity, times_of_day, meal_timing FROM prescription_items WHERE prescription_id = ?", (p['id'],)).fetchall()
        order = db.execute("SELECT * FROM pharmacy_orders WHERE prescription_id = ?", (p['id'],)).fetchone()
        prescriptions_with_items.append({
            'prescription': dict(p),
            'medicine_items': [dict(it) for it in items],
            'order': dict(order) if order else None
        })

    if not reports and not prescriptions_with_items:
        flash("No report or prescription found for this patient ID.", "warning")
        return redirect(url_for('patient_login'))

    return render_template('patient_reports.html', patient_id=patient_id, reports=[dict(r) for r in reports], prescriptions=prescriptions_with_items)

# Download report
@app.route('/reports/<int:report_id>/download')
def download_report(report_id):
    db = get_db()
    rep = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not rep:
        abort(404, description="Report not found in database")

    uploads_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
    file_path = os.path.join(uploads_dir, rep['filename'])

    if not os.path.exists(file_path):
        abort(404, description="File not found on server")

    return send_file(file_path, as_attachment=True, download_name=rep['filename'])

# ============ APPOINTMENT MANAGEMENT ============
@app.route('/book-appointment', methods=['GET', 'POST'])
def book_appointment():
    db = get_db()
    
    if request.method == 'POST':
        patient_id = request.form.get('patient_id', '').strip().upper()
        doctor_id = request.form.get('doctor_id')
        appointment_date = request.form.get('appointment_date')
        appointment_time = request.form.get('appointment_time')
        reason = request.form.get('reason', '').strip()
        
        # Validate patient
        patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        if not patient:
            flash('Patient ID not found. Please register first.', 'danger')
            return redirect(url_for('book_appointment'))
        
        # Check for conflicts
        conflict = db.execute("""
            SELECT * FROM appointments 
            WHERE doctor_id = ? AND appointment_date = ? AND appointment_time = ? 
            AND status != 'cancelled'
        """, (doctor_id, appointment_date, appointment_time)).fetchone()
        
        if conflict:
            flash('This time slot is already booked. Please select another time.', 'warning')
            return redirect(url_for('book_appointment'))
        
        # Create appointment
        db.execute("""
            INSERT INTO appointments (patient_id, doctor_id, appointment_date, appointment_time, reason, status)
            VALUES (?, ?, ?, ?, ?, 'scheduled')
        """, (patient_id, doctor_id, appointment_date, appointment_time, reason))
        db.commit()
        
        flash('Appointment booked successfully!', 'success')
        return redirect(url_for('patient_reports', patient_id=patient_id))
    
    # GET: Show booking form
    departments = db.execute("SELECT * FROM departments ORDER BY name").fetchall()
    doctors = db.execute("""
        SELECT u.id, u.name, dp.specialization, dp.consultation_fee, dp.department_id
        FROM users u
        LEFT JOIN doctor_profiles dp ON u.id = dp.user_id
        WHERE u.role = 'doctor'
        ORDER BY u.name
    """).fetchall()
    
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    return render_template('book_appointment.html', 
                         departments=[dict(d) for d in departments],
                         doctors=[dict(d) for d in doctors],
                         today=today)

@app.route('/api/book-appointment', methods=['POST'])
def api_book_appointment():
    data = request.json
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
        
    patient_id = data.get('patient_id', '').strip().upper()
    doctor_id = data.get('doctor_id')
    appointment_date = data.get('appointment_date')
    appointment_time = data.get('appointment_time')
    reason = data.get('reason', '').strip()
    
    if not all([patient_id, doctor_id, appointment_date, appointment_time, reason]):
        return jsonify({'success': False, 'message': 'All fields are required'}), 400

    db = get_db()
    
    # Validate patient
    patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
    if not patient:
        return jsonify({'success': False, 'message': 'Patient ID not found. Please register first.'}), 404
    
    # Check for conflicts
    conflict = db.execute("""
        SELECT * FROM appointments 
        WHERE doctor_id = ? AND appointment_date = ? AND appointment_time = ? 
        AND status != 'cancelled'
    """, (doctor_id, appointment_date, appointment_time)).fetchone()
    
    if conflict:
         return jsonify({'success': False, 'message': 'This time slot is already booked.'}), 409
    
    try:
        # Create appointment
        db.execute("""
            INSERT INTO appointments (patient_id, doctor_id, appointment_date, appointment_time, reason, status)
            VALUES (?, ?, ?, ?, ?, 'scheduled')
        """, (patient_id, doctor_id, appointment_date, appointment_time, reason))
        db.commit()
        
        return jsonify({'success': True, 'message': 'Appointment booked successfully!'})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/appointments')
def api_appointments_list():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    db = get_db()
    role = session.get('role')
    
    if role == 'doctor':
        appointments = db.execute("""
            SELECT a.*, p.name as patient_name, p.email as patient_email, p.dob
            FROM appointments a
            JOIN patients p ON a.patient_id = p.patient_id
            WHERE a.doctor_id = ?
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """, (session['user_id'],)).fetchall()
    elif role in ('admin', 'receptionist'):
        appointments = db.execute("""
            SELECT a.*, p.name as patient_name, p.email as patient_email, u.name as doctor_name
            FROM appointments a
            JOIN patients p ON a.patient_id = p.patient_id
            JOIN users u ON a.doctor_id = u.id
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """).fetchall()
    elif role == 'patient':
         appointments = db.execute("""
            SELECT a.*, u.name as doctor_name
            FROM appointments a
            JOIN users u ON a.doctor_id = u.id
            WHERE a.patient_id = ?
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """, (session.get('user_id'),)).fetchall()
    else:
        return jsonify({'error': 'Access denied'}), 403
    
    return jsonify([dict(a) for a in appointments])

@app.route('/appointments')
def appointments_list():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    role = session.get('role')
    
    if role == 'doctor':
        # Show doctor's appointments
        appointments = db.execute("""
            SELECT a.*, p.name as patient_name, p.dob
            FROM appointments a
            JOIN patients p ON a.patient_id = p.patient_id
            WHERE a.doctor_id = ?
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """, (session['user_id'],)).fetchall()
    elif role in ('admin', 'receptionist'):
        # Show all appointments
        appointments = db.execute("""
            SELECT a.*, p.name as patient_name, u.name as doctor_name
            FROM appointments a
            JOIN patients p ON a.patient_id = p.patient_id
            JOIN users u ON a.doctor_id = u.id
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        """).fetchall()
    else:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    
    return render_template('appointments.html', appointments=[dict(a) for a in appointments])

@app.route('/appointment/<int:appointment_id>/update-status', methods=['POST'])
def update_appointment_status(appointment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    status = request.form.get('status')
    allowed_statuses = ['scheduled', 'confirmed', 'cancelled', 'completed', 'no-show']
    
    if status not in allowed_statuses:
        flash('Invalid status.', 'danger')
        return redirect(url_for('appointments_list'))
    
    db = get_db()
    db.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
    db.commit()
    
    flash(f'Appointment status updated to {status}.', 'success')
    return redirect(url_for('appointments_list'))

# ============ ADMIN DASHBOARD ============
@app.route('/api/dashboard/admin')
@roles_required('admin')
def api_admin_dashboard():
    db = get_db()
    
    # Get statistics
    stats = {}
    stats['total_patients'] = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    stats['total_doctors'] = db.execute("SELECT COUNT(*) FROM users WHERE role = 'doctor'").fetchone()[0]
    stats['total_appointments'] = db.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    stats['today_appointments'] = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE appointment_date = date('now')"
    ).fetchone()[0]
    stats['pending_tests'] = db.execute(
        "SELECT COUNT(*) FROM lab_bookings WHERE status IN ('scheduled', 'sample_collected', 'processing')"
    ).fetchone()[0]
    stats['total_revenue'] = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM billing WHERE payment_status = 'paid'"
    ).fetchone()[0]
    
    recent_appointments = db.execute("""
        SELECT a.*, p.name as patient_name, u.name as doctor_name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        JOIN users u ON a.doctor_id = u.id
        ORDER BY a.created_at DESC
        LIMIT 10
    """).fetchall()
    
    recent_patients = db.execute("""
        SELECT * FROM patients 
        ORDER BY created_at DESC 
        LIMIT 10
    """).fetchall()
    
    return jsonify({
        'stats': stats,
        'recent_appointments': [dict(a) for a in recent_appointments],
        'recent_patients': [dict(p) for p in recent_patients]
    })

@app.route('/admin/dashboard')
@roles_required('admin')
def admin_dashboard():
    db = get_db()
    
    # Get statistics
    stats = {}
    stats['total_patients'] = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    stats['total_doctors'] = db.execute("SELECT COUNT(*) FROM users WHERE role = 'doctor'").fetchone()[0]
    stats['total_appointments'] = db.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    stats['today_appointments'] = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE appointment_date = date('now')"
    ).fetchone()[0]
    stats['pending_tests'] = db.execute(
        "SELECT COUNT(*) FROM lab_bookings WHERE status IN ('scheduled', 'sample_collected', 'processing')"
    ).fetchone()[0]
    stats['total_revenue'] = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM billing WHERE payment_status = 'paid'"
    ).fetchone()[0]
    
    # Recent activities
    recent_appointments = db.execute("""
        SELECT a.*, p.name as patient_name, u.name as doctor_name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        JOIN users u ON a.doctor_id = u.id
        ORDER BY a.created_at DESC
        LIMIT 10
    """).fetchall()
    
    recent_patients = db.execute("""
        SELECT * FROM patients 
        ORDER BY created_at DESC 
        LIMIT 10
    """).fetchall()
    
    return render_template('admin_dashboard.html',
                         stats=stats,
                         recent_appointments=[dict(a) for a in recent_appointments],
                         recent_patients=[dict(p) for p in recent_patients])

@app.route('/admin/users')
@roles_required('admin')
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return render_template('admin_users.html', users=[dict(u) for u in users])

@app.route('/admin/analytics')
@roles_required('admin')
def admin_analytics():
    db = get_db()
    
    # Get various analytics data
    appointments_by_month = db.execute("""
        SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as count
        FROM appointments
        GROUP BY month
        ORDER BY month DESC
        LIMIT 12
    """).fetchall()
    
    revenue_by_month = db.execute("""
        SELECT strftime('%Y-%m', created_at) as month, SUM(amount) as total
        FROM billing
        WHERE payment_status = 'paid'
        GROUP BY month
        ORDER BY month DESC
        LIMIT 12
    """).fetchall()
    
    department_stats = db.execute("""
        SELECT d.name, COUNT(DISTINCT dp.id) as doctor_count
        FROM departments d
        LEFT JOIN doctor_profiles dp ON d.id = dp.department_id
        GROUP BY d.id, d.name
    """).fetchall()
    
    return render_template('admin_analytics.html',
                         appointments_by_month=[dict(a) for a in appointments_by_month],
                         revenue_by_month=[dict(r) for r in revenue_by_month],
                         department_stats=[dict(d) for d in department_stats])

# ============ PATIENT DASHBOARD ============
@app.route('/api/dashboard/patient/<patient_id>')
def api_patient_dashboard(patient_id):
    # Check if patient is logged in (API version)
    if session.get('user_id') != patient_id.upper() and session.get('role') != 'patient' and session.get('patient_view') != patient_id.upper():
         return jsonify({'error': 'Unauthorized'}), 401
    
    db = get_db()
    patient_id = patient_id.upper()
    
    patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
    if not patient:
        return jsonify({'error': 'Patient not found'}), 404
    
    appointments = db.execute("""
        SELECT a.*, u.name as doctor_name, dp.specialization
        FROM appointments a
        JOIN users u ON a.doctor_id = u.id
        LEFT JOIN doctor_profiles dp ON u.id = dp.user_id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        LIMIT 5
    """, (patient_id,)).fetchall()
    
    reports = db.execute("""
        SELECT * FROM reports 
        WHERE patient_id = ? 
        ORDER BY upload_ts DESC 
        LIMIT 5
    """, (patient_id,)).fetchall()
    
    billing = db.execute("""
        SELECT * FROM billing 
        WHERE patient_id = ? 
        ORDER BY created_at DESC 
        LIMIT 5
    """, (patient_id,)).fetchall()
    
    prescription_count = db.execute(
        "SELECT COUNT(*) FROM prescriptions WHERE patient_id = ?", 
        (patient_id,)
    ).fetchone()[0]
    
    latest_report = None
    diabetes_status = "No Data"
    if reports:
        latest_report = dict(reports[0])
        if latest_report.get('risk_label'):
            diabetes_status = latest_report['risk_label']
            
    # Get doctors for feedback form
    doctors = db.execute("SELECT id, name FROM users WHERE role = 'doctor'").fetchall()
    
    return jsonify({
        'patient': dict(patient),
        'appointments': [dict(a) for a in appointments],
        'reports': [dict(r) for r in reports],
        'billing': [dict(b) for b in billing],
        'doctors': [dict(d) for d in doctors],
        'prescription_count': prescription_count,
        'diabetes_status': diabetes_status,
        'latest_report': latest_report
    })

@app.route('/patient/dashboard/<patient_id>')
def patient_dashboard(patient_id):
    # Check if patient is logged in
    if session.get('patient_view') != patient_id.upper():
        return redirect(url_for('patient_login'))
    
    db = get_db()
    patient_id = patient_id.upper()
    
    patient = db.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
    if not patient:
        flash('Patient not found.', 'danger')
        return redirect(url_for('patient_login'))
    
    # Get appointments
    appointments = db.execute("""
        SELECT a.*, u.name as doctor_name, dp.specialization
        FROM appointments a
        JOIN users u ON a.doctor_id = u.id
        LEFT JOIN doctor_profiles dp ON u.id = dp.user_id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        LIMIT 5
    """, (patient_id,)).fetchall()
    
    # Get recent reports
    reports = db.execute("""
        SELECT * FROM reports 
        WHERE patient_id = ? 
        ORDER BY upload_ts DESC 
        LIMIT 5
    """, (patient_id,)).fetchall()
    
    # Get billing info
    billing = db.execute("""
        SELECT * FROM billing 
        WHERE patient_id = ? 
        ORDER BY created_at DESC 
        LIMIT 5
    """, (patient_id,)).fetchall()
    
    # Get prescription count
    prescription_count = db.execute(
        "SELECT COUNT(*) FROM prescriptions WHERE patient_id = ?", 
        (patient_id,)
    ).fetchone()[0]
    
    # Get latest report for diabetes status
    latest_report = None
    diabetes_status = "No Data"
    if reports:
        latest_report = dict(reports[0])
        if latest_report.get('risk_label'):
            diabetes_status = latest_report['risk_label']
    
    # Get doctors for feedback form
    doctors = db.execute("SELECT id, name FROM users WHERE role = 'doctor'").fetchall()
    
    return render_template('patient_dashboard.html',
                         patient=dict(patient),
                         appointments=[dict(a) for a in appointments],
                         reports=[dict(r) for r in reports],
                         billing=[dict(b) for b in billing],
                         doctors=[dict(d) for d in doctors],
                         prescription_count=prescription_count,
                         diabetes_status=diabetes_status,
                         latest_report=latest_report)

# ============ STAFF DASHBOARD (Receptionist/Nurse) ============
@app.route('/staff/dashboard')
@roles_required('receptionist', 'nurse')
def staff_dashboard():
    db = get_db()
    
    # Similar to admin but with limited access
    stats = {}
    stats['today_appointments'] = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE appointment_date = date('now')"
    ).fetchone()[0]
    stats['pending_appointments'] = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE status = 'scheduled'"
    ).fetchone()[0]
    
    # Today's appointments
    today_appointments = db.execute("""
        SELECT a.*, p.name as patient_name, u.name as doctor_name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        JOIN users u ON a.doctor_id = u.id
        WHERE a.appointment_date = date('now')
        ORDER BY a.appointment_time
    """).fetchall()
    
    return render_template('staff_dashboard.html',
                         stats=stats,
                         today_appointments=[dict(a) for a in today_appointments])

# ========== API ENDPOINTS FOR REACT FRONTEND ==========

@app.route('/api/doctor/<int:doctor_id>')
def api_doctor_detail(doctor_id):
    db = get_db()
    
    # Get doctor info
    doctor = db.execute("SELECT id, name, email, role, created_at FROM users WHERE id = ? AND role = 'doctor'", (doctor_id,)).fetchone()
    if not doctor:
        return jsonify({'error': 'Doctor not found'}), 404
    
    # Get doctor profile
    profile = db.execute("SELECT * FROM doctor_profiles WHERE user_id = ?", (doctor_id,)).fetchone()
    
    # Get department info if exists
    department = None
    if profile and profile['department_id']:
        department = db.execute("SELECT * FROM departments WHERE id = ?", (profile['department_id'],)).fetchone()
        
    return jsonify({
        'doctor': dict(doctor),
        'profile': dict(profile) if profile else {},
        'department': dict(department) if department else None
    })

@app.route('/api/profile', methods=['GET'])
def api_get_profile():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    db = get_db()
    user = db.execute("SELECT id, name, email, role, created_at FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    if not user:
         return jsonify({'error': 'User not found'}), 404
         
    # Get role specific data
    pharmacy = None
    stats = {}
    
    if user['role'] == 'pharmacy':
        pharmacy = db.execute("SELECT * FROM pharmacies WHERE user_id = ?", (user['id'],)).fetchone()
        if pharmacy:
             stats['total_orders'] = db.execute("SELECT COUNT(*) FROM pharmacy_orders WHERE pharmacy_id = ?", (pharmacy['id'],)).fetchone()[0]
             stats['delivered_orders'] = db.execute("SELECT COUNT(*) FROM pharmacy_orders WHERE pharmacy_id = ? AND status = 'delivered'", (pharmacy['id'],)).fetchone()[0]
             
    elif user['role'] == 'doctor':
         stats['total_patients'] = db.execute("SELECT COUNT(DISTINCT patient_id) FROM prescriptions WHERE doctor_id = ?", (user['id'],)).fetchone()[0]
         stats['total_prescriptions'] = db.execute("SELECT COUNT(*) FROM prescriptions WHERE doctor_id = ?", (user['id'],)).fetchone()[0]
         
    elif user['role'] == 'lab':
         stats['total_reports'] = db.execute("SELECT COUNT(*) FROM reports WHERE uploaded_by = ?", (user['id'],)).fetchone()[0]
         
    return jsonify({
        'user': dict(user),
        'role_data': dict(pharmacy) if pharmacy else None,
        'stats': stats
    })

@app.route('/api/profile/update', methods=['POST'])
def api_update_profile():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.json
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    
    if not name or not email:
        return jsonify({'success': False, 'message': 'Name and email are required'}), 400
    
    db = get_db()
    try:
        db.execute("UPDATE users SET name = ?, email = ? WHERE id = ?", (name, email, session['user_id']))
        db.commit()
        session['user_name'] = name
        return jsonify({'success': True, 'message': 'Profile updated successfully'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Email already exists'}), 409
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/profile/change-password', methods=['POST'])
def api_change_password():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.json
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    confirm_new_password = data.get('confirm_new_password', '')
    
    if not all([current_password, new_password, confirm_new_password]):
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
        
    if new_password != confirm_new_password:
        return jsonify({'success': False, 'message': 'New passwords do not match'}), 400
        
    if len(new_password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
        
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    if not user or not check_password_hash(user['password_hash'], current_password):
        return jsonify({'success': False, 'message': 'Current password is incorrect'}), 401
        
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), session['user_id']))
    db.commit()
    
    return jsonify({'success': True, 'message': 'Password changed successfully'})

@app.route('/api/appointments/<int:appointment_id>/status', methods=['POST'])
def api_update_appointment_status(appointment_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
    data = request.json
    status = data.get('status')
    allowed = ['scheduled', 'confirmed', 'cancelled', 'completed', 'no-show']
    
    if status not in allowed:
        return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
    db = get_db()
    # Optional: Check if user is allowed to update (e.g. only doctor/admin)
    # For now, simplistic check
    
    db.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
    db.commit()
    
    return jsonify({'success': True, 'message': f'Status updated to {status}'})

# Initialize DB
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True)
