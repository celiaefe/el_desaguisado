#!/usr/bin/env python3
"""Sincroniza catálogo de productos desde Google Sheets a JSON local."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1NmOf1U8jYrgKuM36rYv_mnh2RvWFg2IOe-vgQt9-F9M/edit?usp=sharing"
)
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "static" / "productos_catalogo.json"


def sheet_csv_url(sheet_url: str) -> str:
    """Convierte una URL de Google Sheets en URL de exportación CSV."""

    path_parts = [part for part in urlparse(sheet_url).path.split("/") if part]
    if "d" not in path_parts:
        raise ValueError("URL de Google Sheets no válida.")
    doc_index = path_parts.index("d")
    if doc_index + 1 >= len(path_parts):
        raise ValueError("No se encontró el ID de la hoja.")
    sheet_id = path_parts[doc_index + 1]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def normalize_producto(value: str) -> str:
    return " ".join(value.split()).strip()


def should_skip(value: str) -> bool:
    lowered = value.casefold()
    return lowered in {"", "nombre", "descripción", "descripcion"}


def fetch_productos(sheet_url: str) -> list[str]:
    csv_url = sheet_csv_url(sheet_url)
    with urlopen(csv_url) as response:  # nosec B310
        decoded = response.read().decode("utf-8-sig", errors="ignore")

    reader = csv.reader(decoded.splitlines())
    productos: list[str] = []
    seen: set[str] = set()

    for row in reader:
        if not row:
            continue
        producto = normalize_producto(row[0])
        if should_skip(producto):
            continue
        if producto not in seen:
            seen.add(producto)
            productos.append(producto)

    return productos


def main() -> None:
    productos = fetch_productos(DEFAULT_SHEET_URL)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(productos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Productos sincronizados: {len(productos)}")
    print(f"Archivo actualizado: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
