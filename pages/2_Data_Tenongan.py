"""
pages/2_Data_Tenongan.py
Halaman pengelolaan daftar mitra tenongan secara dinamis:
- Ringkasan KPI & tabel performa setiap tenongan (titip, laku, retur, STR, omzet, setoran, piutang)
- Pencarian, filter status aktif/nonaktif, dan filter pola penitipan
- Form tambah tenongan baru & form edit/nonaktifkan tenongan (tanpa menghapus riwayat transaksi)
- Inspeksi riwayat transaksi & grafik performa per tenongan
- Impor daftar tenongan dari Excel/CSV & ekspor data
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from components import get_active_db_path, render_kpi_card, tampilkan_header_halaman
from data_processing import (
    df_ke_csv_bytes,
    dfs_ke_excel_bytes,
    get_daftar_tenongan_lengkap,
    get_transaksi_harian_df,
    impor_daftar_tenongan_df,
    tambah_tenongan_baru,
    update_data_tenongan,
)
from utils import format_angka, format_persen, format_rupiah, format_tanggal_id


def render_halaman_tenongan() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Data Mitra Tenongan",
        subjudul="Kelola daftar tenongan, skema harga/komisi, status aktif, dan pantau performa serta piutang per mitra.",
        ikon="🏪",
    )

    df_all = get_daftar_tenongan_lengkap(db_path=db_path, hanya_aktif=False)

    # Baris KPI Ringkasan Tenongan
    total_mitra = len(df_all)
    mitra_aktif = int((df_all["status_aktif"] == 1).sum()) if not df_all.empty else 0
    mitra_nonaktif = total_mitra - mitra_aktif
    total_omzet_all = float(df_all["total_omzet"].sum()) if not df_all.empty else 0.0
    total_piutang_all = float(df_all["total_piutang"].sum()) if not df_all.empty else 0.0

    top_nama = "-"
    top_laku = 0
    if not df_all.empty and df_all["total_laku"].sum() > 0:
        baris_top = df_all.sort_values("total_laku", ascending=False).iloc[0]
        top_nama = str(baris_top["nama_tenongan"])
        top_laku = int(baris_top["total_laku"])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            "Total Mitra Tenongan",
            f"{mitra_aktif} Aktif",
            f"Total terdaftar: {total_mitra} ({mitra_nonaktif} nonaktif)",
            kategori_warna="netral",
        )
    with c2:
        render_kpi_card(
            "Tenongan Penjualan Tertinggi",
            top_nama,
            f"Total terjual: {format_angka(top_laku)} pcs",
            kategori_warna="keuntungan",
        )
    with c3:
        render_kpi_card(
            "Akumulasi Omzet Tenongan",
            format_rupiah(total_omzet_all),
            f"Total laku: {format_angka(df_all['total_laku'].sum() if not df_all.empty else 0)} pcs",
            kategori_warna="pendapatan",
        )
    with c4:
        render_kpi_card(
            "Total Piutang Setoran",
            format_rupiah(total_piutang_all),
            "Selisih penjualan bersih vs uang disetor",
            kategori_warna="piutang" if total_piutang_all > 0 else "keuntungan",
        )

    tab_daftar, tab_kelola, tab_detail, tab_impor = st.tabs(
        [
            "📋 Daftar & Ringkasan Tenongan",
            "➕ Tambah / Edit Tenongan",
            "📈 Riwayat & Performa per Tenongan",
            "📥 Impor Daftar Tenongan (Excel/CSV)",
        ]
    )

    # ==========================================================================
    # TAB 1: DAFTAR & RINGKASAN TENONGAN
    # ==========================================================================
    with tab_daftar:
        col_f1, col_f2, col_f3 = st.columns([2.0, 1.2, 1.2])
        with col_f1:
            kata_kunci = st.text_input(
                "Cari Nama / Kode / Pemilik / Lokasi Tenongan",
                placeholder="Ketik misal: BINTANG, ALFI, MAJU, TNG-001...",
                key="tng_search_kw",
            )
        with col_f2:
            filter_status = st.selectbox(
                "Filter Status",
                options=["SEMUA", "AKTIF", "NONAKTIF"],
                index=0,
                key="tng_filter_status",
            )
        with col_f3:
            filter_pola = st.selectbox(
                "Filter Pola Titip",
                options=["SEMUA", "HARIAN", "SELANG_SELING", "INSIDENTAL"],
                index=0,
                key="tng_filter_pola",
            )

        df_filtered = get_daftar_tenongan_lengkap(
            db_path=db_path,
            hanya_aktif=(filter_status == "AKTIF"),
            kata_kunci=kata_kunci,
            filter_pola=filter_pola,
        )
        if filter_status == "NONAKTIF" and not df_filtered.empty:
            df_filtered = df_filtered[df_filtered["status_aktif"] == 0]

        if df_filtered.empty:
            st.info("Tidak ada data tenongan yang cocok dengan filter pencarian.")
        else:
            df_tampil = df_filtered.copy()
            df_tampil["Status"] = df_tampil["status_aktif"].apply(lambda x: "Aktif" if x == 1 else "Nonaktif")
            kolom_tabel = [
                "kode_tenongan",
                "nama_tenongan",
                "Status",
                "pola_titip",
                "kuota_default",
                "harga_default",
                "komisi_default",
                "total_hari_transaksi",
                "total_titip",
                "total_laku",
                "total_retur",
                "sell_through_persen",
                "total_omzet",
                "total_uang_setoran",
                "total_piutang",
                "nama_pemilik",
                "kontak",
                "alamat_lokasi",
            ]
            st.dataframe(
                df_tampil[kolom_tabel].rename(
                    columns={
                        "kode_tenongan": "Kode",
                        "nama_tenongan": "Nama Tenongan",
                        "pola_titip": "Pola Titip",
                        "kuota_default": "Kuota (pcs)",
                        "harga_default": "Harga Jual (Rp)",
                        "komisi_default": "Komisi (Rp)",
                        "total_hari_transaksi": "Hari Aktif",
                        "total_titip": "Total Titip",
                        "total_laku": "Total Laku",
                        "total_retur": "Total Retur",
                        "sell_through_persen": "Sell-Through (%)",
                        "total_omzet": "Omzet (Rp)",
                        "total_uang_setoran": "Setoran Masuk (Rp)",
                        "total_piutang": "Piutang (Rp)",
                        "nama_pemilik": "Pemilik",
                        "kontak": "Kontak",
                        "alamat_lokasi": "Lokasi",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            col_dl1, col_dl2, _ = st.columns([1.3, 1.3, 3.0])
            with col_dl1:
                st.download_button(
                    "⬇️ Ekspor ke Excel (.xlsx)",
                    data=dfs_ke_excel_bytes({"Data_Tenongan": df_tampil[kolom_tabel]}),
                    file_name="ringkasan_data_tenongan.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with col_dl2:
                st.download_button(
                    "⬇️ Ekspor ke CSV",
                    data=df_ke_csv_bytes(df_tampil[kolom_tabel]),
                    file_name="ringkasan_data_tenongan.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    # ==========================================================================
    # TAB 2: TAMBAH & EDIT / NONAKTIFKAN TENONGAN
    # ==========================================================================
    with tab_kelola:
        col_tambah, col_edit = st.columns(2)

        with col_tambah:
            st.markdown("#### ➕ Tambah Tenongan Baru")
            st.caption("Mendukung penambahan mitra baru secara dinamis tanpa mengubah kode program.")
            with st.form("form_tambah_tenongan", clear_on_submit=True):
                nama_baru = st.text_input("Nama Tenongan *", placeholder="Contoh: BERKAH JAYA")
                kode_baru = st.text_input(
                    "Kode Tenongan (Opsional - Otomatis jika kosong)",
                    placeholder="Kosongkan untuk kode otomatis (misal TNG-034)",
                )
                ca1, ca2 = st.columns(2)
                with ca1:
                    pemilik_baru = st.text_input("Nama Pemilik / Penanggung Jawab")
                    pola_baru = st.selectbox("Pola Penitipan", ["HARIAN", "SELANG_SELING", "INSIDENTAL"])
                    harga_baru = st.number_input("Harga Jual per Buah (Rp)", min_value=0, value=2000, step=100)
                with ca2:
                    kontak_baru = st.text_input("Nomor HP / WhatsApp")
                    kuota_baru = st.number_input("Kuota Titip Default (pcs)", min_value=1, value=4, step=1)
                    komisi_baru = st.number_input("Potongan Komisi per Buah (Rp)", min_value=0, value=0, step=100)

                lokasi_baru = st.text_input("Alamat / Titik Lokasi Tenongan")
                catatan_baru = st.text_area("Catatan Tambahan", height=68)
                tombol_tambah = st.form_submit_button("💾 Simpan Tenongan Baru", type="primary", use_container_width=True)

                if tombol_tambah:
                    ok, msg, _ = tambah_tenongan_baru(
                        nama_tenongan=nama_baru,
                        kode_tenongan=kode_baru,
                        nama_pemilik=pemilik_baru,
                        kontak=kontak_baru,
                        alamat_lokasi=lokasi_baru,
                        harga_default=float(harga_baru),
                        komisi_default=float(komisi_baru),
                        pola_titip=pola_baru,
                        kuota_default=int(kuota_baru),
                        status_aktif=1,
                        catatan=catatan_baru,
                        db_path=db_path,
                    )
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

        with col_edit:
            st.markdown("#### ✏️ Edit Profil / Nonaktifkan Tenongan")
            st.caption("Menonaktifkan tenongan hanya menyembunyikannya dari form input harian baru tanpa menghapus riwayat transaksinya.")

            if df_all.empty:
                st.info("Belum ada data tenongan untuk diedit.")
            else:
                opsi_id = df_all["id_tenongan"].tolist()
                peta_label = {
                    int(r["id_tenongan"]): f"{r['kode_tenongan']} - {r['nama_tenongan']} ({'Aktif' if r['status_aktif']==1 else 'Nonaktif'})"
                    for _, r in df_all.iterrows()
                }
                id_pilih = st.selectbox(
                    "Pilih Tenongan yang Akan Diedit",
                    options=opsi_id,
                    format_func=lambda x: peta_label.get(x, str(x)),
                    key="edit_pilih_tenongan_id",
                )
                baris_pilih = df_all[df_all["id_tenongan"] == id_pilih].iloc[0]

                with st.form("form_edit_tenongan"):
                    nama_ed = st.text_input("Nama Tenongan *", value=str(baris_pilih["nama_tenongan"]))
                    ce1, ce2 = st.columns(2)
                    with ce1:
                        pemilik_ed = st.text_input("Nama Pemilik", value=str(baris_pilih["nama_pemilik"] or ""))
                        pola_opsi = ["HARIAN", "SELANG_SELING", "INSIDENTAL"]
                        idx_pola = pola_opsi.index(baris_pilih["pola_titip"]) if baris_pilih["pola_titip"] in pola_opsi else 0
                        pola_ed = st.selectbox("Pola Penitipan", pola_opsi, index=idx_pola)
                        harga_ed = st.number_input(
                            "Harga Jual Default (Rp)",
                            min_value=0,
                            value=int(baris_pilih["harga_default"]),
                            step=100,
                        )
                    with ce2:
                        kontak_ed = st.text_input("Kontak", value=str(baris_pilih["kontak"] or ""))
                        kuota_ed = st.number_input(
                            "Kuota Titip Default (pcs)",
                            min_value=0,
                            value=int(baris_pilih["kuota_default"]),
                            step=1,
                        )
                        komisi_ed = st.number_input(
                            "Komisi per Buah (Rp)",
                            min_value=0,
                            value=int(baris_pilih["komisi_default"]),
                            step=100,
                        )

                    lokasi_ed = st.text_input("Alamat / Lokasi", value=str(baris_pilih["alamat_lokasi"] or ""))
                    status_ed = st.toggle(
                        "Status Tenongan Aktif (Menerima Titipan Harian)",
                        value=bool(baris_pilih["status_aktif"] == 1),
                    )
                    catatan_ed = st.text_area("Catatan", value=str(baris_pilih["catatan"] or ""), height=68)
                    tombol_update = st.form_submit_button("💾 Simpan Perubahan", type="primary", use_container_width=True)

                    if tombol_update:
                        ok_u, msg_u = update_data_tenongan(
                            id_tenongan=int(id_pilih),
                            nama_tenongan=nama_ed,
                            nama_pemilik=pemilik_ed,
                            kontak=kontak_ed,
                            alamat_lokasi=lokasi_ed,
                            tgl_mulai=str(baris_pilih["tgl_mulai"] or ""),
                            harga_default=float(harga_ed),
                            komisi_default=float(komisi_ed),
                            pola_titip=pola_ed,
                            kuota_default=int(kuota_ed),
                            status_aktif=1 if status_ed else 0,
                            catatan=catatan_ed,
                            db_path=db_path,
                        )
                        if ok_u:
                            st.success(msg_u)
                            st.rerun()
                        else:
                            st.error(msg_u)

    # ==========================================================================
    # TAB 3: RIWAYAT TRANSAKSI & GRAFIK PER TENONGAN
    # ==========================================================================
    with tab_detail:
        if df_all.empty:
            st.info("Belum ada data tenongan.")
        else:
            opsi_id_det = df_all["id_tenongan"].tolist()
            peta_label_det = {
                int(r["id_tenongan"]): f"{r['kode_tenongan']} - {r['nama_tenongan']} (Laku: {int(r['total_laku'])} pcs)"
                for _, r in df_all.iterrows()
            }
            id_det = st.selectbox(
                "Pilih Tenongan untuk Melihat Riwayat & Grafik Performa",
                options=opsi_id_det,
                format_func=lambda x: peta_label_det.get(x, str(x)),
                key="detail_pilih_tenongan_id",
            )
            info_tng = df_all[df_all["id_tenongan"] == id_det].iloc[0]
            df_trx_tng = get_transaksi_harian_df(id_tenongan=int(id_det), db_path=db_path)

            k1, k2, k3, k4 = st.columns(4)
            with k1:
                render_kpi_card(
                    "Total Titip vs Laku",
                    f"{format_angka(info_tng['total_laku'])} / {format_angka(info_tng['total_titip'])} pcs",
                    f"Retur: {format_angka(info_tng['total_retur'])} pcs | Aktif: {info_tng['total_hari_transaksi']} hari",
                    kategori_warna="pendapatan",
                )
            with k2:
                render_kpi_card(
                    "Sell-Through Rate",
                    format_persen(info_tng["sell_through_persen"]),
                    f"Return Rate: {format_persen(info_tng['return_rate_persen'])}",
                    kategori_warna="keuntungan" if info_tng["sell_through_persen"] >= 75 else "piutang",
                )
            with k3:
                render_kpi_card(
                    "Total Omzet & Setoran",
                    format_rupiah(info_tng["total_uang_setoran"]),
                    f"Omzet Kotor: {format_rupiah(info_tng['total_omzet'])}",
                    kategori_warna="keuntungan",
                )
            with k4:
                render_kpi_card(
                    "Sisa Piutang Tenongan",
                    format_rupiah(info_tng["total_piutang"]),
                    f"Belum Konfirmasi: {format_angka(info_tng['belum_konfirmasi_pcs'])} pcs",
                    kategori_warna="piutang" if info_tng["total_piutang"] > 0 else "netral",
                )

            if df_trx_tng.empty:
                st.info(f"Tenongan **{info_tng['nama_tenongan']}** belum memiliki riwayat transaksi harian.")
            else:
                df_plot = df_trx_tng.sort_values("tanggal", ascending=True)
                fig = go.Figure()
                fig.add_trace(
                    go.Bar(
                        x=df_plot["tanggal"],
                        y=df_plot["qty_titip"],
                        name="Titip (pcs)",
                        marker_color="#93c5fd",
                    )
                )
                fig.add_trace(
                    go.Bar(
                        x=df_plot["tanggal"],
                        y=df_plot["qty_laku"],
                        name="Laku (pcs)",
                        marker_color="#1d4ed8",
                    )
                )
                fig.update_layout(
                    title=f"Tren Harian Titip vs Laku - {info_tng['nama_tenongan']}",
                    barmode="group",
                    xaxis_title="Tanggal",
                    yaxis_title="Jumlah Roti (pcs)",
                    height=360,
                    margin=dict(l=20, r=20, t=45, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                st.markdown("##### Riwayat Transaksi Lengkap")
                df_hist = df_trx_tng[
                    [
                        "tanggal",
                        "qty_titip",
                        "qty_laku",
                        "qty_retur",
                        "omzet_kotor",
                        "uang_setoran",
                        "piutang",
                        "status_konfirmasi",
                        "status_pembayaran",
                        "catatan",
                    ]
                ].copy()
                df_hist["tanggal"] = df_hist["tanggal"].apply(lambda d: format_tanggal_id(d, singkat=True))
                st.dataframe(
                    df_hist.rename(
                        columns={
                            "tanggal": "Tanggal",
                            "qty_titip": "Titip",
                            "qty_laku": "Laku",
                            "qty_retur": "Retur",
                            "omzet_kotor": "Omzet (Rp)",
                            "uang_setoran": "Setoran (Rp)",
                            "piutang": "Piutang (Rp)",
                            "status_konfirmasi": "Status Barang",
                            "status_pembayaran": "Status Bayar",
                            "catatan": "Catatan",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    # ==========================================================================
    # TAB 4: IMPOR DAFTAR TENONGAN DARI EXCEL / CSV
    # ==========================================================================
    with tab_impor:
        st.markdown("#### 📥 Impor Daftar Mitra Tenongan dari Excel / CSV")
        st.write(
            "Gunakan fitur ini untuk menambahkan banyak tenongan baru sekaligus dari file `.xlsx` atau `.csv`. "
            "Tenongan yang namanya sudah ada di database akan dilewati secara otomatis agar tidak terjadi duplikasi."
        )

        df_template = pd.DataFrame(
            [
                {
                    "nama_tenongan": "TENONGAN CONTOH 1",
                    "nama_pemilik": "Bapak Ahmad",
                    "kontak": "08123456789",
                    "alamat_lokasi": "Pasar Klewer",
                    "pola_titip": "HARIAN",
                    "kuota_default": 4,
                    "harga_default": 2000,
                },
                {
                    "nama_tenongan": "TENONGAN CONTOH 2",
                    "nama_pemilik": "Ibu Siti",
                    "kontak": "08567891234",
                    "alamat_lokasi": "Kartasura",
                    "pola_titip": "SELANG_SELING",
                    "kuota_default": 3,
                    "harga_default": 2000,
                },
            ]
        )
        st.download_button(
            "📄 Unduh Template Excel Daftar Tenongan (.xlsx)",
            data=dfs_ke_excel_bytes({"Template_Tenongan": df_template}),
            file_name="template_impor_tenongan.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        file_up = st.file_uploader(
            "Unggah File Daftar Tenongan (.xlsx atau .csv)",
            type=["xlsx", "csv"],
            key="uploader_daftar_tenongan",
        )
        if file_up is not None:
            try:
                if file_up.name.lower().endswith(".csv"):
                    df_up = pd.read_csv(file_up)
                else:
                    df_up = pd.read_excel(file_up)
                st.write("Pratinjau Data yang Akan Diimpor:")
                st.dataframe(df_up.head(10), use_container_width=True)

                if st.button("🚀 Proses Impor Daftar Tenongan", type="primary"):
                    ok_imp, msg_imp, _ = impor_daftar_tenongan_df(df_up, db_path=db_path)
                    if ok_imp:
                        st.success(msg_imp)
                        st.rerun()
                    else:
                        st.error(msg_imp)
            except Exception as exc:
                st.error(f"Gagal membaca file yang diunggah: {exc}")


render_halaman_tenongan()
