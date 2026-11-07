"""
pages/3_Input_Harian.py
Halaman Operasional Utama: Input Harian Titip & Laku.
Menyediakan:
1. Mode Input Massal Cepat (Editable Table) untuk seluruh tenongan aktif pada satu tanggal.
2. Mode Input Satuan (1 Tenongan) dengan kalkulasi real-time, opsi rapel, dan status pembayaran.
3. Manajemen Transaksi Tercatat: filter, pelunasan piutang cepat, konfirmasi sisa, & hapus data terkonfirmasi.
4. Impor Langsung Workbook Historis (projectapp.xlsx) & Pemantauan Audit Log Perubahan.
"""

from datetime import date
from pathlib import Path
import pandas as pd
import streamlit as st

from calculations import hitung_transaksi_item
from components import get_active_db_path, render_kpi_card, tampilkan_header_halaman
from data_processing import (
    df_ke_csv_bytes,
    dfs_ke_excel_bytes,
    get_daftar_tenongan_lengkap,
    get_transaksi_harian_df,
    hapus_transaksi_harian,
    impor_workbook_projectapp,
    simpan_atau_update_transaksi_harian,
    simpan_batch_transaksi_harian,
)
from database import query_df
from utils import format_angka, format_rupiah, format_tanggal_id


def render_halaman_input_harian() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Input Harian: Titip & Laku",
        subjudul="Catat pengiriman roti taiyaki (titip), hasil penjualan (laku), retur/sisa, dan uang setoran harian.",
        ikon="📝",
    )

    # Pemilih Tanggal Operasional Utama di Bagian Atas
    col_tgl, col_info = st.columns([1.4, 2.6])
    with col_tgl:
        tgl_pilih = st.date_input(
            "📅 Pilih Tanggal Transaksi Operasional",
            value=st.session_state.get("input_tgl_aktif", date.today()),
            key="picker_tgl_operasional",
        )
        st.session_state["input_tgl_aktif"] = tgl_pilih
        tgl_iso = tgl_pilih.isoformat()

    # Ambil ringkasan transaksi yang sudah tersimpan pada tanggal yang dipilih
    df_hari_ini = get_transaksi_harian_df(tgl_mulai=tgl_iso, tgl_selesai=tgl_iso, db_path=db_path)
    jml_trx_hari = len(df_hari_ini)
    titip_hari = int(df_hari_ini["qty_titip"].sum()) if not df_hari_ini.empty else 0
    laku_hari = int(df_hari_ini["qty_laku"].sum()) if not df_hari_ini.empty else 0
    retur_hari = int(df_hari_ini["qty_retur"].sum()) if not df_hari_ini.empty else 0
    omzet_hari = float(df_hari_ini["omzet_kotor"].sum()) if not df_hari_ini.empty else 0.0
    setoran_hari = float(df_hari_ini["uang_setoran"].sum()) if not df_hari_ini.empty else 0.0
    piutang_hari = float(df_hari_ini["piutang"].sum()) if not df_hari_ini.empty else 0.0
    unconf_hari = int((df_hari_ini["status_konfirmasi"] == "BELUM_KONFIRMASI").sum()) if not df_hari_ini.empty else 0

    with col_info:
        st.info(
            f"**Ringkasan Tanggal {format_tanggal_id(tgl_iso, sertakan_hari=True)}**\n\n"
            f"Tenongan Tercatat: **{jml_trx_hari} mitra** | "
            f"Titip: **{format_angka(titip_hari)} pcs** | "
            f"Laku: **{format_angka(laku_hari)} pcs** | "
            f"Retur: **{format_angka(retur_hari)} pcs** | "
            f"Belum Konfirmasi: **{unconf_hari} mitra**"
        )

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Roti Dititipkan (Hari Ini)",
            f"{format_angka(titip_hari)} pcs",
            f"Dari {jml_trx_hari} tenongan aktif",
            kategori_warna="netral",
        )
    with k2:
        render_kpi_card(
            "Roti Terjual / Laku",
            f"{format_angka(laku_hari)} pcs",
            f"Retur/Sisa: {format_angka(retur_hari)} pcs",
            kategori_warna="pendapatan",
        )
    with k3:
        render_kpi_card(
            "Omzet vs Setoran Masuk",
            format_rupiah(setoran_hari),
            f"Omzet Penjualan: {format_rupiah(omzet_hari)}",
            kategori_warna="keuntungan",
        )
    with k4:
        render_kpi_card(
            "Piutang Tanggal Ini",
            format_rupiah(piutang_hari),
            f"Belum dikonfirmasi: {unconf_hari} transaksi",
            kategori_warna="piutang" if (piutang_hari > 0 or unconf_hari > 0) else "keuntungan",
        )

    tab_massal, tab_satuan, tab_kelola, tab_impor_audit = st.tabs(
        [
            "⚡ Mode 1: Input Massal Cepat (Tabel)",
            "🎯 Mode 2: Input Satu Tenongan",
            "🗂️ Daftar Transaksi, Koreksi & Hapus",
            "📥 Impor projectapp.xlsx & Audit Log",
        ]
    )

    df_tenongan_aktif = get_daftar_tenongan_lengkap(db_path=db_path, hanya_aktif=True)

    # ==========================================================================
    # TAB 1: INPUT MASSAL CEPAT (BANYAK TENONGAN DALAM SATU TANGGAL)
    # ==========================================================================
    with tab_massal:
        st.markdown(f"#### ⚡ Input Massal untuk Tanggal: **{format_tanggal_id(tgl_iso, sertakan_hari=True)}**")
        st.caption(
            "Isi hanya tenongan yang memiliki transaksi pada tanggal ini. "
            "Baris tenongan dengan **Titip = 0** dan **Laku = 0** akan dilewati otomatis saat disimpan."
        )

        if df_tenongan_aktif.empty:
            st.warning("Tidak ada tenongan berstatus aktif. Silakan aktifkan atau tambah tenongan di menu Data Tenongan.")
        else:
            # Tombol aksi cepat pengisian tabel
            cb1, cb2, cb3, cb4 = st.columns(4)
            preset_mode = st.session_state.get(f"preset_massal_{tgl_iso}", "EXISTING")

            with cb1:
                if st.button("📋 Muat Data Tersimpan / Kosongkan", use_container_width=True):
                    st.session_state[f"preset_massal_{tgl_iso}"] = "EXISTING"
                    st.rerun()
            with cb2:
                if st.button("🌅 Isi Kuota Default (Pola Harian)", use_container_width=True):
                    st.session_state[f"preset_massal_{tgl_iso}"] = "KUOTA_HARIAN"
                    st.rerun()
            with cb3:
                if st.button("📦 Isi Kuota Default (Semua Aktif)", use_container_width=True):
                    st.session_state[f"preset_massal_{tgl_iso}"] = "KUOTA_SEMUA"
                    st.rerun()
            with cb4:
                otomatis_setor_penuh = st.checkbox(
                    "Otomatis Setoran = Laku × Harga jika Setoran Rp0",
                    value=True,
                    help="Jika dicentang, saat Anda mengisi kolom Laku dan membiarkan Setoran = 0, sistem otomatis menghitung uang setoran lunas sesuai harga jual.",
                )

            # Bangun dataframe awal untuk st.data_editor
            peta_eksisting = {}
            if not df_hari_ini.empty:
                for _, r_eks in df_hari_ini.iterrows():
                    peta_eksisting[int(r_eks["id_tenongan"])] = r_eks

            baris_editor = []
            for _, tng in df_tenongan_aktif.iterrows():
                id_t = int(tng["id_tenongan"])
                kuota_def = int(tng["kuota_default"])
                pola_t = str(tng["pola_titip"])
                harga_def = float(tng["harga_default"])

                if id_t in peta_eksisting and preset_mode == "EXISTING":
                    rek = peta_eksisting[id_t]
                    titip_awal = int(rek["qty_titip"])
                    laku_awal = int(rek["qty_laku"])
                    retur_awal = int(rek["qty_retur"])
                    setoran_awal = float(rek["uang_setoran"])
                    status_k_awal = str(rek["status_konfirmasi"])
                    rapel_awal = bool(rek["is_rapel"] == 1)
                    cat_awal = str(rek["catatan"] or "")
                elif preset_mode == "KUOTA_HARIAN":
                    titip_awal = kuota_def if pola_t == "HARIAN" else 0
                    laku_awal = 0
                    retur_awal = 0
                    setoran_awal = 0.0
                    status_k_awal = "TERKONFIRMASI"
                    rapel_awal = False
                    cat_awal = ""
                elif preset_mode == "KUOTA_SEMUA":
                    titip_awal = kuota_def
                    laku_awal = 0
                    retur_awal = 0
                    setoran_awal = 0.0
                    status_k_awal = "TERKONFIRMASI"
                    rapel_awal = False
                    cat_awal = ""
                else:
                    titip_awal = 0
                    laku_awal = 0
                    retur_awal = 0
                    setoran_awal = 0.0
                    status_k_awal = "TERKONFIRMASI"
                    rapel_awal = False
                    cat_awal = ""

                baris_editor.append(
                    {
                        "id_tenongan": id_t,
                        "Kode": str(tng["kode_tenongan"]),
                        "Nama Tenongan": str(tng["nama_tenongan"]),
                        "Pola": pola_t,
                        "Harga (Rp)": int(harga_def),
                        "Titip (pcs)": titip_awal,
                        "Laku (pcs)": laku_awal,
                        "Retur/Sisa (pcs)": retur_awal,
                        "Setoran Diterima (Rp)": int(setoran_awal),
                        "Belum Konfirmasi?": (status_k_awal == "BELUM_KONFIRMASI"),
                        "Rapel?": rapel_awal,
                        "Catatan": cat_awal,
                    }
                )

            df_editor_input = pd.DataFrame(baris_editor)

            edited_df = st.data_editor(
                df_editor_input,
                key=f"editor_massal_{tgl_iso}_{preset_mode}",
                disabled=["id_tenongan", "Kode", "Nama Tenongan", "Pola"],
                hide_index=True,
                use_container_width=True,
                height=520,
                column_config={
                    "id_tenongan": None,
                    "Harga (Rp)": st.column_config.NumberColumn("Harga (Rp)", min_value=0, step=100),
                    "Titip (pcs)": st.column_config.NumberColumn("Titip (pcs)", min_value=0, max_value=500, step=1),
                    "Laku (pcs)": st.column_config.NumberColumn("Laku (pcs)", min_value=0, max_value=500, step=1),
                    "Retur/Sisa (pcs)": st.column_config.NumberColumn(
                        "Retur/Sisa (pcs)",
                        min_value=0,
                        max_value=500,
                        step=1,
                        help="Jika dibiarkan 0 saat Laku < Titip, sistem otomatis menghitung Sisa = Titip - Laku.",
                    ),
                    "Setoran Diterima (Rp)": st.column_config.NumberColumn(
                        "Setoran Diterima (Rp)",
                        min_value=0,
                        step=1000,
                    ),
                    "Belum Konfirmasi?": st.column_config.CheckboxColumn(
                        "Belum Rekap?",
                        help="Centang jika roti baru dititipkan dan belum ditarik hasil lakunya.",
                    ),
                    "Rapel?": st.column_config.CheckboxColumn(
                        "Rapel?",
                        help="Centang jika jumlah Laku merupakan gabungan dengan titipan hari sebelumnya.",
                    ),
                },
            )

            if st.button("💾 Simpan Seluruh Transaksi Tabel Massal", type="primary", use_container_width=True):
                payload_batch = []
                for _, row_e in edited_df.iterrows():
                    t_val = int(row_e["Titip (pcs)"] or 0)
                    l_val = int(row_e["Laku (pcs)"] or 0)
                    r_val = int(row_e["Retur/Sisa (pcs)"] or 0)
                    hrg_val = float(row_e["Harga (Rp)"] or 2000.0)
                    setor_val = float(row_e["Setoran Diterima (Rp)"] or 0.0)
                    unconf_flag = bool(row_e["Belum Konfirmasi?"])
                    rapel_flag = bool(row_e["Rapel?"])

                    if t_val == 0 and l_val == 0:
                        continue

                    # Jika retur diisi 0 padahal terkonfirmasi dan l_val < t_val, biarkan dihitung otomatis
                    retur_kirim = r_val if r_val > 0 else max(0, t_val - l_val)

                    # Jika otomatis_setor_penuh aktif dan setor_val == 0 saat l_val > 0, kirim None agar lunas otomatis
                    uang_setor_kirim = (
                        None
                        if (otomatis_setor_penuh and setor_val == 0 and l_val > 0 and not unconf_flag)
                        else setor_val
                    )

                    payload_batch.append(
                        {
                            "id_tenongan": int(row_e["id_tenongan"]),
                            "nama_tenongan": str(row_e["Nama Tenongan"]),
                            "qty_titip": t_val,
                            "qty_laku": l_val,
                            "qty_retur": retur_kirim,
                            "harga_jual_unit": hrg_val,
                            "komisi_unit": 0.0,
                            "uang_setoran": uang_setor_kirim,
                            "status_konfirmasi": "BELUM_KONFIRMASI" if unconf_flag else "TERKONFIRMASI",
                            "is_rapel": rapel_flag,
                            "catatan": str(row_e["Catatan"] or ""),
                        }
                    )

                ok_b, msg_b, _ = simpan_batch_transaksi_harian(
                    tanggal=tgl_iso,
                    daftar_input=payload_batch,
                    db_path=db_path,
                )
                if ok_b:
                    st.session_state[f"preset_massal_{tgl_iso}"] = "EXISTING"
                    st.success(msg_b)
                    st.rerun()
                else:
                    st.error(msg_b)

    # ==========================================================================
    # TAB 2: INPUT SATU TENONGAN (FORM SATUAN & PREVIEW)
    # ==========================================================================
    with tab_satuan:
        st.markdown(f"#### 🎯 Input atau Koreksi Satu Tenongan ({format_tanggal_id(tgl_iso, singkat=True)})")
        if df_tenongan_aktif.empty:
            st.warning("Belum ada tenongan aktif.")
        else:
            opsi_tng_satuan = df_tenongan_aktif["id_tenongan"].tolist()
            peta_nama_satuan = {
                int(r["id_tenongan"]): f"{r['kode_tenongan']} - {r['nama_tenongan']} (Kuota Default: {int(r['kuota_default'])} pcs)"
                for _, r in df_tenongan_aktif.iterrows()
            }

            id_tng_s = st.selectbox(
                "Pilih Mitra Tenongan",
                options=opsi_tng_satuan,
                format_func=lambda x: peta_nama_satuan.get(x, str(x)),
                key="satuan_pilih_tng",
            )
            info_s = df_tenongan_aktif[df_tenongan_aktif["id_tenongan"] == id_tng_s].iloc[0]

            # Cek apakah sudah ada transaksi pada tanggal ini
            eks_s = df_hari_ini[df_hari_ini["id_tenongan"] == id_tng_s] if not df_hari_ini.empty else pd.DataFrame()
            ada_eks = not eks_s.empty

            if ada_eks:
                baris_eks = eks_s.iloc[0]
                st.info(
                    f"Tenongan **{info_s['nama_tenongan']}** sudah memiliki catatan pada tanggal ini "
                    f"(Titip: {baris_eks['qty_titip']}, Laku: {baris_eks['qty_laku']}, Setoran: {format_rupiah(baris_eks['uang_setoran'])}). "
                    "Mengirim form di bawah akan memperbarui data tersebut dan mencatatnya di Audit Log."
                )

            cs1, cs2, cs3 = st.columns(3)
            with cs1:
                titip_s = st.number_input(
                    "Jumlah Roti Dititipkan (pcs)",
                    min_value=0,
                    max_value=500,
                    value=int(eks_s.iloc[0]["qty_titip"]) if ada_eks else int(info_s["kuota_default"]),
                    step=1,
                )
                status_konf_s = st.selectbox(
                    "Status Konfirmasi Barang",
                    options=["TERKONFIRMASI", "BELUM_KONFIRMASI"],
                    index=1 if (ada_eks and eks_s.iloc[0]["status_konfirmasi"] == "BELUM_KONFIRMASI") else 0,
                    format_func=lambda x: (
                        "Sudah Direkap (Terkonfirmasi)" if x == "TERKONFIRMASI" else "Baru Dititipkan (Belum Dikonfirmasi)"
                    ),
                )
            with cs2:
                laku_s = st.number_input(
                    "Jumlah Roti Terjual / Laku (pcs)",
                    min_value=0,
                    max_value=500,
                    value=int(eks_s.iloc[0]["qty_laku"]) if ada_eks else int(titip_s),
                    step=1,
                    disabled=(status_konf_s == "BELUM_KONFIRMASI"),
                )
                default_retur_s = max(0, int(titip_s) - int(laku_s)) if status_konf_s == "TERKONFIRMASI" else 0
                retur_s = st.number_input(
                    "Jumlah Retur / Sisa (pcs)",
                    min_value=0,
                    max_value=500,
                    value=default_retur_s,
                    step=1,
                    disabled=(status_konf_s == "BELUM_KONFIRMASI"),
                )
            with cs3:
                harga_s = st.number_input(
                    "Harga Jual per Buah (Rp)",
                    min_value=0,
                    value=int(eks_s.iloc[0]["harga_jual_unit"]) if ada_eks else int(info_s["harga_default"]),
                    step=100,
                )
                komisi_s = st.number_input(
                    "Komisi Potongan per Buah (Rp)",
                    min_value=0,
                    value=int(eks_s.iloc[0]["komisi_unit"]) if ada_eks else int(info_s["komisi_default"]),
                    step=100,
                )

            omzet_harapan_s = int(laku_s) * max(0, int(harga_s) - int(komisi_s)) if status_konf_s == "TERKONFIRMASI" else 0

            cp1, cp2 = st.columns([1.5, 2.5])
            with cp1:
                skema_bayar_s = st.radio(
                    "Pencatatan Setoran Uang",
                    options=["LUNAS_OTOMATIS", "INPUT_MANUAL", "BELUM_BAYAR"],
                    format_func=lambda x: {
                        "LUNAS_OTOMATIS": "Lunas Penuh Sesuai Tagihan",
                        "INPUT_MANUAL": "Input Nominal Setoran Manual (Sebagian/Cicil)",
                        "BELUM_BAYAR": "Belum Dibayar (Catat sebagai Piutang)",
                    }[x],
                )
            with cp2:
                if skema_bayar_s == "LUNAS_OTOMATIS":
                    uang_setor_s = float(omzet_harapan_s)
                    st.metric("Nominal Uang Disetorkan", format_rupiah(uang_setor_s), "Status: LUNAS")
                elif skema_bayar_s == "BELUM_BAYAR":
                    uang_setor_s = 0.0
                    st.metric("Nominal Uang Disetorkan", "Rp0", f"Piutang: {format_rupiah(omzet_harapan_s)}")
                else:
                    uang_setor_s = float(
                        st.number_input(
                            "Masukkan Jumlah Uang yang Diterima (Rp)",
                            min_value=0,
                            value=int(eks_s.iloc[0]["uang_setoran"]) if ada_eks else omzet_harapan_s,
                            step=1000,
                        )
                    )

            ca_rapel, ca_timpa = st.columns(2)
            with ca_rapel:
                is_rapel_s = st.checkbox(
                    "Transaksi Rapel / Penyelesaian Sisa Hari Sebelumnya (Izinkan Laku > Titip)",
                    value=bool(ada_eks and eks_s.iloc[0]["is_rapel"] == 1),
                )
            with ca_timpa:
                izinkan_timpa_s = st.checkbox(
                    "Izinkan Koreksi/Update jika transaksi tanggal ini sudah ada",
                    value=True,
                )

            catatan_s = st.text_input(
                "Catatan Transaksi",
                value=str(eks_s.iloc[0]["catatan"] or "") if ada_eks else "",
                placeholder="Contoh: Titip pagi 4 pcs, sisa 1 pcs ditarik sore...",
            )

            # Pratinjau Perhitungan Otomatis
            preview_s = hitung_transaksi_item(
                qty_titip=int(titip_s),
                qty_laku=None if status_konf_s == "BELUM_KONFIRMASI" else int(laku_s),
                qty_retur=int(retur_s),
                harga_jual_unit=float(harga_s),
                komisi_unit=float(komisi_s),
                uang_setoran=uang_setor_s,
                status_konfirmasi_input=status_konf_s,
                is_rapel=is_rapel_s,
            )
            st.caption(
                f"**Pratinjau Perhitungan:** Omzet Kotor = **{format_rupiah(preview_s['omzet_kotor'])}** | "
                f"Tagihan Setoran = **{format_rupiah(preview_s['setoran_harapan'])}** | "
                f"Setoran Diterima = **{format_rupiah(preview_s['uang_setoran'])}** | "
                f"Piutang = **{format_rupiah(preview_s['piutang'])}** ({preview_s['status_pembayaran']})"
            )

            if st.button("💾 Simpan Transaksi Tenongan", type="primary", use_container_width=True):
                ok_s, msg_s, _ = simpan_atau_update_transaksi_harian(
                    id_tenongan=int(id_tng_s),
                    tanggal=tgl_iso,
                    qty_titip=int(titip_s),
                    qty_laku=None if status_konf_s == "BELUM_KONFIRMASI" else int(laku_s),
                    qty_retur=int(retur_s),
                    harga_jual_unit=float(harga_s),
                    komisi_unit=float(komisi_s),
                    uang_setoran=uang_setor_s,
                    status_konfirmasi=status_konf_s,
                    is_rapel=is_rapel_s,
                    catatan=catatan_s,
                    izinkan_timpa=izinkan_timpa_s,
                    db_path=db_path,
                )
                if ok_s:
                    st.success(msg_s)
                    st.rerun()
                else:
                    st.error(msg_s)

    # ==========================================================================
    # TAB 3: DAFTAR TRANSAKSI, KONFIRMASI SISA & HAPUS DATA
    # ==========================================================================
    with tab_kelola:
        st.markdown("#### 🗂️ Daftar Transaksi Harian & Penyelesaian Piutang/Konfirmasi")
        cf1, cf2, cf3, cf4 = st.columns(4)
        with cf1:
            mode_rentang = st.selectbox(
                "Cakupan Tanggal",
                options=["Hanya Tanggal Dipilih", "Semua Tanggal Historis"],
                index=0,
            )
        with cf2:
            f_konf = st.selectbox(
                "Status Konfirmasi Barang",
                options=["SEMUA", "TERKONFIRMASI", "BELUM_KONFIRMASI"],
            )
        with cf3:
            f_bayar = st.selectbox(
                "Status Pembayaran Setoran",
                options=["SEMUA", "LUNAS", "SEBAGIAN", "BELUM_BAYAR"],
            )
        with cf4:
            opsi_filter_tng = [("SEMUA", "Semua Tenongan")] + [
                (int(r["id_tenongan"]), f"{r['kode_tenongan']} - {r['nama_tenongan']}")
                for _, r in df_tenongan_aktif.iterrows()
            ]
            f_tng = st.selectbox(
                "Filter Tenongan",
                options=[x[0] for x in opsi_filter_tng],
                format_func=lambda k: dict(opsi_filter_tng).get(k, str(k)),
            )

        df_list = get_transaksi_harian_df(
            tgl_mulai=tgl_iso if mode_rentang == "Hanya Tanggal Dipilih" else None,
            tgl_selesai=tgl_iso if mode_rentang == "Hanya Tanggal Dipilih" else None,
            id_tenongan=None if f_tng == "SEMUA" else int(f_tng),
            status_konfirmasi=f_konf,
            status_pembayaran=f_bayar,
            db_path=db_path,
        )

        if df_list.empty:
            st.info("Tidak ada transaksi yang sesuai dengan filter di atas.")
        else:
            st.dataframe(
                df_list[
                    [
                        "id_transaksi",
                        "tanggal",
                        "kode_tenongan",
                        "nama_tenongan",
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
                ].rename(
                    columns={
                        "id_transaksi": "ID",
                        "tanggal": "Tanggal",
                        "kode_tenongan": "Kode",
                        "nama_tenongan": "Tenongan",
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

            col_ex1, col_ex2, _ = st.columns([1.4, 1.4, 2.5])
            with col_ex1:
                st.download_button(
                    "⬇️ Ekspor Transaksi ke Excel (.xlsx)",
                    data=dfs_ke_excel_bytes({"Transaksi_Harian": df_list}),
                    file_name=f"transaksi_taiyaki_{tgl_iso}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with col_ex2:
                st.download_button(
                    "⬇️ Ekspor Transaksi ke CSV",
                    data=df_ke_csv_bytes(df_list),
                    file_name=f"transaksi_taiyaki_{tgl_iso}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            st.divider()
            st.markdown("##### 🗑️ Hapus Transaksi (Dengan Konfirmasi & Audit Log)")
            col_del1, col_del2, col_del3 = st.columns([1.8, 1.8, 1.2])
            with col_del1:
                opsi_del = df_list["id_transaksi"].tolist()
                peta_del = {
                    int(r["id_transaksi"]): f"ID #{r['id_transaksi']} | {r['tanggal']} | {r['nama_tenongan']} (Titip:{r['qty_titip']}, Laku:{r['qty_laku']})"
                    for _, r in df_list.iterrows()
                }
                id_hapus = st.selectbox(
                    "Pilih ID Transaksi yang Akan Dihapus",
                    options=opsi_del,
                    format_func=lambda x: peta_del.get(x, str(x)),
                )
            with col_del2:
                alasan_hapus = st.text_input("Alasan Penghapusan (Wajib untuk Audit)", value="Salah input tanggal/tenongan")
                konfirmasi_cek = st.checkbox("Saya yakin ingin menghapus transaksi ini")
            with col_del3:
                st.write("")
                st.write("")
                if st.button("🗑️ Hapus Transaksi", type="secondary", use_container_width=True):
                    if not konfirmasi_cek:
                        st.warning("Centang kotak konfirmasi terlebih dahulu sebelum menghapus data.")
                    else:
                        ok_d, msg_d = hapus_transaksi_harian(
                            id_transaksi=int(id_hapus),
                            alasan=alasan_hapus,
                            db_path=db_path,
                        )
                        if ok_d:
                            st.success(msg_d)
                            st.rerun()
                        else:
                            st.error(msg_d)

    # ==========================================================================
    # TAB 4: IMPOR WORKBOOK projectapp.xlsx & RIWAYAT AUDIT LOG
    # ==========================================================================
    with tab_impor_audit:
        col_imp, col_aud = st.columns([1.3, 1.7])

        with col_imp:
            st.markdown("#### 📥 Impor Data Awal `projectapp.xlsx`")
            st.write(
                "Muat seluruh data historis dari file Excel **`projectapp.xlsx`** Anda "
                "(Sheet `TAIYAKI` Blok 1 & Blok 2 beserta Sheet `kulakan`) langsung ke database SQLite."
            )

            ci1, ci2 = st.columns(2)
            with ci1:
                thn_b1 = st.number_input("Tahun Tabel Blok 1", min_value=2020, max_value=2030, value=2026)
                bln_b1 = st.selectbox("Bulan Tabel Blok 1", list(range(1, 13)), index=7)
            with ci2:
                thn_b2 = st.number_input("Tahun Tabel Blok 2", min_value=2020, max_value=2030, value=2026)
                bln_b2 = st.selectbox("Bulan Tabel Blok 2", list(range(1, 13)), index=8)

            up_wb = st.file_uploader("Unggah File projectapp.xlsx (Opsional jika file lokal sudah ada)", type=["xlsx"])

            # Cek apakah projectapp.xlsx tersedia di root atau parent folder
            kandidat_lokal = [
                Path("projectapp.xlsx"),
                Path(__file__).resolve().parent.parent.parent / "projectapp.xlsx",
                Path(__file__).resolve().parent.parent / "projectapp.xlsx",
            ]
            path_lokal_ada = next((p for p in kandidat_lokal if p.exists()), None)

            if st.button("🚀 Jalankan Impor Workbook projectapp.xlsx", type="primary", use_container_width=True):
                sumber_file = up_wb if up_wb is not None else path_lokal_ada
                if sumber_file is None:
                    st.error("Silakan unggah file projectapp.xlsx terlebih dahulu.")
                else:
                    ok_w, msg_w, ringkasan_w = impor_workbook_projectapp(
                        file_source=sumber_file,
                        tahun_blok1=int(thn_b1),
                        bulan_blok1=int(bln_b1),
                        tahun_blok2=int(thn_b2),
                        bulan_blok2=int(bln_b2),
                        impor_belanja_kulakan=True,
                        tanggal_belanja_awal=f"{int(thn_b1)}-{int(bln_b1):02d}-01",
                        db_path=db_path,
                    )
                    if ok_w:
                        st.success(msg_w)
                        with st.expander("Lihat Detail Ringkasan & Penanganan Anomali Data Excel", expanded=True):
                            st.json(ringkasan_w)
                    else:
                        st.error(msg_w)

        with col_aud:
            st.markdown("#### 🛡️ Riwayat Audit Log Perubahan Data")
            st.caption("Mencatat secara otomatis setiap penambahan, koreksi nilai, penghapusan, dan impor transaksi.")
            df_audit = query_df(
                """
                SELECT id_log, waktu_perubahan, tabel_target, id_record, aksi, keterangan, data_lama_json, data_baru_json
                FROM audit_log
                ORDER BY id_log DESC
                LIMIT 100;
                """,
                db_path=db_path,
            )
            if df_audit.empty:
                st.info("Belum ada catatan di Audit Log.")
            else:
                st.dataframe(
                    df_audit.rename(
                        columns={
                            "id_log": "ID Log",
                            "waktu_perubahan": "Waktu",
                            "tabel_target": "Tabel",
                            "id_record": "ID Record",
                            "aksi": "Aksi",
                            "keterangan": "Keterangan",
                            "data_lama_json": "Nilai Sebelum",
                            "data_baru_json": "Nilai Sesudah",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=380,
                )


render_halaman_input_harian()
