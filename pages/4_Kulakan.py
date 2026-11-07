"""
pages/4_Kulakan.py
Modul khusus transaksi Kulakan (Grosir) yang tidak harus terjadi setiap hari.
Memisahkan secara akuntansi antara:
- Pembelian barang jadi untuk dijual kembali (BELI_BARANG_JUAL_KEMBALI)
- Penjualan grosir produk taiyaki di luar tenongan (JUAL_GROSIR_TAIYAKI)
- Pembelian bahan skala grosir khusus (BELI_GROSIR_BAHAN)
- Pelunasan utang kulakan (BAYAR_UTANG_KULAKAN)
- Penerimaan piutang kulakan (TERIMA_PIUTANG_KULAKAN)
"""

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st

from components import (
    get_active_db_path,
    pilih_rentang_tanggal,
    render_kpi_card,
    tampilkan_header_halaman,
)
from data_processing import df_ke_csv_bytes, dfs_ke_excel_bytes
from database import catat_audit_log, get_connection, query_df
from utils import bersihkan_teks, format_angka, format_rupiah, format_tanggal_id

LABEL_KATEGORI_KULAKAN = {
    "BELI_BARANG_JUAL_KEMBALI": "🛒 Beli Barang untuk Dijual Kembali (Trading)",
    "JUAL_GROSIR_TAIYAKI": "📦 Penjualan Grosir / Partai Besar Taiyaki",
    "BELI_GROSIR_BAHAN": "🌾 Kulakan Grosir Bahan Produksi",
    "BAYAR_UTANG_KULAKAN": "💸 Pembayaran Utang Transaksi Kulakan",
    "TERIMA_PIUTANG_KULAKAN": "💰 Penerimaan Piutang Transaksi Kulakan",
}


def buat_nomor_kulakan_otomatis(tanggal_iso: str, db_path: Optional[Path] = None) -> str:
    """Menghasilkan nomor transaksi kulakan unik dengan format KLK-YYYYMMDD-XXX."""
    tgl_compact = tanggal_iso.replace("-", "")[:8]
    prefix = f"KLK-{tgl_compact}-"
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM transaksi_kulakan WHERE nomor_transaksi LIKE ?;",
            (f"{prefix}%",),
        ).fetchone()
        urut = int(row["cnt"]) + 1
        kandidat = f"{prefix}{urut:03d}"
        while conn.execute(
            "SELECT 1 FROM transaksi_kulakan WHERE nomor_transaksi = ?;", (kandidat,)
        ).fetchone():
            urut += 1
            kandidat = f"{prefix}{urut:03d}"
        return kandidat


def simpan_transaksi_kulakan(
    tanggal: str,
    nomor_transaksi: str,
    kategori_transaksi: str,
    nama_item: str,
    qty: float,
    satuan: str,
    harga_unit: float,
    mitra_nama: str,
    metode_bayar: str,
    jumlah_dibayar: float,
    catatan: str,
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Menyimpan transaksi kulakan baru ke SQLite dengan validasi penuh dan Audit Log."""
    nama_bersih = bersihkan_teks(nama_item)
    if not nama_bersih:
        return False, "Nama barang atau produk kulakan wajib diisi."
    if qty <= 0:
        return False, "Jumlah (kuantitas) barang harus lebih dari 0."
    if harga_unit < 0:
        return False, "Harga per unit tidak boleh bernilai negatif."
    if jumlah_dibayar < 0:
        return False, "Jumlah pembayaran tidak boleh bernilai negatif."

    total_trx = round(float(qty) * float(harga_unit), 2)
    dibayar_aktual = round(min(float(jumlah_dibayar), total_trx), 2) if total_trx > 0 else float(jumlah_dibayar)

    if total_trx == 0 or dibayar_aktual >= total_trx - 1e-4:
        status_bayar = "LUNAS"
        dibayar_aktual = total_trx
    elif dibayar_aktual > 0:
        status_bayar = "SEBAGIAN"
    else:
        status_bayar = "BELUM_BAYAR"

    no_trx = bersihkan_teks(nomor_transaksi).upper() or buat_nomor_kulakan_otomatis(tanggal, db_path)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cek = cursor.execute(
            "SELECT 1 FROM transaksi_kulakan WHERE nomor_transaksi = ?;", (no_trx,)
        ).fetchone()
        if cek:
            return False, f"Nomor transaksi '{no_trx}' sudah terdaftar. Gunakan nomor lain."

        cursor.execute(
            """
            INSERT INTO transaksi_kulakan (
                nomor_transaksi, tanggal, kategori_transaksi, nama_item,
                qty, satuan, harga_unit, total_transaksi, mitra_nama,
                metode_bayar, jumlah_dibayar, status_bayar, catatan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                no_trx,
                str(tanggal)[:10],
                kategori_transaksi,
                nama_bersih,
                float(qty),
                bersihkan_teks(satuan) or "pcs",
                float(harga_unit),
                total_trx,
                bersihkan_teks(mitra_nama),
                metode_bayar,
                dibayar_aktual,
                status_bayar,
                bersihkan_teks(catatan),
            ),
        )
        id_baru = int(cursor.lastrowid)
        catat_audit_log(
            conn,
            tabel_target="transaksi_kulakan",
            id_record=id_baru,
            aksi="INSERT",
            data_baru={
                "nomor_transaksi": no_trx,
                "kategori": kategori_transaksi,
                "nama_item": nama_bersih,
                "total_transaksi": total_trx,
                "jumlah_dibayar": dibayar_aktual,
                "status_bayar": status_bayar,
            },
            keterangan=f"Input transaksi kulakan {no_trx}",
        )
        return True, f"Transaksi kulakan '{no_trx}' ({nama_bersih}) berhasil disimpan."


