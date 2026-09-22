"""
Test Execute DAX Queries (Arrow) pakai Service Principal
===========================================================
Auth pakai client credentials flow (Service Principal) dari .env kamu.

Prasyarat:
  pip install requests pyarrow pandas python-dotenv

.env yang dibutuhkan (tambahkan DATASET_ID & GROUP_ID ke .env kamu yang
sudah ada TENANT_ID / CLIENT_ID / CLIENT_SECRET):
  TENANT_ID=...
  CLIENT_ID=...
  CLIENT_SECRET=...
  DATASET_ID=...      # wajib
  GROUP_ID=...         # opsional, isi kalau dataset ada di workspace tertentu

Checklist sebelum jalan (kalau masih error auth/izin, cek ini dulu):
  1. Dataset ada di Premium/Fabric capacity (bukan Pro biasa).
  2. Tenant setting "Dataset Execute Queries REST API" aktif.
  3. Tenant setting "Allow XMLA endpoints and Analyze in Excel with
     on-premises semantic models" aktif.
  4. Tenant setting "Allow service principals to use Power BI APIs" aktif,
     dan service principal ini termasuk di security group yang diizinkan.
  5. Service principal ini sudah ditambahkan sebagai Member/Admin di
     workspace yang berisi dataset targetnya (Manage access di workspace).
"""

import io
import json
import os
import time

import pyarrow as pa
import requests
from dotenv import load_dotenv

load_dotenv()

# ============ 1) KONFIGURASI ============
TENANT_ID = os.environ["TENANT_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
DATASET_ID = os.environ["DATASET_ID"]
GROUP_ID = os.environ.get("GROUP_ID", "")  # opsional

# Ganti dengan query DAX terhadap model semantik kamu sendiri
DAX_QUERY = "EVALUATE TOPN(5, 'FactSalesOrder')"


def get_access_token() -> str:
    """Ambil access token via client credentials flow (Service Principal)."""
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://analysis.windows.net/powerbi/api/.default",
    }
    resp = requests.post(url, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _dataset_base_url() -> str:
    if GROUP_ID:
        return f"https://api.powerbi.com/v1.0/myorg/groups/{GROUP_ID}/datasets/{DATASET_ID}"
    return f"https://api.powerbi.com/v1.0/myorg/datasets/{DATASET_ID}"


def execute_dax_arrow(token: str, query: str, **extra_params) -> bytes:
    """Panggil endpoint BARU (Arrow). Mengembalikan raw bytes (Arrow IPC stream)."""
    url = f"{_dataset_base_url()}/executeDaxQueries"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"query": query, **extra_params}
    resp = requests.post(url, headers=headers, json=body)
    if not resp.ok:
        print(f"[ERROR] Status {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.content


def parse_arrow_response(content: bytes):
    """pyarrow otomatis menangani LZ4_FRAME compression yang dipakai di record batch."""
    stream = io.BytesIO(content)
    results = []
    while stream.tell() < len(content):
        try:
            reader = pa.ipc.open_stream(stream)
            table = reader.read_all()
            metadata = {
                k.decode(): v.decode() for k, v in (reader.schema.metadata or {}).items()
            }
            if metadata.get("IsError") == "true":
                raise RuntimeError(
                    f"Query error [{metadata.get('FaultCode')}]: {metadata.get('FaultString')}"
                )
            table = _decode_dictionary_columns(table)
            results.append(table)
        except pa.ArrowInvalid:
            break
    return results


def _decode_dictionary_columns(table: pa.Table) -> pa.Table:
    """String di Arrow sering dikirim sebagai dictionary-encoded (hemat ukuran).
    Tapi kalau kamus (dictionary) itu sendiri berisi value duplikat -- yang bisa
    kejadian tergantung bagaimana server membentuknya -- pandas menolak karena
    Categorical mewajibkan kategori unik ("Categorical categories must be
    unique"). unify_dictionaries() cuma menyatukan kamus ANTAR CHUNK, tidak
    membersihkan duplikat DI DALAM satu kamus. Solusi paling aman: cast semua
    kolom dictionary jadi string biasa sebelum dikonversi ke pandas.
    """
    for i, field in enumerate(table.schema):
        if pa.types.is_dictionary(field.type):
            table = table.set_column(
                i, field.name, table.column(i).cast(field.type.value_type)
            )
    return table


def execute_dax_json(token: str, query: str) -> bytes:
    """Endpoint lama (JSON), untuk perbandingan."""
    url = f"{_dataset_base_url()}/executeQueries"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"queries": [{"query": query}]}
    resp = requests.post(url, headers=headers, json=body)
    if not resp.ok:
        print(f"[ERROR] Status {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.content


def main():
    print("Mengambil access token (Service Principal)...")
    token = get_access_token()
    print("Token didapat.\n")

    # ---- 1) Endpoint baru: Execute DAX Queries (Arrow) ----
    t0 = time.perf_counter()
    arrow_bytes = execute_dax_arrow(token, DAX_QUERY, queryTimeout=120)
    tables = parse_arrow_response(arrow_bytes)
    t1 = time.perf_counter()

    table = tables[0]
    df = table.to_pandas()

    print("=== Hasil via Execute DAX Queries (Arrow) ===")
    print("Schema (tipe kolom asli):")
    print(table.schema)
    print("\nData:")
    print(df)
    print(f"\nWaktu  : {t1 - t0:.3f} detik")
    print(f"Ukuran : {len(arrow_bytes)} bytes")

    # ---- 2) Endpoint lama: Execute Queries (JSON), untuk perbandingan ----
    t2 = time.perf_counter()
    json_bytes = execute_dax_json(token, DAX_QUERY)
    t3 = time.perf_counter()
    json_data = json.loads(json_bytes)

    print("\n=== Hasil via Execute Queries (JSON) ===")
    print(json_data)
    print(f"Waktu  : {t3 - t2:.3f} detik")
    print(f"Ukuran : {len(json_bytes)} bytes")

    # ---- 3) Ringkasan ----
    saving_pct = (1 - len(arrow_bytes) / len(json_bytes)) * 100 if json_bytes else 0
    print("\n=== RINGKASAN ===")
    print(f"Payload Arrow : {len(arrow_bytes):>8} bytes")
    print(f"Payload JSON  : {len(json_bytes):>8} bytes")
    print(f"Penghematan   : {saving_pct:.1f}%")


if __name__ == "__main__":
    main()