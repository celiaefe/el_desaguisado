import io
import os
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from flask import Flask, Response, flash, redirect, render_template, request, url_for
from flask_login import (
    current_user,
    LoginManager,
    UserMixin,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, or_, text
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DEFAULT_DB_PATH = INSTANCE_DIR / "el_desaguisado.db"
DEFAULT_UPLOADS = BASE_DIR / "static" / "uploads"
VERCEL_TMP_DIR = Path("/tmp")
OPTIONAL_INCIDENCIA_COLUMNS = {
    "fecha_compra": {
        "sqlite": "DATE",
        "postgresql": "DATE",
    },
    "unidades_compradas": {
        "sqlite": "INTEGER",
        "postgresql": "INTEGER",
    },
    "transportista": {
        "sqlite": "VARCHAR(120)",
        "postgresql": "VARCHAR(120)",
    },
    "numero_pedido": {
        "sqlite": "VARCHAR(120)",
        "postgresql": "VARCHAR(120)",
    },
    "fecha_deteccion": {
        "sqlite": "DATE",
        "postgresql": "DATE",
    },
    "origen_incidencia": {
        "sqlite": "VARCHAR(80)",
        "postgresql": "VARCHAR(80)",
    },
}

db = SQLAlchemy()
login_manager = LoginManager()
TIPOS_INCIDENCIA = [
    "Rotura en envío",
    "Extravío",
    "Producto deteriorado",
    "Error de pedido",
    "Falta de stock",
    "Pedido no servido por falta de stock",
    "Pedido incompleto",
    "Incidencia de lote",
    "Envase dañado",
    "Incidencia logística",
    "Reclamación cliente",
    "Incidencia interna",
    "Otro",
]
ESTADOS_INCIDENCIA = [
    "Nueva",
    "En revisión",
    "Pendiente",
    "Resuelta",
    "Cerrada",
]
PRIORIDADES_INCIDENCIA = ["Baja", "Media", "Alta", "Urgente"]
ORIGENES_INCIDENCIA = [
    "Interno",
    "Tienda",
    "Cliente",
    "Transporte",
    "Comercial",
    "Otro",
]
ESTADO_BADGE_CLASSES = {
    "Nueva": "nueva",
    "En revisión": "en-revision",
    "Pendiente": "pendiente",
    "Resuelta": "resuelta",
    "Cerrada": "cerrada",
}
PRIORIDAD_BADGE_CLASSES = {
    "Baja": "baja",
    "Media": "media",
    "Alta": "alta",
    "Urgente": "urgente",
}
ESTADOS_ABIERTOS = ["Nueva", "En revisión", "Pendiente"]
ESTADOS_CON_CIERRE = {"Resuelta", "Cerrada"}


class Usuario(UserMixin, db.Model):
    """Usuario interno con acceso a la aplicación."""

    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def is_active(self) -> bool:
        return bool(self.activo)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(
            password, method="pbkdf2:sha256"
        )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Incidencia(db.Model):
    """Modelo principal para el registro y seguimiento de incidencias."""

    __tablename__ = "incidencias"

    id = db.Column(db.Integer, primary_key=True)
    fecha_registro = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    fecha_incidencia = db.Column(db.Date, nullable=False, default=date.today)
    fecha_compra = db.Column(db.Date)
    fecha_deteccion = db.Column(db.Date)
    tienda = db.Column(db.String(150), nullable=False)
    producto = db.Column(db.String(150), nullable=False)
    lote = db.Column(db.String(100))
    transportista = db.Column(db.String(120))
    numero_pedido = db.Column(db.String(120))
    origen_incidencia = db.Column(db.String(80))
    unidades_compradas = db.Column(db.Integer)
    unidades_afectadas = db.Column(db.Integer, nullable=False, default=1)
    tipo_incidencia = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(50), nullable=False, default="Nueva")
    prioridad = db.Column(db.String(50), nullable=False, default="Media")
    responsable = db.Column(db.String(120))
    observaciones_internas = db.Column(db.Text)
    resolucion = db.Column(db.Text)
    fecha_cierre = db.Column(db.Date)
    seguimientos = db.relationship(
        "SeguimientoIncidencia",
        back_populates="incidencia",
        cascade="all, delete-orphan",
    )


