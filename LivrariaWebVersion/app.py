from flask import Flask, request, jsonify, send_file, render_template
import sqlite3
from pathlib import Path
import os
import shutil
import csv
from datetime import datetime
import io
import time
from contextlib import contextmanager
from typing import List, Tuple, Optional, Dict, Any
import logging

# ======== Configuração de logging ===========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======== Tentativa de import =========
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("ReportLab não disponível - funcionalidade PDF desativada")

# ============ CONFIGURAÇÃO ============
class Config:
    """Configurações centralizadas da aplicação"""
    ROOT = Path.cwd() / "meu_sistema_livraria"
    DATA_DIR = ROOT / "data"
    BACKUP_DIR = ROOT / "backups"
    EXPORT_DIR = ROOT / "exports"
    DB_FILE = DATA_DIR / "livraria.db"
    BACKUP_PREFIX = "backup_livraria_"
    MAX_BACKUPS_TO_KEEP = 5
    UPLOAD_FOLDER = 'uploads/'
    MODEL_FOLDER = 'models/'

# ============ VALIDAÇÃO ============
class Validators:
    """Classe para validações centralizadas"""
    
    @staticmethod
    def validar_ano(ano_str: str) -> Optional[int]:
        """Valida e converte ano"""
        try:
            ano = int(ano_str)
            if 1000 <= ano <= datetime.now().year + 1:
                return ano
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def validar_preco(preco_str: str) -> Optional[float]:
        """Valida e converte preço"""
        try:
            preco = float(preco_str)
            if preco >= 0:
                return preco
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def validar_livro_data(data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """Valida dados do livro de forma completa"""
        titulo = (data.get("titulo") or "").strip()
        autor = (data.get("autor") or "").strip()
        
        if not titulo or not autor:
            return False, "Título e autor são obrigatórios", {}
        
        # ======== Validação de ano ===========
        ano = data.get("ano_publicacao")
        ano_val = None
        if ano not in (None, ""):
            ano_val = Validators.validar_ano(ano)
            if ano_val is None:
                return False, "Ano de publicação inválido", {}

        # ======== Validação de preço ==========
        preco = data.get("preco")
        preco_val = None
        if preco not in (None, ""):
            preco_val = Validators.validar_preco(preco)
            if preco_val is None:
                return False, "Preço inválido", {}

        validated_data = {
            "titulo": titulo,
            "autor": autor,
            "ano_publicacao": ano_val,
            "preco": preco_val
        }
        
        return True, "", validated_data

# ============ BANCO DE DADOS ============
class DatabaseManager:
    """Gerenciador centralizado de operações de banco de dados"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Garante que os diretórios necessários existam"""
        for directory in [Config.ROOT, Config.DATA_DIR, Config.BACKUP_DIR, Config.EXPORT_DIR]:
            directory.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def get_connection(self):
        """Context manager para conexões de banco de dados"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Para acesso por nome de coluna
        try:
            yield conn
        except Exception as e:
            conn.rollback()
            logger.error(f"Erro no banco de dados: {e}")
            raise
        finally:
            conn.close()
    
    def init_db(self):
        """Inicializa o banco de dados com tabelas necessárias"""
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS livros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo TEXT NOT NULL,
                    autor TEXT NOT NULL,
                    ano_publicacao INTEGER,
                    preco REAL,
                    data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        logger.info("Banco de dados inicializado")

# ============ BACKUP ============
class BackupManager:
    """Gerenciador de backups do banco de dados"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.backup_dir = Config.BACKUP_DIR
    
    def create_backup(self, reason: str = "manual") -> str:
        """Cria backup do banco de dados"""
        self.db_manager.init_db()
        time.sleep(0.05)
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_name = f"{Config.BACKUP_PREFIX}{timestamp}_{reason}.db"
        backup_path = self.backup_dir / backup_name
        
        shutil.copy2(self.db_manager.db_path, backup_path)
        self._prune_old_backups()
        
        logger.info(f"Backup criado: {backup_path} (motivo: {reason})")
        return str(backup_path)
    
    def _prune_old_backups(self):
        """Remove backups antigos mantendo apenas os mais recentes"""
        backups = sorted(
            self.backup_dir.glob(f"{Config.BACKUP_PREFIX}*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        for old_backup in backups[Config.MAX_BACKUPS_TO_KEEP:]:
            try:
                old_backup.unlink()
                logger.info(f"Backup antigo removido: {old_backup}")
            except OSError as e:
                logger.warning(f"Erro ao remover backup {old_backup}: {e}")

# ============ SERVIÇOS DE DADOS ============
class LivroService:
    """Serviço para operações relacionadas a livros"""
    
    def __init__(self, db_manager: DatabaseManager, backup_manager: BackupManager):
        self.db = db_manager
        self.backup = backup_manager
    
    def listar_todos(self) -> List[Dict[str, Any]]:
        """Lista todos os livros"""
        with self.db.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, titulo, autor, ano_publicacao, preco 
                FROM livros 
                ORDER BY id
            """)
            return [dict(row) for row in cursor.fetchall()]
    
    def buscar_por_autor(self, autor_query: str) -> List[Dict[str, Any]]:
        """Busca livros por autor"""
        with self.db.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, titulo, autor, ano_publicacao, preco 
                FROM livros 
                WHERE autor LIKE ? 
                ORDER BY id
            """, (f"%{autor_query}%",))
            return [dict(row) for row in cursor.fetchall()]
    
    def adicionar(self, livro_data: Dict[str, Any]) -> int:
        """Adiciona um novo livro"""
        self.backup.create_backup(reason="add_livro")
        
        with self.db.get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO livros (titulo, autor, ano_publicacao, preco) 
                VALUES (?, ?, ?, ?)
            """, (
                livro_data["titulo"].strip(),
                livro_data["autor"].strip(),
                livro_data["ano_publicacao"],
                livro_data["preco"]
            ))
            conn.commit()
            return cursor.lastrowid
    
    def atualizar_preco(self, livro_id: int, novo_preco: float) -> bool:
        """Atualiza preço de um livro"""
        if not self._existe_livro(livro_id):
            return False
        
        self.backup.create_backup(reason="update_preco")
        
        with self.db.get_connection() as conn:
            conn.execute("""
                UPDATE livros 
                SET preco = ?, data_atualizacao = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (novo_preco, livro_id))
            conn.commit()
            return True
    
    def excluir(self, livro_id: int) -> bool:
        """Exclui um livro"""
        if not self._existe_livro(livro_id):
            return False
        
        self.backup.create_backup(reason="delete_livro")
        
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM livros WHERE id = ?", (livro_id,))
            conn.commit()
            return True
    
    def _existe_livro(self, livro_id: int) -> bool:
        """Verifica se um livro existe"""
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT 1 FROM livros WHERE id = ?", (livro_id,))
            return cursor.fetchone() is not None

# ============ EXPORTAÇÃO/IMPORTAÇÃO ============
class CSVHandler:
    """Manipulador de operações CSV"""
    
    @staticmethod
    def exportar_livros(livros: List[Dict[str, Any]]) -> bytes:
        """Exporta livros para CSV em memória"""
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(["id", "titulo", "autor", "ano_publicacao", "preco"])
        
        for livro in livros:
            writer.writerow([
                livro["id"],
                livro["titulo"],
                livro["autor"],
                livro["ano_publicacao"] or "",
                livro["preco"] or ""
            ])
        
        return output.getvalue().encode("utf-8")
    
    @staticmethod
    def detectar_delimitador(linha_exemplo: str) -> str:
        """Detecta o delimitador do CSV"""
        return "," if linha_exemplo.count(",") >= linha_exemplo.count(";") else ";"
    
    @staticmethod
    def processar_arquivo_csv(file_stream, livro_service: LivroService) -> int:
        """Processa e importa arquivo CSV"""
        texto = file_stream.read().decode("utf-8")
        linhas = texto.splitlines()
        
        if not linhas:
            return 0
        
        delimitador = CSVHandler.detectar_delimitador(linhas[0])
        reader = csv.DictReader(io.StringIO(texto), delimiter=delimitador)
        registros = list(reader)
        
        if not registros:
            return 0
        
        livros_inseridos = 0
        for registro in registros:
            
            titulo = (registro.get("titulo") or registro.get("title") or "").strip()
            autor = (registro.get("autor") or registro.get("author") or "").strip()
            ano_raw = registro.get("ano_publicacao") or registro.get("year") or ""
            preco_raw = registro.get("preco") or registro.get("price") or ""
            
            ano = Validators.validar_ano(ano_raw) if ano_raw else None
            preco = Validators.validar_preco(preco_raw) if preco_raw else None
            
            if titulo and autor:
                livro_data = {
                    "titulo": titulo,
                    "autor": autor,
                    "ano_publicacao": ano,
                    "preco": preco
                }
                livro_service.adicionar(livro_data)
                livros_inseridos += 1
        
        return livros_inseridos

# ============ RELATÓRIOS ============
class ReportGenerator:
    """Gerador de relatórios"""
    
    def __init__(self, livro_service: LivroService):
        self.livro_service = livro_service
    
    def gerar_html(self) -> str:
        """Gera relatório em HTML"""
        livros = self.livro_service.listar_todos()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset='utf-8'>
            <title>Relatório de Livros</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 20px; }
                table { border-collapse: collapse; width: 100%; margin-top: 20px; }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th { background-color: #f8f9fa; font-weight: bold; }
                .header { margin-bottom: 30px; }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Relatório de Livros</h1>
                <p>Gerado em: {timestamp}</p>
                <p>Total de livros: {total_livros}</p>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Título</th>
                        <th>Autor</th>
                        <th>Ano</th>
                        <th>Preço</th>
                    </tr>
                </thead>
                <tbody>
                    {linhas}
                </tbody>
            </table>
        </body>
        </html>
        """
        
        linhas_html = ""
        for livro in livros:
            preco_formatado = f"R$ {livro['preco']:.2f}" if livro['preco'] is not None else ""
            linhas_html += f"""
            <tr>
                <td>{livro['id']}</td>
                <td>{livro['titulo']}</td>
                <td>{livro['autor']}</td>
                <td>{livro['ano_publicacao'] or ''}</td>
                <td>{preco_formatado}</td>
            </tr>
            """
        
        html_final = html_template.format(
            timestamp=timestamp,
            total_livros=len(livros),
            linhas=linhas_html
        )
        
        caminho_arquivo = Config.EXPORT_DIR / "relatorio_livros.html"
        with open(caminho_arquivo, "w", encoding="utf-8") as f:
            f.write(html_final)
        
        return str(caminho_arquivo)
    
    def gerar_pdf(self) -> str:
        """Gera relatório em PDF"""
        if not REPORTLAB_AVAILABLE:
            raise RuntimeError("ReportLab não disponível para geração de PDF")
        
        livros = self.livro_service.listar_todos()
        caminho_arquivo = Config.EXPORT_DIR / "relatorio_livros.pdf"
        
        doc = SimpleDocTemplate(
            str(caminho_arquivo),
            pagesize=A4,
            leftMargin=15*mm,
            rightMargin=15*mm,
            topMargin=15*mm,
            bottomMargin=15*mm
        )
        
        styles = getSampleStyleSheet()
        elementos = [
            Paragraph("Relatório de Livros", styles['Title']),
            Paragraph(f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']),
            Spacer(1, 12)
        ]
        
        dados_tabela = [["ID", "Título", "Autor", "Ano", "Preço"]]
        for livro in livros:
            preco_formatado = f"R$ {livro['preco']:.2f}" if livro['preco'] is not None else ""
            dados_tabela.append([
                str(livro['id']),
                livro['titulo'],
                livro['autor'],
                str(livro['ano_publicacao']) if livro['ano_publicacao'] else "",
                preco_formatado
            ])
        
        tabela = Table(dados_tabela, colWidths=[20*mm, 70*mm, 50*mm, 20*mm, 30*mm])
        tabela.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (-1, 1), (-1, -1), 'RIGHT'),
            ('FONTSIZE', (0, 0), (-1, -1), 9)
        ]))
        
        elementos.append(tabela)
        doc.build(elementos)
        
        return str(caminho_arquivo)

# ============ INICIALIZAÇÃO DA APLICAÇÃO ============
def create_app():
    """Factory function para criar a aplicação Flask"""
    app = Flask(__name__, static_folder="static", template_folder="templates")
    
    # ======== Configurações ============
    app.config['UPLOAD_FOLDER'] = Config.UPLOAD_FOLDER
    app.config['MODEL_FOLDER'] = Config.MODEL_FOLDER
    
    # ======== Criar diretórios =========
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['MODEL_FOLDER'], exist_ok=True)
    
    # ======== Inicializar componentes ========
    db_manager = DatabaseManager(Config.DB_FILE)
    backup_manager = BackupManager(db_manager)
    livro_service = LivroService(db_manager, backup_manager)
    csv_handler = CSVHandler()
    report_generator = ReportGenerator(livro_service)
    
    # ======== Inicializar banco de dados =======
    db_manager.init_db()

    # ============ ROTAS ============
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/books", methods=["GET"])
    def api_listar_livros():
        livros = livro_service.listar_todos()
        return jsonify(livros)

    @app.route("/api/books", methods=["POST"])
    def api_adicionar_livro():
        dados = request.get_json() or {}
        
        valido, mensagem_erro, dados_validados = Validators.validar_livro_data(dados)
        if not valido:
            return jsonify({"error": mensagem_erro}), 400
        
        try:
            livro_id = livro_service.adicionar(dados_validados)
            return jsonify({"id": livro_id, "message": "Livro adicionado com sucesso"}), 201
        except Exception as e:
            logger.error(f"Erro ao adicionar livro: {e}")
            return jsonify({"error": "Erro interno ao adicionar livro"}), 500

    @app.route("/api/books/<int:livro_id>/price", methods=["PUT"])
    def api_atualizar_preco(livro_id):
        dados = request.get_json() or {}
        preco = dados.get("preco")
        
        preco_valido = Validators.validar_preco(preco)
        if preco_valido is None:
            return jsonify({"error": "Preço inválido"}), 400
        
        sucesso = livro_service.atualizar_preco(livro_id, preco_valido)
        if not sucesso:
            return jsonify({"error": "Livro não encontrado"}), 404
        
        return jsonify({"success": True, "message": "Preço atualizado com sucesso"})

    @app.route("/api/books/<int:livro_id>", methods=["DELETE"])
    def api_excluir_livro(livro_id):
        sucesso = livro_service.excluir(livro_id)
        if not sucesso:
            return jsonify({"error": "Livro não encontrado"}), 404
        
        return jsonify({"success": True, "message": "Livro excluído com sucesso"})

    @app.route("/api/search", methods=["GET"])
    def api_buscar_livros():
        query = request.args.get("q", "").strip()
        livros = livro_service.buscar_por_autor(query) if query else []
        return jsonify(livros)

    @app.route("/api/export", methods=["GET"])
    def api_exportar_csv():
        livros = livro_service.listar_todos()
        dados_csv = csv_handler.exportar_livros(livros)
        return send_file(
            io.BytesIO(dados_csv),
            as_attachment=True,
            download_name="livros_exportados.csv",
            mimetype="text/csv"
        )

    @app.route("/api/import", methods=["POST"])
    def api_importar_csv():
        if "file" not in request.files:
            return jsonify({"error": "Nenhum arquivo enviado"}), 400
        
        arquivo = request.files["file"]
        if arquivo.filename == "":
            return jsonify({"error": "Nenhum arquivo selecionado"}), 400
        
        try:
            registros_inseridos = csv_handler.processar_arquivo_csv(arquivo.stream, livro_service)
            return jsonify({
                "success": True,
                "inserted": registros_inseridos,
                "message": f"{registros_inseridos} livros importados com sucesso"
            })
        except Exception as e:
            logger.error(f"Erro na importação: {e}")
            return jsonify({"error": "Erro ao processar arquivo CSV"}), 500

    @app.route("/api/backup", methods=["GET", "POST"])
    def api_criar_backup():
        caminho_backup = backup_manager.create_backup(
            reason="manual_api" if request.method == "POST" else "auto_api"
        )
        return jsonify({
            "success": True,
            "backup_path": caminho_backup,
            "message": "Backup criado com sucesso"
        })

    @app.route("/api/backups", methods=["GET"])
    def api_listar_backups():
        backups = sorted(
            Config.BACKUP_DIR.glob(f"{Config.BACKUP_PREFIX}*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return jsonify([str(p) for p in backups])

    @app.route("/api/report/html", methods=["GET"])
    def api_relatorio_html():
        caminho = report_generator.gerar_html()
        return send_file(
            caminho,
            as_attachment=True,
            download_name="relatorio_livros.html",
            mimetype="text/html"
        )

    @app.route("/api/report/pdf", methods=["GET"])
    def api_relatorio_pdf():
        if not REPORTLAB_AVAILABLE:
            return jsonify({"error": "Funcionalidade PDF não disponível"}), 400
        
        try:
            caminho = report_generator.gerar_pdf()
            return send_file(
                caminho,
                as_attachment=True,
                download_name="relatorio_livros.pdf",
                mimetype="application/pdf"
            )
        except Exception as e:
            logger.error(f"Erro ao gerar PDF: {e}")
            return jsonify({"error": "Erro ao gerar relatório PDF"}), 500

    # Tratamento de erros
    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Erro interno do servidor: {error}")
        return jsonify({"error": "Erro interno do servidor"}), 500

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Endpoint não encontrado"}), 404

    return app

# ============ EXECUÇÃO ============
if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)