"""
pages/6_Kalkulasi_Modal_HPP.py
Kalkulator Biaya Produksi, Harga Pokok Produksi (HPP) per Batch & per Buah,
Evaluasi Laba Harian (Model Sheet 'kalkulasi' projectapp.xlsx),
Simulasi Perubahan Harga Bahan & Harga Jual, serta Analisis Break-Even Point (BEP)
dengan 3 Skenario (Konservatif, Moderat, Optimistis).
"""

from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculations import (
    hitung_bep_dan_simulasi,
    hitung_hpp_batch,
    hitung_laba_rugi_periode,
)
from components import get_active_db_path, render_kpi_card, tampilkan_header_halaman
from data_processing import dfs_ke_excel_bytes
from database import catat_audit_log, get_connection, get_setting, query_df, set_setting
from utils import format_angka, format_persen, format_rupiah


def simpan_resep_hpp_ke_db(
    id_resep: int,
    output_batch_pcs: int,
    biaya_tenaga_kerja: float,
    biaya_energi_batch: float,
    biaya_distribusi_batch: float,
    biaya_lain_batch: float,
    df_komponen: pd.DataFrame,
    aktifkan_sebagai_default: bool = True,
    db_path: Path = None,
) -> float:
    """Menyimpan rincian resep HPP batch ke SQLite dan memperbarui hpp_unit_aktif."""
    daftar_biaya_item = []
    baris_bersih = []
    for _, r in df_komponen.iterrows():
        nama_k = str(r.get("Komponen Bahan", "")).strip()
        if not nama_k:
            continue
        kat_k = str(r.get("Kategori", "Bahan Utama")).strip()
        qty_k = max(0.0, float(r.get("Qty Pakai per Batch", 0.0) or 0.0))
        sat_k = str(r.get("Satuan Pakai", "gram")).strip()
        hrg_k = max(0.0, float(r.get("Harga Beli Acuan (Rp)", 0.0) or 0.0))
        ukr_k = max(0.0001, float(r.get("Ukuran per Harga Acuan", 1.0) or 1.0))
        biaya_item = round((qty_k / ukr_k) * hrg_k, 2)
        daftar_biaya_item.append(biaya_item)
        baris_bersih.append((nama_k, kat_k, qty_k, sat_k, hrg_k, ukr_k, biaya_item))

    hasil = hitung_hpp_batch(
        daftar_biaya_bahan=daftar_biaya_item,
        output_batch_pcs=int(output_batch_pcs),
        biaya_tenaga_kerja_batch=biaya_tenaga_kerja,
        biaya_energi_batch=biaya_energi_batch,
        biaya_distribusi_batch=biaya_distribusi_batch,
        biaya_lain_batch=biaya_lain_batch,
    )

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE resep_hpp_batch SET
                output_batch_pcs = ?,
                biaya_tenaga_kerja = ?,
                biaya_energi_batch = ?,
                biaya_distribusi_batch = ?,
                biaya_lain_batch = ?,
                total_biaya_bahan = ?,
                total_biaya_batch = ?,
                hpp_per_pcs = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id_resep = ?;
            """,
            (
                int(output_batch_pcs),
                float(biaya_tenaga_kerja),
                float(biaya_energi_batch),
                float(biaya_distribusi_batch),
                float(biaya_lain_batch),
                hasil["total_biaya_bahan"],
                hasil["total_biaya_batch"],
                hasil["hpp_per_pcs"],
                int(id_resep),
            ),
        )
        cursor.execute("DELETE FROM resep_hpp_item WHERE id_resep = ?;", (int(id_resep),))
        for nm, kt, qt, st_p, hr, uk, bi in baris_bersih:
            cursor.execute(
                """
                INSERT INTO resep_hpp_item (
                    id_resep, nama_komponen, kategori, qty_pakai,
                    satuan_pakai, harga_acuan_satuan, ukuran_acuan, biaya_item_batch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (int(id_resep), nm, kt, qt, st_p, hr, uk, bi),
            )
        catat_audit_log(
            conn,
            tabel_target="resep_hpp_batch",
            id_record=int(id_resep),
            aksi="UPDATE_RESEP",
            data_baru=hasil,
            keterangan="Update kalkulasi resep HPP batch",
        )

    if aktifkan_sebagai_default:
        set_setting("hpp_unit_aktif", hasil["hpp_per_pcs"], tipe_data="float", db_path=db_path)
        set_setting("output_batch_default", int(output_batch_pcs), tipe_data="int", db_path=db_path)

    return hasil["hpp_per_pcs"]


