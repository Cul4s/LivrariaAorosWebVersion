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
    # Usar row_factory para facilitar a leitura dos resultados (opcional, mas bom)
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa o banco de dados e cria as tabelas 'livros' e 'vendas'."""
    ensure_directories()
    with get_connection() as conn:
        cur = conn.cursor()
        
        # Tabela LIVROS (original)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS livros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                autor TEXT NOT NULL,
                ano_publicacao INTEGER,
                preco REAL
            )
        """)
        
        # Nova Tabela VENDAS
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
        # Usamos o row_factory acima, mas a lógica de retorno do Flask exige a lista de tuplas
        cur.execute("SELECT id, titulo, autor, ano_publicacao, preco FROM livros ORDER BY id")
        # Converte para lista de tuplas se row_factory estiver em uso, para compatibilidade com o código original
        return [tuple(row) for row in cur.fetchall()]

def add_livro(titulo, autor, ano, preco):
    backup_db(reason="add_web")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO livros (titulo, autor, ano_publicacao, preco) VALUES (?, ?, ?, ?)",
                    (titulo.strip(), autor.strip(), ano, preco))
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
        cur.execute("SELECT id, titulo, autor, ano_publicacao, preco FROM livros WHERE autor LIKE ? ORDER BY id", (like,))
        return [tuple(row) for row in cur.fetchall()]

# ---------------- CSV ----------------
def export_csv_to_memory():
    rows = listar_livros()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["titulo", "autor", "ano_publicacao", "preco"])
    for _id, titulo, autor, ano, preco in rows:
        writer.writerow([titulo, autor, ano if ano is not None else "", preco if preco is not None else ""])
    return output.getvalue().encode("utf-8")

def detect_delimiter(sample_line):
    return "," if sample_line.count(",") >= sample_line.count(";") else ";"

def import_csv_file(file_stream, filename):
    """Importa dados, detectando se é um arquivo de 'livros' ou 'vendas'."""
    text = file_stream.read().decode("utf-8")
    lines = text.splitlines()
    if not lines:
        return 0
    
    delim = detect_delimiter(lines[0])
    
    # Usa DictReader para leitura
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = list(reader)
    if not rows:
        return 0
    
    # 1. Determina o tipo de tabela com base nos nomes das colunas
    fieldnames = [f.lower() for f in reader.fieldnames]
    
    is_vendas = 'sale_id' in fieldnames and 'total_amount' in fieldnames
    is_livros = 'titulo' in fieldnames or 'title' in fieldnames
    
    if not is_vendas and not is_livros:
        return 0 # Formato desconhecido

    backup_db(reason="import_web")
    inserted = 0
    with get_connection() as conn:
        cur = conn.cursor()

        if is_vendas:
            # Lógica de importação para a tabela VENDAS
            vendas_cols = [
                'sale_id', 'timestamp', 'book_id', 'title', 'author', 'genre', 'publisher', 'isbn', 
                'quantity', 'unit_price', 'discount_pct', 'discount_amount', 'total_amount', 
                'customer_id', 'customer_age', 'customer_gender', 'city', 'payment_method', 
                'channel', 'promo_code', 'returned', 'rating', 'stock_before', 'stock_after'
            ]
            insert_sql = f"INSERT INTO vendas ({', '.join(vendas_cols)}) VALUES ({', '.join('?' * len(vendas_cols))})"
            
            for r in rows:
                try:
                    # Normaliza as chaves da linha para acesso case-insensitive
                    row_data = {k.lower(): v for k, v in r.items()}
                    
                    # Prepara os dados, convertendo tipos e tratando valores vazios/nulos
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
                        row_data.get('customer_id') or None,
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
                    
                    # Validação mínima para campos NOT NULL
                    if data[1] is None or data[0] == 0:
                        continue 

                    cur.execute(insert_sql, data)
                    inserted += 1
                except (ValueError, sqlite3.IntegrityError):
                    # Ignora linhas com erro de conversão de tipo ou PK duplicada
                    pass

        elif is_livros:
            # Lógica de importação original para a tabela LIVROS
            for r in rows:
                titulo = (r.get("titulo") or r.get("title") or "").strip()
                autor = (r.get("autor") or r.get("author") or "").strip()
                ano_raw = r.get("ano_publicacao") or r.get("year") or ""
                preco_raw = r.get("preco") or r.get("price") or ""
                
                ano = validar_ano(ano_raw) if ano_raw != "" else None
                preco = validar_preco(preco_raw) if preco_raw != "" else None
                
                if titulo and autor:
                    cur.execute("INSERT INTO livros (titulo, autor, ano_publicacao, preco) VALUES (?, ?, ?, ?)",
                                (titulo, autor, ano, preco))
                    inserted += 1
        
        conn.commit()
    return inserted

