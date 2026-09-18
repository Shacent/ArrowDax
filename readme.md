# DAX Arrow Demo — Kenapa Apache Arrow Lebih Cepat dari JSON

Repo kecil ini adalah simulasi lokal untuk memahami **kenapa** Power BI merilis
endpoint baru **Execute DAX Queries** yang mengembalikan hasil dalam format
**Apache Arrow**, dibanding endpoint lama **Execute Queries** yang
mengembalikan **JSON**.

> Skrip di sini **tidak memanggil Power BI API sama sekali**. Semuanya
> disimulasikan secara lokal dengan data contoh, supaya konsepnya bisa
> dipahami dan didemokan tanpa perlu akun Azure AD / Client ID dulu.

---

## 1. Latar Belakang: Ada Apa dengan Arrow?

Power BI punya dua endpoint untuk menjalankan query DAX lewat REST API:

| | Execute Queries (lama) | Execute DAX Queries (baru) |
|---|---|---|
| Format response | JSON | Apache Arrow (biner) |
| Orientasi data | Per baris (row-oriented) | Per kolom (columnar) |
| Tipe data | Semua jadi string / number generik | Tipe asli: `int64`, `decimal128`, `date64`, dst |
| Ukuran payload | Lebih besar | Lebih kecil (terkompresi) |
| Cocok untuk | Query kecil, integrasi sederhana | Data besar, pipeline analitik |

Analoginya: JSON itu seperti mengirim **daftar surat**, satu baris = satu
"amplop" berisi semua kolom sebagai teks. Arrow itu seperti mengirim
**tabel Excel yang sudah dikelompokkan per kolom**, plus setiap kolom sudah
tahu tipenya sendiri (angka tetap angka, tanggal tetap tanggal), jadi
aplikasi penerima tidak perlu menebak-nebak atau mengonversi ulang.

## 1.5 Contoh Query DAX dan Bentuk Hasilnya

Biar tidak abstrak, mari lihat contoh nyata. Misalkan semantic model kamu
punya tabel `Sales` dengan kolom `Region` dan `Amount`. Query DAX-nya:

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Sales'[Region],
    "Total Amount", SUM('Sales'[Amount])
)
```

Artinya: "kelompokkan tabel Sales per Region, lalu jumlahkan Amount-nya".
Kalau dijalankan, DAX Engine di Power BI menghasilkan sebuah **tabel hasil**
seperti ini (ini yang disebut "rowset" di dokumentasi):

| Region | Total Amount |
|---|---|
| Jakarta | 125000.50 |
| Surabaya | 89000.00 |
| Bandung | 67500.25 |

Tabel di atas itu **konsep**-nya sama di kedua endpoint. Yang beda adalah
**cara mengemasnya jadi bytes untuk dikirim lewat internet**. Di sinilah
Arrow vs JSON mulai berbeda.

### Kalau dikirim sebagai JSON (endpoint lama, `executeQueries`)

Responsnya kurang lebih begini — perhatikan setiap baris jadi objek
berulang, dan nama kolom diulang-ulang di setiap baris:

```json
{
  "results": [
    {
      "tables": [
        {
          "rows": [
            { "Sales[Region]": "Jakarta",  "[Total Amount]": "125000.50" },
            { "Sales[Region]": "Surabaya", "[Total Amount]": "89000.00" },
            { "Sales[Region]": "Bandung",  "[Total Amount]": "67500.25" }
          ]
        }
      ]
    }
  ]
}
```

Masalahnya: `"125000.50"` itu **string**, bukan angka. Aplikasi penerima
harus tahu sendiri untuk mengubahnya jadi `float`/`Decimal` sebelum bisa
dihitung. Kalau kolomnya tanggal, sama saja — jadi string yang harus
di-parse ulang manual.

### Kalau dikirim sebagai Arrow (endpoint baru, `executeDaxQueries`)

Arrow tidak mengirim satu-satu baris sebagai objek. Arrow mengirim dalam
bentuk **kolom**, plus sebuah "kepala surat" (schema) yang bilang di depan:
"kolom pertama namanya Region, tipenya teks; kolom kedua namanya Total
Amount, tipenya decimal dengan 4 angka di belakang koma". Kira-kira begini
konsepnya kalau digambarkan (bukan format bytes sungguhan, tapi biar kebayang):

```
Schema:
  Region       : utf8
  Total Amount : decimal128(19, 4)

