"""
Demo LOKAL: Apache Arrow vs JSON untuk hasil query DAX
=======================================================
Skrip ini TIDAK memanggil Power BI API sama sekali -- cocok buat kamu yang
belum punya Client ID / belum bisa login. Kita bikin data contoh yang
meniru hasil sebuah query DAX (kolom-kolom umum: Region, Date, Amount,
Quantity, IsActive), sesuai mapping tipe DAX -> Arrow di dokumentasi resmi:

    DAX type    -> Arrow type
    Integer     -> int64
    Currency    -> decimal128(19,4)
    DateTime    -> date64
    String      -> utf8
    Boolean     -> bool

Lalu kita serialize data itu dengan DUA cara:
    1. Apache Arrow IPC (seperti yang dikembalikan endpoint executeDaxQueries)
    2. JSON row-oriented (seperti yang dikembalikan endpoint executeQueries lama)

...dan membandingkan ukuran payload, kecepatan deserialize, dan fidelitas tipe
data -- persis argumen yang dipakai di artikel/dokumentasi Microsoft.

Kalau nanti Client ID sudah ada, kamu tinggal ganti fungsi build_sample_table()
di bawah dengan hasil parsing response asli dari executeDaxQueries (lihat
dax_arrow_demo.py), sisanya (perbandingan ukuran & kecepatan) bisa dipakai apa
adanya.

Prasyarat:
  pip install pyarrow pandas
"""

import io
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pyarrow as pa


# ============ 1) BIKIN DATA CONTOH (meniru hasil query DAX) ============
def build_sample_table(n_rows: int = 5000) -> pa.Table:
    """Bikin tabel Arrow dengan tipe data yang mencerminkan mapping DAX -> Arrow.

    Ganti nama kolom / logika di sini kalau mau lebih mirip semantic model
    kamu sendiri (mis. tabel Sales dengan Region, Date, Amount, Quantity).
    """
    regions = ["Jakarta", "Surabaya", "Bandung", "Medan", "Makassar"]
    base_date = datetime(2026, 1, 1)

    region_col = [regions[i % len(regions)] for i in range(n_rows)]
    date_col = [base_date + timedelta(days=i % 365) for i in range(n_rows)]
    amount_col = [Decimal(f"{(i * 137) % 100000}.{(i * 7) % 100:02d}") for i in range(n_rows)]
    qty_col = [i % 500 for i in range(n_rows)]
    is_active_col = [i % 3 != 0 for i in range(n_rows)]

    schema = pa.schema(
        [
            ("Region", pa.utf8()),                 # DAX String   -> utf8
            ("Date", pa.date64()),                 # DAX DateTime -> date64
            ("Amount", pa.decimal128(19, 4)),       # DAX Currency -> decimal128(19,4)
            ("Quantity", pa.int64()),               # DAX Integer  -> int64
            ("IsActive", pa.bool_()),               # DAX Boolean  -> bool
        ]
    )

    return pa.table(
        {
            "Region": region_col,
            "Date": date_col,
            "Amount": amount_col,
            "Quantity": qty_col,
            "IsActive": is_active_col,
        },
        schema=schema,
    )


# ============ 2) SERIALIZE ke Arrow IPC (mensimulasikan response executeDaxQueries) ============
def to_arrow_ipc_bytes(table: pa.Table, compressed: bool = True) -> bytes:
    options = pa.ipc.IpcWriteOptions(compression="lz4_frame" if compressed else None)
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema, options=options) as writer:
        writer.write_table(table)
    return sink.getvalue()


def parse_arrow_ipc_bytes(data: bytes) -> pa.Table:
    reader = pa.ipc.open_stream(io.BytesIO(data))
    return reader.read_all()