class SeguimientoIncidencia(db.Model):
    """Comentario interno asociado a una incidencia."""

    __tablename__ = "seguimientos_incidencia"

    id = db.Column(db.Integer, primary_key=True)
    incidencia_id = db.Column(
        db.Integer,
        db.ForeignKey("incidencias.id"),
        nullable=False,
        index=True,
    )
    fecha_creacion = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    comentario = db.Column(db.Text, nullable=False)
    incidencia = db.relationship("Incidencia", back_populates="seguimientos")


def clean_text(value: str, max_length: Optional[int] = None) -> str:
    """Limpia espacios sobrantes y limita longitud cuando aplica."""

    cleaned = " ".join(value.split())
    if max_length:
        return cleaned[:max_length]
    return cleaned


def parse_date_field(value: str, field_name: str, errors: list[str]) -> Optional[date]:
    """Convierte una fecha del formulario o añade un error legible."""

    if not value:
        errors.append(f"El campo '{field_name}' es obligatorio.")
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"El campo '{field_name}' debe tener una fecha válida.")
        return None


def parse_optional_date_field(
    value: str, field_name: str, errors: list[str]
) -> Optional[date]:
    """Convierte una fecha opcional; si viene mal formada, añade error."""

    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"El campo '{field_name}' debe tener una fecha válida.")
        return None


def parse_optional_positive_int(
    value: str, field_name: str, errors: list[str]
) -> Optional[int]:
    """Convierte un entero positivo opcional o añade un error legible."""

    if not value:
        return None

    try:
        parsed = int(value)
        if parsed < 1:
            raise ValueError
        return parsed
    except ValueError:
        errors.append(f"El campo '{field_name}' debe ser un número entero mayor que 0.")
        return None


def badge_class(value: str, choices: dict[str, str]) -> str:
    """Devuelve una clase CSS estable para etiquetas visuales."""

    return choices.get(value, "default")


def safe_next_url(value: Optional[str]) -> Optional[str]:
    """Permite redirecciones despues del login solo dentro de la app."""

    if not value:
        return None

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None
    return value


def validate_incidencia_form(form_data) -> tuple[dict, list[str]]:
    """Valida y normaliza los datos del formulario principal."""

    errors: list[str] = []

    tienda = clean_text(form_data.get("tienda", ""), 150)
    producto = clean_text(form_data.get("producto", ""), 150)
    lote = clean_text(form_data.get("lote", ""), 100)
    transportista = clean_text(form_data.get("transportista", ""), 120)
    numero_pedido = clean_text(form_data.get("numero_pedido", ""), 120)
    origen_incidencia = clean_text(form_data.get("origen_incidencia", ""), 80)
    tipo_incidencia = clean_text(form_data.get("tipo_incidencia", ""), 100)
    descripcion = form_data.get("descripcion", "").strip()
    estado = clean_text(form_data.get("estado", ""), 50) or "Nueva"
    prioridad = clean_text(form_data.get("prioridad", ""), 50) or "Media"
    responsable = clean_text(form_data.get("responsable", ""), 120)
    observaciones_internas = form_data.get("observaciones_internas", "").strip()
    resolucion = form_data.get("resolucion", "").strip()

    fecha_incidencia = parse_date_field(
        form_data.get("fecha_incidencia", ""), "fecha_incidencia", errors
    )
    fecha_compra = parse_optional_date_field(
        form_data.get("fecha_compra", ""), "fecha_compra", errors
    )
    fecha_deteccion = parse_optional_date_field(
        form_data.get("fecha_deteccion", ""), "fecha_deteccion", errors
    )
    fecha_cierre = parse_optional_date_field(
        form_data.get("fecha_cierre", ""), "fecha_cierre", errors
    )
    unidades_compradas = parse_optional_positive_int(
        form_data.get("unidades_compradas", "").strip(),
        "unidades_compradas",
        errors,
    )

    if not tienda:
        errors.append("La tienda es obligatoria.")
    if not producto:
        errors.append("El producto es obligatorio.")
    if not descripcion:
        errors.append("La descripción es obligatoria.")
    if tipo_incidencia not in TIPOS_INCIDENCIA:
        errors.append("Selecciona un tipo de incidencia válido.")
    if estado not in ESTADOS_INCIDENCIA:
        errors.append("Selecciona un estado válido.")
    if prioridad not in PRIORIDADES_INCIDENCIA:
        errors.append("Selecciona una prioridad válida.")
    if origen_incidencia and origen_incidencia not in ORIGENES_INCIDENCIA:
        errors.append("Selecciona un origen de incidencia válido.")

    unidades_raw = form_data.get("unidades_afectadas", "").strip()
    try:
        unidades_afectadas = int(unidades_raw)
        if unidades_afectadas < 1:
            raise ValueError
    except ValueError:
        errors.append("Las unidades afectadas deben ser un número entero mayor que 0.")
        unidades_afectadas = 1

    if fecha_cierre and estado not in ESTADOS_CON_CIERRE:
        errors.append("La fecha de cierre solo debe informarse para incidencias resueltas o cerradas.")
    if fecha_cierre and fecha_incidencia and fecha_cierre < fecha_incidencia:
        errors.append("La fecha de cierre no puede ser anterior a la fecha de incidencia.")
    if fecha_compra and fecha_incidencia and fecha_compra > fecha_incidencia:
        errors.append("La fecha de compra no puede ser posterior a la fecha de incidencia.")
    if unidades_compradas and unidades_compradas < unidades_afectadas:
        errors.append("Las unidades compradas no pueden ser menores que las unidades afectadas.")

    data = {
        "fecha_incidencia": fecha_incidencia,
        "fecha_compra": fecha_compra,
        "fecha_deteccion": fecha_deteccion,
        "tienda": tienda,
        "producto": producto,
        "lote": lote or None,
        "transportista": transportista or None,
        "numero_pedido": numero_pedido or None,
        "origen_incidencia": origen_incidencia or None,
        "unidades_compradas": unidades_compradas,
        "unidades_afectadas": unidades_afectadas,
        "tipo_incidencia": tipo_incidencia,
        "descripcion": descripcion,
        "estado": estado,
        "prioridad": prioridad,
        "responsable": responsable or None,
        "observaciones_internas": observaciones_internas or None,
        "resolucion": resolucion or None,
        "fecha_cierre": fecha_cierre,
    }
    return data, errors


