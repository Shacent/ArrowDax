"""
schema_explorer.py

Eksplorasi Skema Semantic Model via DAX INFO Functions (Arrow)
================================================================
Lanjutan dari comparison.py kamu. Auth & endpoint SAMA PERSIS
(Service Principal, executeDaxQueries) -- cuma DAX query-nya diganti
ke fungsi metadata bawaan tabular model (INFO.VIEW.*), jadi kamu bisa
tahu nama tabel, nama & tipe kolom, measure, dan relasi antar tabel
tanpa perlu izin/endpoint tambahan.

Kenapa bisa gitu? INFO.VIEW.TABLES(), INFO.VIEW.COLUMNS(),
INFO.VIEW.MEASURES(), dan INFO.VIEW.RELATIONSHIPS() adalah DAX table
functions yang query metadata model itu sendiri -- dieksekusi lewat
endpoint executeDaxQueries yang sama seperti query data biasa.

Prasyarat: sama seperti comparison.py (.env dengan TENANT_ID,
CLIENT_ID, CLIENT_SECRET, DATASET_ID, GROUP_ID).

  pip install requests pyarrow pandas python-dotenv
"""

import io
import json
import os

import pandas as pd
import pyarrow as pa
import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.environ["TENANT_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
DATASET_ID = os.environ["DATASET_ID"]
GROUP_ID = os.environ.get("GROUP_ID", "")

# Ke mana hasil eksplorasi disimpan (JSON, bisa dipakai lagi nanti
# sebagai input buat LLM/Copilot kalau kamu lanjutkan bagian itu)
OUTPUT_JSON = "schema_profile.json"


# ============ AUTH & EKSEKUSI DAX (identik dengan comparison.py) ============

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
    for i, field in enumerate(table.schema):
        if pa.types.is_dictionary(field.type):
            table = table.set_column(
                i, field.name, table.column(i).cast(field.type.value_type)
            )
    return table


def parse_arrow_response(content: bytes) -> pd.DataFrame:
    """Ambil batch pertama dari response Arrow dan langsung ubah ke DataFrame."""
    stream = io.BytesIO(content)
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
            return table.to_pandas()
        except pa.ArrowInvalid:
            break
    return pd.DataFrame()


def run_dax(token: str, query: str) -> pd.DataFrame:
    return parse_arrow_response(execute_dax_arrow(token, query, queryTimeout=120))


# ============ QUERY METADATA (INFO.VIEW.*) ============

QUERY_TABLES = "EVALUATE INFO.VIEW.TABLES()"
QUERY_COLUMNS = "EVALUATE INFO.VIEW.COLUMNS()"
QUERY_MEASURES = "EVALUATE INFO.VIEW.MEASURES()"
QUERY_RELATIONSHIPS = "EVALUATE INFO.VIEW.RELATIONSHIPS()"


def get_tables(token: str) -> pd.DataFrame:
    df = run_dax(token, QUERY_TABLES)
    # Buang tabel sistem/hidden supaya fokus ke tabel yang relevan buat analisis
    if "IsHidden" in df.columns:
        df = df[df["IsHidden"] == False]  # noqa: E712
    return df.reset_index(drop=True)


def get_columns(token: str) -> pd.DataFrame:
    df = run_dax(token, QUERY_COLUMNS)
    if "IsHidden" in df.columns:
        df = df[df["IsHidden"] == False]  # noqa: E712
    return df.reset_index(drop=True)


def get_measures(token: str) -> pd.DataFrame:
    return run_dax(token, QUERY_MEASURES).reset_index(drop=True)


def get_relationships(token: str) -> pd.DataFrame:
    return run_dax(token, QUERY_RELATIONSHIPS).reset_index(drop=True)


def get_sample_rows(token: str, table_name: str, n: int = 5) -> pd.DataFrame:
    """Ambil beberapa baris contoh dari satu tabel -- berguna buat ngecek
    isi data (bukan cuma nama kolom), tanpa narik seluruh tabel."""
    query = f"EVALUATE TOPN({n}, '{table_name}')"
    return run_dax(token, query)


# ============ RANGKUM JADI SATU "SCHEMA PROFILE" ============

def build_schema_profile(token: str, sample_rows_per_table: int = 3) -> dict:
    tables_df = get_tables(token)
    columns_df = get_columns(token)
    measures_df = get_measures(token)
    relationships_df = get_relationships(token)

    # Nama kolom hasil INFO.VIEW.COLUMNS() biasanya: TableID/[Table Name (kalau
    # di-join), Column Name, Data Type / ExplicitDataType, dsb. Kita cocokkan
    # ke nama tabel via kolom umum yang tersedia.
    table_col = "Table Name" if "Table Name" in columns_df.columns else next(
        (c for c in columns_df.columns if "Table" in c), None
    )
    name_col = "Column Name" if "Column Name" in columns_df.columns else next(
        (c for c in columns_df.columns if "Name" in c), None
    )
    type_col = next((c for c in columns_df.columns if "DataType" in c or "Data Type" in c), None)

    profile = {"tables": []}

    for _, trow in tables_df.iterrows():
        tname = trow.get("Name") or trow.get("Table Name")
        if not tname:
            continue

        cols_for_table = pd.DataFrame()
        if table_col and name_col:
            cols_for_table = columns_df[columns_df[table_col] == tname]

        columns_list = []
        for _, crow in cols_for_table.iterrows():
            columns_list.append({
                "name": crow.get(name_col),
                "data_type": crow.get(type_col) if type_col else None,
            })

        table_entry = {
            "table_name": tname,
            "columns": columns_list,
        }

        try:
            sample = get_sample_rows(token, tname, sample_rows_per_table)
            table_entry["sample_rows"] = sample.to_dict(orient="records")
        except Exception as e:  # noqa: BLE001
            table_entry["sample_rows"] = []
            table_entry["sample_error"] = str(e)

        profile["tables"].append(table_entry)

    profile["measures"] = measures_df.to_dict(orient="records") if not measures_df.empty else []
    profile["relationships"] = relationships_df.to_dict(orient="records") if not relationships_df.empty else []

    return profile


# ============ MAIN ============

def main():
    print("Mengambil access token (Service Principal)...")
    token = get_access_token()
    print("Token didapat.\n")

    print("=== TABEL ===")
    tables_df = get_tables(token)
    print(tables_df[[c for c in ["Name", "Table Name"] if c in tables_df.columns]]
          if tables_df.shape[1] > 0 else "(tidak ada tabel ditemukan / semua hidden)")

    print("\n=== KOLOM (per tabel) ===")
    columns_df = get_columns(token)
    print(columns_df.head(30))
    print(f"... total {len(columns_df)} kolom (non-hidden)")

    print("\n=== MEASURE ===")
    measures_df = get_measures(token)
    if not measures_df.empty:
        cols_to_show = [c for c in ["Name", "Expression"] if c in measures_df.columns]
        print(measures_df[cols_to_show] if cols_to_show else measures_df)
    else:
        print("(tidak ada measure di model ini)")

    print("\n=== RELASI ANTAR TABEL ===")
    rel_df = get_relationships(token)
    print(rel_df if not rel_df.empty else "(tidak ada relasi terdeteksi)")

    print("\nMenyusun schema profile lengkap (termasuk sample rows per tabel)...")
    profile = build_schema_profile(token)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, default=str, ensure_ascii=False)

    print(f"\nSelesai. Schema profile disimpan ke: {OUTPUT_JSON}")
    print(f"Jumlah tabel: {len(profile['tables'])}")
    print(f"Jumlah measure: {len(profile['measures'])}")
    print(f"Jumlah relasi: {len(profile['relationships'])}")
    print("\nCatatan: schema_profile.json ini formatnya sudah siap kalau nanti mau")
    print("dipakai sebagai konteks buat LLM (Claude/Azure OpenAI/dll) untuk minta")
    print("rekomendasi visual & narasi -- tinggal load JSON-nya dan kirim ke API LLM pilihanmu.")


if __name__ == "__main__":
    main()