"""
Ambil metadata semantic model: deskripsi Tabel & Kolom via INFO functions
============================================================================

INFO.TABLES() dan INFO.VIEW.COLUMNS() itu fungsi DAX khusus buat baca
METADATA model (nama tabel, nama kolom, description-nya) -- bukan data
transaksional biasa. Query-nya tetap DAX biasa, dikirim ke endpoint
executeDaxQueries yang sama seperti query data biasa; bedanya cuma isinya.

Karena executeDaxQueries mendukung BANYAK EVALUATE dalam satu request, kita
ambil dua-duanya (tabel + kolom) sekaligus dalam satu kali panggilan API.

Prasyarat:
  pip install requests pyarrow pandas python-dotenv

.env yang dibutuhkan (sama seperti skrip sebelumnya):
  TENANT_ID=...
  CLIENT_ID=...
  CLIENT_SECRET=...
  DATASET_ID=...
  GROUP_ID=...   # opsional
"""

import io
import os

import pyarrow as pa
import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.environ["TENANT_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
DATASET_ID = os.environ["DATASET_ID"]
GROUP_ID = os.environ.get("GROUP_ID", "")


def get_access_token() -> str:
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
    url = f"{_dataset_base_url()}/executeDaxQueries"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"query": query, **extra_params}
    resp = requests.post(url, headers=headers, json=body)
    if not resp.ok:
        print(f"[ERROR] Status {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    return resp.content


def _decode_dictionary_columns(table: pa.Table) -> pa.Table:
    """Cast kolom dictionary-encoded jadi string biasa (lihat penjelasan di
    dax_arrow_service_principal.py kalau lupa kenapa ini perlu)."""
    for i, field in enumerate(table.schema):
        if pa.types.is_dictionary(field.type):
            table = table.set_column(i, field.name, table.column(i).cast(field.type.value_type))
    return table


def parse_arrow_response(content: bytes):
    """Query ini punya 2 EVALUATE -> response-nya 2 Arrow stream digabung jadi
    satu. Loop ini membaca semuanya, satu pa.Table per EVALUATE, urut sesuai
    urutan di query."""
    stream = io.BytesIO(content)
    results = []
    while stream.tell() < len(content):
        try:
            reader = pa.ipc.open_stream(stream)
            table = reader.read_all()
            metadata = {k.decode(): v.decode() for k, v in (reader.schema.metadata or {}).items()}
            if metadata.get("IsError") == "true":
                raise RuntimeError(f"Query error [{metadata.get('FaultCode')}]: {metadata.get('FaultString')}")
            results.append(_decode_dictionary_columns(table))
        except pa.ArrowInvalid:
            break
    return results


# Dua EVALUATE dalam satu query -> dua tabel hasil, dalam satu request.
DAX_QUERY = """
EVALUATE
SELECTCOLUMNS(
    INFO.TABLES(),
    "Table", [Name],
    "Description", [Description]
)

EVALUATE
SELECTCOLUMNS(
    INFO.VIEW.COLUMNS(),
    "Table", [Table],
    "Column", [Name],
    "Description", [Description]
)
"""

def main():
    print("Mengambil access token...")
    token = get_access_token()
    print("Token didapat. Menjalankan query metadata...\n")

    arrow_bytes = execute_dax_arrow(token, DAX_QUERY, queryTimeout=120)
    tables = parse_arrow_response(arrow_bytes)

    print(f"Dapat {len(tables)} tabel hasil (sesuai jumlah EVALUATE di query)\n")

    # EVALUATE pertama -> deskripsi tabel
    df_tables = tables[0].to_pandas()
    print("=== Deskripsi Tabel (INFO.TABLES) ===")
    print(df_tables.to_string(index=False))
    print(f"\nTotal: {len(df_tables)} tabel\n")

    # EVALUATE kedua -> deskripsi kolom
    df_columns = tables[1].to_pandas()
    print("=== Deskripsi Kolom (INFO.VIEW.COLUMNS) ===")
    print(df_columns.to_string(index=False))
    print(f"\nTotal: {len(df_columns)} kolom")

    # Opsional: simpan ke CSV buat dishare ke lead
    # df_tables.to_csv("deskripsi_tabel.csv", index=False)
    # df_columns.to_csv("deskripsi_kolom.csv", index=False)
    # print("\nTersimpan ke deskripsi_tabel.csv dan deskripsi_kolom.csv")


if __name__ == "__main__":
    main()