def get_database_uri() -> str:
    """Devuelve la URI de base de datos.

    Desarrollo: si no hay DATABASE_URL, usa SQLite local en el proyecto.
    Produccion/Vercel: configurar DATABASE_URL con PostgreSQL persistente.
    """

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Vercel y otros proveedores suelen exponer postgres://
        return database_url.replace("postgres://", "postgresql://", 1)

    if os.getenv("VERCEL"):
        # Solo fallback tecnico: en Vercel /tmp es efimero y no persiste datos.
        return f"sqlite:///{VERCEL_TMP_DIR / 'el_desaguisado.db'}"

    return f"sqlite:///{DEFAULT_DB_PATH}"


def get_upload_folder() -> Path:
    """Devuelve una carpeta de subida compatible con local y Vercel."""

    upload_folder = os.getenv("UPLOAD_FOLDER")
    if upload_folder:
        return Path(upload_folder)

    if os.getenv("VERCEL"):
        # En produccion los archivos subidos deberian ir a almacenamiento externo.
        return VERCEL_TMP_DIR / "uploads"

    return DEFAULT_UPLOADS


def create_tables(app: Flask) -> None:
    """Crea las tablas definidas si no existen.

    Es suficiente para este arranque; en produccion madura conviene usar migraciones.
    """

    with app.app_context():
        db.create_all()
        ensure_optional_incidencia_columns()
        ensure_initial_user()


def ensure_initial_user() -> None:
    """Crea un usuario inicial si la instalación no tiene usuarios."""

    if Usuario.query.first():
        return

    usuario = Usuario(nombre="Administrador", username="admin")
    usuario.set_password("admin123")
    db.session.add(usuario)
    db.session.commit()


def ensure_optional_incidencia_columns() -> None:
    """Añade columnas opcionales nuevas si la tabla ya existia."""

    dialect_name = db.engine.dialect.name
    if dialect_name not in {"sqlite", "postgresql"}:
        return
    if not inspect(db.engine).has_table(Incidencia.__tablename__):
        return

    existing_columns = {
        column["name"] for column in inspect(db.engine).get_columns(Incidencia.__tablename__)
    }
    for column_name, column_types in OPTIONAL_INCIDENCIA_COLUMNS.items():
        if column_name not in existing_columns:
            column_type = column_types[dialect_name]
            db.session.execute(
                text(
                    f"ALTER TABLE {Incidencia.__tablename__} "
                    f"ADD COLUMN {column_name} {column_type}"
                )
            )
    db.session.commit()