Data (per kolom, bukan per baris):
  Region       = ["Jakarta", "Surabaya", "Bandung"]
  Total Amount = [125000.5000, 89000.0000, 67500.2500]
```

Karena tipenya sudah dideklarasikan di depan (di schema), dan nilainya
disimpan per kolom secara biner (bukan teks), aplikasi penerima (lewat
`pyarrow`) bisa langsung tahu `Total Amount` itu angka desimal presisi
tinggi, tanpa perlu menebak atau parsing string. Itulah yang bikin lebih
cepat dan lebih hemat ukuran (karena tidak perlu mengetik ulang nama kolom
di setiap baris, dan angka disimpan biner bukan teks).

### Hubungannya dengan simulasi di `main.py`

Fungsi `build_sample_table()` di `main.py` itu **berpura-pura jadi hasil**
dari query DAX semacam di atas (bedanya cuma kolomnya lebih banyak: Region,
Date, Amount, Quantity, IsActive, dan barisnya 5.000 biar bedanya kelihatan
jelas). Fungsi `to_arrow_ipc_bytes()` dan `to_json_bytes()` lalu
mensimulasikan **cara mengemas tabel itu** persis seperti dua contoh di
atas — itu sebabnya di hasil run kamu, kolom `Amount` dan `Date` dari versi
JSON berubah jadi `str`, sedangkan dari versi Arrow tetap `Decimal` dan
`date` asli.

## 2. Kenapa Ini Penting? (3 Alasan Utama)

### a) Ukuran payload jauh lebih kecil
Arrow menyimpan data per kolom dan dikompresi (LZ4), sedangkan JSON menyimpan
setiap nilai sebagai teks berulang-ulang (termasuk nama kolom yang diulang di
tiap baris). Semakin banyak baris, semakin besar bedanya.

### b) Tipe data presisi, tanpa konversi manual
Di JSON, angka desimal (`Currency`) dan tanggal (`DateTime`) semuanya jadi
**string**. Aplikasi penerima harus parsing ulang manual, dan berisiko salah
format (misal `12345.6789` kepotong presisi kalau di-parse sebagai `float`
biasa). Di Arrow, tipe-tipe ini sudah datang dalam bentuk aslinya:

| Tipe di DAX | Jadi di Arrow | Jadi di JSON |
|---|---|---|
| Integer | `int64` (angka bulat asli) | angka biasa (aman) |
| Currency | `decimal128(19,4)` (presisi 4 desimal, tanpa error pembulatan) | string, misal `"0.0000"` |
| DateTime | `date64` (objek tanggal asli) | string, misal `"2026-01-01"` |
| Boolean | `bool` | boolean biasa (aman) |

### c) "Zero-copy read" — lebih cepat dibaca aplikasi
Karena strukturnya sudah kolom-per-kolom dan tipenya sudah jelas, library
seperti `pyarrow` bisa langsung memetakan data itu ke memori (mis. jadi
`pandas.DataFrame`) tanpa proses parsing + tebak tipe seperti JSON. Ini yang
bikin Arrow lebih cepat dibaca aplikasi klien.

## 3. Hasil Simulasi di Repo Ini

Skrip `main.py` membuat 5.000 baris data contoh (meniru hasil query DAX: kolom
`Region`, `Date`, `Amount`, `Quantity`, `IsActive`), lalu menyimpannya dengan
dua cara (Arrow vs JSON) dan membandingkannya. Berikut hasil aktual dari salah
satu run:

```
=== Ukuran payload ===
Arrow (LZ4 compressed) :     55,824 bytes
JSON                    :    520,033 bytes
Penghematan ukuran      : 89.3%

=== Kecepatan deserialize (rata-rata dari 20x percobaan) ===
Arrow -> pandas.DataFrame : 2.58 ms
JSON  -> pandas.DataFrame : 6.08 ms
Arrow lebih cepat 2.4x

=== Fidelitas tipe data ===
Contoh nilai Amount dari Arrow : Decimal('0.0000')  (tipe: decimal.Decimal)
Contoh nilai Date dari Arrow   : datetime.date(2026, 1, 1)  (tipe: datetime.date)