def update_pembayaran_kulakan(
    id_kulakan: int,
    jumlah_dibayar_baru: float,
    catatan_tambahan: str = "",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Memperbarui nominal pembayaran (pelunasan utang/piutang) pada transaksi kulakan."""
    if jumlah_dibayar_baru < 0:
        return False, "Nominal pembayaran tidak boleh negatif."

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        eks = cursor.execute(
            "SELECT * FROM transaksi_kulakan WHERE id_kulakan = ?;", (int(id_kulakan),)
        ).fetchone()
        if not eks:
            return False, "Data transaksi kulakan tidak ditemukan."

        total_trx = float(eks["total_transaksi"])
        dibayar = min(float(jumlah_dibayar_baru), total_trx) if total_trx > 0 else float(jumlah_dibayar_baru)
        if dibayar >= total_trx - 1e-4:
            status_baru = "LUNAS"
            dibayar = total_trx
        elif dibayar > 0:
            status_baru = "SEBAGIAN"
        else:
            status_baru = "BELUM_BAYAR"

        catatan_gabung = eks["catatan"] or ""
        if catatan_tambahan.strip():
            catatan_gabung = f"{catatan_gabung} | {bersihkan_teks(catatan_tambahan)}".strip(" |")

        cursor.execute(
            """
            UPDATE transaksi_kulakan
            SET jumlah_dibayar = ?, status_bayar = ?, catatan = ?
            WHERE id_kulakan = ?;
            """,
            (round(dibayar, 2), status_baru, catatan_gabung, int(id_kulakan)),
        )
        catat_audit_log(
            conn,
            tabel_target="transaksi_kulakan",
            id_record=int(id_kulakan),
            aksi="UPDATE_PAYMENT",
            data_lama={"jumlah_dibayar": eks["jumlah_dibayar"], "status_bayar": eks["status_bayar"]},
            data_baru={"jumlah_dibayar": dibayar, "status_bayar": status_baru},
            keterangan=f"Update pembayaran kulakan {eks['nomor_transaksi']}",
        )
        return True, f"Pembayaran transaksi {eks['nomor_transaksi']} diperbarui menjadi {status_baru}."


def hapus_transaksi_kulakan(
    id_kulakan: int,
    alasan: str,
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Menghapus transaksi kulakan dengan pencatatan Audit Log."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        eks = cursor.execute(
            "SELECT * FROM transaksi_kulakan WHERE id_kulakan = ?;", (int(id_kulakan),)
        ).fetchone()
        if not eks:
            return False, "Transaksi kulakan tidak ditemukan."

        catat_audit_log(
            conn,
            tabel_target="transaksi_kulakan",
            id_record=int(id_kulakan),
            aksi="DELETE",
            data_lama=dict(eks),
            keterangan=bersihkan_teks(alasan) or "Hapus transaksi kulakan",
        )
        cursor.execute("DELETE FROM transaksi_kulakan WHERE id_kulakan = ?;", (int(id_kulakan),))
        return True, f"Transaksi kulakan {eks['nomor_transaksi']} berhasil dihapus."


def render_halaman_kulakan() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Transaksi Kulakan & Grosir",
        subjudul="Pencatatan transaksi kulakan/grosir non-harian yang dipisahkan dari konsinyasi tenongan dan belanja bahan reguler.",
        ikon="📦",
    )

    bounds = query_df(
        "SELECT MIN(tanggal) AS tmin, MAX(tanggal) AS tmax FROM transaksi_kulakan;",
        db_path=db_path,
    )
    tmin = bounds.iloc[0]["tmin"] if not bounds.empty else None
    tmax = bounds.iloc[0]["tmax"] if not bounds.empty else None
    tgl_mulai, tgl_selesai = pilih_rentang_tanggal(tmin, tmax, key_prefix="kulakan")

    df_kul = query_df(
        """
        SELECT *
        FROM transaksi_kulakan
        WHERE tanggal BETWEEN ? AND ?
        ORDER BY tanggal DESC, id_kulakan DESC;
        """,
        (tgl_mulai, tgl_selesai),
        db_path=db_path,
    )

    # Perhitungan KPI terpisah berdasarkan kategori akuntansi kulakan
    if df_kul.empty:
        total_jual_grosir = 0.0
        total_beli_kulakan = 0.0
        sisa_utang_kulakan = 0.0
        sisa_piutang_kulakan = 0.0
    else:
        df_kul["sisa_tagihan"] = (df_kul["total_transaksi"] - df_kul["jumlah_dibayar"]).clip(lower=0.0)
        mask_masuk = df_kul["kategori_transaksi"].isin(["JUAL_GROSIR_TAIYAKI", "TERIMA_PIUTANG_KULAKAN"])
        mask_keluar = df_kul["kategori_transaksi"].isin(
            ["BELI_BARANG_JUAL_KEMBALI", "BELI_GROSIR_BAHAN", "BAYAR_UTANG_KULAKAN"]
        )
        total_jual_grosir = float(df_kul.loc[mask_masuk, "total_transaksi"].sum())
        total_beli_kulakan = float(df_kul.loc[mask_keluar, "total_transaksi"].sum())
        sisa_piutang_kulakan = float(
            df_kul.loc[df_kul["kategori_transaksi"] == "JUAL_GROSIR_TAIYAKI", "sisa_tagihan"].sum()
        )
        sisa_utang_kulakan = float(
            df_kul.loc[
                df_kul["kategori_transaksi"].isin(["BELI_BARANG_JUAL_KEMBALI", "BELI_GROSIR_BAHAN"]),
                "sisa_tagihan",
            ].sum()
        )

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Pembelian Kulakan (Grosir)",
            format_rupiah(total_beli_kulakan),
            "Barang jual kembali & kulakan grosir",
            kategori_warna="pengeluaran",
        )
    with k2:
        render_kpi_card(
            "Penjualan Grosir Taiyaki",
            format_rupiah(total_jual_grosir),
            "Terpisah dari omzet titip jual tenongan",
            kategori_warna="pendapatan",
        )
    with k3:
        render_kpi_card(
            "Sisa Utang Kulakan",
            format_rupiah(sisa_utang_kulakan),
            "Kewajiban ke pemasok kulakan",
            kategori_warna="piutang" if sisa_utang_kulakan > 0 else "keuntungan",
        )
    with k4:
        render_kpi_card(
            "Sisa Piutang Grosir",
            format_rupiah(sisa_piutang_kulakan),
            "Tagihan ke pelanggan grosir",
            kategori_warna="piutang" if sisa_piutang_kulakan > 0 else "netral",
        )

    tab_input, tab_riwayat, tab_rekap = st.tabs(
        [
            "➕ Catat Transaksi Kulakan Baru",
            "📋 Riwayat, Filter & Penyelesaian Utang/Piutang",
            "📊 Ringkasan Bulanan & Ekspor Excel",
        ]
    )

    # ==========================================================================
    # TAB 1: FORM INPUT TRANSAKSI KULAKAN
    # ==========================================================================
    with tab_input:
        st.markdown("#### ➕ Form Pencatatan Transaksi Kulakan")
        st.caption(
            "Pilih jenis transaksi dengan tepat agar omzet penjualan grosir tidak tercampur "
            "dengan omzet konsinyasi tenongan dan tidak terhitung ganda dengan belanja bahan reguler."
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            tgl_in = st.date_input("Tanggal Transaksi", value=date.today(), key="kul_tgl_in")
            no_otomatis = buat_nomor_kulakan_otomatis(tgl_in.isoformat(), db_path=db_path)
            no_trx_in = st.text_input(
                "Nomor Transaksi (Otomatis / Kustom)",
                value=no_otomatis,
                help="Nomor unik otomatis untuk mencegah pencatatan ganda.",
            )
        with c2:
            kat_in = st.selectbox(
                "Kategori / Jenis Transaksi *",
                options=list(LABEL_KATEGORI_KULAKAN.keys()),
                format_func=lambda k: LABEL_KATEGORI_KULAKAN[k],
            )
            nama_item_in = st.text_input(
                "Nama Barang / Produk *",
                placeholder="Contoh: Snack Pendamping / Pesanan Taiyaki 150 pcs",
            )
        with c3:
            mitra_in = st.text_input(
                "Nama Pemasok / Pelanggan",
                placeholder="Contoh: Agen Grosir Pasar Gede / Ibu Rina",
            )
            metode_in = st.selectbox(
                "Metode Pembayaran",
                options=["Tunai", "Transfer Bank", "QRIS", "Tempo / Utang"],
            )

        cq1, cq2, cq3, cq4 = st.columns(4)
        with cq1:
            qty_in = st.number_input("Jumlah (Kuantitas) *", min_value=0.1, value=1.0, step=1.0)
        with cq2:
            satuan_in = st.selectbox(
                "Satuan",
                options=["pcs", "pak", "dus", "kg", "bal", "ikat", "paket"],
            )
        with cq3:
            harga_in = st.number_input("Harga per Unit (Rp) *", min_value=0, value=15000, step=500)
        with cq4:
            total_hitung = round(float(qty_in) * float(harga_in), 2)
            st.metric("Total Nilai Transaksi", format_rupiah(total_hitung))

        cb_lunas, cb_nominal = st.columns([1.5, 2.5])
        with cb_lunas:
            opsi_bayar = st.radio(
                "Status Pembayaran Saat Ini",
                options=["LUNAS", "SEBAGIAN", "BELUM_BAYAR"],
                horizontal=True,
            )
        with cb_nominal:
            if opsi_bayar == "LUNAS":
                dibayar_in = total_hitung
                st.caption(f"Uang dibayar/diterima penuh: **{format_rupiah(dibayar_in)}**")
            elif opsi_bayar == "BELUM_BAYAR":
                dibayar_in = 0.0
                st.caption(f"Belum ada kas keluar/masuk. Tercatat sebagai Utang/Piutang sebesar **{format_rupiah(total_hitung)}**.")
            else:
                dibayar_in = float(
                    st.number_input(
                        "Masukkan Nominal yang Sudah Dibayar / Diterima (Rp)",
                        min_value=0.0,
                        max_value=float(total_hitung),
                        value=float(total_hitung) / 2.0,
                        step=1000.0,
                    )
                )

        catatan_in = st.text_input("Catatan Transaksi", placeholder="Keterangan tambahan jika ada...")

        if st.button("💾 Simpan Transaksi Kulakan", type="primary", use_container_width=True):
            ok_k, msg_k = simpan_transaksi_kulakan(
                tanggal=tgl_in.isoformat(),
                nomor_transaksi=no_trx_in,
                kategori_transaksi=kat_in,
                nama_item=nama_item_in,
                qty=float(qty_in),
                satuan=satuan_in,
                harga_unit=float(harga_in),
                mitra_nama=mitra_in,
                metode_bayar=metode_in,
                jumlah_dibayar=float(dibayar_in),
                catatan=catatan_in,
                db_path=db_path,
            )
            if ok_k:
                st.success(msg_k)
                st.rerun()
            else:
                st.error(msg_k)

    # ==========================================================================
    # TAB 2: RIWAYAT TRANSAKSI, FILTER & PENYELESAIAN UTANG/PIUTANG
    # ==========================================================================
    with tab_riwayat:
        cf1, cf2, cf3 = st.columns(3)
        with cf1:
            f_kat = st.selectbox(
                "Filter Jenis Transaksi",
                options=["SEMUA"] + list(LABEL_KATEGORI_KULAKAN.keys()),
                format_func=lambda k: "Semua Kategori" if k == "SEMUA" else LABEL_KATEGORI_KULAKAN[k],
            )
        with cf2:
            f_stat = st.selectbox(
                "Filter Status Pembayaran",
                options=["SEMUA", "LUNAS", "SEBAGIAN", "BELUM_BAYAR"],
            )
        with cf3:
            f_cari = st.text_input("Cari Nama Barang / Mitra / Nomor", placeholder="Ketik kata kunci...")

        df_view = df_kul.copy()
        if not df_view.empty:
            if f_kat != "SEMUA":
                df_view = df_view[df_view["kategori_transaksi"] == f_kat]
            if f_stat != "SEMUA":
                df_view = df_view[df_view["status_bayar"] == f_stat]
            if f_cari.strip():
                kw = f_cari.strip().lower()
                df_view = df_view[
                    df_view["nama_item"].str.lower().str.contains(kw, na=False)
                    | df_view["mitra_nama"].str.lower().str.contains(kw, na=False)
                    | df_view["nomor_transaksi"].str.lower().str.contains(kw, na=False)
                ]

        if df_view.empty:
            st.info("Belum ada riwayat transaksi kulakan pada rentang dan filter yang dipilih.")
        else:
            df_view["Jenis Transaksi"] = df_view["kategori_transaksi"].map(LABEL_KATEGORI_KULAKAN)
            st.dataframe(
                df_view[
                    [
                        "id_kulakan",
                        "tanggal",
                        "nomor_transaksi",
                        "Jenis Transaksi",
                        "nama_item",
                        "qty",
                        "satuan",
                        "harga_unit",
                        "total_transaksi",
                        "jumlah_dibayar",
                        "sisa_tagihan",
                        "status_bayar",
                        "mitra_nama",
                        "metode_bayar",
                        "catatan",
                    ]
                ].rename(
                    columns={
                        "id_kulakan": "ID",
                        "tanggal": "Tanggal",
                        "nomor_transaksi": "No. Transaksi",
                        "nama_item": "Nama Barang",
                        "qty": "Qty",
                        "satuan": "Satuan",
                        "harga_unit": "Harga/Unit (Rp)",
                        "total_transaksi": "Total (Rp)",
                        "jumlah_dibayar": "Dibayar (Rp)",
                        "sisa_tagihan": "Sisa Utang/Piutang (Rp)",
                        "status_bayar": "Status",
                        "mitra_nama": "Pemasok/Pelanggan",
                        "metode_bayar": "Metode",
                        "catatan": "Catatan",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.divider()
            col_upd, col_del = st.columns(2)
            with col_upd:
                st.markdown("##### 💳 Update Pelunasan Utang / Piutang Kulakan")
                opsi_id_k = df_view["id_kulakan"].tolist()
                peta_k = {
                    int(r["id_kulakan"]): f"{r['nomor_transaksi']} - {r['nama_item']} (Total: {format_rupiah(r['total_transaksi'])}, Status: {r['status_bayar']})"
                    for _, r in df_view.iterrows()
                }
                id_k_pilih = st.selectbox("Pilih Transaksi", options=opsi_id_k, format_func=lambda x: peta_k[x])
                baris_k = df_view[df_view["id_kulakan"] == id_k_pilih].iloc[0]
                nom_baru = st.number_input(
                    "Total Akumulasi yang Sudah Dibayar/Diterima (Rp)",
                    min_value=0.0,
                    max_value=float(baris_k["total_transaksi"]),
                    value=float(baris_k["total_transaksi"]),
                    step=1000.0,
                )
                cat_pelunasan = st.text_input("Catatan Pelunasan", placeholder="Misal: Dilunasi tunai tanggal...")
                if st.button("✅ Simpan Update Pembayaran", use_container_width=True):
                    ok_up, msg_up = update_pembayaran_kulakan(
                        id_kulakan=int(id_k_pilih),
                        jumlah_dibayar_baru=float(nom_baru),
                        catatan_tambahan=cat_pelunasan,
                        db_path=db_path,
                    )
                    if ok_up:
                        st.success(msg_up)
                        st.rerun()
                    else:
                        st.error(msg_up)

            with col_del:
                st.markdown("##### 🗑️ Hapus Transaksi Kulakan")
                id_k_hapus = st.selectbox(
                    "Pilih Transaksi yang Akan Dihapus",
                    options=opsi_id_k,
                    format_func=lambda x: peta_k[x],
                    key="del_kul_id",
                )
                alasan_k = st.text_input("Alasan Penghapusan", value="Salah input transaksi kulakan")
                konf_k = st.checkbox("Konfirmasi hapus transaksi kulakan ini")
                if st.button("🗑️ Hapus Data Kulakan", type="secondary", use_container_width=True):
                    if not konf_k:
                        st.warning("Centang konfirmasi terlebih dahulu.")
                    else:
                        ok_dk, msg_dk = hapus_transaksi_kulakan(int(id_k_hapus), alasan_k, db_path=db_path)
                        if ok_dk:
                            st.success(msg_dk)
                            st.rerun()
                        else:
                            st.error(msg_dk)

    # ==========================================================================
    # TAB 3: RINGKASAN BULANAN & EKSPOR EXCEL
    # ==========================================================================
    with tab_rekap:
        st.markdown("#### 📊 Ringkasan Bulanan Transaksi Kulakan")
        df_all_kul = query_df("SELECT * FROM transaksi_kulakan ORDER BY tanggal ASC;", db_path=db_path)
        if df_all_kul.empty:
            st.info("Belum ada data transaksi kulakan untuk diringkas.")
        else:
            df_all_kul["bulan"] = df_all_kul["tanggal"].str.slice(0, 7)
            df_all_kul["Label Kategori"] = df_all_kul["kategori_transaksi"].map(LABEL_KATEGORI_KULAKAN)
            rekap_bulanan = (
                df_all_kul.groupby(["bulan", "Label Kategori"], as_index=False)
                .agg(
                    jumlah_transaksi=("id_kulakan", "count"),
                    total_nilai=("total_transaksi", "sum"),
                    total_dibayar=("jumlah_dibayar", "sum"),
                )
            )
            rekap_bulanan["sisa_belum_lunas"] = rekap_bulanan["total_nilai"] - rekap_bulanan["total_dibayar"]

            fig_kul = px.bar(
                rekap_bulanan,
                x="bulan",
                y="total_nilai",
                color="Label Kategori",
                barmode="group",
                title="Perbandingan Nilai Transaksi Kulakan per Bulan & Kategori",
                labels={"bulan": "Bulan (YYYY-MM)", "total_nilai": "Total Transaksi (Rp)"},
            )
            st.plotly_chart(fig_kul, use_container_width=True)

            st.dataframe(
                rekap_bulanan.rename(
                    columns={
                        "bulan": "Bulan",
                        "jumlah_transaksi": "Jml Transaksi",
                        "total_nilai": "Total Transaksi (Rp)",
                        "total_dibayar": "Sudah Dibayar (Rp)",
                        "sisa_belum_lunas": "Sisa Utang/Piutang (Rp)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            ce1, ce2, _ = st.columns([1.4, 1.4, 2.5])
            with ce1:
                st.download_button(
                    "⬇️ Ekspor Kulakan ke Excel (.xlsx)",
                    data=dfs_ke_excel_bytes(
                        {"Riwayat_Kulakan": df_all_kul, "Rekap_Bulanan_Kulakan": rekap_bulanan}
                    ),
                    file_name="laporan_kulakan_taiyaki.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with ce2:
                st.download_button(
                    "⬇️ Ekspor Kulakan ke CSV",
                    data=df_ke_csv_bytes(df_all_kul),
                    file_name="laporan_kulakan_taiyaki.csv",
                    mime="text/csv",
                    use_container_width=True,
                )


render_halaman_kulakan()