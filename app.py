from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3, os
from datetime import datetime, date, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'barberpro-dev-secret-change-in-prod')

DB = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'barberpro.db'))

# ─── DB ───────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'barber',  -- 'owner' or 'barber'
            phone TEXT,
            commission REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barber_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(barber_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            description TEXT,
            amount REAL NOT NULL,
            expense_date TEXT NOT NULL DEFAULT (date('now')),
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)
        # Create default owner if none exists
        existing = db.execute("SELECT id FROM users WHERE role='owner'").fetchone()
        if not existing:
            db.execute(
                "INSERT INTO users (name, username, password, role) VALUES (?,?,?,?)",
                ('Salon Owner', 'owner', generate_password_hash('owner1234'), 'owner')
            )
            db.commit()

init_db()

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────

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

# ─── AUTH ROUTES ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['role'] = user['role']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── MAIN DASHBOARD ───────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    period = request.args.get('period', 'day')
    today_str = date.today().isoformat()
    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    month_start = date.today().replace(day=1).isoformat()

    if period == 'day':   date_filter = today_str
    elif period == 'week':  date_filter = week_start
    else:                   date_filter = month_start

    with get_db() as db:
        if session['role'] == 'owner':
            income_rows = db.execute(
                "SELECT SUM(amount) as total, COUNT(*) as cnt FROM records WHERE DATE(recorded_at) >= ?",
                (date_filter,)).fetchone()
            exp_rows = db.execute(
                "SELECT SUM(amount) as total FROM expenses WHERE expense_date >= ?",
                (date_filter,)).fetchone()
            top_barbers = db.execute("""
                SELECT u.name, SUM(r.amount) as total, COUNT(*) as cnt
                FROM records r JOIN users u ON r.barber_id=u.id
                WHERE DATE(r.recorded_at) >= ?
                GROUP BY r.barber_id ORDER BY total DESC LIMIT 6
            """, (date_filter,)).fetchall()
            services = db.execute("""
                SELECT service, SUM(amount) as total, COUNT(*) as cnt
                FROM records WHERE DATE(recorded_at) >= ?
                GROUP BY service ORDER BY total DESC
            """, (date_filter,)).fetchall()
            recent = db.execute("""
                SELECT r.*, u.name as barber_name FROM records r
                JOIN users u ON r.barber_id=u.id
                ORDER BY r.recorded_at DESC LIMIT 10
            """).fetchall()
            recent_exp = db.execute(
                "SELECT * FROM expenses ORDER BY created_at DESC LIMIT 5").fetchall()
        else:
            income_rows = db.execute(
                "SELECT SUM(amount) as total, COUNT(*) as cnt FROM records WHERE barber_id=? AND DATE(recorded_at) >= ?",
                (session['user_id'], date_filter)).fetchone()
            exp_rows = None
            top_barbers = []
            services = db.execute("""
                SELECT service, SUM(amount) as total, COUNT(*) as cnt
                FROM records WHERE barber_id=? AND DATE(recorded_at) >= ?
                GROUP BY service ORDER BY total DESC
            """, (session['user_id'], date_filter)).fetchall()
            recent = db.execute("""
                SELECT r.*, u.name as barber_name FROM records r
                JOIN users u ON r.barber_id=u.id
                WHERE r.barber_id=?
                ORDER BY r.recorded_at DESC LIMIT 10
            """, (session['user_id'],)).fetchall()
            recent_exp = []

    income = income_rows['total'] or 0
    income_cnt = income_rows['cnt'] or 0
    exp_total = (exp_rows['total'] or 0) if exp_rows else 0
    net = income - exp_total

    return render_template('dashboard.html',
        period=period, income=income, income_cnt=income_cnt,
        exp_total=exp_total, net=net, top_barbers=top_barbers,
        services=services, recent=recent, recent_exp=recent_exp)

# ─── RECORDS ──────────────────────────────────────────────────────────────────

@app.route('/records')
@login_required
def records():
    barber_id = request.args.get('barber_id', '')
    date_from = request.args.get('date_from', date.today().isoformat())
    date_to   = request.args.get('date_to',   date.today().isoformat())

    with get_db() as db:
        barbers = db.execute("SELECT id, name FROM users WHERE role='barber' ORDER BY name").fetchall()

        query = """SELECT r.*, u.name as barber_name FROM records r
                   JOIN users u ON r.barber_id=u.id WHERE 1=1"""
        params = []

        if session['role'] != 'owner':
            query += " AND r.barber_id=?"
            params.append(session['user_id'])
        elif barber_id:
            query += " AND r.barber_id=?"
            params.append(barber_id)

        query += " AND DATE(r.recorded_at) BETWEEN ? AND ?"
        params += [date_from, date_to]
        query += " ORDER BY r.recorded_at DESC"

        rows = db.execute(query, params).fetchall()
        total = sum(r['amount'] for r in rows)

    return render_template('records.html', records=rows, barbers=barbers,
        barber_id=barber_id, date_from=date_from, date_to=date_to, total=total)

