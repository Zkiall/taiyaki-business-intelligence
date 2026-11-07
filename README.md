# 🐟 Taiyaki Business Manager & Data Science Dashboard

Aplikasi web *end-to-end* berbasis **Python**, **Streamlit**, **SQLite**, **Plotly**, dan **Scikit-learn** untuk mengelola operasional harian usaha produksi dan distribusi konsinyasi (titip jual) roti Taiyaki, sekaligus berfungsi sebagai sistem *Business Intelligence* dan *Demand Forecasting* berbasis Data Science.

---

## 📌 1. Problem Statement (Latar Belakang Masalah)

Usaha produksi roti Taiyaki dengan model distribusi konsinyasi (*titip jual*) kepada puluhan pedagang keliling/lapak (**33 mitra tenongan**) menghadapi sejumlah tantangan operasional dan analitik ketika masih dicatat secara manual menggunakan spreadsheet melebar (*crosstab* Excel):

1. **Distorsi Akuntansi Kas vs Akrual & Retur Barang:** Pencatatan spreadsheet tradisional sering menyamakan barang yang baru dititipkan (`TITIP`) tetapi belum direkap hasilnya sebagai barang sisa/retur (`SISA = TITIP - LAKU`), serta mencampuradukkan pengeluaran belanja bahan baku mentah di gudang dengan Harga Pokok Produksi (HPP) harian.
2. **Kerentanan Human Error pada Spreadsheet:** Dalam audit data historis (`projectapp.xlsx`), ditemukan baris mitra ke-33 (`sukaja`) yang terlewat dari rumus `=SUM(C4:C35)`, sel berisi spasi kosong `' '`, serta transaksi rapel (`Laku > Titip`) yang tidak memiliki penanda audit.
3. **Ketidakseimbangan Alokasi Kuota (*Stockout vs Overproduction*):** Sebanyak **56,62%** transaksi harian mengalami kondisi habis terjual 100% (*censored demand / lost sales*), sementara sejumlah mitra tenongan tertentu secara konsisten mencatat tingkat retur di atas **30%**.

---

## 🎯 2. Tujuan Bisnis & Solusi Aplikasi

1. **Digitalisasi Operasional Harian yang Cepat & Valid:** Menyediakan antarmuka input ganda (**Mode Tabel Massal Cepat** & **Mode Input Satuan**) dengan validasi anti-duplikat, pemisahan status `BELUM_KONFIRMASI` vs `TERKONFIRMASI`, pelacakan piutang setoran, dan pencatatan **Audit Log** otomatis.
2. **Pemisahan Akuntansi Kas vs Akrual & Kalkulator HPP Batch:** Memisahkan laporan arus kas masuk/keluar (*Cash Basis*) dari laporan laba rugi berbasis akrual sederhana, dilengkapi kalkulator resep standar **120 pcs/batch** (HPP **Rp999,22/pcs**) dan analisis titik impas (*Break-Even Point*).
3. **Optimasi Distribusi & Peramalan Permintaan (*Data Science*):** Menganalisis elastisitas penjualan setiap tenongan, memprediksi permintaan harian beberapa hari ke depan tanpa *data leakage*, dan merekomendasikan realokasi kuota titipan untuk meminimalkan retur sekaligus memaksimalkan *sell-through rate*.

---

## 🛠️ 3. Tech Stack & Arsitektur Modul

* **Bahasa & Framework Web:** Python 3.10+, Streamlit (Multipage Navigation API)
* **Database Persisten:** SQLite 3 (`WAL Mode` + `Foreign Key Constraints`)
* **Pengolahan Data & Statistik:** Pandas, NumPy, OpenPyXL
* **Visualisasi Interaktif:** Plotly Express & Plotly Graph Objects
* **Machine Learning & Forecasting:** Scikit-learn (`Ridge`, `RandomForestRegressor`, `mean_absolute_error`, `mean_squared_error`)
* **Pengujian Kualitas Kode:** `unittest` & `pytest`

### Struktur Folder Proyek
```text
taiyaki_manager/
├── app.py                          # Entry point utama, sidebar status real-time & pemilih Mode Produksi/Demo
├── database.py                     # Skema 11 tabel relasional SQLite, koneksi WAL, seeding 33 tenongan & 18 bahan
├── calculations.py                 # Pure functions: Omzet, HPP, Laba Rugi Akrual/Kas, Piutang, BEP, STR
├── data_processing.py              # CRUD transaksi, parser khusus projectapp.xlsx, ekspor multi-sheet Excel/CSV
├── analytics.py                    # Engine EDA, evaluasi kuadran & tren tenongan, sensitivitas bahan, auto-insight
├── forecasting.py                  # Walk-Forward Time-Series Forecasting (SMA, EWMA, Ridge, RF) & Rekomendasi Kuota
├── components.py                   # Komponen UI reusable: KPI Cards semantik, filter tanggal, isolasi DB Demo
├── utils.py                        # Formatter Rupiah (Rp2.000), Tanggal Indonesia, konversi formula Excel & validator
├── pages/
│   ├── 1_Dashboard_Utama.py        # Dashboard Eksekutif (11 KPI, Delta %, Alert Operasional, 4 Grafik Utama)
│   ├── 2_Data_Tenongan.py          # Manajemen dinamis mitra tenongan, riwayat per mitra, impor Excel/CSV
│   ├── 3_Input_Harian.py           # Input harian (Tabel Massal & Form Satuan), koreksi, hapus & Audit Log
│   ├── 4_Kulakan.py                # Modul transaksi grosir/kulakan terpisah dari konsinyasi & belanja bahan
│   ├── 5_Belanja_Bahan_Baku.py     # Pengadaan bahan baku, riwayat perubahan harga, mutasi stok & alert restock
│   ├── 6_Kalkulasi_Modal_HPP.py    # Kalkulator HPP batch 120 pcs, simulasi harga bahan/jual, & kurva BEP
│   ├── 7_Laporan_Keuangan.py       # Laporan Laba Rugi (Akrual) vs Arus Kas (Kas), rekap periode & biaya ops
│   ├── 8_Analisis_Data_Science.py  # Portofolio DS: EDA, Kuadran Tenongan, Backtesting Forecasting & Alokasi Kuota
│   └── 9_Pengaturan_Data.py        # Parameter bisnis, editor bahan, full export/import, backup DB & generator Demo
├── data/
│   ├── taiyaki_business.db         # Database produksi SQLite (terbentuk otomatis)
│   ├── demo_synthetic.db           # Database khusus Mode Demo Portofolio (terpisah 100% dari data produksi)
│   └── backups/                    # Direktori penyimpanan snapshot backup database (.db)
├── tests/
│   └── test_taiyaki_suite.py       # 16 unit test komprehensif (rumus bisnis, DB, edge-cases, & forecasting)
├── requirements.txt
└── README.md