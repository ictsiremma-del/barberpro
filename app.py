from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os, psycopg2, psycopg2.extras
from datetime import datetime, date, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'barberpro-dev-secret')

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'barber',
                phone TEXT,
                commission REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS records (
                id SERIAL PRIMARY KEY,
                barber_id INTEGER NOT NULL REFERENCES users(id),
                service TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                description TEXT,
                amount REAL NOT NULL,
                expense_date DATE NOT NULL DEFAULT CURRENT_DATE,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """)
            cur.execute("SELECT id FROM users WHERE role='owner' LIMIT 1")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (name, username, password, role) VALUES (%s,%s,%s,%s)",
                    ('Salon Owner', 'owner', generate_password_hash('owner1234'), 'owner'))
        conn.commit()

init_db()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'owner':
            flash('Owner access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as conn:
            with get_cursor(conn) as cur:
                cur.execute("SELECT * FROM users WHERE username=%s", (username,))
                user = cur.fetchone()
        if user and check_password_hash(user['password'], password):
            session.update({'user_id': user['id'], 'name': user['name'], 'role': user['role'], 'username': user['username']})
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    period = request.args.get('period', 'day')
    today_str   = date.today().isoformat()
    week_start  = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    month_start = date.today().replace(day=1).isoformat()
    date_filter = today_str if period == 'day' else (week_start if period == 'week' else month_start)

    with get_db() as conn:
        with get_cursor(conn) as cur:
            if session['role'] == 'owner':
                cur.execute("SELECT SUM(amount) as total, COUNT(*) as cnt FROM records WHERE DATE(recorded_at)>=%s", (date_filter,))
                income_rows = cur.fetchone()
                cur.execute("SELECT SUM(amount) as total FROM expenses WHERE expense_date>=%s", (date_filter,))
                exp_rows = cur.fetchone()
                cur.execute("""SELECT u.name, SUM(r.amount) as total, COUNT(*) as cnt
                    FROM records r JOIN users u ON r.barber_id=u.id
                    WHERE DATE(r.recorded_at)>=%s GROUP BY u.name ORDER BY total DESC LIMIT 6""", (date_filter,))
                top_barbers = cur.fetchall()
                cur.execute("""SELECT service, SUM(amount) as total, COUNT(*) as cnt
                    FROM records WHERE DATE(recorded_at)>=%s GROUP BY service ORDER BY total DESC""", (date_filter,))
                services = cur.fetchall()
                cur.execute("""SELECT r.*, u.name as barber_name FROM records r
                    JOIN users u ON r.barber_id=u.id ORDER BY r.recorded_at DESC LIMIT 10""")
                recent = cur.fetchall()
            else:
                cur.execute("SELECT SUM(amount) as total, COUNT(*) as cnt FROM records WHERE barber_id=%s AND DATE(recorded_at)>=%s", (session['user_id'], date_filter))
                income_rows = cur.fetchone()
                exp_rows = None; top_barbers = []
                cur.execute("""SELECT service, SUM(amount) as total, COUNT(*) as cnt
                    FROM records WHERE barber_id=%s AND DATE(recorded_at)>=%s GROUP BY service ORDER BY total DESC""", (session['user_id'], date_filter))
                services = cur.fetchall()
                cur.execute("""SELECT r.*, u.name as barber_name FROM records r
                    JOIN users u ON r.barber_id=u.id WHERE r.barber_id=%s ORDER BY r.recorded_at DESC LIMIT 10""", (session['user_id'],))
                recent = cur.fetchall()

    income = income_rows['total'] or 0
    income_cnt = income_rows['cnt'] or 0
    exp_total = (exp_rows['total'] or 0) if exp_rows else 0
    return render_template('dashboard.html', period=period, income=income, income_cnt=income_cnt,
        exp_total=exp_total, net=income-exp_total, top_barbers=top_barbers, services=services, recent=recent)

@app.route('/records')
@login_required
def records():
    barber_id = request.args.get('barber_id', '')
    date_from = request.args.get('date_from', date.today().isoformat())
    date_to   = request.args.get('date_to',   date.today().isoformat())
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT id, name FROM users WHERE role='barber' ORDER BY name")
            barbers = cur.fetchall()
            query = "SELECT r.*, u.name as barber_name FROM records r JOIN users u ON r.barber_id=u.id WHERE 1=1"
            params = []
            if session['role'] != 'owner':
                query += " AND r.barber_id=%s"; params.append(session['user_id'])
            elif barber_id:
                query += " AND r.barber_id=%s"; params.append(barber_id)
            query += " AND DATE(r.recorded_at) BETWEEN %s AND %s ORDER BY r.recorded_at DESC"
            params += [date_from, date_to]
            cur.execute(query, params)
            rows = cur.fetchall()
    total = sum(r['amount'] for r in rows)
    return render_template('records.html', records=rows, barbers=barbers,
        barber_id=barber_id, date_from=date_from, date_to=date_to, total=total)

@app.route('/records/add', methods=['POST'])
@login_required
def add_record():
    service = request.form.get('service', '').strip()
    custom  = request.form.get('custom_service', '').strip()
    if service == 'Custom' and custom: service = custom
    amount      = request.form.get('amount', 0)
    note        = request.form.get('note', '').strip()
    recorded_at = request.form.get('recorded_at') or datetime.now().isoformat(timespec='seconds')
    barber_id   = request.form.get('barber_id') if session['role'] == 'owner' else session['user_id']
    if not service or not amount:
        flash('Service and amount are required.', 'error')
        return redirect(url_for('records'))
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("INSERT INTO records (barber_id, service, amount, note, recorded_at) VALUES (%s,%s,%s,%s,%s)",
                (barber_id, service, float(amount), note, recorded_at))
        conn.commit()
    flash('Record saved!', 'success')
    return redirect(url_for('records'))

@app.route('/records/delete/<int:rid>', methods=['POST'])
@login_required
def delete_record(rid):
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT * FROM records WHERE id=%s", (rid,))
            row = cur.fetchone()
            if row and (session['role'] == 'owner' or row['barber_id'] == session['user_id']):
                cur.execute("DELETE FROM records WHERE id=%s", (rid,))
        conn.commit()
    flash('Record deleted.', 'success')
    return redirect(url_for('records'))

@app.route('/expenses')
@login_required
@owner_required
def expenses():
    today_str   = date.today().isoformat()
    week_start  = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    month_start = date.today().replace(day=1).isoformat()
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT * FROM expenses ORDER BY expense_date DESC, created_at DESC")
            rows = cur.fetchall()
            cur.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date=%s", (today_str,))
            today_total = cur.fetchone()['t'] or 0
            cur.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date>=%s", (week_start,))
            week_total  = cur.fetchone()['t'] or 0
            cur.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date>=%s", (month_start,))
            month_total = cur.fetchone()['t'] or 0
    return render_template('expenses.html', expenses=rows,
        today_total=today_total, week_total=week_total, month_total=month_total)

@app.route('/expenses/add', methods=['POST'])
@login_required
@owner_required
def add_expense():
    amount = request.form.get('amount', 0)
    if not amount:
        flash('Amount is required.', 'error')
        return redirect(url_for('expenses'))
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("INSERT INTO expenses (category, description, amount, expense_date, created_by) VALUES (%s,%s,%s,%s,%s)",
                (request.form.get('category',''), request.form.get('description','').strip(),
                 float(amount), request.form.get('expense_date') or date.today().isoformat(), session['user_id']))
        conn.commit()
    flash('Expense added!', 'success')
    return redirect(url_for('expenses'))

@app.route('/expenses/delete/<int:eid>', methods=['POST'])
@login_required
@owner_required
def delete_expense(eid):
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("DELETE FROM expenses WHERE id=%s", (eid,))
        conn.commit()
    flash('Expense deleted.', 'success')
    return redirect(url_for('expenses'))

@app.route('/barbers')
@login_required
@owner_required
def barbers():
    today_str  = date.today().isoformat()
    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT * FROM users WHERE role='barber' ORDER BY name")
            rows = cur.fetchall()
            stats = {}
            for b in rows:
                cur.execute("SELECT SUM(amount) as t FROM records WHERE barber_id=%s AND DATE(recorded_at)=%s", (b['id'], today_str))
                today_inc = cur.fetchone()['t'] or 0
                cur.execute("SELECT SUM(amount) as t FROM records WHERE barber_id=%s AND DATE(recorded_at)>=%s", (b['id'], week_start))
                week_inc = cur.fetchone()['t'] or 0
                cur.execute("SELECT SUM(amount) as t, COUNT(*) as c FROM records WHERE barber_id=%s", (b['id'],))
                r = cur.fetchone()
                stats[b['id']] = {'today': today_inc, 'week': week_inc, 'total': r['t'] or 0, 'cuts': r['c'] or 0}
    return render_template('barbers.html', barbers=rows, stats=stats)

@app.route('/barbers/add', methods=['POST'])
@login_required
@owner_required
def add_barber():
    name     = request.form.get('name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if not name or not username or not password:
        flash('Name, username and password are required.', 'error')
        return redirect(url_for('barbers'))
    try:
        with get_db() as conn:
            with get_cursor(conn) as cur:
                cur.execute("INSERT INTO users (name, username, password, role, phone, commission) VALUES (%s,%s,%s,%s,%s,%s)",
                    (name, username, generate_password_hash(password), request.form.get('role','barber'),
                     request.form.get('phone','').strip(), float(request.form.get('commission',0) or 0)))
            conn.commit()
        flash(f'{name} added!', 'success')
    except psycopg2.errors.UniqueViolation:
        flash('Username already exists.', 'error')
    return redirect(url_for('barbers'))

@app.route('/barbers/delete/<int:bid>', methods=['POST'])
@login_required
@owner_required
def delete_barber(bid):
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("DELETE FROM users WHERE id=%s AND role='barber'", (bid,))
        conn.commit()
    flash('Barber removed.', 'success')
    return redirect(url_for('barbers'))

@app.route('/reports')
@login_required
@owner_required
def reports():
    period = request.args.get('period', 'day')
    today_str   = date.today().isoformat()
    week_start  = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    month_start = date.today().replace(day=1).isoformat()
    date_filter = today_str if period == 'day' else (week_start if period == 'week' else month_start)
    with get_db() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT SUM(amount) as t, COUNT(*) as c FROM records WHERE DATE(recorded_at)>=%s", (date_filter,))
            income_row = cur.fetchone()
            cur.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date>=%s", (date_filter,))
            exp_row = cur.fetchone()
            cur.execute("""SELECT u.name, SUM(r.amount) as total, COUNT(*) as cnt, AVG(r.amount) as avg
                FROM records r JOIN users u ON r.barber_id=u.id WHERE DATE(r.recorded_at)>=%s
                GROUP BY u.name ORDER BY total DESC""", (date_filter,))
            barber_perf = cur.fetchall()
            cur.execute("SELECT service, SUM(amount) as total, COUNT(*) as cnt FROM records WHERE DATE(recorded_at)>=%s GROUP BY service ORDER BY total DESC", (date_filter,))
            services = cur.fetchall()
            cur.execute("SELECT category, SUM(amount) as total, COUNT(*) as cnt FROM expenses WHERE expense_date>=%s GROUP BY category ORDER BY total DESC", (date_filter,))
            exp_cats = cur.fetchall()
            cur.execute("SELECT r.*, u.name as barber_name FROM records r JOIN users u ON r.barber_id=u.id WHERE DATE(r.recorded_at)>=%s ORDER BY r.recorded_at DESC", (date_filter,))
            all_records = cur.fetchall()
            cur.execute("SELECT * FROM expenses WHERE expense_date>=%s ORDER BY expense_date DESC", (date_filter,))
            all_expenses = cur.fetchall()
    income = income_row['t'] or 0; exp_total = exp_row['t'] or 0
    return render_template('reports.html', period=period, income=income, income_cnt=income_row['c'] or 0,
        exp_total=exp_total, net=income-exp_total, barber_perf=barber_perf, services=services,
        exp_cats=exp_cats, all_records=all_records, all_expenses=all_expenses)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new_pw  = request.form.get('new_password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()
        with get_db() as conn:
            with get_cursor(conn) as cur:
                cur.execute("SELECT * FROM users WHERE id=%s", (session['user_id'],))
                user = cur.fetchone()
        if not check_password_hash(user['password'], current):
            flash('Current password is incorrect.', 'error')
        elif new_pw != confirm:
            flash('New passwords do not match.', 'error')
        elif len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'error')
        else:
            with get_db() as conn:
                with get_cursor(conn) as cur:
                    cur.execute("UPDATE users SET password=%s WHERE id=%s", (generate_password_hash(new_pw), session['user_id']))
                conn.commit()
            flash('Password changed!', 'success')
    return render_template('profile.html')

if __name__ == '__main__':
    app.run(debug=True)

@app.errorhandler(500)
def internal_error(e):
    return f"<h2>Server Error</h2><pre>{e}</pre><p>Check Render logs for details.</p>", 500