@app.route('/records/add', methods=['POST'])
@login_required
def add_record():
    service = request.form.get('service','').strip()
    custom  = request.form.get('custom_service','').strip()
    if service == 'Custom' and custom:
        service = custom
    amount = request.form.get('amount', 0)
    note   = request.form.get('note','').strip()
    recorded_at = request.form.get('recorded_at') or datetime.now().isoformat(timespec='seconds')

    if session['role'] == 'owner':
        barber_id = request.form.get('barber_id') or session['user_id']
    else:
        barber_id = session['user_id']

    if not service or not amount:
        flash('Service and amount are required.', 'error')
        return redirect(url_for('records'))

    with get_db() as db:
        db.execute(
            "INSERT INTO records (barber_id, service, amount, note, recorded_at) VALUES (?,?,?,?,?)",
            (barber_id, service, float(amount), note, recorded_at))
        db.commit()
    flash('Record saved!', 'success')
    return redirect(url_for('records'))

@app.route('/records/delete/<int:rid>', methods=['POST'])
@login_required
def delete_record(rid):
    with get_db() as db:
        row = db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        if row and (session['role'] == 'owner' or row['barber_id'] == session['user_id']):
            db.execute("DELETE FROM records WHERE id=?", (rid,))
            db.commit()
            flash('Record deleted.', 'success')
    return redirect(url_for('records'))

# ─── EXPENSES ─────────────────────────────────────────────────────────────────

@app.route('/expenses')
@login_required
@owner_required
def expenses():
    today_str   = date.today().isoformat()
    week_start  = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    month_start = date.today().replace(day=1).isoformat()
    with get_db() as db:
        rows  = db.execute("SELECT * FROM expenses ORDER BY expense_date DESC, created_at DESC").fetchall()
        today_total = db.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date=?", (today_str,)).fetchone()['t'] or 0
        week_total  = db.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date>=?", (week_start,)).fetchone()['t'] or 0
        month_total = db.execute("SELECT SUM(amount) as t FROM expenses WHERE expense_date>=?", (month_start,)).fetchone()['t'] or 0
    return render_template('expenses.html', expenses=rows,
        today_total=today_total, week_total=week_total, month_total=month_total)

@app.route('/expenses/add', methods=['POST'])
@login_required
@owner_required
def add_expense():
    category = request.form.get('category','')
    desc     = request.form.get('description','').strip()
    amount   = request.form.get('amount', 0)
    exp_date = request.form.get('expense_date') or date.today().isoformat()
    if not amount:
        flash('Amount is required.', 'error')
        return redirect(url_for('expenses'))
    with get_db() as db:
        db.execute(
            "INSERT INTO expenses (category, description, amount, expense_date, created_by) VALUES (?,?,?,?,?)",
            (category, desc, float(amount), exp_date, session['user_id']))
        db.commit()
    flash('Expense added!', 'success')
    return redirect(url_for('expenses'))

@app.route('/expenses/delete/<int:eid>', methods=['POST'])
@login_required
@owner_required
def delete_expense(eid):
    with get_db() as db:
        db.execute("DELETE FROM expenses WHERE id=?", (eid,))
        db.commit()
    flash('Expense deleted.', 'success')
    return redirect(url_for('expenses'))

# ─── BARBERS (OWNER ONLY) ─────────────────────────────────────────────────────

@app.route('/barbers')
@login_required
@owner_required
def barbers():
    today_str   = date.today().isoformat()
    week_start  = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    with get_db() as db:
        rows = db.execute("SELECT * FROM users WHERE role='barber' ORDER BY name").fetchall()
        stats = {}
        for b in rows:
            today_inc = db.execute("SELECT SUM(amount) as t FROM records WHERE barber_id=? AND DATE(recorded_at)=?", (b['id'], today_str)).fetchone()['t'] or 0
            week_inc  = db.execute("SELECT SUM(amount) as t FROM records WHERE barber_id=? AND DATE(recorded_at)>=?", (b['id'], week_start)).fetchone()['t'] or 0
            all_inc   = db.execute("SELECT SUM(amount) as t, COUNT(*) as c FROM records WHERE barber_id=?", (b['id'],)).fetchone()
            stats[b['id']] = {'today': today_inc, 'week': week_inc, 'total': all_inc['t'] or 0, 'cuts': all_inc['c'] or 0}
    return render_template('barbers.html', barbers=rows, stats=stats)