def render_incidencia_form(template_name: str, incidencia=None, form_data=None):
    """Renderiza formularios de alta y edicion con contexto común."""

    return render_template(
        template_name,
        app_name="Entre Lotes",
        incidencia=incidencia,
        form_data=form_data,
    )


def get_incidencia_filters(args) -> dict:
    """Normaliza los filtros del listado de incidencias."""

    return {
        "q": clean_text(args.get("q", ""), 200),
        "tienda": clean_text(args.get("tienda", ""), 150),
        "producto": clean_text(args.get("producto", ""), 150),
        "lote": clean_text(args.get("lote", ""), 100),
        "numero_pedido": clean_text(args.get("numero_pedido", ""), 120),
        "transportista": clean_text(args.get("transportista", ""), 120),
        "origen_incidencia": clean_text(args.get("origen_incidencia", ""), 80),
        "estado": clean_text(args.get("estado", ""), 50),
        "prioridad": clean_text(args.get("prioridad", ""), 50),
        "tipo_incidencia": clean_text(args.get("tipo_incidencia", ""), 100),
        "fecha_desde": args.get("fecha_desde", "").strip(),
        "fecha_hasta": args.get("fecha_hasta", "").strip(),
    }


def build_incidencias_query(filters: dict):
    """Aplica busqueda, filtros y orden al listado de incidencias."""

    query = Incidencia.query

    if filters["q"]:
        search = f"%{filters['q']}%"
        query = query.filter(
            or_(
                Incidencia.tienda.ilike(search),
                Incidencia.producto.ilike(search),
                Incidencia.lote.ilike(search),
                Incidencia.transportista.ilike(search),
                Incidencia.numero_pedido.ilike(search),
                Incidencia.origen_incidencia.ilike(search),
                Incidencia.descripcion.ilike(search),
                Incidencia.responsable.ilike(search),
            )
        )
    if filters["tienda"]:
        query = query.filter(Incidencia.tienda.ilike(f"%{filters['tienda']}%"))
    if filters["producto"]:
        query = query.filter(Incidencia.producto.ilike(f"%{filters['producto']}%"))
    if filters["lote"]:
        query = query.filter(Incidencia.lote.ilike(f"%{filters['lote']}%"))
    if filters["numero_pedido"]:
        query = query.filter(
            Incidencia.numero_pedido.ilike(f"%{filters['numero_pedido']}%")
        )
    if filters["transportista"]:
        query = query.filter(
            Incidencia.transportista.ilike(f"%{filters['transportista']}%")
        )
    if filters["origen_incidencia"] in ORIGENES_INCIDENCIA:
        query = query.filter(
            Incidencia.origen_incidencia == filters["origen_incidencia"]
        )
    if filters["estado"] in ESTADOS_INCIDENCIA:
        query = query.filter(Incidencia.estado == filters["estado"])
    if filters["prioridad"] in PRIORIDADES_INCIDENCIA:
        query = query.filter(Incidencia.prioridad == filters["prioridad"])
    if filters["tipo_incidencia"] in TIPOS_INCIDENCIA:
        query = query.filter(Incidencia.tipo_incidencia == filters["tipo_incidencia"])

    fecha_desde = parse_optional_date_field(filters["fecha_desde"], "fecha_desde", [])
    fecha_hasta = parse_optional_date_field(filters["fecha_hasta"], "fecha_hasta", [])
    if fecha_desde:
        query = query.filter(Incidencia.fecha_incidencia >= fecha_desde)
    if fecha_hasta:
        query = query.filter(Incidencia.fecha_incidencia <= fecha_hasta)

    return query.order_by(Incidencia.fecha_registro.desc())


def format_export_date(value) -> str:
    """Formatea fechas para la exportacion sin fallar si vienen vacias."""

    if not value:
        return ""
    return value.strftime("%Y-%m-%d")


