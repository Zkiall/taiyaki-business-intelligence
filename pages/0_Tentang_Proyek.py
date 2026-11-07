"""
pages/0_Tentang_Proyek.py
Halaman Dokumentasi Portofolio Data Science & Business Intelligence (About Project).
Dirancang khusus agar rekruter, klien, atau pengunjung publik dapat langsung memahami
konteks bisnis, arsitektur teknis, metodologi Data Science, evaluasi model, dan insight
tanpa mengekspos data transaksi asli pemilik usaha.
"""

import streamlit as st

from components import get_active_db_path, render_kpi_card, tampilkan_header_halaman
from synthetic_generator import get_metadata_dataset_demo
from utils import format_angka, format_tanggal_id


def render_halaman_tentang_proyek() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Tentang Proyek: Taiyaki Business Manager & Data Science Portfolio",
        subjudul="Sistem ERP Mikro Konsinyasi & Analitik Prediktif End-to-End Berbasis Python, Streamlit, SQLite, dan Scikit-Learn.",
        ikon="📖",
    )

    st.warning(
        "📢 **Pemberitahuan Privasi & Data Sintetis (`DEMO PORTOFOLIO — DATA SINTETIS`):**\n\n"
        "Seluruh angka transaksi, omzet, piutang, belanja bahan baku, dan grafik yang ditampilkan pada **Mode Demo Publik** "
        "ini dibangkitkan menggunakan *Synthetic Data Generator* berbasis distribusi statistik yang realistis. "
        "Data operasional asli milik usaha disimpan di dalam database produksi terpisah yang dilindungi autentikasi kriptografis."
    )

    meta_demo = get_metadata_dataset_demo()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            "Cakupan Dataset Demo",
            f"{meta_demo['periode_bulan']} Bulan",
            f"{format_tanggal_id(meta_demo['tgl_mulai'], singkat=True)} – {format_tanggal_id(meta_demo['tgl_akhir'], singkat=True)}",
            kategori_warna="pendapatan",
        )
    with c2:
        render_kpi_card(
            "Volume Transaksi Sintetis",
            f"{format_angka(meta_demo['total_transaksi'])} Baris",
            f"Dari {meta_demo['total_hari_aktif']} hari operasional aktif",
            kategori_warna="keuntungan",
        )
    with c3:
        render_kpi_card(
            "Mitra Distribusi Konsinyasi",
            "33 Tenongan",
            "Terbagi dalam Tier A, B, dan C",
            kategori_warna="netral",
        )
    with c4:
        render_kpi_card(
            "Arsitektur Keamanan",
            "Dual-DB Isolation",
            "SQLite Read-Only Authorizer + Scrypt/Bcrypt",
            kategori_warna="piutang",
        )

    tab_bisnis, tab_metodologi, tab_insight, tab_arsitektur = st.tabs(
        [
            "🎯 1. Konteks Bisnis & Problem Statement",
            "🧠 2. Metodologi Data Science & Forecasting",
            "💡 3. Business Insights & Keterbatasan Model",
            "🏗️ 4. Tech Stack & Arsitektur Keamanan",
        ]
    )

    with tab_bisnis:
        st.markdown(
            """
            ### Latar Belakang Bisnis
            Usaha produksi roti **Taiyaki** ini beroperasi dengan model manufaktur harian mandiri dan distribusi **konsinyasi (*titip jual*)** kepada puluhan pedagang keliling/lapak (**33 mitra tenongan**) dengan harga setoran dasar **Rp2.000 per buah**.

            ### Problem Statement (Masalah Utama)
            1. **Ketidakakuratan Pencatatan Spreadsheet Manual:** Pencatatan *crosstab* melebar di Excel rentan terhadap human error (seperti baris mitra yang terlewat dari fungsi `SUM`, pencampuran transaksi yang belum direkap dengan barang retur, serta pencampuran pengeluaran kas belanja bahan mentah dengan beban HPP harian).
            2. **Dilema Alokasi Kuota (*Stockout vs Spoilage*):** Sebagai produk roti basah tanpa pengawet, kelebihan titipan (*overproduction*) di tenongan berdaya serap rendah akan menjadi retur yang membebani HPP (*spoilage cost*). Sebaliknya, kekurangan titipan (*under-allocation*) pada tenongan ramai menyebabkan kehilangan potensi pendapatan (*censored demand / lost sales*).
            3. **Kebutuhan Sistem Dual-Purpose:** Pemilik usaha membutuhkan aplikasi yang benar-benar andal untuk operasional kasir/konsinyasi sehari-hari, namun sekaligus dapat dipublikasikan secara aman sebagai portofolio Data Science profesional tanpa membocorkan rahasia keuangan perusahaan.

            ### Tujuan Aplikasi
            - Mengotomatiskan pencatatan harian (*Titip, Laku, Retur, Setoran, Piutang*), pengadaan bahan baku, mutasi inventori, dan laporan keuangan berbasis **Akrual vs Kas**.
            - Menyediakan pipeline **Exploratory Data Analysis (EDA)** otomatis dan evaluasi matriks performa mitra tenongan.
            - Mengimplementasikan **Time-Series Demand Forecasting** bebas *data leakage* dan **Sistem Rekomendasi Alokasi Kuota** berbasis histori.
            """
        )

    with tab_metodologi:
        st.markdown(
            """
            ### 1. Proses ETL & Penjaminan Kualitas Data
            - **Normalisasi Skema Relasional:** Mengubah struktur *wide crosstab* menjadi 11 tabel relasional ternormalisasi di SQLite dengan *Foreign Key Constraints* dan *Unique Composite Index* `(id_tenongan, tanggal)`.
            - **Pemisahan Status Operasional:** Membedakan secara tegas transaksi berstatus `BELUM_KONFIRMASI` (*in-transit consignment*) dari `TERKONFIRMASI`, sehingga perhitungan *Sell-Through Rate* dan *Return Rate* tidak terdistorsi oleh barang yang baru dikirim pagi hari.

            ### 2. Exploratory Data Analysis (EDA) & Segmentasi Mitra
            - **Metrik Efisiensi Distribusi:**
              $$\\text{Sell-Through Rate (STR)} = \\left(\\frac{\\sum \\text{Qty Laku}}{\\sum \\text{Qty Titip Terkonfirmasi}}\\right) \\times 100\\%$$
              $$\\text{Return Rate (RR)} = \\left(\\frac{\\sum \\text{Qty Retur}}{\\sum \\text{Qty Titip Terkonfirmasi}}\\right) \\times 100\\%$$
            - **Deteksi Tren Performa Mitra:** Membandingkan rata-rata penjualan paruh awal vs paruh akhir periode serta menghitung kemiringan regresi linier (*linear slope*) pada setiap tenongan untuk mengklasifikasikan mitra ke dalam segmen **MENINGKAT**, **STABIL**, **MENURUN**, atau **DATA_TERBATAS**.

            ### 3. Time-Series Forecasting & Walk-Forward Validation
            - **Pencegahan Data Leakage:** Evaluasi model dilakukan menggunakan skema **Walk-Forward Expanding Window** pada himpunan uji kronologis terakhir. Pada setiap langkah waktu uji $t$, pembuatan fitur (*autoregressive lags* $t-1, t-2, t-3, t-6$, *rolling mean*, *rolling standard deviation*, dan *day-of-week*) serta pelatihan model hanya menggunakan data $[1, t-1]$.
            - **Kandidat Model yang Dievaluasi:**
              1. *Simple Moving Average 3 Hari (`SMA-3`)* — Baseline responsif jangka pendek.
              2. *Simple Moving Average 6 Hari (`SMA-6`)* — Baseline 1 siklus kerja mingguan (Senin–Sabtu).
              3. *Exponentially Weighted Moving Average (`EWMA` $\\alpha=0,35$)* — Pemulusan eksponensial.
              4. *Ridge Regression (L2 Regularized Linear Model)* — Model linier multivariat pada fitur *lag* & kalender.
              5. *Random Forest Regressor* — Model *ensemble non-linear* dengan pembatasan kedalaman pohon (`max_depth=4`, `min_samples_leaf=2`) untuk menekan *overfitting*.
            - **Metrik Evaluasi:** *Mean Absolute Error* (**MAE** dalam satuan pcs roti), *Root Mean Squared Error* (**RMSE**), dan *Mean Absolute Percentage Error* (**MAPE**).
            """
        )

    with tab_insight:
        st.markdown(
            """
            ### Business Insights Utama
            1. **Realokasi Kuota Berbasis Bukti (*Zero-Extra-Cost Optimization*):** Dengan menggeser 1–2 pcs kuota harian dari tenongan ber-retur tinggi (*RR > 30%*) ke tenongan *Tier A* yang sering habis terjual (*Sold-Out Rate > 65%*), usaha dapat meningkatkan volume roti terjual dan margin laba bersih tanpa perlu menambah biaya produksi adonan.
            2. **Pola Musiman Mingguan (*Day-of-Week Effect*):** Permintaan mencapai puncaknya menjelang akhir pekan (**Kamis, Jumat, dan Sabtu**), sementara hari **Senin** cenderung memiliki serapan lebih rendah. Kapasitas batch produksi harian dapat disesuaikan mengikuti indeks hari ini.
            3. **Sensitivitas Komponen HPP:** Komponen **Telur Ayam**, **Bahan Isian**, dan **Tepung Terigu** menyumbang porsi terbesar terhadap HPP per batch (120 pcs). Kenaikan harga pada ketiga bahan ini paling sensitif terhadap penurunan margin kotor per biji.

            ### Keterbatasan Model (*Model Limitations*)
            - **Fenomena *Censored Demand*:** Variabel target yang diamati adalah volume *terjual* (`qty_laku`), yang dibatasi dari atas oleh kuota *titipan* (`qty_titip`). Ketika suatu tenongan habis terjual 100%, permintaan konsumen riil yang tidak terlayani tidak teramati secara langsung.
            - **Variabel Eksogen Eksternal:** Curah hujan harian, libur akademik sekolah/kampus, dan acara keramaian insidental belum dimasukkan sebagai fitur prediktor eksternal.
            """
        )

    with tab_arsitektur:
        st.markdown(
            """
            ### Tech Stack & Arsitektur Deployment VPS
            - **Frontend & Web App Framework:** Python, Streamlit (Multipage Navigation API)
            - **Database Engine:** SQLite 3 dengan mode *Write-Ahead Logging* (`WAL`), `PRAGMA foreign_keys = ON`, dan pemisahan fisik `data/production/taiyaki_production.db` vs `data/demo/taiyaki_demo.db`.
            - **Keamanan & Isolasi Data:**
              - Hashing password menggunakan `bcrypt` / *memory-hard* `hashlib.scrypt` dengan verifikasi *constant-time* (`hmac.compare_digest`).
              - Pembatasan percobaan login (*rate-limiting lockout*) dan kedaluwarsa sesi otomatis (*session timeout*).
              - Otorisasi level C-engine SQLite (`conn.set_authorizer`) yang mengunci database demo menjadi **Read-Only** bagi pengunjung publik.
            - **Infrastruktur Server Produksi:** Ubuntu Linux VPS, `systemd` daemon service, dan **Nginx Reverse Proxy**.
            """
        )


render_halaman_tentang_proyek()