@app.route('/barbers/add', methods=['POST'])
@login_required
@owner_required
def add_barber():
    name       = request.form.get('name','').strip()
    username   = request.form.get('username','').strip()
    password   = request.form.get('password','').strip()
    phone      = request.form.get('phone','').strip()
    commission = request.form.get('commission', 0)
    role       = request.form.get('role', 'barber')
    if not name or not username or not password:
        flash('Name, username and password are required.', 'error')
        return redirect(url_for('barbers'))
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (name, username, password, role, phone, commission) VALUES (?,?,?,?,?,?)",
                (name, username, generate_password_hash(password), role, phone, float(commission or 0)))
            db.commit()
        flash(f'{name} added successfully!', 'success')
    except sqlite3.IntegrityError:
        flash('Username already exists.', 'error')
    return redirect(url_for('barbers'))

@app.route('/barbers/delete/<int:bid>', methods=['POST'])
@login_required
@owner_required
def delete_barber(bid):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=? AND role='barber'", (bid,))
        db.commit()
    flash('Barber removed.', 'success')
    return redirect(url_for('barbers'))

# ─── REPORTS ──────────────────────────────────────────────────────────────────

@app.route('/reports')
@login_required
@owner_required
def reports():
    period = request.args.get('period', 'day')
    today_str   = date.today().isoformat()
    week_start  = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    month_start = date.today().replace(day=1).isoformat()

    if period == 'day':   date_filter = today_str
    elif period == 'week':  date_filter = week_start
    else:                   date_filter = month_start

    with get_db() as db:
        income_row = db.execute(
            "SELECT SUM(amount) as t, COUNT(*) as c FROM records WHERE DATE(recorded_at)>=?",
            (date_filter,)).fetchone()
        exp_row = db.execute(
            "SELECT SUM(amount) as t FROM expenses WHERE expense_date>=?",
            (date_filter,)).fetchone()
        barber_perf = db.execute("""
            SELECT u.name, SUM(r.amount) as total, COUNT(*) as cnt,
                   AVG(r.amount) as avg
            FROM records r JOIN users u ON r.barber_id=u.id
            WHERE DATE(r.recorded_at) >= ?
            GROUP BY r.barber_id ORDER BY total DESC
        """, (date_filter,)).fetchall()
        services = db.execute("""
            SELECT service, SUM(amount) as total, COUNT(*) as cnt
            FROM records WHERE DATE(recorded_at) >= ?
            GROUP BY service ORDER BY total DESC
        """, (date_filter,)).fetchall()
        exp_cats = db.execute("""
            SELECT category, SUM(amount) as total, COUNT(*) as cnt
            FROM expenses WHERE expense_date >= ?
            GROUP BY category ORDER BY total DESC
        """, (date_filter,)).fetchall()
        all_records = db.execute("""
            SELECT r.*, u.name as barber_name FROM records r
            JOIN users u ON r.barber_id=u.id
            WHERE DATE(r.recorded_at) >= ?
            ORDER BY r.recorded_at DESC
        """, (date_filter,)).fetchall()
        all_expenses = db.execute(
            "SELECT * FROM expenses WHERE expense_date >= ? ORDER BY expense_date DESC",
            (date_filter,)).fetchall()

    income = income_row['t'] or 0
    exp_total = exp_row['t'] or 0
    return render_template('reports.html',
        period=period, income=income, income_cnt=income_row['c'] or 0,
        exp_total=exp_total, net=income-exp_total,
        barber_perf=barber_perf, services=services, exp_cats=exp_cats,
        all_records=all_records, all_expenses=all_expenses)

# ─── PROFILE / CHANGE PASSWORD ────────────────────────────────────────────────

@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    if request.method == 'POST':
        current = request.form.get('current_password','')
        new_pw  = request.form.get('new_password','').strip()
        confirm = request.form.get('confirm_password','').strip()
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
        if not check_password_hash(user['password'], current):
            flash('Current password is incorrect.', 'error')
        elif new_pw != confirm:
            flash('New passwords do not match.', 'error')
        elif len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'error')
        else:
            with get_db() as db:
                db.execute("UPDATE users SET password=? WHERE id=?",
                    (generate_password_hash(new_pw), session['user_id']))
                db.commit()
            flash('Password changed successfully!', 'success')
    return render_template('profile.html')

if __name__ == '__main__':
    app.run(debug=True)