Contoh nilai Amount dari JSON  : '0.0000'  (tipe: str)
Contoh nilai Date dari JSON    : '2026-01-01'  (tipe: str)
```

**Kesimpulan dari angka di atas:**
- Payload Arrow **89% lebih kecil** dari JSON untuk jumlah baris yang sama.
- Arrow **2–4x lebih cepat** dibaca jadi DataFrame (angkanya bisa sedikit
  beda tiap run, tergantung beban komputer saat itu).
- Arrow mengembalikan `Amount` sebagai `Decimal` dan `Date` sebagai objek
  tanggal asli — siap dipakai langsung. JSON mengembalikan keduanya sebagai
  teks polos yang harus di-parse manual sebelum bisa dihitung/dibandingkan.

> Catatan: angka di atas hanya berlaku untuk simulasi 5.000 baris ini.
> Selisihnya biasanya makin besar seiring jumlah baris/kolom yang makin
> banyak — karena itu dokumentasi resmi Microsoft merekomendasikan Arrow
> khusus untuk **query dengan hasil besar** (ratusan/ribuan baris ke atas),
> bukan untuk query kecil sekali jalan.

## 4. Cara Menjalankan

### Prasyarat
- Python 3.10+
- Install dependency:
  ```bash
  pip install pyarrow pandas
  ```
  (kalau pakai Windows dan `pip` tidak dikenali, pakai `py -m pip install pyarrow pandas`)

### Menjalankan simulasi
```bash
python main.py
```
atau di Windows:
```powershell
py main.py
```

Skrip akan mencetak: ukuran payload, kecepatan deserialize, dan contoh
fidelitas tipe data — seperti contoh output di bagian 3.

## 5. Struktur Repo

```
.
├── main.py       # Skrip simulasi Arrow vs JSON (tidak butuh koneksi/API)
└── README.md     # Dokumen ini
```

## 6. Cara Kerja `main.py` (Ringkas)

1. **`build_sample_table()`** — membuat data contoh dengan tipe kolom yang
   meniru mapping tipe DAX → Arrow resmi dari Microsoft (`int64`,
   `decimal128`, `date64`, `utf8`, `bool`).
2. **`to_arrow_ipc_bytes()`** — menyimpan tabel itu ke format Arrow IPC
   (dengan kompresi LZ4), persis seperti yang dikembalikan endpoint
   `executeDaxQueries` yang asli.
3. **`to_json_bytes()`** — menyimpan data yang sama ke JSON row-oriented,
   meniru bentuk respons endpoint `executeQueries` yang lama (di sinilah
   tanggal & desimal "berubah" jadi string).
4. **`parse_arrow_ipc_bytes()` / `parse_json_bytes()`** — membaca ulang
   kedua format itu, lalu dibandingkan ukuran & kecepatannya.

## 7. Lanjut ke API Asli (Kalau Sudah Punya Akses)

Simulasi ini sengaja dibuat supaya konsepnya bisa dipahami dulu tanpa
kredensial Azure AD. Kalau nanti kamu sudah punya **Client ID** (dari App
Registration di Microsoft Entra ID) dan izin ke semantic model target,
langkah lanjutannya:

1. Ganti isi `build_sample_table()` dengan hasil query DAX asli lewat POST
   request ke:
   ```
   POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/executeDaxQueries
   ```
   (gunakan `msal` untuk login & ambil access token, `requests` untuk
   memanggil API, `pyarrow` untuk parsing response Arrow-nya).
2. Bandingkan hasilnya dengan endpoint lama `executeQueries` (JSON) dengan
   cara yang sama seperti di `main.py`.
3. Bagian `to_json_bytes()`, `parse_arrow_ipc_bytes()`, dan logika
   perbandingan ukuran/kecepatan di `main.py` bisa dipakai ulang tanpa
   perubahan berarti.

## 8. Referensi

- [Execute DAX Queries REST API — Overview](https://learn.microsoft.com/power-bi/developer/execute-dax-queries-arrow/overview)
- [Quickstart: Run your first Execute DAX Queries request](https://learn.microsoft.com/power-bi/developer/execute-dax-queries-arrow/get-started)
- [Best practices for the Execute DAX Queries REST API](https://learn.microsoft.com/power-bi/developer/execute-dax-queries-arrow/best-practices)
- [Apache Arrow — Columnar Format](https://arrow.apache.org/docs/format/Columnar.html)