# ============ 3) SERIALIZE ke JSON (mensimulasikan response executeQueries lama) ============
def to_json_bytes(table: pa.Table) -> bytes:
    rows = table.to_pylist()
    # JSON tidak punya tipe date/decimal native -> semuanya jadi string, sama
    # seperti perilaku endpoint Execute Queries yang lama.
    for row in rows:
        row["Date"] = row["Date"].isoformat()
        row["Amount"] = str(row["Amount"])
    payload = {"results": [{"tables": [{"rows": rows}]}]}
    return json.dumps(payload).encode("utf-8")


def parse_json_bytes(data: bytes):
    return json.loads(data)


# ============ 4) DEMO & PERBANDINGAN ============
def main():
    n_rows = 5000
    print(f"Membuat {n_rows} baris data contoh (meniru hasil query DAX)...\n")
    table = build_sample_table(n_rows)

    # --- Serialize ---
    arrow_bytes = to_arrow_ipc_bytes(table, compressed=True)
    json_bytes = to_json_bytes(table)

    print("=== Ukuran payload ===")
    print(f"Arrow (LZ4 compressed) : {len(arrow_bytes):>10,} bytes")
    print(f"JSON                    : {len(json_bytes):>10,} bytes")
    saving_pct = (1 - len(arrow_bytes) / len(json_bytes)) * 100
    print(f"Penghematan ukuran      : {saving_pct:.1f}%\n")

    # --- Kecepatan deserialize (rata-rata dari beberapa kali percobaan) ---
    n_trials = 20

    t0 = time.perf_counter()
    for _ in range(n_trials):
        restored_table = parse_arrow_ipc_bytes(arrow_bytes)
        df_arrow = restored_table.to_pandas()
    t_arrow = (time.perf_counter() - t0) / n_trials

    t0 = time.perf_counter()
    for _ in range(n_trials):
        restored_json = parse_json_bytes(json_bytes)
        rows = restored_json["results"][0]["tables"][0]["rows"]
        import pandas as pd  # local import biar jelas ini "cara lama"

        df_json = pd.DataFrame(rows)
    t_json = (time.perf_counter() - t0) / n_trials

    print("=== Kecepatan deserialize (rata-rata dari 20x percobaan) ===")
    print(f"Arrow -> pandas.DataFrame : {t_arrow * 1000:.2f} ms")
    print(f"JSON  -> pandas.DataFrame : {t_json * 1000:.2f} ms")
    print(f"Arrow lebih cepat {t_json / t_arrow:.1f}x\n")

    # --- Fidelitas tipe data ---
    print("=== Fidelitas tipe data ===")
    print("Arrow (native, siap analisis):")
    print(df_arrow.dtypes)
    print(f"\nContoh nilai Amount dari Arrow : {df_arrow['Amount'].iloc[0]!r} (tipe: {type(df_arrow['Amount'].iloc[0])})")
    print(f"Contoh nilai Date dari Arrow   : {df_arrow['Date'].iloc[0]!r} (tipe: {type(df_arrow['Date'].iloc[0])})")

    print("\nJSON (semua jadi string, perlu parsing manual lagi):")
    print(df_json.dtypes)
    print(f"Contoh nilai Amount dari JSON  : {df_json['Amount'].iloc[0]!r} (tipe: {type(df_json['Amount'].iloc[0])})")
    print(f"Contoh nilai Date dari JSON    : {df_json['Date'].iloc[0]!r} (tipe: {type(df_json['Date'].iloc[0])})")

    print("\n=== RINGKASAN UNTUK DEMO KE ATASAN ===")
    print(f"- Payload Arrow {saving_pct:.0f}% lebih kecil dari JSON, untuk {n_rows} baris data.")
    print(f"- Deserialize ke DataFrame {t_json / t_arrow:.1f}x lebih cepat pakai Arrow.")
    print("- Tipe data (tanggal, currency) datang sudah presisi & siap pakai,")
    print("  tidak perlu parsing/konversi manual seperti dari JSON.")
    print("- Catatan: ini simulasi lokal. Angka sebenarnya dari API asli bisa")
    print("  berbeda tergantung ukuran & bentuk data di semantic model kamu.")


if __name__ == "__main__":
    main()