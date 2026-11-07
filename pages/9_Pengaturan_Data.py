"""
pages/9_Pengaturan_Data.py
Halaman I. Pengaturan dan Manajemen Data Taiyaki Business Manager.
- Menampilkan informasi versi aplikasi, metadata dataset demo, dan ekspor data.
- Mengunci seluruh fitur mutasi pengaturan, editor bahan, impor, backup database,
  dan Synthetic Data Generator (1-6 bulan) khusus untuk Administrator terautentikasi.
"""

from datetime import date
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

from auth import is_admin_authenticated
from components import (
    cek_izin_tulis_ui,
    get_active_db_path,
    render_kpi_card,
    tampilkan_header_halaman,
)
from data_processing import (
    df_ke_csv_bytes,
    dfs_ke_excel_bytes,
    impor_daftar_tenongan_df,
    impor_workbook_projectapp,
    simpan_batch_transaksi_harian,
)
from database import (
    BACKUP_DIR,
    DEMO_DB_PATH,
    PROD_DB_PATH,
    buat_backup_database,
    get_connection,
    get_ringkasan_cepat_db,
    get_setting,
    query_df,
    set_setting,
)
from synthetic_generator import (
    generate_ulang_dataset_demo_admin,
    get_metadata_dataset_demo,
)
from utils import bersihkan_teks, format_angka, format_rupiah, konversi_angka_aman


def ekspor_seluruh_database_dict(db_path: Path) -> Dict[str, pd.DataFrame]:
    """Mengambil seluruh tabel di database aktif sebagai dictionary DataFrame untuk ekspor."""
    daftar_tabel = [
        ("Tenongan", "SELECT * FROM tenongan ORDER BY COALESCE(no_urut, 9999) ASC;"),
        ("Transaksi_Harian", "SELECT * FROM transaksi_harian ORDER BY tanggal DESC, id_tenongan ASC;"),
        ("Master_Bahan", "SELECT * FROM master_bahan ORDER BY kode_bahan ASC;"),
        ("Belanja_Bahan", "SELECT * FROM belanja_bahan_baku ORDER BY tanggal DESC;"),
        ("Mutasi_Stok", "SELECT * FROM mutasi_stok_bhn ORDER BY tanggal DESC;"),
        ("Resep_HPP_Batch", "SELECT * FROM resep_hpp_batch;"),
        ("Resep_HPP_Item", "SELECT * FROM resep_hpp_item;"),
        ("Transaksi_Kulakan", "SELECT * FROM transaksi_kulakan ORDER BY tanggal DESC;"),
        ("Biaya_Operasional", "SELECT * FROM biaya_operasional ORDER BY tanggal DESC;"),
        ("Pengaturan_App", "SELECT * FROM app_settings;"),
    ]
    return {nama_sheet: query_df(sql, db_path=db_path) for nama_sheet, sql in daftar_tabel}


def impor_transaksi_csv_standar(df_in: pd.DataFrame, db_path: Path) -> Tuple[bool, str]:
    """Memvalidasi dan mengimpor file CSV/Excel transaksi harian berformat tabel standar (Khusus Admin)."""
    if df_in is None or df_in.empty:
        return False, "File yang diunggah kosong."

    peta_kolom = {str(c).strip().lower(): c for c in df_in.columns}
    kolom_wajib = ["tanggal", "nama_tenongan", "qty_titip", "qty_laku"]
    kolom_kurang = [k for k in kolom_wajib if k not in peta_kolom]
    if kolom_kurang:
        return False, f"Format kolom tidak valid. Kolom wajib yang belum ada: {kolom_kurang}."

    rows_tng = query_df("SELECT id_tenongan, nama_tenongan, harga_default FROM tenongan;", db_path=db_path)
    peta_tng = {
        bersihkan_teks(r["nama_tenongan"]).lower(): (int(r["id_tenongan"]), float(r["harga_default"]))
        for _, r in rows_tng.iterrows()
    }

    per_tanggal: Dict[str, list] = {}
    for idx, r in df_in.iterrows():
        tgl_str = str(r[peta_kolom["tanggal"]]).strip()[:10]
        nama_tng = bersihkan_teks(r[peta_kolom["nama_tenongan"]])
        if not tgl_str or not nama_tng:
            continue
        kunci = nama_tng.lower()
        if kunci not in peta_tng:
            return False, f"Baris ke-{idx+1}: Nama tenongan '{nama_tng}' belum terdaftar di Master Tenongan."
        id_tng, hrg_def = peta_tng[kunci]
        titip = int(konversi_angka_aman(r[peta_kolom["qty_titip"]], 0) or 0)
        laku = int(konversi_angka_aman(r[peta_kolom["qty_laku"]], 0) or 0)
        per_tanggal.setdefault(tgl_str, []).append(
            {
                "id_tenongan": id_tng,
                "nama_tenongan": nama_tng,
                "qty_titip": titip,
                "qty_laku": laku,
                "harga_jual_unit": hrg_def,
                "status_konfirmasi": "TERKONFIRMASI",
                "is_rapel": (laku > titip),
                "catatan": "Impor CSV/Excel Standar",
            }
        )

    total_berhasil = 0
    for tgl_k, list_item in per_tanggal.items():
        ok, msg, stat = simpan_batch_transaksi_harian(tgl_k, list_item, db_path=db_path)
        if not ok:
            return False, f"Gagal pada tanggal {tgl_k}: {msg}"
        total_berhasil += stat["disimpan"] + stat["diperbarui"]

    return True, f"Berhasil memvalidasi dan mengimpor {total_berhasil} baris transaksi harian."


