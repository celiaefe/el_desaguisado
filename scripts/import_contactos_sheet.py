#!/usr/bin/env python3
"""Importa contactos desde Google Sheets usando la misma lógica de la app."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    Contacto,
    app,
    csv_value,
    normalize_csv_headers,
    validate_contacto_form,
    db,
)


DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1aqSx6KBZALru-ZAbPiGUFy6KiO0X68SfJmCFRzcnawA/edit?usp=sharing"
)


def sheet_csv_url(sheet_url: str) -> str:
    path_parts = [part for part in urlparse(sheet_url).path.split("/") if part]
    if "d" not in path_parts:
        raise ValueError("URL de Google Sheets no válida.")
    doc_index = path_parts.index("d")
    if doc_index + 1 >= len(path_parts):
        raise ValueError("No se encontró el ID de la hoja.")
    sheet_id = path_parts[doc_index + 1]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def import_contacts(sheet_url: str) -> tuple[int, int, int, int]:
    csv_url = sheet_csv_url(sheet_url)
    with urlopen(csv_url) as response:  # nosec B310
        raw = response.read().decode("utf-8-sig", errors="ignore")
    rows = list(csv.reader(raw.splitlines()))
    if not rows:
        return 0, 0, 0, 0

    headers_map = normalize_csv_headers(rows[0])
    created = 0
    updated = 0
    skipped = 0
    errors_count = 0

    for row in rows[1:]:
        if not "".join(row).strip():
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
                row, headers_map, ["comunidad_autonoma", "comunidad_autónoma", "comunidad"]
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
    return created, updated, skipped, errors_count


if __name__ == "__main__":
    with app.app_context():
        created, updated, skipped, errors_count = import_contacts(DEFAULT_SHEET_URL)
        print(
            f"Importación completada. Creados: {created}. "
            f"Actualizados: {updated}. Omitidos: {skipped}. Errores: {errors_count}."
        )
