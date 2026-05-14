import csv
import io
import json
import os
import re
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
DEFAULT_PRODUCTOS_CATALOGO = BASE_DIR / "static" / "productos_catalogo.json"
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
    "numero_bultos": {
        "sqlite": "INTEGER",
        "postgresql": "INTEGER",
    },
    "fecha_deteccion": {
        "sqlite": "DATE",
        "postgresql": "DATE",
    },
    "origen_incidencia": {
        "sqlite": "VARCHAR(80)",
        "postgresql": "VARCHAR(80)",
    },
    "contacto_id": {
        "sqlite": "INTEGER",
        "postgresql": "INTEGER",
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
TIPOS_DASHBOARD = [
    "Falta de stock",
    "Pedido incompleto",
    "Rotura en envío",
    "Producto deteriorado",
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
TRANSPORTISTAS = [
    "GLS",
    "Seur Frío",
    "Meana",
    "Lofriastur",
    "Otro",
]
TIPOS_CLIENTE = [
    "Tienda gourmet",
    "Distribuidor",
    "Hostelería",
    "Particular",
    "Empresa",
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
SORTABLE_INCIDENCIA_COLUMNS = [
    ("id", "ID"),
    ("fecha_incidencia", "Fecha"),
    ("tienda", "Tienda"),
    ("producto", "Producto"),
    ("numero_pedido", "Albarán"),
    ("transportista", "Transportista"),
    ("tipo_incidencia", "Tipo"),
    ("estado", "Estado"),
    ("prioridad", "Prioridad"),
]


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


class Contacto(db.Model):
    """Contacto o cliente de referencia para incidencias y pedidos."""

    __tablename__ = "contactos"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False, index=True)
    razon_social = db.Column(db.String(180))
    cif = db.Column(db.String(30), index=True)
    email = db.Column(db.String(150), index=True)
    telefono = db.Column(db.String(50))
    direccion = db.Column(db.String(220))
    provincia = db.Column(db.String(100), index=True)
    codigo_postal = db.Column(db.String(20))
    comunidad_autonoma = db.Column(db.String(120), index=True)
    tipo_cliente = db.Column(db.String(100), index=True)
    etiquetas_holded = db.Column(db.String(250))
    activo = db.Column(db.Boolean, nullable=False, default=True, index=True)
    fecha_creacion = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    fecha_actualizacion = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    incidencias = db.relationship("Incidencia", back_populates="contacto")


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
    contacto_id = db.Column(db.Integer, db.ForeignKey("contactos.id"), index=True)
    tienda = db.Column(db.String(150), nullable=False)
    producto = db.Column(db.String(150), nullable=False)
    lote = db.Column(db.String(100))
    transportista = db.Column(db.String(120))
    numero_pedido = db.Column(db.String(120))
    numero_bultos = db.Column(db.Integer)
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
    contacto = db.relationship("Contacto", back_populates="incidencias")
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


def is_valid_email(value: str) -> bool:
    """Validación simple de correo electrónico."""

    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


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

    contacto_id_value = clean_text(form_data.get("contacto_id", ""), 20)
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
    numero_bultos = parse_optional_positive_int(
        form_data.get("numero_bultos", "").strip(),
        "Nº de bultos",
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
    if transportista and transportista not in TRANSPORTISTAS:
        errors.append("Selecciona un transportista válido.")

    contacto = None
    if contacto_id_value:
        try:
            contacto_id = int(contacto_id_value)
            contacto = db.session.get(Contacto, contacto_id)
            if not contacto:
                errors.append("Selecciona un contacto válido.")
            elif not contacto.activo:
                errors.append("El contacto seleccionado está inactivo.")
        except ValueError:
            errors.append("Selecciona un contacto válido.")
    elif tienda:
        contacto = (
            Contacto.query.filter(Contacto.activo.is_(True))
            .filter(Contacto.nombre.ilike(tienda))
            .order_by(Contacto.id.asc())
            .first()
        )
    if contacto and not tienda:
        tienda = contacto.nombre

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
        "contacto_id": contacto.id if contacto else None,
        "tienda": tienda,
        "producto": producto,
        "lote": lote or None,
        "transportista": transportista or None,
        "numero_pedido": numero_pedido or None,
        "numero_bultos": numero_bultos,
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


def validate_contacto_form(form_data) -> tuple[dict, list[str]]:
    """Valida y normaliza datos del formulario de contactos."""

    errors: list[str] = []
    nombre = clean_text(
        form_data.get("nombre", "") or form_data.get("nombre_comercial", ""),
        150,
    )
    razon_social = clean_text(form_data.get("razon_social", ""), 180)
    cif = clean_text(form_data.get("cif", ""), 30)
    email = clean_text(form_data.get("email", ""), 150).lower()
    telefono = clean_text(form_data.get("telefono", ""), 50)
    direccion = clean_text(form_data.get("direccion", ""), 220)
    provincia = clean_text(form_data.get("provincia", ""), 100)
    codigo_postal = clean_text(form_data.get("codigo_postal", ""), 20)
    comunidad_autonoma = clean_text(form_data.get("comunidad_autonoma", ""), 120)
    tipo_cliente = clean_text(form_data.get("tipo_cliente", ""), 100)
    etiquetas_holded = clean_text(form_data.get("etiquetas_holded", ""), 250)
    activo = form_data.get("activo") in {"1", "on", "true", "True"}

    if not nombre:
        errors.append("El nombre del contacto es obligatorio.")
    if email and not is_valid_email(email):
        errors.append("El email no tiene un formato válido.")

    data = {
        "nombre": nombre,
        "razon_social": razon_social or None,
        "cif": cif or None,
        "email": email or None,
        "telefono": telefono or None,
        "direccion": direccion or None,
        "provincia": provincia or None,
        "codigo_postal": codigo_postal or None,
        "comunidad_autonoma": comunidad_autonoma or None,
        "tipo_cliente": tipo_cliente or None,
        "etiquetas_holded": etiquetas_holded or None,
        "activo": activo,
    }
    return data, errors


def normalize_csv_headers(headers: list[str]) -> dict[str, int]:
    """Normaliza cabeceras CSV para mapping tolerante."""

    result: dict[str, int] = {}
    for index, header in enumerate(headers):
        normalized = clean_text(header or "").lower().replace(" ", "_")
        result[normalized] = index
    return result


def csv_value(row: list[str], headers_map: dict[str, int], aliases: list[str]) -> str:
    """Obtiene valor por alias de columna desde una fila CSV."""

    for alias in aliases:
        key = alias.lower().replace(" ", "_")
        if key in headers_map:
            idx = headers_map[key]
            if idx < len(row):
                return row[idx]
    return ""


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

    contactos_activos = (
        Contacto.query.filter_by(activo=True)
        .order_by(Contacto.nombre.asc())
        .all()
    )
    return render_template(
        template_name,
        app_name="Entre Lotes",
        incidencia=incidencia,
        form_data=form_data,
        contactos_activos=contactos_activos,
        contactos_lookup={contacto.nombre: contacto.id for contacto in contactos_activos},
    )


def get_incidencia_filters(args) -> dict:
    """Normaliza los filtros del listado de incidencias."""

    allowed_sort_keys = {key for key, _label in SORTABLE_INCIDENCIA_COLUMNS}
    sort_key = clean_text(args.get("sort", ""), 50)
    direction = clean_text(args.get("direction", ""), 10).lower()

    if sort_key not in allowed_sort_keys:
        sort_key = "fecha_incidencia"
    if direction not in {"asc", "desc"}:
        direction = "desc"

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
        "sort": sort_key,
        "direction": direction,
    }


def get_contacto_filters(args) -> dict:
    """Normaliza los filtros del listado de contactos."""

    return {
        "q": clean_text(args.get("q", ""), 200),
        "tipo_cliente": clean_text(args.get("tipo_cliente", ""), 100),
        "comunidad_autonoma": clean_text(args.get("comunidad_autonoma", ""), 120),
        "provincia": clean_text(args.get("provincia", ""), 100),
        "estado": clean_text(args.get("estado", ""), 20) or "activos",
    }


def build_contactos_query(filters: dict):
    """Aplica búsqueda y filtros del listado de contactos."""

    query = Contacto.query
    if filters["estado"] == "activos":
        query = query.filter(Contacto.activo.is_(True))
    elif filters["estado"] == "inactivos":
        query = query.filter(Contacto.activo.is_(False))

    if filters["q"]:
        search = f"%{filters['q']}%"
        query = query.filter(
            or_(
                Contacto.nombre.ilike(search),
                Contacto.razon_social.ilike(search),
                Contacto.cif.ilike(search),
                Contacto.email.ilike(search),
                Contacto.provincia.ilike(search),
                Contacto.comunidad_autonoma.ilike(search),
                Contacto.tipo_cliente.ilike(search),
                Contacto.etiquetas_holded.ilike(search),
            )
        )
    if filters["tipo_cliente"]:
        query = query.filter(Contacto.tipo_cliente.ilike(f"%{filters['tipo_cliente']}%"))
    if filters["comunidad_autonoma"]:
        query = query.filter(
            Contacto.comunidad_autonoma.ilike(f"%{filters['comunidad_autonoma']}%")
        )
    if filters["provincia"]:
        query = query.filter(Contacto.provincia.ilike(f"%{filters['provincia']}%"))

    return query.order_by(Contacto.nombre.asc(), Contacto.id.desc())


def build_sort_links(filters: dict) -> list[dict]:
    """Construye enlaces de ordenación preservando los filtros activos."""

    params = {key: value for key, value in filters.items() if value}
    links = []

    for key, label in SORTABLE_INCIDENCIA_COLUMNS:
        is_active = filters["sort"] == key
        next_direction = "asc"
        if is_active and filters["direction"] == "asc":
            next_direction = "desc"

        link_params = params.copy()
        link_params["sort"] = key
        link_params["direction"] = next_direction
        links.append(
            {
                "key": key,
                "label": label,
                "url": url_for("incidencias_list", **link_params),
                "active": is_active,
                "direction": filters["direction"] if is_active else "",
            }
        )

    return links


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

    sort_columns = {
        "id": Incidencia.id,
        "fecha_incidencia": Incidencia.fecha_incidencia,
        "tienda": Incidencia.tienda,
        "producto": Incidencia.producto,
        "numero_pedido": Incidencia.numero_pedido,
        "transportista": Incidencia.transportista,
        "tipo_incidencia": Incidencia.tipo_incidencia,
        "estado": Incidencia.estado,
        "prioridad": Incidencia.prioridad,
    }
    sort_column = sort_columns.get(filters["sort"], Incidencia.fecha_incidencia)
    sort_expression = sort_column.asc() if filters["direction"] == "asc" else sort_column.desc()

    return query.order_by(sort_expression, Incidencia.id.desc())


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


def parse_selected_incidencia_ids(values) -> list[int]:
    """Normaliza IDs enviados desde acciones masivas."""

    selected_ids = []
    for value in values:
        try:
            selected_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return selected_ids


def build_incidencias_export_response(incidencias, suffix: str = "") -> Response:
    """Construye la descarga Excel de incidencias."""

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
            "Nº de albarán",
            "Nº de bultos",
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
                incidencia.numero_bultos or "",
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

    filename_suffix = f"_{suffix}" if suffix else ""
    filename = f"incidencias_entre_lotes{filename_suffix}_{date.today().isoformat()}.xlsx"
    return Response(
        build_excel_workbook(rows),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def build_contactos_export_response(contactos, suffix: str = "") -> Response:
    """Construye la descarga Excel de contactos."""

    rows = [[
        "Nombre",
        "Razón social",
        "CIF",
        "Email",
        "Teléfono",
        "Dirección",
        "Provincia",
        "Código postal",
        "Comunidad autónoma",
        "Tipo cliente",
        "Etiquetas Holded",
        "Activo",
    ]]
    for contacto in contactos:
        rows.append([
            contacto.nombre,
            contacto.razon_social or "",
            contacto.cif or "",
            contacto.email or "",
            contacto.telefono or "",
            contacto.direccion or "",
            contacto.provincia or "",
            contacto.codigo_postal or "",
            contacto.comunidad_autonoma or "",
            contacto.tipo_cliente or "",
            contacto.etiquetas_holded or "",
            "Sí" if contacto.activo else "No",
        ])
    filename_suffix = f"_{suffix}" if suffix else ""
    filename = f"contactos_entre_lotes{filename_suffix}_{date.today().isoformat()}.xlsx"
    return Response(
        build_excel_workbook(rows),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


def load_productos_catalogo() -> list[str]:
    """Carga el catálogo de productos para autocompletar formularios."""

    if not DEFAULT_PRODUCTOS_CATALOGO.exists():
        return []

    try:
        with DEFAULT_PRODUCTOS_CATALOGO.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    productos = [clean_text(str(item), 150) for item in data if str(item).strip()]
    return list(dict.fromkeys(productos))


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
    app.config["PRODUCTOS_CATALOGO"] = load_productos_catalogo()

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
            "transportistas": TRANSPORTISTAS,
            "tipos_cliente": TIPOS_CLIENTE,
            "productos_catalogo": app.config.get("PRODUCTOS_CATALOGO", []),
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

    @app.route("/cuenta/cambiar-password", methods=["GET", "POST"])
    @login_required
    def cambiar_password():
        if request.method == "POST":
            password_actual = request.form.get("password_actual", "")
            nueva_password = request.form.get("nueva_password", "")
            repetir_password = request.form.get("repetir_password", "")

            if not current_user.check_password(password_actual):
                flash("La contraseña actual no es correcta.", "error")
                return render_template("cambiar_password.html", app_name="Entre Lotes")
            if not nueva_password:
                flash("La nueva contraseña no puede estar vacía.", "error")
                return render_template("cambiar_password.html", app_name="Entre Lotes")
            if nueva_password != repetir_password:
                flash("La nueva contraseña y su repetición deben coincidir.", "error")
                return render_template("cambiar_password.html", app_name="Entre Lotes")

            current_user.set_password(nueva_password)
            db.session.commit()
            flash("La contraseña se ha actualizado correctamente.", "success")
            return redirect(url_for("dashboard"))

        return render_template("cambiar_password.html", app_name="Entre Lotes")

    @app.get("/dashboard")
    @login_required
    def dashboard():
        total = Incidencia.query.count()
        nuevas = Incidencia.query.filter_by(estado="Nueva").count()
        abiertas = Incidencia.query.filter(
            Incidencia.estado.in_(ESTADOS_ABIERTOS)
        ).count()
        resueltas = Incidencia.query.filter_by(estado="Resuelta").count()
        conteos_por_tipo = dict(
            db.session.query(Incidencia.tipo_incidencia, db.func.count(Incidencia.id))
            .filter(Incidencia.tipo_incidencia.in_(TIPOS_DASHBOARD))
            .group_by(Incidencia.tipo_incidencia)
            .all()
        )
        total_tipos_dashboard = sum(conteos_por_tipo.values())
        incidencias_por_tipo = []
        for tipo in TIPOS_DASHBOARD:
            count = conteos_por_tipo.get(tipo, 0)
            incidencias_por_tipo.append(
                {
                    "nombre": tipo,
                    "total": count,
                    "porcentaje": round((count / total_tipos_dashboard) * 100)
                    if total_tipos_dashboard
                    else 0,
                }
            )
        conteos_transportista = dict(
            db.session.query(Incidencia.transportista, db.func.count(Incidencia.id))
            .filter(Incidencia.transportista.isnot(None))
            .filter(Incidencia.transportista != "")
            .group_by(Incidencia.transportista)
            .order_by(db.func.count(Incidencia.id).desc())
            .limit(4)
            .all()
        )
        total_transportistas_dashboard = sum(conteos_transportista.values())
        incidencias_por_transportista = []
        for transportista, count in conteos_transportista.items():
            incidencias_por_transportista.append(
                {
                    "nombre": transportista,
                    "total": count,
                    "porcentaje": round((count / total_transportistas_dashboard) * 100)
                    if total_transportistas_dashboard
                    else 0,
                }
            )
        incidencias_por_tipo_cliente = dict(
            db.session.query(Contacto.tipo_cliente, db.func.count(Incidencia.id))
            .join(Incidencia, Incidencia.contacto_id == Contacto.id)
            .filter(Contacto.tipo_cliente.isnot(None))
            .filter(Contacto.tipo_cliente != "")
            .group_by(Contacto.tipo_cliente)
            .order_by(db.func.count(Incidencia.id).desc())
            .limit(5)
            .all()
        )

        return render_template(
            "dashboard.html",
            app_name="Entre Lotes",
            total=total,
            nuevas=nuevas,
            abiertas=abiertas,
            resueltas=resueltas,
            incidencias_por_tipo=incidencias_por_tipo,
            incidencias_por_transportista=incidencias_por_transportista,
            incidencias_por_tipo_cliente=incidencias_por_tipo_cliente,
        )

    @app.get("/contactos")
    @login_required
    def contactos_list():
        filters = get_contacto_filters(request.args)
        contactos = build_contactos_query(filters).all()
        return render_template(
            "contactos.html",
            app_name="Entre Lotes",
            contactos=contactos,
            filters=filters,
        )

    @app.get("/contactos/exportar")
    @login_required
    def contactos_exportar():
        filters = get_contacto_filters(request.args)
        contactos = build_contactos_query(filters).all()
        return build_contactos_export_response(contactos)

    @app.route("/contactos/nuevo", methods=["GET", "POST"])
    @login_required
    def contacto_nuevo():
        if request.method == "POST":
            data, errors = validate_contacto_form(request.form)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "nuevo_contacto.html",
                    app_name="Entre Lotes",
                    form_data=request.form,
                )
            contacto = Contacto(**data)
            db.session.add(contacto)
            db.session.commit()
            flash("El contacto se ha creado correctamente.", "success")
            return redirect(url_for("contacto_detalle", id=contacto.id))

        return render_template("nuevo_contacto.html", app_name="Entre Lotes")

    @app.get("/contactos/<int:id>")
    @login_required
    def contacto_detalle(id: int):
        contacto = db.get_or_404(Contacto, id)
        incidencias_contacto = (
            Incidencia.query.filter_by(contacto_id=contacto.id)
            .order_by(Incidencia.fecha_incidencia.desc(), Incidencia.id.desc())
            .all()
        )
        total_incidencias = len(incidencias_contacto)
        incidencias_abiertas = sum(
            1 for incidencia in incidencias_contacto if incidencia.estado in ESTADOS_ABIERTOS
        )
        incidencias_cerradas = sum(
            1 for incidencia in incidencias_contacto if incidencia.estado in ESTADOS_CON_CIERRE
        )
        ultima_incidencia = (
            max(incidencias_contacto, key=lambda incidencia: incidencia.fecha_registro)
            if incidencias_contacto
            else None
        )
        return render_template(
            "detalle_contacto.html",
            app_name="Entre Lotes",
            contacto=contacto,
            incidencias_contacto=incidencias_contacto,
            total_incidencias=total_incidencias,
            incidencias_abiertas=incidencias_abiertas,
            incidencias_cerradas=incidencias_cerradas,
            ultima_incidencia=ultima_incidencia,
        )

    @app.route("/contactos/<int:id>/editar", methods=["GET", "POST"])
    @login_required
    def contacto_editar(id: int):
        contacto = db.get_or_404(Contacto, id)
        if request.method == "POST":
            data, errors = validate_contacto_form(request.form)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template(
                    "editar_contacto.html",
                    app_name="Entre Lotes",
                    contacto=contacto,
                    form_data=request.form,
                )
            for field, value in data.items():
                setattr(contacto, field, value)
            db.session.commit()
            flash("El contacto se ha actualizado correctamente.", "success")
            return redirect(url_for("contacto_detalle", id=contacto.id))
        return render_template(
            "editar_contacto.html",
            app_name="Entre Lotes",
            contacto=contacto,
        )

    @app.post("/contactos/<int:id>/eliminar")
    @login_required
    def contacto_eliminar(id: int):
        contacto = db.get_or_404(Contacto, id)
        contacto.activo = False
        db.session.commit()
        flash("El contacto se ha desactivado correctamente.", "success")
        return redirect(url_for("contactos_list"))

    @app.route("/contactos/importar", methods=["GET", "POST"])
    @login_required
    def contactos_importar():
        if request.method == "POST":
            file = request.files.get("archivo_csv")
            if not file or not file.filename:
                flash("Selecciona un archivo CSV para importar.", "error")
                return render_template("contactos_importar.html", app_name="Entre Lotes")

            raw = file.read().decode("utf-8-sig", errors="ignore")
            reader = csv.reader(raw.splitlines())
            rows = list(reader)
            if not rows:
                flash("El archivo CSV está vacío.", "error")
                return render_template("contactos_importar.html", app_name="Entre Lotes")

            headers_map = normalize_csv_headers(rows[0])
            created = 0
            updated = 0
            skipped = 0
            errors_count = 0

            for row in rows[1:]:
                joined = "".join(row).strip()
                if not joined:
                    skipped += 1
                    continue

                payload = {
                    "nombre": csv_value(row, headers_map, ["nombre", "nombre_comercial"]),
                    "razon_social": csv_value(row, headers_map, ["razon_social"]),
                    "cif": csv_value(row, headers_map, ["cif", "nif"]),
                    "email": csv_value(row, headers_map, ["email", "correo"]),
                    "telefono": csv_value(row, headers_map, ["telefono", "teléfono", "movil", "móvil"]),
                    "direccion": csv_value(row, headers_map, ["direccion", "dirección"]),
                    "provincia": csv_value(row, headers_map, ["provincia"]),
                    "codigo_postal": csv_value(row, headers_map, ["codigo_postal", "código_postal", "cp"]),
                    "comunidad_autonoma": csv_value(
                        row,
                        headers_map,
                        ["comunidad_autonoma", "comunidad_autónoma", "comunidad"],
                    ),
                    "tipo_cliente": csv_value(row, headers_map, ["tipo_cliente", "tipo"]),
                    "etiquetas_holded": csv_value(row, headers_map, ["etiquetas_holded", "etiquetas", "tags"]),
                    "activo": "1",
                }
                data, validation_errors = validate_contacto_form(payload)
                if validation_errors:
                    errors_count += 1
                    skipped += 1
                    continue

                existing = None
                if data["cif"]:
                    existing = Contacto.query.filter_by(cif=data["cif"]).first()
                if not existing and data["email"]:
                    existing = Contacto.query.filter_by(email=data["email"]).first()
                if not existing:
                    existing = Contacto.query.filter_by(nombre=data["nombre"]).first()

                if existing:
                    for field, value in data.items():
                        setattr(existing, field, value)
                    updated += 1
                else:
                    db.session.add(Contacto(**data))
                    created += 1

            db.session.commit()
            flash(
                f"Importación completada. Creados: {created}. Actualizados: {updated}. Omitidos: {skipped}. Errores: {errors_count}.",
                "success",
            )
            return redirect(url_for("contactos_list"))

        return render_template("contactos_importar.html", app_name="Entre Lotes")

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
            sort_links=build_sort_links(filters),
        )

    @app.get("/incidencias/exportar")
    @login_required
    def incidencias_exportar():
        filters = get_incidencia_filters(request.args)
        incidencias = build_incidencias_query(filters).all()
        return build_incidencias_export_response(incidencias)

    @app.post("/incidencias/acciones")
    @login_required
    def incidencias_acciones_masivas():
        selected_ids = parse_selected_incidencia_ids(request.form.getlist("ids"))
        action = clean_text(request.form.get("bulk_action", ""), 30)

        if not selected_ids:
            flash("Selecciona al menos una incidencia.", "error")
            return redirect(url_for("incidencias_list"))

        incidencias = (
            Incidencia.query.filter(Incidencia.id.in_(selected_ids))
            .order_by(Incidencia.id.desc())
            .all()
        )

        if action == "export":
            return build_incidencias_export_response(incidencias, "seleccionadas")

        if action == "delete":
            deleted_count = len(incidencias)
            for incidencia in incidencias:
                db.session.delete(incidencia)
            db.session.commit()
            flash(f"Se han eliminado {deleted_count} incidencias.", "success")
            return redirect(url_for("incidencias_list"))

        flash("Selecciona una acción válida.", "error")
        return redirect(url_for("incidencias_list"))

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
        return render_template(
            "detalle_incidencia.html",
            app_name="Entre Lotes",
            incidencia=incidencia,
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

    @app.post("/incidencias/<int:id>/eliminar")
    @login_required
    def incidencia_eliminar(id: int):
        incidencia = db.get_or_404(Incidencia, id)
        db.session.delete(incidencia)
        db.session.commit()
        flash("La incidencia se ha eliminado correctamente.", "success")
        return redirect(url_for("incidencias_list"))

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