def format_export_datetime(value) -> str:
    """Formatea fecha y hora para columnas legibles de exportación."""

    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def excel_column_name(index: int) -> str:
    """Convierte un indice de columna base 1 en nombre Excel: A, B, AA..."""

    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def build_excel_workbook(rows: list[list]) -> bytes:
    """Crea un libro XLSX sencillo compatible con Excel y Google Sheets."""

    output = io.BytesIO()
    worksheet_rows = []

    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cell_ref = f"{excel_column_name(column_index)}{row_index}"
            if isinstance(value, int):
                cells.append(f'<c r="{cell_ref}"><v>{value}</v></c>')
                continue

            text_value = "" if value is None else str(value)
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr">'
                f"<is><t>{escape(text_value)}</t></is>"
                f"</c>"
            )
        worksheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f"{''.join(worksheet_rows)}"
        "</sheetData>"
        "</worksheet>"
    )

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        workbook.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Incidencias" sheetId="1" r:id="rId1"/>'
            "</sheets>"
            "</workbook>",
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)

    return output.getvalue()


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
        template_folder=str(BASE_DIR / "templates"),
    )
    # En local se usa el valor de desarrollo; en Vercel define SECRET_KEY.
    if not os.getenv("VERCEL"):
        INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["UPLOAD_FOLDER"] = get_upload_folder()

    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.login_message = "Inicia sesión para acceder a Entre Lotes."
    login_manager.login_message_category = "error"
    create_tables(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return db.session.get(Usuario, int(user_id))
        except (TypeError, ValueError):
            return None

    @app.context_processor
    def inject_form_choices():
        return {
            "tipos_incidencia": TIPOS_INCIDENCIA,
            "estados_incidencia": ESTADOS_INCIDENCIA,
            "prioridades_incidencia": PRIORIDADES_INCIDENCIA,
            "origenes_incidencia": ORIGENES_INCIDENCIA,
            "estado_badge_class": lambda value: badge_class(
                value, ESTADO_BADGE_CLASSES
            ),
            "prioridad_badge_class": lambda value: badge_class(
                value, PRIORIDAD_BADGE_CLASSES
            ),
        }

    @app.get("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            username = clean_text(request.form.get("username", ""), 80)
            password = request.form.get("password", "")
            usuario = Usuario.query.filter_by(username=username).first()

            if not usuario or not usuario.check_password(password) or not usuario.activo:
                flash("Usuario o contraseña incorrectos.", "error")
                return render_template(
                    "login.html",
                    app_name="Entre Lotes",
                    username=username,
                )

            login_user(usuario)
            flash("Sesión iniciada correctamente.", "success")
            return redirect(
                safe_next_url(request.args.get("next")) or url_for("dashboard")
            )

        return render_template("login.html", app_name="Entre Lotes")

    @app.get("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Sesión cerrada correctamente.", "success")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        total = Incidencia.query.count()
        nuevas = Incidencia.query.filter_by(estado="Nueva").count()
        abiertas = Incidencia.query.filter(
            Incidencia.estado.in_(ESTADOS_ABIERTOS)
        ).count()
        resueltas = Incidencia.query.filter_by(estado="Resuelta").count()
        recientes = (
            Incidencia.query.order_by(Incidencia.fecha_registro.desc()).limit(5).all()
        )
        return render_template(
            "dashboard.html",
            app_name="Entre Lotes",
            total=total,
            nuevas=nuevas,
            abiertas=abiertas,
            resueltas=resueltas,
            recientes=recientes,
        )

    @app.route("/incidencias")
    @login_required
    def incidencias_list():
        filters = get_incidencia_filters(request.args)
        incidencias = build_incidencias_query(filters).all()
        return render_template(
            "incidencias.html",
            app_name="Entre Lotes",
            incidencias=incidencias,
            filters=filters,
        )

    @app.get("/incidencias/exportar")
    @login_required
    def incidencias_exportar():
        filters = get_incidencia_filters(request.args)
        incidencias = build_incidencias_query(filters).all()
        rows = [
            [
                "ID",
                "Fecha registro",
                "Fecha incidencia",
                "Fecha compra",
                "Fecha detección",
                "Tienda",
                "Producto",
                "Lote",
                "Transportista",
                "Número pedido",
                "Origen incidencia",
                "Unidades compradas",
                "Unidades afectadas",
                "Tipo de incidencia",
                "Descripción",
                "Estado",
                "Prioridad",
                "Responsable",
                "Observaciones internas",
                "Resolución",
                "Fecha cierre",
            ]
        ]

        for incidencia in incidencias:
            rows.append(
                [
                    incidencia.id,
                    format_export_datetime(incidencia.fecha_registro),
                    format_export_date(incidencia.fecha_incidencia),
                    format_export_date(incidencia.fecha_compra),
                    format_export_date(incidencia.fecha_deteccion),
                    incidencia.tienda,
                    incidencia.producto,
                    incidencia.lote or "",
                    incidencia.transportista or "",
                    incidencia.numero_pedido or "",
                    incidencia.origen_incidencia or "",
                    incidencia.unidades_compradas or "",
                    incidencia.unidades_afectadas,
                    incidencia.tipo_incidencia,
                    incidencia.descripcion,
                    incidencia.estado,
                    incidencia.prioridad,
                    incidencia.responsable or "",
                    incidencia.observaciones_internas or "",
                    incidencia.resolucion or "",
                    format_export_date(incidencia.fecha_cierre),
                ]
            )

        filename = f"incidencias_entre_lotes_{date.today().isoformat()}.xlsx"
        return Response(
            build_excel_workbook(rows),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @app.route("/incidencias/nueva", methods=["GET", "POST"])
    @login_required
    def incidencia_nueva():
        if request.method == "POST":
            data, errors = validate_incidencia_form(request.form)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_incidencia_form(
                    "nueva_incidencia.html", form_data=request.form
                )

            incidencia = Incidencia(**data)
            db.session.add(incidencia)
            db.session.commit()
            flash("La incidencia se ha creado correctamente.", "success")
            return redirect(url_for("incidencia_detalle", id=incidencia.id))

        return render_incidencia_form("nueva_incidencia.html")

    @app.get("/incidencias/<int:id>")
    @login_required
    def incidencia_detalle(id: int):
        incidencia = db.get_or_404(Incidencia, id)
        seguimientos = (
            SeguimientoIncidencia.query.filter_by(incidencia_id=incidencia.id)
            .order_by(SeguimientoIncidencia.fecha_creacion.desc())
            .all()
        )
        return render_template(
            "detalle_incidencia.html",
            app_name="Entre Lotes",
            incidencia=incidencia,
            seguimientos=seguimientos,
        )

    @app.post("/incidencias/<int:id>/seguimiento")
    @login_required
    def incidencia_agregar_seguimiento(id: int):
        incidencia = db.get_or_404(Incidencia, id)
        comentario = request.form.get("comentario", "").strip()

        if not comentario:
            flash("El comentario de seguimiento es obligatorio.", "error")
            return redirect(url_for("incidencia_detalle", id=incidencia.id))

        seguimiento = SeguimientoIncidencia(
            incidencia_id=incidencia.id,
            comentario=comentario,
        )
        db.session.add(seguimiento)
        db.session.commit()
        flash("El comentario de seguimiento se ha añadido.", "success")
        return redirect(url_for("incidencia_detalle", id=incidencia.id))

    @app.route("/incidencias/<int:id>/editar", methods=["GET", "POST"])
    @login_required
    def incidencia_editar(id: int):
        incidencia = db.get_or_404(Incidencia, id)

        if request.method == "POST":
            data, errors = validate_incidencia_form(request.form)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_incidencia_form(
                    "editar_incidencia.html",
                    incidencia=incidencia,
                    form_data=request.form,
                )

            for field, value in data.items():
                setattr(incidencia, field, value)

            db.session.commit()
            flash("La incidencia se ha actualizado correctamente.", "success")
            return redirect(url_for("incidencia_detalle", id=incidencia.id))

        return render_incidencia_form("editar_incidencia.html", incidencia=incidencia)

    @app.post("/incidencias/<int:id>/estado")
    @login_required
    def incidencia_cambiar_estado(id: int):
        incidencia = db.get_or_404(Incidencia, id)
        nuevo_estado = clean_text(request.form.get("estado", ""), 50)

        if nuevo_estado not in ESTADOS_INCIDENCIA:
            flash("Selecciona un estado válido.", "error")
            return redirect(url_for("incidencia_detalle", id=incidencia.id))

        incidencia.estado = nuevo_estado
        if nuevo_estado in ESTADOS_CON_CIERRE and not incidencia.fecha_cierre:
            incidencia.fecha_cierre = date.today()
        if nuevo_estado not in ESTADOS_CON_CIERRE:
            incidencia.fecha_cierre = None

        db.session.commit()
        flash("El estado de la incidencia se ha actualizado.", "success")
        return redirect(url_for("incidencia_detalle", id=incidencia.id))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
