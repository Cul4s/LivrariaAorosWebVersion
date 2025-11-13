from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for
import sqlite3
from pathlib import Path
import os
import shutil
import csv
from datetime import datetime
import io
import time

# opcional: reportlab para gerar PDF
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

# ---------------- Config ----------------
ROOT = Path.cwd() / "meu_sistema_livraria"
DATA_DIR = ROOT / "data"
BACKUP_DIR = ROOT / "backups"
EXPORT_DIR = ROOT / "exports"
DB_FILE = DATA_DIR / "livraria.db"
BACKUP_PREFIX = "backup_livraria_"
MAX_BACKUPS_TO_KEEP = 5
CSV_EXPORT_FILE = EXPORT_DIR / "livros_exportados.csv"
HTML_REPORT_FILE = EXPORT_DIR / "relatorio_livros.html"
PDF_REPORT_FILE = EXPORT_DIR / "relatorio_livros.pdf"

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------- utilities ----------------
def ensure_directories():
    for p in (ROOT, DATA_DIR, BACKUP_DIR, EXPORT_DIR):
        os.makedirs(p, exist_ok=True)

def get_connection():
    ensure_directories()
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Cria e/ou atualiza as tabelas no banco de dados."""
    ensure_directories()
    with get_connection() as conn:
        cur = conn.cursor()
        
        # 1. Tabela LIVROS (Estrutura Atualizada)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS livros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT UNIQUE NOT NULL, 
                isbn TEXT UNIQUE,             
                title TEXT NOT NULL,          
                author TEXT NOT NULL,         
                genre TEXT,
                publisher TEXT,
                ano_publicacao INTEGER,
                preco REAL,
                estoque INTEGER
            )
        """)
        
        # 2. Tabela VENDAS 
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vendas (
                sale_id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                book_id TEXT,
                title TEXT,
                author TEXT,
                genre TEXT,
                publisher TEXT,
                isbn TEXT,
                quantity INTEGER,
                unit_price REAL,
                discount_pct REAL,
                discount_amount REAL,
                total_amount REAL,
                customer_id TEXT,
                customer_age INTEGER,
                customer_gender TEXT,
                city TEXT,
                payment_method TEXT,
                channel TEXT,
                promo_code TEXT,
                returned TEXT,
                rating REAL,
                stock_before INTEGER,
                stock_after INTEGER
            )
        """)
        
        # 3. Tabela CLIENTES
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                customer_id TEXT PRIMARY KEY,
                customer_age INTEGER,
                customer_gender TEXT,
                city TEXT
            )
        """)
        
        conn.commit()

# ---------------- backups ----------------
def backup_db(reason="manual"):
    ensure_directories()
    if not DB_FILE.exists():
        init_db()
        time.sleep(0.05)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = f"{BACKUP_PREFIX}{ts}.db"
    path = BACKUP_DIR / name
    shutil.copy2(DB_FILE, path)
    prune_old_backups()
    return str(path)

