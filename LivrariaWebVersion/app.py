# app.py
from flask import Flask, request, jsonify, send_file, render_template
import sqlite3
from pathlib import Path
import os
import shutil
from datetime import datetime
import csv
import io

# Config paths (usar mesmo padrão da versão CLI)
ROOT = Path.cwd() / "meu_sistema_livraria"
DATA_DIR = ROOT / "data"
BACKUP_DIR = ROOT / "backups"
EXPORT_DIR = ROOT / "exports"
DB_FILE = DATA_DIR / "livraria.db"
BACKUP_PREFIX = "backup_livraria_"
MAX_BACKUPS_TO_KEEP = 5
CSV_EXPORT_FILE = EXPORT_DIR / "livros_exportados.csv"

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------- Utilities ----------
def ensure_directories():
    for p in (ROOT, DATA_DIR, BACKUP_DIR, EXPORT_DIR):
        os.makedirs(p, exist_ok=True)

def get_connection():
    ensure_directories()
    return sqlite3.connect(str(DB_FILE))

def init_db():
    ensure_directories()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS livros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                autor TEXT NOT NULL,
                ano_publicacao INTEGER,
                preco REAL
            )
        """)
        conn.commit()

def backup_db(reason="manual"):
    ensure_directories()
    if not DB_FILE.exists():
        init_db()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_name = f"{BACKUP_PREFIX}{timestamp}.db"
    backup_path = BACKUP_DIR / backup_name
    shutil.copy2(DB_FILE, backup_path)
    prune_old_backups()
    return str(backup_path)

def prune_old_backups():
    backups = sorted(BACKUP_DIR.glob(f"{BACKUP_PREFIX}*.db"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[MAX_BACKUPS_TO_KEEP:]:
        try:
            old.unlink()
        except:
            pass

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

# ---------- API endpoints ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/books", methods=["GET"])
def api_list_books():
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, titulo, autor, ano_publicacao, preco FROM livros ORDER BY id")
        rows = cur.fetchall()
    books = []
    for r in rows:
        books.append({
            "id": r[0],
            "titulo": r[1],
            "autor": r[2],
            "ano_publicacao": r[3],
            "preco": r[4]
        })
    return jsonify(books)

@app.route("/api/books", methods=["POST"])
def api_add_book():
    data = request.json or {}
    titulo = (data.get("titulo") or "").strip()
    autor = (data.get("autor") or "").strip()
    ano = data.get("ano_publicacao")
    preco = data.get("preco")
    if not titulo or not autor:
        return jsonify({"error":"Título e autor são obrigatórios."}), 400
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
    backup_db(reason="add_api")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO livros (titulo, autor, ano_publicacao, preco) VALUES (?,?,?,?)",
                    (titulo, autor, ano_val, preco_val))
        conn.commit()
        book_id = cur.lastrowid
    return jsonify({"id": book_id}), 201

@app.route("/api/books/<int:book_id>/price", methods=["PUT"])
def api_update_price(book_id):
    data = request.json or {}
    preco = data.get("preco")
    preco_val = validar_preco(preco)
    if preco_val is None:
        return jsonify({"error":"Preço inválido."}), 400
    # verify exists
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM livros WHERE id = ?", (book_id,))
        if cur.fetchone() is None:
            return jsonify({"error":"Livro não encontrado."}), 404
    backup_db(reason="update_price_api")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE livros SET preco = ? WHERE id = ?", (preco_val, book_id))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/books/<int:book_id>", methods=["DELETE"])
def api_delete_book(book_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM livros WHERE id = ?", (book_id,))
        if cur.fetchone() is None:
            return jsonify({"error":"Livro não encontrado."}), 404
    backup_db(reason="delete_api")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM livros WHERE id = ?", (book_id,))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/search", methods=["GET"])
def api_search_author():
    q = request.args.get("q", "").strip()
    with get_connection() as conn:
        cur = conn.cursor()
        like = f"%{q}%"
        cur.execute("SELECT id, titulo, autor, ano_publicacao, preco FROM livros WHERE autor LIKE ? ORDER BY id", (like,))
        rows = cur.fetchall()
    books = [{"id":r[0],"titulo":r[1],"autor":r[2],"ano_publicacao":r[3],"preco":r[4]} for r in rows]
    return jsonify(books)

@app.route("/api/export", methods=["GET"])
def api_export_csv():
    books = []
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT titulo, autor, ano_publicacao, preco FROM livros ORDER BY id")
        books = cur.fetchall()
    # build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["titulo","autor","ano_publicacao","preco"])
    for titulo, autor, ano, preco in books:
        writer.writerow([titulo, autor, ano if ano is not None else "", preco if preco is not None else ""])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        as_attachment=True,
        download_name="livros_exportados.csv",
        mimetype="text/csv"
    )

@app.route("/api/import", methods=["POST"])
def api_import_csv():
    if "file" not in request.files:
        return jsonify({"error":"Nenhum arquivo enviado."}), 400
    f = request.files["file"]
    data = f.read().decode("utf-8")
    # tenta detectar ; ou , delimitador
    sample = data.splitlines()[0] if data else ""
    delimiter = "," if sample.count(",") >= sample.count(";") else ";"
    reader = csv.DictReader(io.StringIO(data), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return jsonify({"inserted":0})
    backup_db(reason="import_api")
    inserted = 0
    with get_connection() as conn:
        cur = conn.cursor()
        for r in rows:
            titulo = (r.get("titulo") or r.get("title") or "").strip()
            autor = (r.get("autor") or r.get("author") or "").strip()
            ano_raw = r.get("ano_publicacao") or r.get("year") or ""
            preco_raw = r.get("preco") or r.get("price") or ""
            ano = validar_ano(ano_raw) if ano_raw != "" else None
            preco = validar_preco(preco_raw) if preco_raw != "" else None
            cur.execute("INSERT INTO livros (titulo, autor, ano_publicacao, preco) VALUES (?,?,?,?)",
                        (titulo, autor, ano, preco))
            inserted += 1
        conn.commit()
    return jsonify({"inserted": inserted})

@app.route("/api/backup", methods=["GET"])
def api_backup():
    path = backup_db(reason="manual_api")
    return jsonify({"backup": path})

# Run init
init_db()

if __name__ == "__main__":
    # debug True para desenvolvimento; remova em produção
    app.run(debug=True)
