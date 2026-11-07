"""
pages/8_Analisis_Data_Science.py
Halaman H. Analisis Data Science (Modul Unggulan Portofolio).
Mencakup 5 pilar analitik & machine learning:
1. Exploratory Data Analysis (EDA): distribusi penjualan harian, tren omzet/laku,
   pola hari dalam minggu, distribusi retur, korelasi titip vs laku, dan sensitivitas harga bahan.
2. Evaluasi Performa & Kuadran Tenongan: peringkat, Sell-Through Rate (STR), Return Rate,
   deteksi tren meningkat/menurun, dan analisis Pareto.
3. Demand Forecasting: perbandingan Moving Average (SMA-3, SMA-6), Exponential Smoothing (EWMA),
   Ridge Regression, dan Random Forest dengan Walk-Forward Validation bebas data leakage (MAE, RMSE, MAPE).
4. Rekomendasi Distribusi Interaktif: saran kuota titipan per tenongan yang dapat diedit
   dan diterapkan ke kuota default tenongan atau jadwal input harian.
5. Generator Insight Eksekutif Otomatis berbasis bukti data aktual.
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from analytics import (
    analisis_hubungan_titip_laku,
    analisis_pola_hari_minggu,
    analisis_sensitivitas_bahan_baku,
    evaluasi_performa_dan_tren_tenongan,
    generate_insight_otomatis,
    get_dataset_eda_harian,
    hitung_statistik_deskriptif_eda,
)
from components import (
    get_active_db_path,
    pilih_rentang_tanggal,
    render_kpi_card,
    tampilkan_header_halaman,
)
from data_processing import (
    df_ke_csv_bytes,
    dfs_ke_excel_bytes,
    simpan_batch_transaksi_harian,
)
from database import get_connection, query_df
from forecasting import (
    hitung_rekomendasi_alokasi_tenongan,
    jalankan_evaluasi_dan_forecasting,
)
from utils import format_angka, format_persen, format_rupiah, format_tanggal_id


def render_halaman_data_science() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Analisis Data Science, Forecasting & Optimasi Distribusi",
        subjudul="Exploratory Data Analysis (EDA), segmentasi performa tenongan, prediksi permintaan bebas data leakage, dan rekomendasi kuota titipan.",
        ikon="🧠",
    )

    bounds = query_df(
        "SELECT MIN(tanggal) AS tmin, MAX(tanggal) AS tmax, COUNT(*) AS cnt FROM transaksi_harian WHERE status_konfirmasi = 'TERKONFIRMASI';",
        db_path=db_path,
    )
    jml_konf = int(bounds.iloc[0]["cnt"]) if not bounds.empty else 0

    if jml_konf == 0:
        st.warning(
            "📊 **Data Historis Belum Tersedia:** Halaman Analisis Data Science membutuhkan data transaksi "
            "yang sudah terkonfirmasi. Silakan impor `projectapp.xlsx` pada menu **📝 Input Harian** "
            "atau beralih ke **🧪 Mode Demo Portofolio** melalui panel di Sidebar kiri."
        )
        return

    tmin = str(bounds.iloc[0]["tmin"])
    tmax = str(bounds.iloc[0]["tmax"])
    tgl_mulai, tgl_selesai = pilih_rentang_tanggal(tmin, tmax, key_prefix="ds_page")

    df_daily = get_dataset_eda_harian(tgl_mulai, tgl_selesai, hanya_terkonfirmasi=True, db_path=db_path)
    df_perf = evaluasi_performa_dan_tren_tenongan(tgl_mulai, tgl_selesai, db_path=db_path)
    kor_info = analisis_hubungan_titip_laku(tgl_mulai, tgl_selesai, db_path=db_path)

    # Ringkasan KPI Analitik di Bagian Atas
    rata2_laku = float(df_daily["total_laku"].mean()) if not df_daily.empty else 0.0
    std_laku = float(df_daily["total_laku"].std()) if len(df_daily) > 1 else 0.0
    str_keseluruhan = (
        float(df_daily["total_laku"].sum() / df_daily["total_titip"].sum() * 100.0)
        if (not df_daily.empty and df_daily["total_titip"].sum() > 0)
        else 0.0
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            "Rata-rata Permintaan Harian",
            f"{format_angka(rata2_laku, 2)} pcs/hari",
            f"Simpangan Baku (σ): ±{format_angka(std_laku, 2)} pcs ({len(df_daily)} hari valid)",
            kategori_warna="pendapatan",
        )
    with c2:
        render_kpi_card(
            "Efisiensi Sell-Through Valid",
            format_persen(str_keseluruhan, 2),
            f"Dari {format_angka(kor_info['jumlah_data'])} transaksi terkonfirmasi",
            kategori_warna="keuntungan" if str_keseluruhan >= 80 else "piutang",
        )
    with c3:
        render_kpi_card(
            "Tingkat Habis Terjual (Stockout)",
            format_persen(kor_info.get("persen_habis_terjual", 0.0), 1),
            f"{format_angka(kor_info.get('jml_habis_terjual', 0))} kali tenongan laku 100%",
            kategori_warna="piutang",
        )
    with c4:
        render_kpi_card(
            "Korelasi Titip vs Laku (r)",
            format_angka(kor_info.get("korelasi_pearson", 0.0), 3),
            f"Spearman ρ = {format_angka(kor_info.get('korelasi_spearman', 0.0), 3)} (Per Transaksi)",
            kategori_warna="netral",
        )

    tab_insight, tab_eda, tab_tenongan, tab_forecast, tab_rekomendasi = st.tabs(
        [
            "💡 1. Insight Otomatis",
            "📊 2. Exploratory Data Analysis (EDA)",
            "🏆 3. Analisis Performa & Tren Tenongan",
            "🔮 4. Demand Forecasting & Evaluasi Model",
            "🎯 5. Rekomendasi Alokasi Distribusi",
        ]
    )

    # ==========================================================================
    # TAB 1: INSIGHT EKSEKUTIF OTOMATIS BERBASIS BUKTI DATA
    # ==========================================================================
    with tab_insight:
        st.markdown("#### 💡 Ringkasan Insight Otomatis Berbasis Bukti Data")
        st.caption(
            "Seluruh temuan di bawah dihasilkan secara algoritmis dari pengujian statistik atas data aktual di SQLite "
            "(tanpa asumsi atau klaim yang tidak didukung data)."
        )
        daftar_insight = generate_insight_otomatis(tgl_mulai, tgl_selesai, db_path=db_path)
        for item in daftar_insight:
            kat = item.get("kategori", "info")
            if kat == "positif":
                st.success(f"**{item['judul']}**\n\n{item['isi']}")
            elif kat == "peringatan":
                st.warning(f"**{item['judul']}**\n\n{item['isi']}")
            else:
                st.info(f"**{item['judul']}**\n\n{item['isi']}")

        st.markdown("##### 📋 Statistik Deskriptif Harian (Count, Mean, Median, Min, Max, Std, Sum)")
        df_stat_eda = hitung_statistik_deskriptif_eda(df_daily)
        if not df_stat_eda.empty:
            st.dataframe(df_stat_eda, use_container_width=True, hide_index=True)

    # ==========================================================================
    # TAB 2: EXPLORATORY DATA ANALYSIS (EDA) MENDALAM
    # ==========================================================================
    with tab_eda:
        st.markdown("#### 📊 Exploratory Data Analysis (EDA) Operasional & Biaya")

        # Baris 1: Distribusi Penjualan Harian & Pola Hari dalam Minggu
        col_e1, col_e2 = st.columns(2)
        with col_e1:
            df_daily_aktif = df_daily[df_daily["total_titip"] > 0]
            fig_dist = px.histogram(
                df_daily_aktif,
                x="total_laku",
                nbins=15,
                marginal="box",
                color_discrete_sequence=["#2563eb"],
                title="Distribusi Penjualan Harian (Roti Laku per Hari Aktif)",
                labels={"total_laku": "Jumlah Roti Terjual per Hari (pcs)"},
            )
            fig_dist.update_layout(height=370, margin=dict(l=20, r=20, t=45, b=20))
            st.plotly_chart(fig_dist, use_container_width=True)

        with col_e2:
            df_dow = analisis_pola_hari_minggu(df_daily)
            if not df_dow.empty:
                fig_dow = go.Figure()
                fig_dow.add_trace(
                    go.Bar(
                        x=df_dow["nama_hari"],
                        y=df_dow["rata2_titip"],
                        name="Rata-rata Dititipkan (pcs)",
                        marker_color="#93c5fd",
                    )
                )
                fig_dow.add_trace(
                    go.Bar(
                        x=df_dow["nama_hari"],
                        y=df_dow["rata2_laku"],
                        name="Rata-rata Terjual (pcs)",
                        marker_color="#1d4ed8",
                        error_y=dict(type="data", array=df_dow["std_laku"], visible=True),
                    )
                )
                fig_dow.update_layout(
                    title="Pola Penjualan Menurut Hari dalam Minggu (±1 Simpangan Baku)",
                    barmode="group",
                    xaxis_title="Hari",
                    yaxis_title="Rata-rata Roti (pcs/hari)",
                    height=370,
                    margin=dict(l=20, r=20, t=45, b=20),
                )
                st.plotly_chart(fig_dow, use_container_width=True)

        # Baris 2: Distribusi Retur & Hubungan Titip vs Laku
        col_e3, col_e4 = st.columns(2)
        with col_e3:
            fig_ret = px.histogram(
                df_daily_aktif,
                x="total_retur",
                nbins=12,
                marginal="box",
                color_discrete_sequence=["#ef4444"],
                title="Distribusi Retur / Sisa Harian (pcs/hari)",
                labels={"total_retur": "Total Roti Retur per Hari (pcs)"},
            )
            fig_ret.update_layout(height=360, margin=dict(l=20, r=20, t=45, b=20))
            st.plotly_chart(fig_ret, use_container_width=True)

        with col_e4:
            df_kor = kor_info.get("df", pd.DataFrame())
            if not df_kor.empty:
                rekap_pasangan = (
                    df_kor.groupby(["qty_titip", "qty_laku"], as_index=False)
                    .agg(frekuensi=("id_transaksi", "count"))
                )
                fig_kor = px.scatter(
                    rekap_pasangan,
                    x="qty_titip",
                    y="qty_laku",
                    size="frekuensi",
                    color="frekuensi",
                    color_continuous_scale="Blues",
                    title=f"Hubungan Jumlah Titip vs Laku per Transaksi (r = {kor_info['korelasi_pearson']:.3f})",
                    labels={
                        "qty_titip": "Jumlah Dititipkan per Tenongan (pcs)",
                        "qty_laku": "Jumlah Terjual / Laku (pcs)",
                        "frekuensi": "Jumlah Kejadian",
                    },
                )
                max_q = max(int(rekap_pasangan["qty_titip"].max()), 5)
                fig_kor.add_trace(
                    go.Scatter(
                        x=[0, max_q],
                        y=[0, max_q],
                        mode="lines",
                        name="Batas Habis Terjual 100% (Laku = Titip)",
                        line=dict(color="#16a34a", dash="dash"),
                    )
                )
                fig_kor.update_layout(height=360, margin=dict(l=20, r=20, t=45, b=20))
                st.plotly_chart(fig_kor, use_container_width=True)

        # Baris 3: Analisis Sensitivitas Harga & Biaya Bahan Baku terhadap Margin
        st.markdown("##### 🧪 Analisis Sensitivitas Biaya Bahan Baku terhadap HPP & Margin")
        df_sens = analisis_sensitivitas_bahan_baku(db_path=db_path)
        if not df_sens.empty:
            st.dataframe(df_sens, use_container_width=True, hide_index=True)

    # ==========================================================================
    # TAB 3: ANALISIS PERFORMA & DETEKSI TREN TENONGAN
    # ==========================================================================
    with tab_tenongan:
        st.markdown("#### 🏆 Evaluasi Peringkat, Kuadran Sell-Through Rate & Deteksi Tren Tenongan")
        if df_perf.empty:
            st.info("Belum ada data performa tenongan terkonfirmasi.")
        else:
            df_perf_valid = df_perf[df_perf["hari_valid"] >= 3].copy()
            if not df_perf_valid.empty:
                fig_quad = px.scatter(
                    df_perf_valid,
                    x="rata2_laku_harian",
                    y="sell_through_rate",
                    size="total_laku",
                    color="status_tren",
                    hover_name="nama_tenongan",
                    text="nama_tenongan",
                    color_discrete_map={
                        "MENINGKAT": "#16a34a",
                        "STABIL": "#2563eb",
                        "MENURUN": "#dc2626",
                        "DATA_TERBATAS": "#94a3b8",
                    },
                    title="Peta Kuadran Performa Tenongan: Rata-rata Laku Harian vs Sell-Through Rate (%)",
                    labels={
                        "rata2_laku_harian": "Rata-rata Terjual per Hari Aktif (pcs)",
                        "sell_through_rate": "Sell-Through Rate (%)",
                        "status_tren": "Status Tren",
                        "total_laku": "Total Laku (pcs)",
                    },
                )
                fig_quad.update_traces(textposition="top center")
                fig_quad.add_hline(y=80.0, line_dash="dash", line_color="#64748b", annotation_text="Target STR 80%")
                fig_quad.update_layout(height=440, margin=dict(l=20, r=20, t=45, b=20))
                st.plotly_chart(fig_quad, use_container_width=True)

            st.dataframe(
                df_perf[
                    [
                        "peringkat",
                        "kode_tenongan",
                        "nama_tenongan",
                        "pola_titip",
                        "hari_valid",
                        "total_titip",
                        "total_laku",
                        "total_retur",
                        "rata2_laku_harian",
                        "sell_through_rate",
                        "return_rate",
                        "sold_out_rate",
                        "perubahan_paruh_persen",
                        "status_tren",
                    ]
                ].rename(
                    columns={
                        "peringkat": "Rank",
                        "kode_tenongan": "Kode",
                        "nama_tenongan": "Nama Tenongan",
                        "pola_titip": "Pola",
                        "hari_valid": "Hari Valid",
                        "total_titip": "Total Titip",
                        "total_laku": "Total Laku",
                        "total_retur": "Total Retur",
                        "rata2_laku_harian": "Rata2 Laku/Hari",
                        "sell_through_rate": "Sell-Through (%)",
                        "return_rate": "Return Rate (%)",
                        "sold_out_rate": "Habis Terjual (%)",
                        "perubahan_paruh_persen": "Delta Paruh (%)",
                        "status_tren": "Tren Performa",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    # ==========================================================================
    # TAB 4: DEMAND FORECASTING & EVALUASI WALK-FORWARD BACKTESTING
    # ==========================================================================
    with tab_forecast:
        st.markdown("#### 🔮 Prediksi Permintaan (Demand Forecasting) & Evaluasi Model")
        cf1, cf2, cf3 = st.columns(3)
        with cf1:
            horizon_in = st.slider("Horizon Prediksi ke Depan (Hari Kalender)", min_value=3, max_value=14, value=7)
        with cf2:
            buffer_in = st.slider(
                "Buffer Cadangan Produksi di Atas Prediksi Laku (%)",
                min_value=0,
                max_value=30,
                value=12,
                help="Tambahan stok cadangan untuk mengakomodasi variasi permintaan antar-tenongan.",
            )
        with cf3:
            rasio_uji_in = st.slider("Proporsi Data Uji Backtesting (%)", min_value=15, max_value=35, value=25, step=5)

        hasil_fc = jalankan_evaluasi_dan_forecasting(
            horizon_hari=int(horizon_in),
            rasio_test=float(rasio_uji_in) / 100.0,
            margin_cadangan_persen=float(buffer_in),
            tgl_mulai=tgl_mulai,
            tgl_selesai=tgl_selesai,
            db_path=db_path,
        )

        if hasil_fc["status"] != "OK":
            st.warning(hasil_fc["pesan"])
        else:
            st.success(
                f"✅ **Model Terbaik Hasil Evaluasi Out-of-Sample ({hasil_fc['n_test']} Hari Uji):** "
                f"**{hasil_fc['model_terbaik_nama']}** dengan **MAE = {format_angka(hasil_fc['mae_terbaik'], 2)} pcs/hari**."
            )

            col_m1, col_m2 = st.columns([1.25, 1.75])
            with col_m1:
                st.markdown("##### 📏 Tabel Evaluasi Metrik (Bebas Data Leakage)")
                st.dataframe(
                    hasil_fc["df_metrik"][["Nama Model", "MAE (pcs)", "RMSE (pcs)", "MAPE (%)"]],
                    use_container_width=True,
                    hide_index=True,
                )
                st.caption(
                    "**Metodologi Validasi:** Menggunakan *Walk-Forward Expanding Window* di mana pada setiap titik uji $t$, "
                    "model hanya dilatih pada data historis $[1, t-1]$ tanpa mengintip masa depan."
                )

            with col_m2:
                df_bt = hasil_fc["df_backtest"]
                fig_bt = go.Figure()
                fig_bt.add_trace(
                    go.Scatter(
                        x=df_bt["tanggal"],
                        y=df_bt["Aktual Laku (pcs)"],
                        mode="lines+markers",
                        name="Nilai Aktual Laku (pcs)",
                        line=dict(color="#0f172a", width=3),
                    )
                )
                for kol_m, warna_m in [
                    ("SMA-6", "#2563eb"),
                    ("EWMA", "#16a34a"),
                    ("Random Forest", "#d97706"),
                    ("Ridge Regression", "#9333ea"),
                ]:
                    if kol_m in df_bt.columns:
                        fig_bt.add_trace(
                            go.Scatter(
                                x=df_bt["tanggal"],
                                y=df_bt[kol_m],
                                mode="lines+markers",
                                name=f"Prediksi {kol_m}",
                                line=dict(color=warna_m, width=2, dash="dash"),
                            )
                        )
                fig_bt.update_layout(
                    title="Perbandingan Hasil Prediksi vs Nilai Aktual pada Data Uji (Out-of-Sample)",
                    xaxis_title="Tanggal Uji",
                    yaxis_title="Roti Terjual (pcs)",
                    height=340,
                    margin=dict(l=20, r=20, t=45, b=20),
                )
                st.plotly_chart(fig_bt, use_container_width=True)

            st.markdown(f"##### 📅 Proyeksi Permintaan {horizon_in} Hari ke Depan")
            df_fc_out = hasil_fc["df_forecast"]
            st.dataframe(df_fc_out, use_container_width=True, hide_index=True)

            with st.expander("ℹ️ Penjelasan Metodologi & Keterbatasan Model Forecasting (Dokumentasi Portofolio)"):
                st.markdown(
                    """
                    1. **Hierarki Model (Baseline vs Machine Learning):**
                       - Sesuai prinsip *parsimony* dalam peramalan deret waktu, model *Simple Moving Average* (`SMA-6` yang merepresentasikan 1 minggu kerja 6 hari aktif) dan *Exponential Smoothing* (`EWMA`) dijadikan *baseline* utama.
                       - Model *Ridge Regression* dan *Random Forest Regressor* menggunakan fitur *autoregressive lags* ($t-1, t-2, t-3, t-6$), statistik bergulir (*rolling mean & std*), serta fitur kalender (*day-of-week*).
                    2. **Keterbatasan Model (*Model Limitations*):**
                       - **Censored Demand (Permintaan Terbatas Kuota):** Data historis mencatat jumlah roti *terjual* (`qty_laku`), yang dibatasi oleh jumlah roti yang *dititipkan* (`qty_titip`). Ketika tenongan habis terjual 100%, permintaan riil konsumen mungkin lebih tinggi daripada angka yang tercatat.
                       - **Faktor Eksternal Tidak Tercatat:** Cuaca (hujan deras), hari libur sekolah/tanggal merah nasional, atau acara lokal belum menjadi variabel input di database, sehingga dapat menimbulkan deviasi pada hari-hari tertentu.
                       - **Ukuran Sampel Historis:** Model *ensemble* seperti *Random Forest* membutuhkan minimal >90 hari observasi agar mengungguli *Moving Average* secara konsisten.
                    """
                )

    # ==========================================================================
    # TAB 5: REKOMENDASI ALOKASI DISTRIBUSI PER TENONGAN (DAPAT DIEDIT)
    # ==========================================================================
    with tab_rekomendasi:
        st.markdown("#### 🎯 Rekomendasi Alokasi Kuota Titipan per Tenongan")
        st.caption(
            "Rekomendasi di bawah dihitung berdasarkan histori penjualan valid, tingkat retur, frekuensi habis terjual, "
            "dan batas kapasitas produksi. Anda dapat **mengedit langsung kolom `Saran Kuota Baru (pcs)`** sebelum menyimpannya."
        )

        cr1, cr2, cr3 = st.columns(3)
        with cr1:
            tgl_target_rek = st.date_input("Tanggal Rencana Distribusi", value=date.today() + timedelta(days=1))
        with cr2:
            kap_maks = st.number_input("Batas Kapasitas Produksi Harian (pcs)", min_value=20, max_value=1000, value=120, step=10)
        with cr3:
            min_hist_in = st.number_input("Minimum Hari Histori Valid", min_value=3, max_value=20, value=5, step=1)

        df_rek = hitung_rekomendasi_alokasi_tenongan(
            target_tanggal=tgl_target_rek,
            kapasitas_produksi_maks=int(kap_maks),
            min_hari_histori=int(min_hist_in),
            db_path=db_path,
        )

        if df_rek.empty:
            st.info("Tidak ada tenongan aktif untuk diberikan rekomendasi.")
        else:
            edited_rek = st.data_editor(
                df_rek,
                disabled=[
                    "id_tenongan",
                    "Kode",
                    "Nama Tenongan",
                    "Pola Titip",
                    "Hari Histori Valid",
                    "Rata-rata Laku (pcs)",
                    "Sell-Through (%)",
                    "Return Rate (%)",
                    "Tren",
                    "Kuota Saat Ini (pcs)",
                    "Perubahan (pcs)",
                    "Skor Prioritas",
                    "Alasan Rekomendasi",
                ],
                hide_index=True,
                use_container_width=True,
                column_config={
                    "id_tenongan": None,
                    "Saran Kuota Baru (pcs)": st.column_config.NumberColumn(
                        "Saran Kuota Baru (pcs) ✏️",
                        min_value=0,
                        max_value=100,
                        step=1,
                        help="Klik dua kali untuk menyesuaikan saran kuota titipan secara manual.",
                    ),
                },
            )

            total_kuota_lama = int(edited_rek["Kuota Saat Ini (pcs)"].sum())
            total_kuota_saran = int(edited_rek["Saran Kuota Baru (pcs)"].sum())
            st.info(
                f"**Ringkasan Alokasi:** Total Kuota Historis = **{total_kuota_lama} pcs** $\\rightarrow$ "
                f"Total Saran Kuota Baru = **{total_kuota_saran} pcs** "
                f"(Selisih: **{total_kuota_saran - total_kuota_lama:+d} pcs** dari kapasitas {kap_maks} pcs)."
            )

            ca1, ca2, ca3 = st.columns(3)
            with ca1:
                if st.button("💾 Perbarui Kuota Default Master Tenongan", use_container_width=True):
                    with get_connection(db_path) as conn:
                        for _, r_k in edited_rek.iterrows():
                            conn.execute(
                                "UPDATE tenongan SET kuota_default = ?, updated_at = datetime('now', 'localtime') WHERE id_tenongan = ?;",
                                (int(r_k["Saran Kuota Baru (pcs)"]), int(r_k["id_tenongan"])),
                            )
                    st.success("Kuota default pada Master Tenongan berhasil diperbarui sesuai tabel rekomendasi!")
            with ca2:
                if st.button(
                    f"📝 Terapkan sebagai Titipan Awal ({format_tanggal_id(tgl_target_rek, singkat=True)})",
                    type="primary",
                    use_container_width=True,
                ):
                    payload_rek = []
                    for _, r_k in edited_rek.iterrows():
                        q_saran = int(r_k["Saran Kuota Baru (pcs)"])
                        if q_saran > 0:
                            payload_rek.append(
                                {
                                    "id_tenongan": int(r_k["id_tenongan"]),
                                    "nama_tenongan": str(r_k["Nama Tenongan"]),
                                    "qty_titip": q_saran,
                                    "qty_laku": 0,
                                    "qty_retur": 0,
                                    "harga_jual_unit": 2000.0,
                                    "status_konfirmasi": "BELUM_KONFIRMASI",
                                    "is_rapel": False,
                                    "catatan": "Alokasi dari Rekomendasi Data Science",
                                }
                            )
                    ok_ap, msg_ap, _ = simpan_batch_transaksi_harian(
                        tanggal=tgl_target_rek.isoformat(),
                        daftar_input=payload_rek,
                        db_path=db_path,
                    )
                    if ok_ap:
                        st.success(
                            f"Berhasil membuat jadwal penitipan awal (BELUM_KONFIRMASI) untuk tanggal {tgl_target_rek.isoformat()}!"
                        )
                    else:
                        st.error(msg_ap)
            with ca3:
                st.download_button(
                    "⬇️ Ekspor Hasil Analisis & Rekomendasi (.xlsx)",
                    data=dfs_ke_excel_bytes(
                        {
                            "Rekomendasi_Kuota": edited_rek,
                            "Performa_Tenongan": df_perf,
                            "Statistik_EDA": df_stat_eda if not df_stat_eda.empty else pd.DataFrame(),
                        }
                    ),
                    file_name=f"analisis_data_science_taiyaki_{tgl_target_rek.isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )


render_halaman_data_science()