def render_halaman_kalkulasi_hpp() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Kalkulasi Modal, HPP Batch & Analisis Titik Impas (BEP)",
        subjudul="Hitung HPP standar per buah, evaluasi keuntungan harian, dan simulasikan perubahan harga bahan serta skenario penjualan.",
        ikon="🧮",
    )

    df_resep = query_df("SELECT * FROM resep_hpp_batch ORDER BY id_resep ASC LIMIT 1;", db_path=db_path)
    if df_resep.empty:
        st.error("Data resep standar belum diinisialisasi.")
        return

    resep_row = df_resep.iloc[0]
    id_resep_aktif = int(resep_row["id_resep"])
    df_items = query_df(
        "SELECT * FROM resep_hpp_item WHERE id_resep = ? ORDER BY id_resep_item ASC;",
        (id_resep_aktif,),
        db_path=db_path,
    )
    harga_jual_default = float(get_setting("harga_jual_default", 2000.0, db_path=db_path) or 2000.0)

    tab_resep, tab_harian, tab_simulasi_bep = st.tabs(
        [
            "🥣 1. Kalkulator Resep & HPP per Batch",
            "⚖️ 2. Kalkulator Evaluasi Harian (Model Sheet Kalkulasi)",
            "📈 3. Simulasi Harga, Target Skenario & Analisis BEP",
        ]
    )

    # ==========================================================================
    # TAB 1: KALKULATOR RESEP & HPP PER BATCH (DEFAULT 120 PCS)
    # ==========================================================================
    with tab_resep:
        st.markdown("#### 🥣 Rincian Komponen Biaya Produksi per Batch")
        st.caption(
            "Rumus Biaya Komponen = `(Qty Pakai per Batch ÷ Ukuran per Harga Acuan) × Harga Beli Acuan`. "
            "Anda dapat mengubah angka pada tabel di bawah atau menambah baris komponen baru."
        )

        cr1, cr2, cr3 = st.columns(3)
        with cr1:
            output_batch = st.number_input(
                "Output Produk Layak Jual per Batch (pcs) *",
                min_value=1,
                max_value=5000,
                value=int(resep_row["output_batch_pcs"]),
                step=10,
            )
            harga_jual_uji = st.number_input(
                "Harga Jual per Buah (Rp)",
                min_value=100.0,
                value=harga_jual_default,
                step=100.0,
            )
        with cr2:
            biaya_energi = st.number_input(
                "Biaya Gas & Listrik per Batch (Rp)",
                min_value=0.0,
                value=float(resep_row["biaya_energi_batch"]),
                step=1000.0,
                help="Biaya gas LPG dan listrik yang dialokasikan langsung ke 1 batch adonan.",
            )
            biaya_tk = st.number_input(
                "Biaya Tenaga Kerja Produksi per Batch (Rp)",
                min_value=0.0,
                value=float(resep_row["biaya_tenaga_kerja"]),
                step=1000.0,
            )
        with cr3:
            biaya_dist = st.number_input(
                "Biaya Distribusi per Batch (Rp)",
                min_value=0.0,
                value=float(resep_row["biaya_distribusi_batch"]),
                step=1000.0,
            )
            biaya_lain = st.number_input(
                "Biaya Operasional Lain per Batch (Rp)",
                min_value=0.0,
                value=float(resep_row["biaya_lain_batch"]),
                step=1000.0,
            )

        df_editor_resep = pd.DataFrame(
            [
                {
                    "Komponen Bahan": str(r["nama_komponen"]),
                    "Kategori": str(r["kategori"]),
                    "Qty Pakai per Batch": float(r["qty_pakai"]),
                    "Satuan Pakai": str(r["satuan_pakai"]),
                    "Harga Beli Acuan (Rp)": float(r["harga_acuan_satuan"]),
                    "Ukuran per Harga Acuan": float(r["ukuran_acuan"]),
                    "Biaya di Batch (Rp)": round(
                        (float(r["qty_pakai"]) / max(0.0001, float(r["ukuran_acuan"])))
                        * float(r["harga_acuan_satuan"]),
                        2,
                    ),
                }
                for _, r in df_items.iterrows()
            ]
        )

        edited_resep_df = st.data_editor(
            df_editor_resep,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            disabled=["Biaya di Batch (Rp)"],
            column_config={
                "Kategori": st.column_config.SelectboxColumn(
                    "Kategori",
                    options=["Bahan Utama", "Bahan Isian", "Kemasan", "Bahan Tambahan", "Energi & Gas"],
                    required=True,
                ),
                "Qty Pakai per Batch": st.column_config.NumberColumn("Qty Pakai / Batch", min_value=0.0, step=10.0),
                "Harga Beli Acuan (Rp)": st.column_config.NumberColumn("Harga Acuan (Rp)", min_value=0.0, step=500.0),
                "Ukuran per Harga Acuan": st.column_config.NumberColumn(
                    "Ukuran Acuan",
                    min_value=0.01,
                    step=10.0,
                    help="Contoh: Jika Harga Tepung Rp10.500 per 1.000 gram, isi Ukuran Acuan = 1000.",
                ),
            },
        )

        # Hitung ulang secara dinamis dari edited_resep_df
        list_biaya_komponen = []
        for _, r in edited_resep_df.iterrows():
            q_p = max(0.0, float(r.get("Qty Pakai per Batch", 0.0) or 0.0))
            h_a = max(0.0, float(r.get("Harga Beli Acuan (Rp)", 0.0) or 0.0))
            u_a = max(0.0001, float(r.get("Ukuran per Harga Acuan", 1.0) or 1.0))
            list_biaya_komponen.append(round((q_p / u_a) * h_a, 2))

        hasil_hpp = hitung_hpp_batch(
            daftar_biaya_bahan=list_biaya_komponen,
            output_batch_pcs=int(output_batch),
            biaya_tenaga_kerja_batch=float(biaya_tk),
            biaya_energi_batch=float(biaya_energi),
            biaya_distribusi_batch=float(biaya_dist),
            biaya_lain_batch=float(biaya_lain),
            harga_jual_unit=float(harga_jual_uji),
        )

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            render_kpi_card(
                f"Total Biaya Batch ({output_batch} pcs)",
                format_rupiah(hasil_hpp["total_biaya_batch"], gunakan_desimal=True),
                f"Bahan: {format_rupiah(hasil_hpp['total_biaya_bahan'])} | Overhead: {format_rupiah(hasil_hpp['total_overhead_batch'])}",
                kategori_warna="pengeluaran",
            )
        with k2:
            render_kpi_card(
                "HPP per Buah Taiyaki",
                format_rupiah(hasil_hpp["hpp_per_pcs"], gunakan_desimal=True),
                f"Biaya pokok untuk 1 pcs roti layak jual",
                kategori_warna="netral",
            )
        with k3:
            render_kpi_card(
                "Laba Kotor per Buah",
                format_rupiah(hasil_hpp["laba_kotor_per_pcs"], gunakan_desimal=True),
                f"Pada harga jual {format_rupiah(harga_jual_uji)}/pcs",
                kategori_warna="keuntungan" if hasil_hpp["laba_kotor_per_pcs"] > 0 else "pengeluaran",
            )
        with k4:
            render_kpi_card(
                "Margin Laba Kotor Standar",
                format_persen(hasil_hpp["margin_kotor_persen"], desimal=2),
                "Tanpa retur (100% terjual)",
                kategori_warna="keuntungan",
            )

        if st.button(
            "💾 Simpan Resep & Tetapkan sebagai HPP Standar Aktif",
            type="primary",
            use_container_width=True,
        ):
            hpp_baru = simpan_resep_hpp_ke_db(
                id_resep=id_resep_aktif,
                output_batch_pcs=int(output_batch),
                biaya_tenaga_kerja=float(biaya_tk),
                biaya_energi_batch=float(biaya_energi),
                biaya_distribusi_batch=float(biaya_dist),
                biaya_lain_batch=float(biaya_lain),
                df_komponen=edited_resep_df,
                aktifkan_sebagai_default=True,
                db_path=db_path,
            )
            st.success(
                f"Resep berhasil disimpan! HPP Standar Aktif diperbarui menjadi **{format_rupiah(hpp_baru, gunakan_desimal=True)}/pcs**."
            )
            st.rerun()

    # ==========================================================================
    # TAB 2: KALKULATOR EVALUASI HARIAN (MODEL SHEET 'kalkulasi' projectapp.xlsx)
    # ==========================================================================
    with tab_harian:
        st.markdown("#### ⚖️ Evaluasi Keuntungan Produksi & Distribusi Harian")
        st.write(
            "Simulasikan hasil distribusi harian dengan memperhitungkan tingkat roti sisa/retur (`SISA`). "
            "Nilai default di bawah diambil langsung dari simulasi pada sheet **`kalkulasi`** di `projectapp.xlsx` Anda "
            "(Titip/Setor = 66 pcs, Sisa/Retur = 8 pcs, Laku = 58 pcs)."
        )

        ce1, ce2, ce3, ce4 = st.columns(4)
        with ce1:
            sim_titip = st.number_input("Jumlah Roti Dititipkan / Diproduksi (pcs)", min_value=1, value=66, step=1)
        with ce2:
            sim_retur = st.number_input(
                "Jumlah Roti Sisa / Retur (pcs)",
                min_value=0,
                max_value=int(sim_titip),
                value=min(8, int(sim_titip)),
                step=1,
            )
        with ce3:
            sim_laku = int(sim_titip) - int(sim_retur)
            st.metric("Jumlah Roti Terjual (Laku)", f"{sim_laku} pcs", f"Sell-Through: {(sim_laku/sim_titip*100):.2f}%")
        with ce4:
            mode_beban_sim = st.selectbox(
                "Perlakuan Biaya Roti Retur",
                options=["BEBANKAN_RETUR", "RITEL_MURNI"],
                format_func=lambda x: (
                    "Bebankan Retur ke Modal Hari Ini (Sesuai Sheet Kalkulasi)"
                    if x == "BEBANKAN_RETUR"
                    else "Hanya Bebankan HPP Barang Terjual"
                ),
            )

        lr_sim = hitung_laba_rugi_periode(
            total_titip_terkonfirmasi=int(sim_titip),
            total_laku=int(sim_laku),
            total_retur=int(sim_retur),
            omzet_kotor=float(sim_laku * harga_jual_uji),
            setoran_harapan=float(sim_laku * harga_jual_uji),
            uang_setoran_diterima=float(sim_laku * harga_jual_uji),
            hpp_per_pcs=hasil_hpp["hpp_per_pcs"],
            biaya_operasional_lain=0.0,
            mode_beban_retur=mode_beban_sim,
        )
        bersih_per_biji_laku = (lr_sim["laba_bersih"] / sim_laku) if sim_laku > 0 else 0.0
        persen_modal_thd_omzet = (
            (lr_sim["total_hpp_dibebankan"] / lr_sim["omzet_kotor"] * 100.0) if lr_sim["omzet_kotor"] > 0 else 0.0
        )

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            render_kpi_card(
                "Omzet Penjualan Aktual",
                format_rupiah(lr_sim["omzet_kotor"]),
                f"{sim_laku} pcs × {format_rupiah(harga_jual_uji)}",
                kategori_warna="pendapatan",
            )
        with m2:
            render_kpi_card(
                "Total Modal / HPP Terbebankan",
                format_rupiah(lr_sim["total_hpp_dibebankan"], gunakan_desimal=True),
                f"{format_persen(persen_modal_thd_omzet, 2)} dari Omzet (Kerugian Retur: {format_rupiah(lr_sim['biaya_kerugian_retur'])})",
                kategori_warna="pengeluaran",
            )
        with m3:
            render_kpi_card(
                "Laba Bersih Harian",
                format_rupiah(lr_sim["laba_bersih"], gunakan_desimal=True),
                f"Margin Keuntungan: {format_persen(lr_sim['margin_bersih_persen'], 2)}",
                kategori_warna="keuntungan" if lr_sim["laba_bersih"] > 0 else "pengeluaran",
            )
        with m4:
            render_kpi_card(
                "Laba Bersih per Biji Laku",
                format_rupiah(bersih_per_biji_laku, gunakan_desimal=True),
                f"Setelah menyerap beban {sim_retur} pcs retur",
                kategori_warna="keuntungan",
            )

    # ==========================================================================
    # TAB 3: SIMULASI HARGA, SKENARIO & ANALISIS BREAK-EVEN POINT (BEP)
    # ==========================================================================
    with tab_simulasi_bep:
        st.markdown("#### 📈 Simulasi Sensitivitas Harga, Target Penjualan & Titik Impas (BEP)")
        st.info(
            "**Catatan Asumsi BEP:** Analisis titik impas memisahkan **Biaya Tetap Periode** (misalnya penyusutan alat, "
            "biaya operasional tetap bulanan) dari **Biaya Variabel per Unit** (`HPP per buah`). "
            "Jika seluruh komponen biaya Anda bersifat variabel per batch, masukkan estimasi biaya tetap bulanan "
            "atau gaji pokok bulanan yang ingin ditutup oleh margin kontribusi produk."
        )

        cs1, cs2, cs3, cs4 = st.columns(4)
        with cs1:
            delta_bahan_persen = st.slider(
                "Simulasi Perubahan Harga Bahan (%)",
                min_value=-30,
                max_value=50,
                value=0,
                step=5,
            )
        with cs2:
            sim_harga_jual = st.number_input(
                "Simulasi Harga Jual / pcs (Rp)",
                min_value=1000.0,
                max_value=10000.0,
                value=float(harga_jual_uji),
                step=100.0,
            )
        with cs3:
            biaya_tetap_bln = st.number_input(
                "Biaya Tetap Operasional Bulanan (Rp)",
                min_value=0.0,
                value=float(get_setting("biaya_tetap_bulanan_default", 150000.0, db_path=db_path) or 150000.0),
                step=25000.0,
            )
        with cs4:
            target_laku_bln = st.number_input(
                "Target Volume Laku Bulanan (pcs)",
                min_value=100,
                max_value=50000,
                value=int(get_setting("target_penjualan_bulanan_pcs", 1500, db_path=db_path) or 1500),
                step=100,
            )

        hpp_simulasi = hasil_hpp["hpp_per_pcs"] * (1.0 + (delta_bahan_persen / 100.0))
        hasil_bep = hitung_bep_dan_simulasi(
            harga_jual_unit=float(sim_harga_jual),
            hpp_variabel_unit=float(hpp_simulasi),
            biaya_tetap_periode=float(biaya_tetap_bln),
            target_volume_laku=int(target_laku_bln),
            estimasi_return_rate_persen=15.0,
        )

        b1, b2, b3, b4 = st.columns(4)
        with b1:
            render_kpi_card(
                "HPP Simulasi per Buah",
                format_rupiah(hpp_simulasi, gunakan_desimal=True),
                f"Perubahan bahan: {delta_bahan_persen:+d}%",
                kategori_warna="pengeluaran" if delta_bahan_persen > 0 else "netral",
            )
        with b2:
            render_kpi_card(
                "Margin Kontribusi / Buah",
                format_rupiah(hasil_bep["margin_kontribusi_unit"], gunakan_desimal=True),
                f"Rasio Kontribusi: {format_persen(hasil_bep['rasio_margin_kontribusi_persen'], 2)}",
                kategori_warna="keuntungan" if hasil_bep["margin_kontribusi_unit"] > 0 else "pengeluaran",
            )
        with b3:
            teks_bep_unit = (
                f"{format_angka(np.ceil(hasil_bep['bep_unit']))} pcs"
                if hasil_bep["bep_unit"] >= 0
                else "Tidak Tercapai (Rugi/pcs)"
            )
            render_kpi_card(
                "BEP Volume (Titik Impas Unit)",
                teks_bep_unit,
                "Jumlah minimal terjual agar tidak rugi",
                kategori_warna="pendapatan",
            )
        with b4:
            teks_bep_rp = (
                format_rupiah(hasil_bep["bep_rupiah"])
                if hasil_bep["bep_rupiah"] >= 0
                else "Tidak Tercapai"
            )
            render_kpi_card(
                "BEP Omzet (Titik Impas Rupiah)",
                teks_bep_rp,
                f"Untuk menutup biaya tetap {format_rupiah(biaya_tetap_bln)}",
                kategori_warna="pendapatan",
            )

        # Grafik Break-Even Point Interaktif
        if hasil_bep["bep_unit"] >= 0:
            max_x = max(int(target_laku_bln * 1.4), int(hasil_bep["bep_unit"] * 2.0), 500)
            vol_x = np.linspace(0, max_x, 50)
            omzet_y = vol_x * float(sim_harga_jual)
            biaya_total_y = float(biaya_tetap_bln) + (vol_x * float(hpp_simulasi))

            fig_bep = go.Figure()
            fig_bep.add_trace(
                go.Scatter(x=vol_x, y=omzet_y, mode="lines", name="Garis Pendapatan (Omzet)", line=dict(color="#16a34a", width=3))
            )
            fig_bep.add_trace(
                go.Scatter(
                    x=vol_x,
                    y=biaya_total_y,
                    mode="lines",
                    name="Garis Total Biaya (Tetap + Variabel)",
                    line=dict(color="#dc2626", width=3, dash="dash"),
                )
            )
            fig_bep.add_trace(
                go.Scatter(
                    x=[hasil_bep["bep_unit"]],
                    y=[hasil_bep["bep_rupiah"]],
                    mode="markers+text",
                    name="Titik Impas (BEP)",
                    text=[f"BEP: {int(np.ceil(hasil_bep['bep_unit']))} pcs ({format_rupiah(hasil_bep['bep_rupiah'])})"],
                    textposition="top left",
                    marker=dict(color="#2563eb", size=12, symbol="diamond"),
                )
            )
            fig_bep.update_layout(
                title="Kurva Analisis Titik Impas (Break-Even Point)",
                xaxis_title="Volume Roti Terjual (pcs)",
                yaxis_title="Nilai Rupiah (Rp)",
                height=390,
                margin=dict(l=20, r=20, t=45, b=20),
            )
            st.plotly_chart(fig_bep, use_container_width=True)

        st.markdown("##### 📊 Perbandingan 3 Skenario (Konservatif, Moderat, Optimistis)")
        df_skenario = pd.DataFrame.from_dict(hasil_bep["skenario"], orient="index").reset_index()
        df_skenario = df_skenario.rename(
            columns={
                "index": "Skenario",
                "qty_produksi": "Kebutuhan Produksi (pcs)",
                "qty_laku": "Estimasi Terjual (pcs)",
                "qty_retur": "Estimasi Retur (pcs)",
                "return_rate_persen": "Asumsi Return Rate (%)",
                "omzet": "Estimasi Omzet (Rp)",
                "biaya_variabel_total": "Biaya Produksi Variabel (Rp)",
                "biaya_tetap": "Biaya Tetap (Rp)",
                "laba_bersih": "Estimasi Laba Bersih (Rp)",
                "margin_bersih_persen": "Margin Bersih (%)",
            }
        )
        st.dataframe(df_skenario, use_container_width=True, hide_index=True)

        st.download_button(
            "⬇️ Ekspor Kalkulasi HPP & Skenario BEP ke Excel (.xlsx)",
            data=dfs_ke_excel_bytes(
                {"Komponen_Resep_HPP": edited_resep_df, "Simulasi_Skenario_BEP": df_skenario}
            ),
            file_name="kalkulasi_hpp_dan_bep_taiyaki.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


render_halaman_kalkulasi_hpp()