def render_halaman_pengaturan() -> None:
    db_path = get_active_db_path()
    admin_aktif = is_admin_authenticated()

    tampilkan_header_halaman(
        judul="Pengaturan Aplikasi & Manajemen Database",
        subjudul="Konfigurasi parameter bisnis, manajemen Synthetic Data Generator (1–6 Bulan), ekspor/impor, dan backup SQLite.",
        ikon="⚙️",
    )

    ringkasan = get_ringkasan_cepat_db(db_path)
    meta_demo = get_metadata_dataset_demo()
    versi_app = str(get_setting("versi_aplikasi", "2.0.0", db_path=db_path))
    ukuran_db_kb = round(db_path.stat().st_size / 1024.0, 1) if db_path.exists() else 0.0

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Lingkungan Database Aktif",
            "PRODUKSI (ASLI)" if (admin_aktif and db_path == PROD_DB_PATH) else "DEMO SINTETIS",
            f"Ukuran DB: {format_angka(ukuran_db_kb, 1)} KB | Versi: v{versi_app}",
            kategori_warna="keuntungan" if (admin_aktif and db_path == PROD_DB_PATH) else "piutang",
        )
    with k2:
        render_kpi_card(
            "Total Baris Transaksi",
            f"{format_angka(ringkasan['total_baris_transaksi'])} Baris",
            f"Dari {ringkasan['total_hari_aktif']} hari operasional aktif",
            kategori_warna="pendapatan",
        )
    with k3:
        hpp_now = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)
        hrg_now = float(get_setting("harga_jual_default", 2000.0, db_path=db_path) or 2000.0)
        render_kpi_card(
            "Harga Jual & HPP Standar",
            format_rupiah(hrg_now),
            f"HPP Aktif: {format_rupiah(hpp_now, gunakan_desimal=True)}/pcs",
            kategori_warna="netral",
        )
    with k4:
        render_kpi_card(
            "Status Dataset Demo Publik",
            f"{meta_demo['periode_bulan']} Bulan ({format_angka(meta_demo['total_transaksi'])} Trx)",
            f"Dibuat: {meta_demo['generated_at']}",
            kategori_warna="pendapatan",
        )

    # Jika pengunjung publik belum login, tampilkan informasi Read-Only dan fitur unduh sampel demo saja
    if not cek_izin_tulis_ui(tampilkan_peringatan=True):
        st.markdown("#### 📊 Informasi Dataset Sintetis Mode Demo Portofolio")
        st.write(
            f"- **Periode Dataset Demo Aktif:** `{meta_demo['periode_bulan']} Bulan` "
            f"(`{meta_demo['tgl_mulai']}` s.d. `{meta_demo['tgl_akhir']}`)\n"
            f"- **Jumlah Hari Operasional Aktif:** `{meta_demo['total_hari_aktif']} hari`\n"
            f"- **Total Transaksi Konsinyasi Harian:** `{format_angka(meta_demo['total_transaksi'])} transaksi`\n"
            f"- **Waktu Terakhir Dibangkitkan:** `{meta_demo['generated_at']}` (Random Seed: `{meta_demo['seed']}`)\n"
            f"- **Hak Akses Pengunjung Publik:** `Read-Only` (Tombol Generate/Reset Dataset Demo dan Akses Database Produksi dikunci khusus untuk Administrator)."
        )
        semua_sheets_demo = ekspor_seluruh_database_dict(DEMO_DB_PATH)
        st.download_button(
            "⬇️ Unduh Sampel Dataset Sintetis Demo (.xlsx)",
            data=dfs_ke_excel_bytes(semua_sheets_demo),
            file_name="dataset_sintetis_demo_taiyaki.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        return

    # ==========================================================================
    # PANEL LENGKAP KHUSUS ADMINISTRATOR TERAUTENTIKASI
    # ==========================================================================
    tab_synth, tab_param, tab_bahan, tab_ekspor_impor, tab_backup = st.tabs(
        [
            "🧪 1. Synthetic Data Generator (Demo 1–6 Bulan)",
            "🎛️ 2. Parameter Bisnis & Aturan Setoran",
            "🥚 3. Daftar Bahan Baku & Satuan",
            "📤 4. Ekspor & Impor Data Tervalidasi",
            "💾 5. Backup Database Produksi & Info Sistem",
        ]
    )

    with tab_synth:
        st.markdown("#### 🧪 Pengelolaan Synthetic Data Generator (Khusus Mode Demo Portofolio)")
        st.info(
            "Fitur ini hanya memodifikasi file **`data/demo/taiyaki_demo.db`** dan **tidak pernah menyentuh** "
            "data bisnis asli Anda di `data/production/taiyaki_production.db`."
        )

        cs1, cs2, cs3 = st.columns(3)
        with cs1:
            pilihan_bulan = st.selectbox(
                "Pilih Durasi Periode Dataset Sintetis",
                options=[1, 2, 3, 4, 5, 6],
                index=5,
                format_func=lambda x: f"{x} Bulan ({'Default Portofolio' if x == 6 else f'~{x*26} Hari Kerja'})",
            )
        with cs2:
            seed_input = st.number_input(
                "Random Seed (Reproducibility)",
                min_value=1,
                max_value=999999,
                value=int(meta_demo.get("seed", 42)),
                step=1,
            )
        with cs3:
            st.metric(
                "Waktu Terakhir Dataset Dibuat",
                str(meta_demo["generated_at"])[:16],
                f"Periode: {meta_demo['periode_bulan']} Bulan ({format_angka(meta_demo['total_transaksi'])} Trx)",
            )

        cb_gen, cb_rst = st.columns(2)
        with cb_gen:
            if st.button("🚀 Generate Synthetic Dataset", type="primary", use_container_width=True):
                hasil_gen = generate_ulang_dataset_demo_admin(
                    periode_bulan=int(pilihan_bulan),
                    seed=int(seed_input),
                )
                st.cache_data.clear()
                st.success(
                    f"Dataset Sintetis **{hasil_gen['periode_bulan']} Bulan** berhasil dibuat pada `{hasil_gen['generated_at']}`! "
                    f"({format_angka(hasil_gen['jml_transaksi_harian'])} transaksi harian, "
                    f"{hasil_gen['jml_belanja_bahan']} nota belanja, {hasil_gen['jml_kulakan']} transaksi kulakan, "
                    f"dan {hasil_gen['jml_biaya_ops']} biaya operasional)."
                )
                st.rerun()
        with cb_rst:
            if st.button("🔄 Reset Demo Dataset ke Default (6 Bulan, Seed=42)", use_container_width=True):
                hasil_rst = generate_ulang_dataset_demo_admin(periode_bulan=6, seed=42)
                st.cache_data.clear()
                st.success(
                    f"Dataset Demo berhasil di-reset ke konfigurasi standar 6 bulan ({format_angka(hasil_rst['jml_transaksi_harian'])} transaksi)!"
                )
                st.rerun()

    with tab_param:
        st.markdown("#### 🎛️ Konfigurasi Parameter Operasional & Akuntansi Usaha")
        mode_retur_now = str(get_setting("mode_beban_retur", "BEBANKAN_RETUR", db_path=db_path))
        with st.form("form_pengaturan_utama"):
            c1, c2 = st.columns(2)
            with c1:
                nama_usaha_in = st.text_input(
                    "Nama Usaha / Bisnis",
                    value=str(get_setting("nama_usaha", "Taiyaki Consignment & Distribution", db_path=db_path)),
                )
                harga_def_in = st.number_input(
                    "Harga Jual / Setoran Default per Buah (Rp)",
                    min_value=100.0,
                    value=float(get_setting("harga_jual_default", 2000.0, db_path=db_path) or 2000.0),
                    step=100.0,
                )
                komisi_def_in = st.number_input(
                    "Potongan Komisi Default Tenongan per Buah (Rp)",
                    min_value=0.0,
                    value=float(get_setting("komisi_default", 0.0, db_path=db_path) or 0.0),
                    step=50.0,
                )
                mode_retur_in = st.selectbox(
                    "Aturan Perlakuan Biaya Roti Retur/Sisa pada Laba Rugi",
                    options=["BEBANKAN_RETUR", "RITEL_MURNI"],
                    index=0 if mode_retur_now == "BEBANKAN_RETUR" else 1,
                )
            with c2:
                batch_def_in = st.number_input(
                    "Ukuran Output Standar per 1 Batch Produksi (pcs)",
                    min_value=1,
                    value=int(get_setting("output_batch_default", 120, db_path=db_path) or 120),
                    step=10,
                )
                hpp_def_in = st.number_input(
                    "HPP Standar Aktif per Buah (Rp)",
                    min_value=0.0,
                    value=float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222),
                    step=10.0,
                    format="%.4f",
                )
                target_bln_in = st.number_input(
                    "Target Penjualan Bulanan (pcs)",
                    min_value=100,
                    value=int(get_setting("target_penjualan_bulanan_pcs", 1500, db_path=db_path) or 1500),
                    step=100,
                )
                biaya_tetap_in = st.number_input(
                    "Estimasi Biaya Tetap Bulanan untuk Simulasi BEP (Rp)",
                    min_value=0.0,
                    value=float(get_setting("biaya_tetap_bulanan_default", 150000.0, db_path=db_path) or 150000.0),
                    step=25000.0,
                )

            if st.form_submit_button("💾 Simpan Pengaturan Bisnis", type="primary", use_container_width=True):
                set_setting("nama_usaha", bersihkan_teks(nama_usaha_in), "str", db_path=db_path)
                set_setting("harga_jual_default", float(harga_def_in), "float", db_path=db_path)
                set_setting("komisi_default", float(komisi_def_in), "float", db_path=db_path)
                set_setting("mode_beban_retur", mode_retur_in, "str", db_path=db_path)
                set_setting("output_batch_default", int(batch_def_in), "int", db_path=db_path)
                set_setting("hpp_unit_aktif", float(hpp_def_in), "float", db_path=db_path)
                set_setting("target_penjualan_bulanan_pcs", int(target_bln_in), "int", db_path=db_path)
                set_setting("biaya_tetap_bulanan_default", float(biaya_tetap_in), "float", db_path=db_path)
                st.success("Parameter bisnis berhasil diperbarui!")
                st.rerun()

    with tab_bahan:
        st.markdown("#### 🥚 Editor Master Bahan Baku, Satuan & Batas Stok Minimum")
        df_bhn = query_df(
            "SELECT id_bahan, kode_bahan, nama_bahan, kategori, satuan_beli, isi_per_satuan, satuan_pakai, harga_acuan, stok_saat_ini, stok_minimum, status_aktif FROM master_bahan ORDER BY kode_bahan ASC;",
            db_path=db_path,
        )
        if not df_bhn.empty:
            edited_bhn = st.data_editor(
                df_bhn,
                disabled=["id_bahan", "kode_bahan"],
                hide_index=True,
                use_container_width=True,
            )
            if st.button("💾 Simpan Perubahan Daftar Bahan Baku", type="primary"):
                with get_connection(db_path) as conn:
                    for _, r in edited_bhn.iterrows():
                        conn.execute(
                            """
                            UPDATE master_bahan SET
                                nama_bahan = ?, kategori = ?, satuan_beli = ?,
                                isi_per_satuan = ?, satuan_pakai = ?, harga_acuan = ?,
                                stok_saat_ini = ?, stok_minimum = ?, status_aktif = ?,
                                updated_at = datetime('now', 'localtime')
                            WHERE id_bahan = ?;
                            """,
                            (
                                bersihkan_teks(r["nama_bahan"]),
                                str(r["kategori"]),
                                bersihkan_teks(r["satuan_beli"]),
                                float(r["isi_per_satuan"]),
                                bersihkan_teks(r["satuan_pakai"]),
                                float(r["harga_acuan"]),
                                float(r["stok_saat_ini"]),
                                float(r["stok_minimum"]),
                                1 if r["status_aktif"] else 0,
                                int(r["id_bahan"]),
                            ),
                        )
                st.success("Master bahan baku berhasil diperbarui.")
                st.rerun()

    with tab_ekspor_impor:
        col_eks, col_imp = st.columns(2)
        with col_eks:
            st.markdown("#### 📤 Ekspor Seluruh Data")
            semua_sheets = ekspor_seluruh_database_dict(db_path)
            st.download_button(
                "📦 Unduh Full Database ke Workbook Excel (.xlsx)",
                data=dfs_ke_excel_bytes(semua_sheets),
                file_name=f"full_export_taiyaki_{date.today().isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )
            pilih_tbl_csv = st.selectbox("Pilih Tabel untuk Ekspor CSV", options=list(semua_sheets.keys()))
            st.download_button(
                f"⬇️ Unduh Tabel '{pilih_tbl_csv}' (.csv)",
                data=df_ke_csv_bytes(semua_sheets[pilih_tbl_csv]),
                file_name=f"{pilih_tbl_csv.lower()}_{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with col_imp:
            st.markdown("#### 📥 Impor Data ke Database Aktif (Khusus Admin)")
            jenis_impor = st.radio(
                "Pilih Jenis File Impor",
                options=[
                    "Workbook Historis Awal (projectapp.xlsx)",
                    "Transaksi Harian Tabel Standar (.xlsx / .csv)",
                    "Daftar Mitra Tenongan (.xlsx / .csv)",
                ],
            )
            file_impor = st.file_uploader("Unggah File Excel (.xlsx) atau CSV (.csv)", type=["xlsx", "csv"])
            if file_impor is not None and st.button("🚀 Validasi & Jalankan Impor", type="primary", use_container_width=True):
                if jenis_impor == "Workbook Historis Awal (projectapp.xlsx)":
                    ok_i, msg_i, det_i = impor_workbook_projectapp(file_source=file_impor, db_path=db_path)
                    if ok_i:
                        st.success(msg_i)
                        st.json(det_i)
                    else:
                        st.error(msg_i)
                elif jenis_impor == "Transaksi Harian Tabel Standar (.xlsx / .csv)":
                    df_up = pd.read_csv(file_impor) if file_impor.name.lower().endswith(".csv") else pd.read_excel(file_impor)
                    ok_t, msg_t = impor_transaksi_csv_standar(df_up, db_path=db_path)
                    st.success(msg_t) if ok_t else st.error(msg_t)
                else:
                    df_up_t = pd.read_csv(file_impor) if file_impor.name.lower().endswith(".csv") else pd.read_excel(file_impor)
                    ok_mt, msg_mt, _ = impor_daftar_tenongan_df(df_up_t, db_path=db_path)
                    st.success(msg_mt) if ok_mt else st.error(msg_mt)

    with tab_backup:
        st.markdown("#### 💾 Backup Database Produksi (`data/production/backups/`)")
        if st.button("🛡️ Buat Snapshot Backup Database Sekarang", type="primary"):
            path_bkp = buat_backup_database(PROD_DB_PATH)
            st.success(f"Backup produksi berhasil dibuat: `{path_bkp.name}`")

        if PROD_DB_PATH.exists():
            with open(PROD_DB_PATH, "rb") as f_db:
                st.download_button(
                    "⬇️ Unduh File Database Produksi (taiyaki_production.db)",
                    data=f_db.read(),
                    file_name=f"taiyaki_production_{date.today().isoformat()}.db",
                    mime="application/octet-stream",
                )