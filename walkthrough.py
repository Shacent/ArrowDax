"""
Walkthrough: Cara "Membungkus" Query DAX di Python
====================================================
Ini versi SIMULASI (mock) yang bisa langsung dijalankan tanpa kredensial,
TAPI strukturnya PERSIS sama dengan versi API asli. Nanti kalau Client ID
sudah ada, kamu tinggal ganti ISI fungsi `execute_dax_arrow()` di bagian 2
dengan versi `requests.post()` yang asli (sudah dikomentarin di bawah) --
kode di bagian 3 dan 4 (parsing & consume) TIDAK PERLU DIUBAH SAMA SEKALI,
karena bentuk data yang keluar dari Power BI asli sama persis bentuknya.

Prasyarat: pip install pyarrow pandas
"""

import io
from decimal import Decimal

import pandas as pd
import pyarrow as pa


# ============================================================
# 1) SYNTAX -- query DAX itu cuma STRING PYTHON biasa
# ============================================================
# Kamu tulis DAX di dalam triple-quote string, persis seperti nulis DAX di
# DAX Studio / Power BI Desktop. Buat Python, ini cuma teks biasa -- Python
# tidak "mengerti" DAX, dia cuma nitip-in teks ini ke Power BI lewat API.

DAX_QUERY = """
EVALUATE
SUMMARIZECOLUMNS(
    'Sales'[Region],
    "Total Amount", SUM('Sales'[Amount])
)
"""

# Query string di atas nanti dibungkus jadi JSON body seperti ini:
#     { "query": DAX_QUERY }
# lalu dikirim lewat HTTP POST ke:
#     https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/executeDaxQueries
# Power BI yang menjalankan DAX-nya di server, Python cuma "nyuruh".


# ============================================================
# 2) EXECUTE -- fungsi yang "menjalankan" query itu
# ============================================================
def execute_dax_arrow(query: str) -> bytes:
    """Mengirim query DAX, mengembalikan RAW BYTES (Arrow IPC) dari response.

    >>> NANTI KALAU CLIENT ID SUDAH ADA, ganti ISI fungsi ini jadi: <<<

        import requests
        url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{GROUP_ID}"
            f"/datasets/{DATASET_ID}/executeDaxQueries"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {"query": query}
        resp = requests.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.content  # <- ini raw bytes Arrow IPC dari server Power BI

    >>> VERSI SIMULASI (dipakai sekarang), kita "pura-pura" jadi server <<<
    Kita bikin data secara lokal dengan bentuk yang SAMA seperti hasil
    SUMMARIZECOLUMNS di atas, supaya alurnya bisa dicoba tanpa API asli.
    """
    print(f"[SIMULASI] 'Mengirim' query ke Power BI:\n{query.strip()}\n")

    # Data ini seharusnya datang dari server Power BI -- di sini kita bikin
    # manual, bentuknya sengaja disamakan dengan hasil query di atas.
    table = pa.table(
        {
            "Region": ["Jakarta", "Surabaya", "Bandung"],
            "Total Amount": pa.array(
                [Decimal("125000.5000"), Decimal("89000.0000"), Decimal("67500.2500")],
                type=pa.decimal128(19, 4),
            ),
        }
    )

    # Bagian ini PERSIS SAMA dengan yang dilakukan server asli: menulis
    # tabel jadi bytes Arrow IPC. Anggap `sink.getvalue()` ini sama dengan
    # `response.content` kalau kamu pakai `requests.post()` beneran.
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


# ============================================================
# 3) OUTPUT -- apa yang kamu terima, dan cara membacanya
# ============================================================
def parse_arrow_response(raw_bytes: bytes) -> pa.Table:
    """Fungsi ini SAMA PERSIS dipakai baik untuk simulasi maupun API asli --
    tidak perlu diubah sama sekali nanti, karena isinya cuma "baca bytes
    Arrow jadi Table", terlepas dari mana asal bytes-nya."""
    reader = pa.ipc.open_stream(io.BytesIO(raw_bytes))
    return reader.read_all()


# ============================================================
# 4) CONSUME -- cara MEMAKAI hasilnya
# ============================================================
def main():
    # --- Langkah A: jalankan query, dapat raw bytes ---
    raw_bytes = execute_dax_arrow(DAX_QUERY)
    print(f"Ukuran response mentah: {len(raw_bytes)} bytes (masih bentuk Arrow biner, belum kebaca)\n")

    # --- Langkah B: parse raw bytes jadi Arrow Table ---
    table = parse_arrow_response(raw_bytes)
    print("Schema hasil (nama kolom + tipe data, tahu duluan sebelum baca datanya):")
    print(table.schema)
    print()

    # --- Langkah C: consume cara 1 -- ubah ke pandas DataFrame (paling umum) ---
    df = table.to_pandas()
    print("Sebagai pandas DataFrame:")
    print(df)
    print()

    # --- Consume cara 2: ambil satu kolom saja langsung dari Arrow Table ---
    region_list = table.column("Region").to_pylist()
    print(f"Cuma kolom Region (list Python biasa): {region_list}\n")

    # --- Consume cara 3: loop per baris, kalau perlu proses satu-satu ---
    print("Loop manual per baris:")
    for _, row in df.iterrows():
        print(f"  {row['Region']}: Rp {float(row['Total Amount']):,.2f}")

    # --- Consume cara 4: agregasi/filter pakai pandas seperti biasa ---
    total_keseluruhan = df["Total Amount"].astype(float).sum()
    print(f"\nTotal keseluruhan semua region: Rp {total_keseluruhan:,.2f}")

    # --- Consume cara 5: ekspor ke file, kalau perlu dishare ---
    # df.to_csv("hasil_dax.csv", index=False)
    # print("Tersimpan ke hasil_dax.csv")


if __name__ == "__main__":
    main()