def prune_old_backups():
    files = sorted(BACKUP_DIR.glob(f"{BACKUP_PREFIX}*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[MAX_BACKUPS_TO_KEEP:]:
        try:
            old.unlink()
        except:
            pass

# ---------------- validation ----------------
def validar_ano(ano_str):
    try:
        ano = int(ano_str)
        if 1000 <= ano <= datetime.now().year + 1:
            return ano
    except:
        pass
    return None

def validar_preco(preco_str):
    try:
        preco = float(preco_str)
        if preco >= 0:
            return preco
    except:
        pass
    return None

# ---------------- DB ops ----------------
def listar_livros():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, book_id, title, author, genre, publisher, isbn, ano_publicacao, preco, estoque FROM livros ORDER BY id")
        return [tuple(row) for row in cur.fetchall()]

def add_livro(title, author, isbn, book_id, genre, publisher, ano=None, preco=None, estoque=None):
    backup_db(reason="add_web")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO livros (book_id, isbn, title, author, genre, publisher, ano_publicacao, preco, estoque) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (book_id.strip(), isbn.strip(), title.strip(), author.strip(), genre.strip(), publisher.strip(), ano, preco, estoque))
        conn.commit()
        return cur.lastrowid

def update_preco(livro_id, novo_preco):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM livros WHERE id = ?", (livro_id,))
        if cur.fetchone() is None:
            return False
    backup_db(reason="update_web")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE livros SET preco = ? WHERE id = ?", (novo_preco, livro_id))
        conn.commit()
        return True

def delete_livro(livro_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM livros WHERE id = ?", (livro_id,))
        if cur.fetchone() is None:
            return False
    backup_db(reason="delete_web")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM livros WHERE id = ?", (livro_id,))
        conn.commit()
        return True

def search_autor(q):
    with get_connection() as conn:
        cur = conn.cursor()
        like = f"%{q}%"
        cur.execute("SELECT id, book_id, title, author, genre, publisher, isbn, ano_publicacao, preco, estoque FROM livros WHERE author LIKE ? ORDER BY id", (like,))
        return [tuple(row) for row in cur.fetchall()]

# ---------------- CSV ----------------
def export_csv_to_memory():
    rows = listar_livros()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "book_id", "title", "author", "genre", "publisher", "isbn", "ano_publicacao", "preco", "estoque"])
    for _id, book_id, title, author, genre, publisher, isbn, ano, preco, estoque in rows:
        writer.writerow([_id, book_id or '', title, author, genre or '', publisher or '', isbn or '', ano or '', preco or '', estoque or ''])
    return output.getvalue().encode("utf-8")

def detect_delimiter(sample_line):
    return "," if sample_line.count(",") >= sample_line.count(";") else ";"