# ---------------- Reports ----------------
def gerar_relatorio_html():
    rows = listar_livros()
    ensure_directories()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = ["<!doctype html><html><head><meta charset='utf-8'><title>Relatório</title>",
            "<style>body{font-family:Arial;padding:20px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px}th{background:#f8f9fa}</style>",
            "</head><body>",
            f"<h1>Relatório de Livros</h1><p>Gerado em {now}</p>",
            "<table><thead><tr><th>ID</th><th>Título</th><th>Autor</th><th>Ano</th><th>Preço</th></tr></thead><tbody>"]
    for _id, titulo, autor, ano, preco in rows:
        preco_s = f"R$ {preco:.2f}" if preco is not None else ""
        html.append(f"<tr><td>{_id}</td><td>{titulo}</td><td>{autor}</td><td>{ano or ''}</td><td>{preco_s}</td></tr>")
    html.append("</tbody></table></body></html>")
    path = EXPORT_DIR / "relatorio_livros.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    return str(path)

def gerar_relatorio_pdf():
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("reportlab não está instalado. pip install reportlab")
    rows = listar_livros()
    ensure_directories()
    path = EXPORT_DIR / "relatorio_livros.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    elems = [Paragraph("Relatório de Livros", styles['Title']),
             Paragraph(f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']),
             Spacer(1,6)]
    data = [["ID","Título","Autor","Ano","Preço"]]
    for _id, titulo, autor, ano, preco in rows:
        preco_s = f"R$ {preco:.2f}" if preco is not None else ""
        data.append([str(_id), titulo, autor, str(ano) if ano else "", preco_s])
    table = Table(data, colWidths=[30*mm, 70*mm, 60*mm, 25*mm, 30*mm])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor("#f2f2f2")),
                               ('GRID',(0,0),(-1,-1),0.4,colors.grey),
                               ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                               ('ALIGN',(-2,1),(-1,-1),'RIGHT')]))
    elems.append(table)
    doc.build(elems)
    return str(path)

# ---------------- Routes ----------------
@app.route("/")
def index():
    init_db()
    return render_template("index.html")

@app.route("/api/books", methods=["GET"])
def api_list():
    rows = listar_livros()
    books = [{"id":r[0],"titulo":r[1],"autor":r[2],"ano_publicacao":r[3],"preco":r[4]} for r in rows]
    return jsonify(books)

@app.route("/api/books", methods=["POST"])
def api_add():
    data = request.json or {}
    titulo = (data.get("titulo") or "").strip()
    autor = (data.get("autor") or "").strip()
    ano = data.get("ano_publicacao")
    preco = data.get("preco")
    if not titulo or not autor:
        return jsonify({"error":"Título e autor obrigatórios."}), 400
    ano_val = None
    if ano not in (None, ""):
        ano_val = validar_ano(ano)
        if ano_val is None:
            return jsonify({"error":"Ano inválido."}), 400
    preco_val = None
    if preco not in (None, ""):
        preco_val = validar_preco(preco)
        if preco_val is None:
            return jsonify({"error":"Preço inválido."}), 400
    book_id = add_livro(titulo, autor, ano_val, preco_val)
    return jsonify({"id": book_id}), 201

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
    books = [{"id":r[0],"titulo":r[1],"autor":r[2],"ano_publicacao":r[3],"preco":r[4]} for r in rows]
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
    # A função import_csv_file agora trata a importação de dados de 'livros' e 'vendas'
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
    # rodar com python app.py
    init_db()
    # Para evitar problemas com watchdog em algumas instalações, recomendamos rodar com python app.py
    app.run(host="127.0.0.1", port=5000, debug=True)
