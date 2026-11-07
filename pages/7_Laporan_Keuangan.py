"""
pages/7_Laporan_Keuangan.py
Modul Laporan Keuangan Terpadu:
- Membedakan secara tegas Laporan Laba Rugi (Berbasis Akrual Sederhana)
  dengan Laporan Arus Kas (Berbasis Kas / Cash Basis)
- Rekapitulasi transaksi harian, mingguan, dan bulanan
- Pelacakan saldo piutang per tenongan
- Pencatatan dan pengelolaan Biaya Operasional non-bahan (Tetap & Variabel)
- Ekspor laporan lengkap ke Excel (.xlsx) dan CSV
"""

from datetime import date
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calculations import hitung_laba_rugi_periode
from components import (
    get_active_db_path,
    pilih_rentang_tanggal,
    render_kpi_card,
    tampilkan_header_halaman,
)
from data_processing import df_ke_csv_bytes, dfs_ke_excel_bytes
from database import catat_audit_log, get_connection, get_setting, query_df
from utils import bersihkan_teks, format_angka, format_persen, format_rupiah


def tambah_biaya_operasional(
    tanggal: str,
    kategori_biaya: str,
    deskripsi: str,
    nominal: float,
    jenis_beban: str,
    metode_bayar: str,
    catatan: str = "",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Menyimpan pengeluaran biaya operasional non-bahan dengan ID unik."""
    desc_bersih = bersihkan_teks(deskripsi)
    if not desc_bersih:
        return False, "Deskripsi pengeluaran operasional wajib diisi."
    if nominal <= 0:
        return False, "Nominal biaya operasional harus lebih dari Rp0."

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO biaya_operasional (
                tanggal, kategori_biaya, deskripsi, nominal, jenis_beban, metode_bayar, catatan
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                str(tanggal)[:10],
                kategori_biaya,
                desc_bersih,
                round(float(nominal), 2),
                jenis_beban,
                metode_bayar,
                bersihkan_teks(catatan),
            ),
        )
        id_ops = int(cursor.lastrowid)
        catat_audit_log(
            conn,
            tabel_target="biaya_operasional",
            id_record=id_ops,
            aksi="INSERT",
            data_baru={"tanggal": tanggal, "kategori": kategori_biaya, "deskripsi": desc_bersih, "nominal": nominal},
            keterangan="Input biaya operasional",
        )
        return True, f"Biaya operasional '{desc_bersih}' sebesar {format_rupiah(nominal)} berhasil disimpan."


def hapus_biaya_operasional(id_biaya_ops: int, db_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Menghapus catatan biaya operasional dengan jejak Audit Log."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        eks = cursor.execute(
            "SELECT * FROM biaya_operasional WHERE id_biaya_ops = ?;", (int(id_biaya_ops),)
        ).fetchone()
        if not eks:
            return False, "Data biaya operasional tidak ditemukan."
        catat_audit_log(
            conn,
            tabel_target="biaya_operasional",
            id_record=int(id_biaya_ops),
            aksi="DELETE",
            data_lama=dict(eks),
            keterangan="Hapus biaya operasional",
        )
        cursor.execute("DELETE FROM biaya_operasional WHERE id_biaya_ops = ?;", (int(id_biaya_ops),))
        return True, f"Biaya operasional ID #{id_biaya_ops} berhasil dihapus."


def render_halaman_laporan_keuangan() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Laporan Keuangan (Laba Rugi Akrual & Arus Kas)",
        subjudul="Evaluasi performa keuangan usaha secara akurat dengan pemisahan laporan berbasis akrual dan berbasis kas.",
        ikon="💰",
    )

    bounds = query_df(
        "SELECT MIN(tanggal) AS tmin, MAX(tanggal) AS tmax FROM transaksi_harian;",
        db_path=db_path,
    )
    tmin = bounds.iloc[0]["tmin"] if not bounds.empty else None
    tmax = bounds.iloc[0]["tmax"] if not bounds.empty else None
    tgl_mulai, tgl_selesai = pilih_rentang_tanggal(tmin, tmax, key_prefix="keuangan")

    hpp_default = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)
    mode_retur_aktif = str(get_setting("mode_beban_retur", "BEBANKAN_RETUR", db_path=db_path) or "BEBANKAN_RETUR")

    # 1. Query Agregat Transaksi Konsinyasi Tenongan pada Periode
    with get_connection(db_path) as conn:
        r_trx = conn.execute(
            """
            SELECT
                COALESCE(SUM(qty_titip), 0) AS total_titip,
                COALESCE(SUM(CASE WHEN status_konfirmasi = 'TERKONFIRMASI' THEN qty_titip ELSE 0 END), 0) AS titip_terkonfirmasi,
                COALESCE(SUM(qty_laku), 0) AS total_laku,
                COALESCE(SUM(qty_retur), 0) AS total_retur,
                COALESCE(SUM(omzet_kotor), 0.0) AS omzet_kotor,
                COALESCE(SUM(setoran_harapan), 0.0) AS setoran_harapan,
                COALESCE(SUM(uang_setoran), 0.0) AS uang_setoran,
                COALESCE(SUM(piutang), 0.0) AS total_piutang,
                COALESCE(SUM(qty_laku * COALESCE(hpp_unit_saat_itu, ?)), 0.0) AS hpp_laku_historis,
                COALESCE(SUM(qty_retur * COALESCE(hpp_unit_saat_itu, ?)), 0.0) AS hpp_retur_historis
            FROM transaksi_harian
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (hpp_default, hpp_default, tgl_mulai, tgl_selesai),
        ).fetchone()

        # 2. Query Agregat Belanja Bahan Baku pada Periode
        r_blj = conn.execute(
            """
            SELECT
                COALESCE(SUM(total_pembelian), 0.0) AS total_tagihan_belanja,
                COALESCE(SUM(jumlah_dibayar), 0.0) AS kas_keluar_belanja
            FROM belanja_bahan_baku
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

        # 3. Query Agregat Kulakan pada Periode
        r_kul = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN kategori_transaksi = 'JUAL_GROSIR_TAIYAKI' THEN total_transaksi ELSE 0 END), 0.0) AS omzet_grosir_kulakan,
                COALESCE(SUM(CASE WHEN kategori_transaksi = 'BELI_BARANG_JUAL_KEMBALI' THEN total_transaksi ELSE 0 END), 0.0) AS hpp_trading_kulakan,
                COALESCE(SUM(CASE WHEN kategori_transaksi IN ('JUAL_GROSIR_TAIYAKI', 'TERIMA_PIUTANG_KULAKAN') THEN jumlah_dibayar ELSE 0 END), 0.0) AS kas_masuk_kulakan,
                COALESCE(SUM(CASE WHEN kategori_transaksi IN ('BELI_BARANG_JUAL_KEMBALI', 'BELI_GROSIR_BAHAN', 'BAYAR_UTANG_KULAKAN') THEN jumlah_dibayar ELSE 0 END), 0.0) AS kas_keluar_kulakan
            FROM transaksi_kulakan
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

        # 4. Query Agregat Biaya Operasional pada Periode
        r_ops = conn.execute(
            """
            SELECT
                COALESCE(SUM(nominal), 0.0) AS total_biaya_ops,
                COALESCE(SUM(CASE WHEN jenis_beban = 'TETAP' THEN nominal ELSE 0 END), 0.0) AS biaya_ops_tetap,
                COALESCE(SUM(CASE WHEN jenis_beban = 'VARIABEL' THEN nominal ELSE 0 END), 0.0) AS biaya_ops_variabel
            FROM biaya_operasional
            WHERE tanggal BETWEEN ? AND ?;
            """,
            (tgl_mulai, tgl_selesai),
        ).fetchone()

    # Hitung Laba Rugi & Arus Kas menggunakan mesin calculations.py
    lr = hitung_laba_rugi_periode(
        total_titip_terkonfirmasi=int(r_trx["titip_terkonfirmasi"]),
        total_laku=int(r_trx["total_laku"]),
        total_retur=int(r_trx["total_retur"]),
        omzet_kotor=float(r_trx["omzet_kotor"]) + float(r_kul["omzet_grosir_kulakan"]),
        setoran_harapan=float(r_trx["setoran_harapan"]) + float(r_kul["omzet_grosir_kulakan"]),
        uang_setoran_diterima=float(r_trx["uang_setoran"]),
        hpp_per_pcs=hpp_default,
        biaya_operasional_lain=float(r_ops["total_biaya_ops"]) + float(r_kul["hpp_trading_kulakan"]),
        belanja_bahan_dibayar_kas=float(r_blj["kas_keluar_belanja"]),
        kas_masuk_kulakan=float(r_kul["kas_masuk_kulakan"]),
        kas_keluar_kulakan=float(r_kul["kas_keluar_kulakan"]),
        mode_beban_retur=mode_retur_aktif,
    )

    # Baris KPI Utama Keuangan
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            "Pendapatan Penjualan (Akrual)",
            format_rupiah(lr["pendapatan_akrual"]),
            f"Konsinyasi: {format_rupiah(r_trx['setoran_harapan'])} | Grosir: {format_rupiah(r_kul['omzet_grosir_kulakan'])}",
            kategori_warna="pendapatan",
        )
    with c2:
        render_kpi_card(
            "Estimasi Laba Bersih (Akrual)",
            format_rupiah(lr["laba_bersih"]),
            f"Margin Bersih: {format_persen(lr['margin_bersih_persen'], 2)} | HPP: {format_rupiah(lr['total_hpp_dibebankan'])}",
            kategori_warna="keuntungan" if lr["laba_bersih"] >= 0 else "pengeluaran",
        )
    with c3:
        render_kpi_card(
            "Arus Kas Bersih (Cash Basis)",
            format_rupiah(lr["arus_kas_bersih"]),
            f"Kas Masuk: {format_rupiah(lr['kas_masuk_total'])} | Kas Keluar: {format_rupiah(lr['kas_keluar_total'])}",
            kategori_warna="keuntungan" if lr["arus_kas_bersih"] >= 0 else "pengeluaran",
        )
    with c4:
        render_kpi_card(
            "Saldo Piutang Tenongan",
            format_rupiah(r_trx["total_piutang"]),
            f"Belanja Bahan (Kas): {format_rupiah(r_blj['kas_keluar_belanja'])}",
            kategori_warna="piutang" if float(r_trx["total_piutang"]) > 0 else "netral",
        )

    tab_statement, tab_rekap_periode, tab_piutang, tab_biaya_ops = st.tabs(
        [
            "📑 1. Laporan Laba Rugi (Akrual) vs Arus Kas",
            "📆 2. Rekap Harian, Mingguan & Bulanan",
            "🧾 3. Rekap Setoran & Piutang Tenongan",
            "🛠️ 4. Biaya Operasional Non-Bahan",
        ]
    )

    # ==========================================================================
    # TAB 1: LAPORAN LABA RUGI (AKRUAL) VS LAPORAN ARUS KAS (CASH BASIS)
    # ==========================================================================
    with tab_statement:
        col_akrual, col_kas = st.columns(2)

        with col_akrual:
            st.markdown("#### 📘 Laporan Laba Rugi (Berbasis Akrual Sederhana)")
            st.caption(
                "Mengakui pendapatan saat produk terjual (`Laku`) dan membebankan biaya produksi (`HPP`) "
                "sesuai jumlah produk yang diedarkan, bukan sebesar nilai belanja bahan mentah di gudang."
            )
            df_laba_rugi = pd.DataFrame(
                [
                    ("1. Pendapatan Kotor Konsinyasi Tenongan", format_rupiah(r_trx["omzet_kotor"])),
                    (
                        "2. Potongan Komisi Tenongan",
                        f"-{format_rupiah(max(0.0, float(r_trx['omzet_kotor']) - float(r_trx['setoran_harapan'])))}",
                    ),
                    ("3. Pendapatan Bersih Konsinyasi", format_rupiah(r_trx["setoran_harapan"])),
                    ("4. Pendapatan Penjualan Grosir (Kulakan)", format_rupiah(r_kul["omzet_grosir_kulakan"])),
                    ("TOTAL PENDAPATAN USAHA (AKRUAL)", format_rupiah(lr["pendapatan_akrual"])),
                    ("5. HPP Barang Terjual (Laku × HPP Unit)", f"-{format_rupiah(lr['hpp_barang_terjual'])}"),
                    (
                        f"6. Biaya Kerugian Roti Retur / Spoilage ({mode_retur_aktif})",
                        f"-{format_rupiah(lr['biaya_kerugian_retur'])}"
                        if mode_retur_aktif == "BEBANKAN_RETUR"
                        else "Rp0 (Mode Ritel Murni)",
                    ),
                    ("TOTAL BEBAN POKOK PRODUKSI (HPP)", f"-{format_rupiah(lr['total_hpp_dibebankan'])}"),
                    (
                        f"ESTIMASI LABA KOTOR ({format_persen(lr['margin_kotor_persen'], 2)})",
                        format_rupiah(lr["laba_kotor"]),
                    ),
                    ("7. Beban Barang Trading Kulakan", f"-{format_rupiah(r_kul['hpp_trading_kulakan'])}"),
                    ("8. Biaya Operasional Non-Bahan (Tetap & Variabel)", f"-{format_rupiah(r_ops['total_biaya_ops'])}"),
                    (
                        f"ESTIMASI LABA BERSIH OPERASIONAL ({format_persen(lr['margin_bersih_persen'], 2)})",
                        format_rupiah(lr["laba_bersih"]),
                    ),
                ],
                columns=["Komponen Laporan Laba Rugi (Akrual)", "Nilai (Rp)"],
            )
            st.dataframe(df_laba_rugi, use_container_width=True, hide_index=True)

        with col_kas:
            st.markdown("#### 📗 Laporan Arus Kas (Berbasis Kas / Cash Basis)")
            st.caption(
                "Hanya mencatat uang tunai/transfer yang benar-benar sudah masuk ke kas (`Setoran Diterima`) "
                "dikurangi uang yang benar-benar keluar untuk belanja bahan, kulakan, dan operasional."
            )
            df_arus_kas = pd.DataFrame(
                [
                    ("A. ARUS KAS MASUK (CASH INFLOW)", ""),
                    ("1. Setoran Uang Tunai/Transfer dari Tenongan", format_rupiah(r_trx["uang_setoran"])),
                    ("2. Kas Masuk Penjualan Grosir & Piutang Kulakan", format_rupiah(r_kul["kas_masuk_kulakan"])),
                    ("TOTAL ARUS KAS MASUK", format_rupiah(lr["kas_masuk_total"])),
                    ("B. ARUS KAS KELUAR (CASH OUTFLOW)", ""),
                    ("3. Pembayaran Kas Belanja Bahan Baku", f"-{format_rupiah(r_blj['kas_keluar_belanja'])}"),
                    ("4. Pembayaran Kas Transaksi & Utang Kulakan", f"-{format_rupiah(r_kul['kas_keluar_kulakan'])}"),
                    ("5. Pengeluaran Kas Biaya Operasional", f"-{format_rupiah(r_ops['total_biaya_ops'])}"),
                    ("TOTAL ARUS KAS KELUAR", f"-{format_rupiah(lr['kas_keluar_total'])}"),
                    ("ARUS KAS BERSIH PERIODE (NET CASH FLOW)", format_rupiah(lr["arus_kas_bersih"])),
                    ("Catatan: Saldo Piutang Tenongan Belum Masuk Kas", format_rupiah(r_trx["total_piutang"])),
                ],
                columns=["Komponen Laporan Arus Kas (Cash Basis)", "Nilai (Rp)"],
            )
            st.dataframe(df_arus_kas, use_container_width=True, hide_index=True)

    # ==========================================================================
    # TAB 2: REKAP TRANSAKSI HARIAN, MINGGUAN & BULANAN
    # ==========================================================================
    with tab_rekap_periode:
        st.markdown("#### 📆 Rekapitulasi Performa Keuangan Harian, Mingguan, dan Bulanan")
        df_harian = query_df(
            """
            SELECT
                tanggal,
                COUNT(DISTINCT id_tenongan) AS jml_tenongan,
                SUM(qty_titip) AS total_titip,
                SUM(qty_laku) AS total_laku,
                SUM(qty_retur) AS total_retur,
                SUM(omzet_kotor) AS omzet_kotor,
                SUM(setoran_harapan) AS setoran_harapan,
                SUM(uang_setoran) AS uang_setoran,
                SUM(piutang) AS piutang,
                SUM(qty_laku * COALESCE(hpp_unit_saat_itu, ?)) AS hpp_terjual,
                SUM(qty_retur * COALESCE(hpp_unit_saat_itu, ?)) AS biaya_retur
            FROM transaksi_harian
            WHERE tanggal BETWEEN ? AND ?
            GROUP BY tanggal
            ORDER BY tanggal ASC;
            """,
            (hpp_default, hpp_default, tgl_mulai, tgl_selesai),
            db_path=db_path,
        )

        if df_harian.empty:
            st.info("Belum ada data transaksi harian pada rentang tanggal ini.")
        else:
            df_harian["total_hpp"] = (
                df_harian["hpp_terjual"] + df_harian["biaya_retur"]
                if mode_retur_aktif == "BEBANKAN_RETUR"
                else df_harian["hpp_terjual"]
            )
            df_harian["estimasi_laba_kotor"] = df_harian["setoran_harapan"] - df_harian["total_hpp"]
            df_harian["dt"] = pd.to_datetime(df_harian["tanggal"])
            df_harian["minggu_iso"] = df_harian["dt"].dt.strftime("%Y-W%W")
            df_harian["bulan_iso"] = df_harian["tanggal"].str.slice(0, 7)

            resolusi = st.radio(
                "Pilih Resolusi Waktu Rekapitulasi",
                options=["Harian", "Mingguan", "Bulanan"],
                horizontal=True,
            )

            if resolusi == "Harian":
                df_rekap_tampil = df_harian[
                    [
                        "tanggal",
                        "jml_tenongan",
                        "total_titip",
                        "total_laku",
                        "total_retur",
                        "omzet_kotor",
                        "uang_setoran",
                        "piutang",
                        "total_hpp",
                        "estimasi_laba_kotor",
                    ]
                ].rename(columns={"tanggal": "Periode"})
            elif resolusi == "Mingguan":
                df_rekap_tampil = (
                    df_harian.groupby("minggu_iso", as_index=False)
                    .agg(
                        jml_tenongan=("tanggal", "count"),
                        total_titip=("total_titip", "sum"),
                        total_laku=("total_laku", "sum"),
                        total_retur=("total_retur", "sum"),
                        omzet_kotor=("omzet_kotor", "sum"),
                        uang_setoran=("uang_setoran", "sum"),
                        piutang=("piutang", "sum"),
                        total_hpp=("total_hpp", "sum"),
                        estimasi_laba_kotor=("estimasi_laba_kotor", "sum"),
                    )
                    .rename(columns={"minggu_iso": "Periode"})
                )
            else:
                df_rekap_tampil = (
                    df_harian.groupby("bulan_iso", as_index=False)
                    .agg(
                        jml_tenongan=("tanggal", "count"),
                        total_titip=("total_titip", "sum"),
                        total_laku=("total_laku", "sum"),
                        total_retur=("total_retur", "sum"),
                        omzet_kotor=("omzet_kotor", "sum"),
                        uang_setoran=("uang_setoran", "sum"),
                        piutang=("piutang", "sum"),
                        total_hpp=("total_hpp", "sum"),
                        estimasi_laba_kotor=("estimasi_laba_kotor", "sum"),
                    )
                    .rename(columns={"bulan_iso": "Periode"})
                )

            fig_fin = go.Figure()
            fig_fin.add_trace(
                go.Bar(x=df_rekap_tampil["Periode"], y=df_rekap_tampil["omzet_kotor"], name="Omzet Penjualan (Rp)", marker_color="#2563eb")
            )
            fig_fin.add_trace(
                go.Bar(x=df_rekap_tampil["Periode"], y=df_rekap_tampil["total_hpp"], name="Total HPP Produksi (Rp)", marker_color="#f59e0b")
            )
            fig_fin.add_trace(
                go.Scatter(
                    x=df_rekap_tampil["Periode"],
                    y=df_rekap_tampil["estimasi_laba_kotor"],
                    mode="lines+markers",
                    name="Estimasi Laba Kotor (Rp)",
                    line=dict(color="#16a34a", width=3),
                )
            )
            fig_fin.update_layout(
                title=f"Grafik Omzet, HPP, dan Estimasi Laba Kotor ({resolusi})",
                barmode="group",
                xaxis_title="Periode",
                yaxis_title="Rupiah (Rp)",
                height=380,
                margin=dict(l=20, r=20, t=45, b=20),
            )
            st.plotly_chart(fig_fin, use_container_width=True)

            st.dataframe(
                df_rekap_tampil.rename(
                    columns={
                        "jml_tenongan": "Hari/Mitra Aktif",
                        "total_titip": "Total Titip (pcs)",
                        "total_laku": "Total Laku (pcs)",
                        "total_retur": "Total Retur (pcs)",
                        "omzet_kotor": "Omzet (Rp)",
                        "uang_setoran": "Setoran Kas (Rp)",
                        "piutang": "Piutang (Rp)",
                        "total_hpp": "Total HPP (Rp)",
                        "estimasi_laba_kotor": "Estimasi Laba Kotor (Rp)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            ce1, ce2, _ = st.columns([1.5, 1.5, 2.5])
            with ce1:
                st.download_button(
                    "⬇️ Ekspor Laporan Keuangan Lengkap (.xlsx)",
                    data=dfs_ke_excel_bytes(
                        {
                            "Laba_Rugi_Akrual": df_laba_rugi,
                            "Arus_Kas": df_arus_kas,
                            f"Rekap_{resolusi}": df_rekap_tampil,
                        }
                    ),
                    file_name=f"laporan_keuangan_taiyaki_{tgl_mulai}_{tgl_selesai}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with ce2:
                st.download_button(
                    f"⬇️ Ekspor Rekap {resolusi} (.csv)",
                    data=df_ke_csv_bytes(df_rekap_tampil),
                    file_name=f"rekap_keuangan_{resolusi.lower()}_{tgl_mulai}_{tgl_selesai}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    # ==========================================================================
    # TAB 3: REKAP SETORAN & PIUTANG PER TENONGAN
    # ==========================================================================
    with tab_piutang:
        st.markdown("#### 🧾 Rekapitulasi Setoran & Saldo Piutang per Tenongan")
        df_piutang_tng = query_df(
            """
            SELECT
                t.kode_tenongan,
                t.nama_tenongan,
                COUNT(tr.id_transaksi) AS jml_hari,
                SUM(tr.qty_titip) AS total_titip,
                SUM(tr.qty_laku) AS total_laku,
                SUM(tr.qty_retur) AS total_retur,
                SUM(tr.setoran_harapan) AS tagihan_setoran,
                SUM(tr.uang_setoran) AS setoran_diterima,
                SUM(tr.piutang) AS saldo_piutang
            FROM tenongan t
            JOIN transaksi_harian tr ON t.id_tenongan = tr.id_tenongan
            WHERE tr.tanggal BETWEEN ? AND ?
            GROUP BY t.id_tenongan
            ORDER BY saldo_piutang DESC, total_laku DESC;
            """,
            (tgl_mulai, tgl_selesai),
            db_path=db_path,
        )
        if df_piutang_tng.empty:
            st.info("Tidak ada data transaksi tenongan pada periode ini.")
        else:
            st.dataframe(
                df_piutang_tng.rename(
                    columns={
                        "kode_tenongan": "Kode",
                        "nama_tenongan": "Nama Tenongan",
                        "jml_hari": "Hari Transaksi",
                        "total_titip": "Total Titip",
                        "total_laku": "Total Laku",
                        "total_retur": "Total Retur",
                        "tagihan_setoran": "Hak Usaha / Tagihan (Rp)",
                        "setoran_diterima": "Setoran Masuk Kas (Rp)",
                        "saldo_piutang": "Sisa Piutang (Rp)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    # ==========================================================================
    # TAB 4: PENCATATAN BIAYA OPERASIONAL NON-BAHAN
    # ==========================================================================
    with tab_biaya_ops:
        col_form_ops, col_tabel_ops = st.columns([1.3, 1.7])

        with col_form_ops:
            st.markdown("#### 🛠️ Catat Biaya Operasional")
            st.caption("Catat biaya bensin distribusi, gas/listrik non-resep, upah tenaga kerja, atau perawatan alat.")
            with st.form("form_biaya_operasional", clear_on_submit=True):
                tgl_ops = st.date_input("Tanggal Pengeluaran", value=date.today())
                kat_ops = st.selectbox(
                    "Kategori Biaya",
                    [
                        "Gas & Energi Produksi",
                        "Distribusi & Transportasi",
                        "Tenaga Kerja",
                        "Perawatan Alat & Cetakan",
                        "Kemasan & Perlengkapan Tambahan",
                        "Operasional Lainnya",
                    ],
                )
                jns_beban_ops = st.selectbox(
                    "Klasifikasi Beban (Untuk Analisis BEP)",
                    options=["VARIABEL", "TETAP"],
                    format_func=lambda x: "Biaya Variabel" if x == "VARIABEL" else "Biaya Tetap Bulanan",
                )
                desc_ops = st.text_input("Deskripsi Pengeluaran *", placeholder="Contoh: Isi ulang LPG 3kg 2 tabung")
                nom_ops = st.number_input("Nominal Biaya (Rp) *", min_value=0.0, value=20000.0, step=5000.0)
                met_ops = st.selectbox("Metode Pembayaran", ["Tunai", "Transfer Bank", "QRIS"])
                cat_ops = st.text_input("Catatan Tambahan")

                if st.form_submit_button("💾 Simpan Biaya Operasional", type="primary", use_container_width=True):
                    ok_o, msg_o = tambah_biaya_operasional(
                        tanggal=tgl_ops.isoformat(),
                        kategori_biaya=kat_ops,
                        deskripsi=desc_ops,
                        nominal=float(nom_ops),
                        jenis_beban=jns_beban_ops,
                        metode_bayar=met_ops,
                        catatan=cat_ops,
                        db_path=db_path,
                    )
                    if ok_o:
                        st.success(msg_o)
                        st.rerun()
                    else:
                        st.error(msg_o)

        with col_tabel_ops:
            st.markdown("#### 📋 Riwayat Biaya Operasional Periode Ini")
            df_ops_list = query_df(
                """
                SELECT * FROM biaya_operasional
                WHERE tanggal BETWEEN ? AND ?
                ORDER BY tanggal DESC, id_biaya_ops DESC;
                """,
                (tgl_mulai, tgl_selesai),
                db_path=db_path,
            )
            if df_ops_list.empty:
                st.info("Belum ada catatan biaya operasional non-bahan pada periode ini.")
            else:
                st.dataframe(
                    df_ops_list[
                        ["id_biaya_ops", "tanggal", "kategori_biaya", "deskripsi", "nominal", "jenis_beban", "metode_bayar"]
                    ].rename(
                        columns={
                            "id_biaya_ops": "ID",
                            "tanggal": "Tanggal",
                            "kategori_biaya": "Kategori",
                            "deskripsi": "Deskripsi",
                            "nominal": "Nominal (Rp)",
                            "jenis_beban": "Jenis Beban",
                            "metode_bayar": "Metode",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                id_ops_del = st.selectbox(
                    "Pilih ID Biaya Operasional untuk Dihapus",
                    options=df_ops_list["id_biaya_ops"].tolist(),
                    format_func=lambda x: f"ID #{x} - {df_ops_list.loc[df_ops_list['id_biaya_ops']==x, 'deskripsi'].iloc[0]}",
                )
                if st.button("🗑️ Hapus Biaya Operasional Terpilih"):
                    ok_do, msg_do = hapus_biaya_operasional(int(id_ops_del), db_path=db_path)
                    if ok_do:
                        st.success(msg_do)
                        st.rerun()
                    else:
                        st.error(msg_do)


render_halaman_laporan_keuangan()