def import_csv_file(file_stream, filename):
    """Importa dados, detectando se é um arquivo de 'livros', 'vendas' ou 'clientes'."""
    text = file_stream.read().decode("utf-8")
    lines = text.splitlines()
    if not lines:
        return 0
    
    delim = detect_delimiter(lines[0])
    
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = list(reader)
    if not rows:
        return 0
    
    fieldnames = [f.lower() for f in reader.fieldnames]
    
    # Detecção do tipo de arquivo
    is_vendas = 'sale_id' in fieldnames and 'total_amount' in fieldnames
    is_livros = 'isbn' in fieldnames and 'title' in fieldnames and 'author' in fieldnames
    is_clientes = 'customer_id' in fieldnames and 'customer_age' in fieldnames
    
    if not is_vendas and not is_livros and not is_clientes:
        return 0 

    backup_db(reason="import_web")
    inserted = 0
    with get_connection() as conn:
        cur = conn.cursor()

        if is_vendas:
            # Lógica de importação para VENDAS E CLIENTES (via arquivo de vendas)
            vendas_cols = [
                'sale_id', 'timestamp', 'book_id', 'title', 'author', 'genre', 'publisher', 'isbn', 
                'quantity', 'unit_price', 'discount_pct', 'discount_amount', 'total_amount', 
                'customer_id', 'customer_age', 'customer_gender', 'city', 'payment_method', 
                'channel', 'promo_code', 'returned', 'rating', 'stock_before', 'stock_after'
            ]
            insert_vendas_sql = f"INSERT INTO vendas ({', '.join(vendas_cols)}) VALUES ({', '.join('?' * len(vendas_cols))})"
            insert_clientes_sql = """
                INSERT OR REPLACE INTO clientes (customer_id, customer_age, customer_gender, city) 
                VALUES (?, ?, ?, ?)
            """
            
            for r in rows:
                try:
                    row_data = {k.lower(): v for k, v in r.items()}
                    
                    # 1. Processa Cliente (INSERT OR REPLACE)
                    customer_id = row_data.get('customer_id') or None
                    if customer_id:
                        cliente_data = [
                            customer_id,
                            int(row_data.get('customer_age')) if row_data.get('customer_age') else None,
                            row_data.get('customer_gender') or None,
                            row_data.get('city') or None
                        ]
                        cur.execute(insert_clientes_sql, cliente_data)
                    
                    # 2. Processa Venda
                    data = [
                        int(row_data.get('sale_id')) if row_data.get('sale_id') else 0,
                        row_data.get('timestamp') or None,
                        row_data.get('book_id') or None,
                        row_data.get('title') or None,
                        row_data.get('author') or None,
                        row_data.get('genre') or None,
                        row_data.get('publisher') or None,
                        row_data.get('isbn') or None,
                        int(row_data.get('quantity')) if row_data.get('quantity') else None,
                        float(row_data.get('unit_price')) if row_data.get('unit_price') else None,
                        float(row_data.get('discount_pct')) if row_data.get('discount_pct') else None,
                        float(row_data.get('discount_amount')) if row_data.get('discount_amount') else None,
                        float(row_data.get('total_amount')) if row_data.get('total_amount') else None,
                        customer_id,
                        int(row_data.get('customer_age')) if row_data.get('customer_age') else None,
                        row_data.get('customer_gender') or None,
                        row_data.get('city') or None,
                        row_data.get('payment_method') or None,
                        row_data.get('channel') or None,
                        row_data.get('promo_code') or None,
                        row_data.get('returned') or 'False', 
                        float(row_data.get('rating')) if row_data.get('rating') else None,
                        int(row_data.get('stock_before')) if row_data.get('stock_before') else None,
                        int(row_data.get('stock_after')) if row_data.get('stock_after') else None
                    ]
                    
                    if data[1] is None or data[0] == 0: continue 

                    cur.execute(insert_vendas_sql, data)
                    inserted += 1
                except (ValueError, sqlite3.IntegrityError):
                    pass

        elif is_livros:
            # Lógica de importação para a tabela LIVROS (Novo esquema)
            for r in rows:
                book_id = r.get("book_id") or ""
                isbn = r.get("isbn") or ""
                title = (r.get("title") or r.get("titulo") or "").strip()
                author = (r.get("author") or r.get("autor") or "").strip()
                genre = r.get("genre") or ""
                publisher = r.get("publisher") or ""
                ano_raw = r.get("ano_publicacao") or r.get("year") or ""
                preco_raw = r.get("preco") or r.get("price") or ""
                estoque_raw = r.get("estoque") or r.get("stock") or ""
                
                ano = validar_ano(ano_raw) if ano_raw != "" else None
                preco = validar_preco(preco_raw) if preco_raw != "" else None
                estoque = int(estoque_raw) if estoque_raw.isdigit() else None
                
                if title and author and book_id and isbn:
                    try:
                        cur.execute("""
                            INSERT INTO livros (book_id, isbn, title, author, genre, publisher, ano_publicacao, preco, estoque) 
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (book_id, isbn, title, author, genre, publisher, ano, preco, estoque))
                        inserted += 1
                    except sqlite3.IntegrityError:
                        pass
        
        elif is_clientes:
            # Lógica de importação para a tabela CLIENTES (Arquivo isolado)
            insert_clientes_sql = """
                INSERT OR REPLACE INTO clientes (customer_id, customer_age, customer_gender, city) 
                VALUES (?, ?, ?, ?)
            """
            for r in rows:
                try:
                    row_data = {k.lower(): v for k, v in r.items()}
                    customer_id = row_data.get('customer_id') or None

                    if customer_id:
                        cliente_data = [
                            customer_id,
                            int(row_data.get('customer_age')) if row_data.get('customer_age') else None,
                            row_data.get('customer_gender') or None,
                            row_data.get('city') or None
                        ]
                        cur.execute(insert_clientes_sql, cliente_data)
                        inserted += 1
                except (ValueError, sqlite3.IntegrityError):
                    pass
        
        conn.commit()
    return inserted

# ---------------- Routes ----------------
@app.route("/")
def index():
    init_db()
    return render_template("index.html")

@app.route("/api/books", methods=["GET"])
def api_list():
    rows = listar_livros()
    books = [{"id":r[0],"book_id":r[1],"title":r[2],"author":r[3],"genre":r[4],"publisher":r[5],"isbn":r[6],"ano_publicacao":r[7],"preco":r[8],"estoque":r[9]} for r in rows]
    return jsonify(books)

@app.route("/api/books", methods=["POST"])
def api_add():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    author = (data.get("author") or "").strip()
    isbn = (data.get("isbn") or "").strip()
    book_id = (data.get("book_id") or "").strip()
    genre = (data.get("genre") or "").strip()
    publisher = (data.get("publisher") or "").strip()
    ano = data.get("ano_publicacao")
    preco = data.get("preco")
    estoque = data.get("estoque")

    if not title or not author or not isbn or not book_id:
        return jsonify({"error":"Título, autor, ISBN e Book ID são obrigatórios."}), 400
    
    ano_val = validar_ano(ano) if ano not in (None, "") else None
    preco_val = validar_preco(preco) if preco not in (None, "") else None
    estoque_val = int(estoque) if isinstance(estoque, (int, str)) and str(estoque).isdigit() else None
    
    book_id_val = add_livro(title, author, isbn, book_id, genre, publisher, ano_val, preco_val, estoque_val)
    return jsonify({"id": book_id_val}), 201

@app.route("/api/books/<int:book_id>/price", methods=["PUT"])
def api_update(book_id):
    data = request.json or {}
    preco = data.get("preco")
    preco_val = validar_preco(preco)
    if preco_val is None:
        return jsonify({"error":"Preço inválido."}), 400
    ok = update_preco(book_id, preco_val)
    if not ok:
        return jsonify({"error":"Livro não encontrado."}), 404
    return jsonify({"ok": True})

@app.route("/api/books/<int:book_id>", methods=["DELETE"])
def api_delete(book_id):
    ok = delete_livro(book_id)
    if not ok:
        return jsonify({"error":"Livro não encontrado."}), 404
    return jsonify({"ok": True})

@app.route("/api/search", methods=["GET"])
def api_search():
    q = request.args.get("q","").strip()
    rows = search_autor(q) if q else []
    books = [{"id":r[0],"book_id":r[1],"title":r[2],"author":r[3],"genre":r[4],"publisher":r[5],"isbn":r[6],"ano_publicacao":r[7],"preco":r[8],"estoque":r[9]} for r in rows]
    return jsonify(books)

@app.route("/api/export", methods=["GET"])
def api_export():
    data = export_csv_to_memory()
    return send_file(io.BytesIO(data), as_attachment=True, download_name="livros_exportados.csv", mimetype="text/csv")

@app.route("/api/import", methods=["POST"])
def api_import():
    if "file" not in request.files:
        return jsonify({"error":"Nenhum arquivo enviado."}), 400
    f = request.files["file"]
    # Esta função agora trata a importação de 'livros', 'vendas' e 'clientes'
    inserted = import_csv_file(f.stream, f.filename)
    return jsonify({"inserted": inserted})

@app.route("/api/backup", methods=["GET"])
def api_backup():
    p = backup_db(reason="manual_api")
    return jsonify({"backup": p})

@app.route("/api/backups", methods=["GET"])
def api_list_backups():
    ensure_directories()
    files = sorted(BACKUP_DIR.glob(f"{BACKUP_PREFIX}*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([str(p) for p in files])

@app.route("/api/report/html", methods=["GET"])
def api_report_html():
    path = gerar_relatorio_html()
    return send_file(path, as_attachment=True, download_name="relatorio_livros.html", mimetype="text/html")

@app.route("/api/report/pdf", methods=["GET"])
def api_report_pdf():
    if not REPORTLAB_AVAILABLE:
        return jsonify({"error":"reportlab não instalado. pip install reportlab"}), 400
    path = gerar_relatorio_pdf()
    return send_file(path, as_attachment=True, download_name="relatorio_livros.pdf", mimetype="application/pdf")

# Run
if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
