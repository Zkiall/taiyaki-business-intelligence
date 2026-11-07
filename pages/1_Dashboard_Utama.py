"""
pages/1_Dashboard_Utama.py
Halaman A. Dashboard Utama Eksekutif Taiyaki Business Manager.
Menampilkan:
- Sistem Peringatan Otomatis (Stok minimum, Piutang setoran, Transaksi belum dikonfirmasi)
- 11 KPI Utama yang dihitung langsung dari database SQLite
- Indikator perubahan performa (Delta %) dibandingkan periode sebelumnya yang setara
- Grafik interaktif Plotly:
  1. Tren penjualan harian & bulanan (Titip, Laku, Retur)
  2. Grafik Omzet, Biaya Produksi (HPP), dan Keuntungan (Laba Bersih)
  3. Grafik Performa Setiap Tenongan (Volume Laku & Sell-Through Rate)
  4. Perbandingan komposisi barang dititipkan, terjual, dan retur
- Ekspor ringkasan dashboard ke Excel (.xlsx) dan CSV
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from calculations import hitung_laba_rugi_periode, hitung_sell_through_dan_retur
from components import (
    get_active_db_path,
    pilih_rentang_tanggal,
    render_kpi_card,
    tampilkan_header_halaman,
)
from data_processing import df_ke_csv_bytes, dfs_ke_excel_bytes
from database import get_connection, get_setting, query_df
from utils import (
    format_angka,
    format_persen,
    format_rupiah,
    format_tanggal_id,
    parse_tanggal,
)


def hitung_metrik_periode(
    tgl_mulai: str,
    tgl_selesai: str,
    db_path: Path,
) -> Dict[str, Any]:
    """
    Menghitung seluruh KPI utama untuk rentang tanggal tertentu secara langsung
    dari database SQLite.
    """
    hpp_default = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)
    mode_retur = str(get_setting("mode_beban_retur", "BEBANKAN_RETUR", db_path=db_path) or "BEBANKAN_RETUR")

    with get_connection(db_path) as conn:
        r_trx = conn.execute(
            """
            SELECT
                COUNT(*) AS jml_baris,
                COUNT(DISTINCT tanggal) AS hari_aktif,
                COALESCE(SUM(qty_titip), 0) AS total_titip,
                COALESCE(SUM(CASE WHEN status_konfirmasi = 'TERKONFIRMASI' THEN qty_titip ELSE 0 END), 0) AS titip_terkonfirmasi,
                COALESCE(SUM(qty_laku), 0) AS total_laku,
                COALESCE(SUM(qty_retur), 0) AS total_retur,
                COALESCE(SUM(CASE WHEN status_konfirmasi = 'BELUM_KONFIRMASI' THEN qty_titip ELSE 0 END), 0) AS pcs_unconfirmed,
                COALESCE(SUM(CASE WHEN status_konfirmasi = 'BELUM_KONFIRMASI' THEN 1 ELSE 0 END), 0) AS trx_unconfirmed,
                COALESCE(SUM(omzet_kotor), 0.0) AS omzet_kotor,
                COALESCE(SUM(setoran_harapan), 0.0) AS setoran_harapan,
                COALESCE(SUM(uang_setoran), 0.0) AS uang_setoran,
                COALESCE(SUM(piutang), 0.0) AS total_piutang
            FROM transaksi_harian
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

        r_kul = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN kategori_transaksi = 'JUAL_GROSIR_TAIYAKI' THEN qty ELSE 0 END), 0) AS qty_grosir,
                COALESCE(SUM(CASE WHEN kategori_transaksi = 'JUAL_GROSIR_TAIYAKI' THEN total_transaksi ELSE 0 END), 0.0) AS omzet_grosir,
                COALESCE(SUM(CASE WHEN kategori_transaksi = 'BELI_BARANG_JUAL_KEMBALI' THEN total_transaksi ELSE 0 END), 0.0) AS biaya_trading
            FROM transaksi_kulakan
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

        r_ops = conn.execute(
            """
            SELECT COALESCE(SUM(nominal), 0.0) AS biaya_ops
            FROM biaya_operasional
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

        r_top = conn.execute(
            """
            SELECT
                t.nama_tenongan,
                SUM(tr.qty_laku) AS laku_tng,
                SUM(tr.omzet_kotor) AS omzet_tng
            FROM transaksi_harian tr
            JOIN tenongan t ON tr.id_tenongan = t.id_tenongan
            WHERE tr.tanggal BETWEEN ? AND ?
            GROUP BY tr.id_tenongan
            ORDER BY laku_tng DESC, omzet_tng DESC
            LIMIT 1;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

    lr = hitung_laba_rugi_periode(
        total_titip_terkonfirmasi=int(r_trx["titip_terkonfirmasi"]),
        total_laku=int(r_trx["total_laku"]),
        total_retur=int(r_trx["total_retur"]),
        omzet_kotor=float(r_trx["omzet_kotor"]) + float(r_kul["omzet_grosir"]),
        setoran_harapan=float(r_trx["setoran_harapan"]) + float(r_kul["omzet_grosir"]),
        uang_setoran_diterima=float(r_trx["uang_setoran"]),
        hpp_per_pcs=hpp_default,
        biaya_operasional_lain=float(r_ops["biaya_ops"]) + float(r_kul["biaya_trading"]),
        mode_beban_retur=mode_retur,
    )
    str_rr = hitung_sell_through_dan_retur(
        total_titip_terkonfirmasi=int(r_trx["titip_terkonfirmasi"]),
        total_laku_terkonfirmasi=int(r_trx["total_laku"]),
        total_retur_terkonfirmasi=int(r_trx["total_retur"]),
    )

    total_produksi = int(r_trx["total_titip"]) + int(r_kul["qty_grosir"])
    hari_aktif = int(r_trx["hari_aktif"])
    rata2_laku_harian = (int(r_trx["total_laku"]) / hari_aktif) if hari_aktif > 0 else 0.0

    return {
        "hari_aktif": hari_aktif,
        "total_produksi": total_produksi,
        "total_titip": int(r_trx["total_titip"]),
        "titip_terkonfirmasi": int(r_trx["titip_terkonfirmasi"]),
        "total_laku": int(r_trx["total_laku"]),
        "total_retur": int(r_trx["total_retur"]),
        "pcs_unconfirmed": int(r_trx["pcs_unconfirmed"]),
        "trx_unconfirmed": int(r_trx["trx_unconfirmed"]),
        "omzet_penjualan": lr["omzet_kotor"],
        "uang_disetorkan": lr["uang_setoran_diterima"],
        "piutang_setoran": float(r_trx["total_piutang"]),
        "total_biaya_produksi": lr["total_hpp_dibebankan"],
        "biaya_operasional_lain": lr["biaya_operasional_lain"],
        "laba_kotor": lr["laba_kotor"],
        "laba_bersih": lr["laba_bersih"],
        "margin_kotor_persen": lr["margin_kotor_persen"],
        "margin_bersih_persen": lr["margin_bersih_persen"],
        "sell_through_rate": str_rr["sell_through_rate"],
        "return_rate": str_rr["return_rate"],
        "rata2_laku_harian": round(rata2_laku_harian, 2),
        "top_tenongan_nama": str(r_top["nama_tenongan"]) if (r_top and r_top["laku_tng"]) else "-",
        "top_tenongan_laku": int(r_top["laku_tng"]) if (r_top and r_top["laku_tng"]) else 0,
        "top_tenongan_omzet": float(r_top["omzet_tng"]) if (r_top and r_top["omzet_tng"]) else 0.0,
    }


def format_delta_periode(nilai_kini: float, nilai_lalu: float, satuan_poin: bool = False) -> str:
    """Memformat indikator perubahan performa dibanding periode sebelumnya."""
    if nilai_lalu == 0:
        if nilai_kini == 0:
            return "Stabil (0% vs periode lalu)"
        return "Baru (Periode sebelumnya 0)"
    if satuan_poin:
        selisih = nilai_kini - nilai_lalu
        tanda = "+" if selisih > 0 else ""
        return f"{tanda}{format_angka(selisih, 1)} poin vs periode lalu"
    persen = ((nilai_kini - nilai_lalu) / abs(nilai_lalu)) * 100.0
    tanda = "+" if persen > 0 else ""
    return f"{tanda}{format_angka(persen, 1)}% vs periode lalu"


def tampilkan_peringatan_operasional(db_path: Path, kpi_aktif: Dict[str, Any]) -> None:
    """Menampilkan banner peringatan stok minimum, piutang, dan transaksi belum lengkap."""
    df_stok_kritis = query_df(
        """
        SELECT nama_bahan, stok_saat_ini, stok_minimum, satuan_beli
        FROM master_bahan
        WHERE status_aktif = 1 AND stok_saat_ini <= stok_minimum
        ORDER BY (stok_saat_ini - stok_minimum) ASC;
        """,
        db_path=db_path,
    )
    df_piutang_tng = query_df(
        """
        SELECT t.nama_tenongan, SUM(tr.piutang) AS sisa_piutang
        FROM transaksi_harian tr
        JOIN tenongan t ON tr.id_tenongan = t.id_tenongan
        WHERE tr.piutang > 0
        GROUP BY tr.id_tenongan
        ORDER BY sisa_piutang DESC;
        """,
        db_path=db_path,
    )

    ada_peringatan = (
        not df_stok_kritis.empty
        or not df_piutang_tng.empty
        or kpi_aktif["trx_unconfirmed"] > 0
    )
    if not ada_peringatan:
        return

    with st.expander("🔔 Peringatan Operasional Aktif (Stok, Piutang & Konfirmasi Barang)", expanded=True):
        ca1, ca2, ca3 = st.columns(3)
        with ca1:
            if kpi_aktif["trx_unconfirmed"] > 0:
                st.warning(
                    f"**📦 Transaksi Belum Dikonfirmasi:**\n\n"
                    f"Terdapat **{kpi_aktif['trx_unconfirmed']} transaksi** ({format_angka(kpi_aktif['pcs_unconfirmed'])} pcs roti) "
                    f"pada periode ini yang sudah dititipkan tetapi belum direkap jumlah lakunya."
                )
            else:
                st.success("**✅ Konfirmasi Titipan:**\n\nSeluruh transaksi pada periode ini sudah direkap lengkap.")
        with ca2:
            if not df_piutang_tng.empty:
                total_p = float(df_piutang_tng["sisa_piutang"].sum())
                top_p = ", ".join(
                    f"{r['nama_tenongan']} ({format_rupiah(r['sisa_piutang'])})"
                    for _, r in df_piutang_tng.head(3).iterrows()
                )
                st.error(
                    f"**💳 Piutang Setoran Tenongan ({len(df_piutang_tng)} mitra):**\n\n"
                    f"Total belum disetor: **{format_rupiah(total_p)}**. Mitra tertinggi: {top_p}."
                )
            else:
                st.success("**✅ Setoran Tenongan:**\n\nTidak ada tunggakan piutang setoran dari mitra tenongan.")
        with ca3:
            if not df_stok_kritis.empty:
                list_bhn = ", ".join(
                    f"{r['nama_bahan']} ({format_angka(r['stok_saat_ini'], 1)} {r['satuan_beli']})"
                    for _, r in df_stok_kritis.head(4).iterrows()
                )
                st.warning(
                    f"**🥚 Stok Bahan Menipis ({len(df_stok_kritis)} item):**\n\n"
                    f"Mendekati/di bawah batas minimum: {list_bhn}."
                )
            else:
                st.success("**✅ Stok Bahan Baku:**\n\nSeluruh stok bahan utama berada di atas batas minimum.")


def render_halaman_dashboard_utama() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Dashboard Utama Eksekutif",
        subjudul="Ringkasan real-time produksi, distribusi konsinyasi tenongan, penjualan, biaya produksi, dan profitabilitas usaha.",
        ikon="📊",
    )

    bounds = query_df(
        "SELECT MIN(tanggal) AS tmin, MAX(tanggal) AS tmax, COUNT(*) AS cnt FROM transaksi_harian;",
        db_path=db_path,
    )
    total_baris_db = int(bounds.iloc[0]["cnt"]) if not bounds.empty else 0

    if total_baris_db == 0:
        st.info(
            "Database saat ini belum memiliki catatan transaksi harian. "
            "Silakan menuju menu **📝 Input Harian: Titip & Laku** (Tab *Impor projectapp.xlsx*) "
            "atau menu **⚙️ Pengaturan & Manajemen Data** untuk mengimpor data historis Excel Anda."
        )
        return

    tmin = str(bounds.iloc[0]["tmin"])
    tmax = str(bounds.iloc[0]["tmax"])
    tgl_mulai, tgl_selesai = pilih_rentang_tanggal(tmin, tmax, key_prefix="dash_utama")

    # Hitung rentang periode pembanding tepat sebelumnya dengan durasi hari yang setara
    dt_mulai = parse_tanggal(tgl_mulai)
    dt_selesai = parse_tanggal(tgl_selesai)
    durasi_hari = max(1, (dt_selesai - dt_mulai).days + 1)
    prev_selesai = (dt_mulai - timedelta(days=1)).isoformat()
    prev_mulai = (dt_mulai - timedelta(days=durasi_hari)).isoformat()

    kpi_kini = hitung_metrik_periode(tgl_mulai, tgl_selesai, db_path)
    kpi_lalu = hitung_metrik_periode(prev_mulai, prev_selesai, db_path)

    # Tampilkan Peringatan Operasional
    tampilkan_peringatan_operasional(db_path, kpi_kini)

    st.markdown("#### 📌 Indikator Kinerja Utama (KPI Eksekutif)")
    st.caption(
        f"Menampilkan metrik aktual dari database untuk periode **{format_tanggal_id(tgl_mulai, singkat=True)} – "
        f"{format_tanggal_id(tgl_selesai, singkat=True)}** ({kpi_kini['hari_aktif']} hari operasional aktif) "
        f"dibandingkan periode sebelumnya ({format_tanggal_id(prev_mulai, singkat=True)} – {format_tanggal_id(prev_selesai, singkat=True)})."
    )

    # Baris 1 KPI: Arus Fisik Produk (Produksi, Titip, Laku, Retur)
    r1_c1, r1_c2, r1_c3, r1_c4 = st.columns(4)
    with r1_c1:
        render_kpi_card(
            "Total Produksi & Distribusi",
            f"{format_angka(kpi_kini['total_produksi'])} pcs",
            f"Dititipkan: {format_angka(kpi_kini['total_titip'])} pcs ({format_delta_periode(kpi_kini['total_titip'], kpi_lalu['total_titip'])})",
            kategori_warna="netral",
        )
    with r1_c2:
        render_kpi_card(
            "Total Roti Terjual (Laku)",
            f"{format_angka(kpi_kini['total_laku'])} pcs",
            f"Rata-rata {format_angka(kpi_kini['rata2_laku_harian'], 1)} pcs/hari ({format_delta_periode(kpi_kini['total_laku'], kpi_lalu['total_laku'])})",
            kategori_warna="pendapatan",
        )
    with r1_c3:
        render_kpi_card(
            "Sell-Through Rate (STR)",
            format_persen(kpi_kini["sell_through_rate"], 2),
            f"{format_delta_periode(kpi_kini['sell_through_rate'], kpi_lalu['sell_through_rate'], satuan_poin=True)} | Unconfirmed: {format_angka(kpi_kini['pcs_unconfirmed'])} pcs",
            kategori_warna="keuntungan" if kpi_kini["sell_through_rate"] >= 80 else "piutang",
        )
    with r1_c4:
        render_kpi_card(
            "Total Retur / Sisa",
            f"{format_angka(kpi_kini['total_retur'])} pcs",
            f"Return Rate: {format_persen(kpi_kini['return_rate'], 2)} dari titipan terkonfirmasi",
            kategori_warna="pengeluaran" if kpi_kini["return_rate"] > 20 else "piutang",
        )

    # Baris 2 KPI: Pendapatan, Setoran Kas, Piutang, dan Biaya Produksi
    r2_c1, r2_c2, r2_c3, r2_c4 = st.columns(4)
    with r2_c1:
        render_kpi_card(
            "Omzet Penjualan Kotor",
            format_rupiah(kpi_kini["omzet_penjualan"]),
            format_delta_periode(kpi_kini["omzet_penjualan"], kpi_lalu["omzet_penjualan"]),
            kategori_warna="pendapatan",
        )
    with r2_c2:
        render_kpi_card(
            "Uang Sudah Disetorkan (Kas)",
            format_rupiah(kpi_kini["uang_disetorkan"]),
            f"Rasio Kas Masuk: {format_persen((kpi_kini['uang_disetorkan']/kpi_kini['omzet_penjualan']*100) if kpi_kini['omzet_penjualan']>0 else 0, 1)}",
            kategori_warna="keuntungan",
        )
    with r2_c3:
        render_kpi_card(
            "Piutang Setoran Tenongan",
            format_rupiah(kpi_kini["piutang_setoran"]),
            "Tagihan penjualan yang belum disetor ke kas",
            kategori_warna="piutang" if kpi_kini["piutang_setoran"] > 0 else "keuntungan",
        )
    with r2_c4:
        render_kpi_card(
            "Total Biaya Produksi (HPP)",
            format_rupiah(kpi_kini["total_biaya_produksi"]),
            f"Biaya Ops Lain: {format_rupiah(kpi_kini['biaya_operasional_lain'])}",
            kategori_warna="pengeluaran",
        )

    # Baris 3 KPI: Laba Kotor, Laba Bersih, Margin Keuntungan, dan Top Tenongan
    r3_c1, r3_c2, r3_c3 = st.columns(3)
    with r3_c1:
        render_kpi_card(
            "Estimasi Laba Kotor & Laba Bersih",
            format_rupiah(kpi_kini["laba_bersih"]),
            f"Laba Kotor: {format_rupiah(kpi_kini['laba_kotor'])} ({format_delta_periode(kpi_kini['laba_bersih'], kpi_lalu['laba_bersih'])})",
            kategori_warna="keuntungan" if kpi_kini["laba_bersih"] >= 0 else "pengeluaran",
        )
    with r3_c2:
        render_kpi_card(
            "Margin Keuntungan Bersih",
            format_persen(kpi_kini["margin_bersih_persen"], 2),
            f"Margin Kotor: {format_persen(kpi_kini['margin_kotor_persen'], 2)} ({format_delta_periode(kpi_kini['margin_bersih_persen'], kpi_lalu['margin_bersih_persen'], satuan_poin=True)})",
            kategori_warna="keuntungan" if kpi_kini["margin_bersih_persen"] >= 30 else "piutang",
        )
    with r3_c3:
        render_kpi_card(
            "Tenongan Penjualan Tertinggi",
            kpi_kini["top_tenongan_nama"],
            f"Terjual: {format_angka(kpi_kini['top_tenongan_laku'])} pcs ({format_rupiah(kpi_kini['top_tenongan_omzet'])})",
            kategori_warna="pendapatan",
        )

    st.divider()

    # ==========================================================================
    # VISUALISASI INTERAKTIF PLOTLY (HARIAN, BULANAN, & PERFORMA TENONGAN)
    # ==========================================================================
    hpp_aktif = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)
    mode_retur_aktif = str(get_setting("mode_beban_retur", "BEBANKAN_RETUR", db_path=db_path) or "BEBANKAN_RETUR")

    df_ts = query_df(
        """
        SELECT
            tanggal,
            COUNT(DISTINCT id_tenongan) AS jml_mitra,
            SUM(qty_titip) AS qty_titip,
            SUM(CASE WHEN status_konfirmasi = 'TERKONFIRMASI' THEN qty_titip ELSE 0 END) AS titip_terkonfirmasi,
            SUM(qty_laku) AS qty_laku,
            SUM(qty_retur) AS qty_retur,
            SUM(omzet_kotor) AS omzet_kotor,
            SUM(setoran_harapan) AS setoran_harapan,
            SUM(uang_setoran) AS uang_setoran,
            SUM(qty_laku * COALESCE(hpp_unit_saat_itu, ?)) AS hpp_laku,
            SUM(qty_retur * COALESCE(hpp_unit_saat_itu, ?)) AS hpp_retur
        FROM transaksi_harian
        WHERE tanggal BETWEEN ? AND ?
        GROUP BY tanggal
        ORDER BY tanggal ASC;
        """,
        (hpp_aktif, hpp_aktif, tgl_mulai, tgl_selesai),
        db_path=db_path,
    )

    if not df_ts.empty:
        df_ts["total_hpp"] = (
            df_ts["hpp_laku"] + df_ts["hpp_retur"]
            if mode_retur_aktif == "BEBANKAN_RETUR"
            else df_ts["hpp_laku"]
        )
        df_ts["laba_kotor"] = df_ts["setoran_harapan"] - df_ts["total_hpp"]
        df_ts["bulan"] = df_ts["tanggal"].str.slice(0, 7)

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            fig_harian = go.Figure()
            fig_harian.add_trace(
                go.Scatter(
                    x=df_ts["tanggal"],
                    y=df_ts["qty_titip"],
                    mode="lines+markers",
                    name="Roti Dititipkan (pcs)",
                    line=dict(color="#2563eb", width=2.5),
                )
            )
            fig_harian.add_trace(
                go.Scatter(
                    x=df_ts["tanggal"],
                    y=df_ts["qty_laku"],
                    mode="lines+markers",
                    name="Roti Terjual / Laku (pcs)",
                    line=dict(color="#16a34a", width=2.5),
                )
            )
            fig_harian.add_trace(
                go.Bar(
                    x=df_ts["tanggal"],
                    y=df_ts["qty_retur"],
                    name="Retur / Sisa (pcs)",
                    marker_color="#ef4444",
                    opacity=0.45,
                )
            )
            fig_harian.update_layout(
                title="Tren Penjualan Harian: Dititipkan vs Terjual vs Retur",
                xaxis_title="Tanggal",
                yaxis_title="Jumlah Roti (pcs)",
                height=380,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=20, r=20, t=55, b=20),
            )
            st.plotly_chart(fig_harian, use_container_width=True)

        with col_g2:
            fig_profit = go.Figure()
            fig_profit.add_trace(
                go.Scatter(
                    x=df_ts["tanggal"],
                    y=df_ts["omzet_kotor"],
                    mode="lines+markers",
                    name="Omzet Penjualan (Rp)",
                    line=dict(color="#1d4ed8", width=2.5),
                )
            )
            fig_profit.add_trace(
                go.Scatter(
                    x=df_ts["tanggal"],
                    y=df_ts["total_hpp"],
                    mode="lines",
                    name="Biaya Produksi / HPP (Rp)",
                    line=dict(color="#d97706", width=2, dash="dash"),
                )
            )
            fig_profit.add_trace(
                go.Scatter(
                    x=df_ts["tanggal"],
                    y=df_ts["laba_kotor"],
                    mode="lines",
                    fill="tozeroy",
                    name="Estimasi Keuntungan (Rp)",
                    line=dict(color="#16a34a", width=2.5),
                )
            )
            fig_profit.update_layout(
                title="Tren Harian Omzet, Biaya Produksi (HPP) & Keuntungan",
                xaxis_title="Tanggal",
                yaxis_title="Nilai Rupiah (Rp)",
                height=380,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=20, r=20, t=55, b=20),
            )
            st.plotly_chart(fig_profit, use_container_width=True)

        # Baris Grafik Kedua: Tren Bulanan & Peringkat Performa Tenongan
        df_bulanan = (
            df_ts.groupby("bulan", as_index=False)
            .agg(
                hari_aktif=("tanggal", "count"),
                qty_titip=("qty_titip", "sum"),
                qty_laku=("qty_laku", "sum"),
                qty_retur=("qty_retur", "sum"),
                omzet_kotor=("omzet_kotor", "sum"),
                total_hpp=("total_hpp", "sum"),
                laba_kotor=("laba_kotor", "sum"),
            )
        )

        df_perf_tng = query_df(
            """
            SELECT
                t.kode_tenongan,
                t.nama_tenongan,
                COUNT(tr.id_transaksi) AS hari_aktif,
                SUM(tr.qty_titip) AS total_titip,
                SUM(CASE WHEN tr.status_konfirmasi = 'TERKONFIRMASI' THEN tr.qty_titip ELSE 0 END) AS titip_konf,
                SUM(tr.qty_laku) AS total_laku,
                SUM(tr.qty_retur) AS total_retur,
                SUM(tr.omzet_kotor) AS total_omzet,
                SUM(tr.uang_setoran) AS total_setoran,
                SUM(tr.piutang) AS total_piutang
            FROM transaksi_harian tr
            JOIN tenongan t ON tr.id_tenongan = t.id_tenongan
            WHERE tr.tanggal BETWEEN ? AND ?
            GROUP BY tr.id_tenongan
            ORDER BY total_laku DESC, total_omzet DESC;
            """,
            (tgl_mulai, tgl_selesai),
            db_path=db_path,
        )

        col_g3, col_g4 = st.columns(2)
        with col_g3:
            fig_bulanan = go.Figure()
            fig_bulanan.add_trace(
                go.Bar(x=df_bulanan["bulan"], y=df_bulanan["qty_titip"], name="Total Dititipkan (pcs)", marker_color="#60a5fa")
            )
            fig_bulanan.add_trace(
                go.Bar(x=df_bulanan["bulan"], y=df_bulanan["qty_laku"], name="Total Terjual / Laku (pcs)", marker_color="#16a34a")
            )
            fig_bulanan.add_trace(
                go.Bar(x=df_bulanan["bulan"], y=df_bulanan["qty_retur"], name="Total Retur (pcs)", marker_color="#ef4444")
            )
            fig_bulanan.update_layout(
                title="Perbandingan Bulanan: Roti Dititipkan, Terjual & Retur",
                barmode="group",
                xaxis_title="Bulan (YYYY-MM)",
                yaxis_title="Jumlah Roti (pcs)",
                height=400,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=20, r=20, t=55, b=20),
            )
            st.plotly_chart(fig_bulanan, use_container_width=True)

        with col_g4:
            if not df_perf_tng.empty:
                df_perf_tng["sell_through_persen"] = df_perf_tng.apply(
                    lambda r: round((r["total_laku"] / r["titip_konf"] * 100.0), 1) if r["titip_konf"] > 0 else 0.0,
                    axis=1,
                )
                top_n_tng = df_perf_tng.head(15).sort_values("total_laku", ascending=True)
                fig_tng = go.Figure()
                fig_tng.add_trace(
                    go.Bar(
                        y=top_n_tng["nama_tenongan"],
                        x=top_n_tng["total_titip"],
                        orientation="h",
                        name="Dititipkan (pcs)",
                        marker_color="#bfdbfe",
                    )
                )
                fig_tng.add_trace(
                    go.Bar(
                        y=top_n_tng["nama_tenongan"],
                        x=top_n_tng["total_laku"],
                        orientation="h",
                        name="Terjual / Laku (pcs)",
                        marker_color="#1d4ed8",
                        text=[f"{int(l)} pcs ({s:.0f}%)" for l, s in zip(top_n_tng["total_laku"], top_n_tng["sell_through_persen"])],
                        textposition="auto",
                    )
                )
                fig_tng.update_layout(
                    title="Top 15 Tenongan Berdasarkan Volume Laku & Sell-Through Rate",
                    barmode="overlay",
                    xaxis_title="Jumlah Roti (pcs)",
                    yaxis_title="Nama Tenongan",
                    height=400,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(l=20, r=20, t=55, b=20),
                )
                st.plotly_chart(fig_tng, use_container_width=True)

        # Tabel Ringkasan Performa Seluruh Tenongan pada Periode Terpilih + Ekspor
        with st.expander("📋 Lihat Tabel Lengkap Performa Setiap Tenongan & Unduh Data Dashboard", expanded=False):
            st.dataframe(
                df_perf_tng[
                    [
                        "kode_tenongan",
                        "nama_tenongan",
                        "hari_aktif",
                        "total_titip",
                        "total_laku",
                        "total_retur",
                        "sell_through_persen",
                        "total_omzet",
                        "total_setoran",
                        "total_piutang",
                    ]
                ].rename(
                    columns={
                        "kode_tenongan": "Kode",
                        "nama_tenongan": "Nama Tenongan",
                        "hari_aktif": "Hari Aktif",
                        "total_titip": "Total Titip (pcs)",
                        "total_laku": "Total Laku (pcs)",
                        "total_retur": "Total Retur (pcs)",
                        "sell_through_persen": "Sell-Through (%)",
                        "total_omzet": "Omzet (Rp)",
                        "total_setoran": "Setoran Kas (Rp)",
                        "total_piutang": "Piutang (Rp)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            cd1, cd2, _ = st.columns([1.5, 1.5, 2.5])
            with cd1:
                st.download_button(
                    "⬇️ Ekspor Ringkasan Dashboard (.xlsx)",
                    data=dfs_ke_excel_bytes(
                        {
                            "Tren_Harian": df_ts,
                            "Rekap_Bulanan": df_bulanan,
                            "Performa_Tenongan": df_perf_tng,
                        }
                    ),
                    file_name=f"ringkasan_dashboard_taiyaki_{tgl_mulai}_{tgl_selesai}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with cd2:
                st.download_button(
                    "⬇️ Ekspor Tren Harian (.csv)",
                    data=df_ke_csv_bytes(df_ts),
                    file_name=f"tren_harian_taiyaki_{tgl_mulai}_{tgl_selesai}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )


render_halaman_dashboard_utama()