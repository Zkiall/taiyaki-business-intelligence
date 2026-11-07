"""
pages/5_Belanja_Bahan_Baku.py
Modul Pengadaan Bahan Baku, Riwayat & Perbandingan Harga Antarperiode,
serta Manajemen Inventori Stok Masuk & Pemakaian Produksi.
Memisahkan dengan tegas:
1. Pengeluaran kas pembelian bahan baku (Arus Kas Keluar & Penambahan Persediaan)
2. Konsumsi bahan dalam produksi (Mutasi Stok Keluar)
3. Pembebanan HPP pada produk terjual (agar laba tidak dihitung dua kali).
"""

from datetime import date
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
from utils import bersihkan_teks, format_angka, format_persen, format_rupiah


def buat_nomor_nota_belanja(tanggal_iso: str, db_path: Optional[Path] = None) -> str:
    """Membuat nomor nota belanja bahan baku unik dengan format BLJ-YYYYMMDD-XXX."""
    tgl_compact = tanggal_iso.replace("-", "")[:8]
    prefix = f"BLJ-{tgl_compact}-"
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM belanja_bahan_baku WHERE nomor_nota LIKE ?;",
            (f"{prefix}%",),
        ).fetchone()
        urut = int(row["cnt"]) + 1
        kandidat = f"{prefix}{urut:03d}"
        while conn.execute(
            "SELECT 1 FROM belanja_bahan_baku WHERE nomor_nota = ?;", (kandidat,)
        ).fetchone():
            urut += 1
            kandidat = f"{prefix}{urut:03d}"
        return kandidat


def tambah_master_bahan_baru(
    nama_bahan: str,
    kategori: str,
    satuan_beli: str,
    isi_per_satuan: float,
    satuan_pakai: str,
    harga_acuan: float,
    stok_awal: float,
    stok_minimum: float,
    catatan: str = "",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Menambahkan master bahan baku/kemasan baru secara dinamis."""
    nama_bersih = bersihkan_teks(nama_bahan)
    if not nama_bersih:
        return False, "Nama bahan baku tidak boleh kosong."
    if isi_per_satuan <= 0:
        return False, "Isi konversi per satuan beli harus lebih dari 0."
    if harga_acuan < 0 or stok_awal < 0 or stok_minimum < 0:
        return False, "Harga acuan dan stok tidak boleh bernilai negatif."

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cek = cursor.execute(
            "SELECT kode_bahan FROM master_bahan WHERE LOWER(TRIM(nama_bahan)) = LOWER(?);",
            (nama_bersih,),
        ).fetchone()
        if cek:
            return False, f"Bahan '{nama_bersih}' sudah terdaftar dengan kode {cek['kode_bahan']}."

        row_max = cursor.execute("SELECT COALESCE(MAX(id_bahan), 0) AS m FROM master_bahan;").fetchone()
        urut = int(row_max["m"]) + 1
        kode_baru = f"BHN-{urut:03d}"
        while cursor.execute("SELECT 1 FROM master_bahan WHERE kode_bahan = ?;", (kode_baru,)).fetchone():
            urut += 1
            kode_baru = f"BHN-{urut:03d}"

        cursor.execute(
            """
            INSERT INTO master_bahan (
                kode_bahan, nama_bahan, kategori, satuan_beli, isi_per_satuan,
                satuan_pakai, harga_acuan, stok_saat_ini, stok_minimum, status_aktif, catatan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?);
            """,
            (
                kode_baru,
                nama_bersih,
                kategori,
                bersihkan_teks(satuan_beli) or "kg",
                float(isi_per_satuan),
                bersihkan_teks(satuan_pakai) or "gram",
                float(harga_acuan),
                float(stok_awal),
                float(stok_minimum),
                bersihkan_teks(catatan),
            ),
        )
        id_b = int(cursor.lastrowid)
        if stok_awal > 0:
            cursor.execute(
                """
                INSERT INTO mutasi_stok_bhn (id_bahan, tanggal, jenis_mutasi, qty_mutasi, keterangan)
                VALUES (?, date('now'), 'PENYESUAIAN', ?, 'Stok awal saat pendaftaran bahan baru');
                """,
                (id_b, float(stok_awal)),
            )
        catat_audit_log(
            conn,
            tabel_target="master_bahan",
            id_record=id_b,
            aksi="INSERT",
            data_baru={"kode_bahan": kode_baru, "nama_bahan": nama_bersih, "harga_acuan": harga_acuan},
            keterangan="Tambah master bahan baku baru",
        )
        return True, f"Bahan baku '{nama_bersih}' ({kode_baru}) berhasil ditambahkan."


def catat_pembelian_bahan(
    tanggal: str,
    nomor_nota: str,
    id_bahan: int,
    qty_beli: float,
    harga_satuan: float,
    pemasok: str,
    metode_bayar: str,
    jumlah_dibayar: float,
    tambah_ke_stok: bool = True,
    perbarui_harga_acuan: bool = True,
    catatan: str = "",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Mencatat transaksi belanja bahan baku, otomatis menambah stok gudang (jika diaktifkan),
    mencatat mutasi masuk, dan memperbarui harga acuan terbaru bahan.
    """
    if qty_beli <= 0:
        return False, "Kuantitas pembelian harus lebih dari 0."
    if harga_satuan < 0 or jumlah_dibayar < 0:
        return False, "Harga satuan dan nominal pembayaran tidak boleh negatif."

    total_beli = round(float(qty_beli) * float(harga_satuan), 2)
    dibayar = min(float(jumlah_dibayar), total_beli) if total_beli > 0 else float(jumlah_dibayar)
    if total_beli == 0 or dibayar >= total_beli - 1e-4:
        status_bayar = "LUNAS"
        dibayar = total_beli
    elif dibayar > 0:
        status_bayar = "SEBAGIAN"
    else:
        status_bayar = "BELUM_BAYAR"

    no_nota = bersihkan_teks(nomor_nota).upper() or buat_nomor_nota_belanja(tanggal, db_path)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if cursor.execute("SELECT 1 FROM belanja_bahan_baku WHERE nomor_nota = ?;", (no_nota,)).fetchone():
            return False, f"Nomor nota '{no_nota}' sudah digunakan."

        bhn = cursor.execute(
            "SELECT nama_bahan, satuan_beli, harga_acuan, stok_saat_ini FROM master_bahan WHERE id_bahan = ?;",
            (int(id_bahan),),
        ).fetchone()
        if not bhn:
            return False, "Bahan baku tidak ditemukan di tabel master."

        cursor.execute(
            """
            INSERT INTO belanja_bahan_baku (
                nomor_nota, tanggal, id_bahan, qty_beli, satuan_beli,
                harga_satuan, total_pembelian, pemasok, metode_bayar,
                jumlah_dibayar, status_bayar, update_ke_stok, catatan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                no_nota,
                str(tanggal)[:10],
                int(id_bahan),
                float(qty_beli),
                bhn["satuan_beli"],
                float(harga_satuan),
                total_beli,
                bersihkan_teks(pemasok),
                metode_bayar,
                round(dibayar, 2),
                status_bayar,
                1 if tambah_ke_stok else 0,
                bersihkan_teks(catatan),
            ),
        )
        id_blj = int(cursor.lastrowid)

        if tambah_ke_stok:
            cursor.execute(
                """
                UPDATE master_bahan
                SET stok_saat_ini = stok_saat_ini + ?, updated_at = datetime('now', 'localtime')
                WHERE id_bahan = ?;
                """,
                (float(qty_beli), int(id_bahan)),
            )
            cursor.execute(
                """
                INSERT INTO mutasi_stok_bhn (id_bahan, tanggal, jenis_mutasi, qty_mutasi, keterangan)
                VALUES (?, ?, 'MASUK_BELANJA', ?, ?);
                """,
                (int(id_bahan), str(tanggal)[:10], float(qty_beli), f"Belanja Nota {no_nota}"),
            )

        if perbarui_harga_acuan and harga_satuan > 0:
            cursor.execute(
                """
                UPDATE master_bahan
                SET harga_acuan = ?, updated_at = datetime('now', 'localtime')
                WHERE id_bahan = ?;
                """,
                (float(harga_satuan), int(id_bahan)),
            )

        catat_audit_log(
            conn,
            tabel_target="belanja_bahan_baku",
            id_record=id_blj,
            aksi="INSERT",
            data_baru={
                "nomor_nota": no_nota,
                "nama_bahan": bhn["nama_bahan"],
                "qty_beli": qty_beli,
                "harga_satuan": harga_satuan,
                "total_pembelian": total_beli,
            },
            keterangan=f"Belanja bahan {bhn['nama_bahan']}",
        )
        return True, f"Belanja '{bhn['nama_bahan']}' ({qty_beli} {bhn['satuan_beli']}) senilai {format_rupiah(total_beli)} berhasil disimpan."


def catat_mutasi_pemakaian_bahan(
    id_bahan: int,
    tanggal: str,
    jenis_mutasi: str,
    qty_mutasi: float,
    keterangan: str,
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Mencatat pemakaian bahan dalam produksi (KELUAR_PRODUKSI) atau penyesuaian fisik (PENYESUAIAN).
    """
    if qty_mutasi <= 0:
        return False, "Jumlah mutasi harus lebih besar dari 0."

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        bhn = cursor.execute(
            "SELECT nama_bahan, satuan_beli, stok_saat_ini FROM master_bahan WHERE id_bahan = ?;",
            (int(id_bahan),),
        ).fetchone()
        if not bhn:
            return False, "Bahan baku tidak ditemukan."

        stok_lama = float(bhn["stok_saat_ini"])
        if jenis_mutasi == "KELUAR_PRODUKSI":
            if qty_mutasi > stok_lama:
                return (
                    False,
                    f"Stok '{bhn['nama_bahan']}' saat ini ({stok_lama:.2f} {bhn['satuan_beli']}) "
                    f"tidak mencukupi untuk pengeluaran {qty_mutasi:.2f} {bhn['satuan_beli']}.",
                )
            stok_baru = stok_lama - float(qty_mutasi)
            delta_catat = -float(qty_mutasi)
        else:
            # PENYESUAIAN: set nilai stok aktual baru atau tambah
            stok_baru = float(qty_mutasi)
            delta_catat = stok_baru - stok_lama

        cursor.execute(
            """
            UPDATE master_bahan
            SET stok_saat_ini = ?, updated_at = datetime('now', 'localtime')
            WHERE id_bahan = ?;
            """,
            (round(stok_baru, 4), int(id_bahan)),
        )
        cursor.execute(
            """
            INSERT INTO mutasi_stok_bhn (id_bahan, tanggal, jenis_mutasi, qty_mutasi, keterangan)
            VALUES (?, ?, ?, ?, ?);
            """,
            (
                int(id_bahan),
                str(tanggal)[:10],
                jenis_mutasi,
                round(delta_catat, 4),
                bersihkan_teks(keterangan) or "Pencatatan mutasi stok produksi",
            ),
        )
        return (
            True,
            f"Stok '{bhn['nama_bahan']}' berhasil diperbarui: {stok_lama:.2f} -> {stok_baru:.2f} {bhn['satuan_beli']}.",
        )


def render_halaman_belanja_bahan() -> None:
    db_path = get_active_db_path()
    tampilkan_header_halaman(
        judul="Belanja Bahan Baku & Inventori Produksi",
        subjudul="Kelola pengadaan bahan baku, pantau tren kenaikan harga bahan, dan kendalikan stok gudang produksi.",
        ikon="🥚",
    )

    df_master = query_df(
        "SELECT * FROM master_bahan ORDER BY kode_bahan ASC;",
        db_path=db_path,
    )
    df_belanja_all = query_df(
        """
        SELECT
            b.*,
            m.kode_bahan,
            m.nama_bahan,
            m.kategori
        FROM belanja_bahan_baku b
        JOIN master_bahan m ON b.id_bahan = m.id_bahan
        ORDER BY b.tanggal DESC, b.id_belanja DESC;
        """,
        db_path=db_path,
    )

    # Deteksi Peringatan Stok Minimum
    df_kritis = (
        df_master[(df_master["status_aktif"] == 1) & (df_master["stok_saat_ini"] <= df_master["stok_minimum"])]
        if not df_master.empty
        else pd.DataFrame()
    )
    if not df_kritis.empty:
        daftar_kritis_str = ", ".join(
            f"**{r['nama_bahan']}** (Sisa: {format_angka(r['stok_saat_ini'], 1)} {r['satuan_beli']}, Min: {format_angka(r['stok_minimum'], 1)})"
            for _, r in df_kritis.head(6).iterrows()
        )
        st.warning(
            f"⚠️ **Peringatan Stok Bahan Menipis ({len(df_kritis)} item mendekati/di bawah batas minimum):** "
            f"{daftar_kritis_str}"
        )

    nilai_persediaan_total = (
        float((df_master["stok_saat_ini"] * df_master["harga_acuan"]).sum()) if not df_master.empty else 0.0
    )
    total_belanja_historis = float(df_belanja_all["total_pembelian"].sum()) if not df_belanja_all.empty else 0.0
    total_kas_belanja = float(df_belanja_all["jumlah_dibayar"].sum()) if not df_belanja_all.empty else 0.0
    utang_belanja = max(0.0, total_belanja_historis - total_kas_belanja)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Total Pengadaan Bahan",
            format_rupiah(total_belanja_historis),
            f"{len(df_belanja_all)} transaksi nota belanja",
            kategori_warna="pengeluaran",
        )
    with k2:
        render_kpi_card(
            "Estimasi Nilai Persediaan",
            format_rupiah(nilai_persediaan_total),
            "Stok bahan belum terpakai (Bukan HPP)",
            kategori_warna="pendapatan",
        )
    with k3:
        render_kpi_card(
            "Bahan Perlu Restock",
            f"{len(df_kritis)} Bahan",
            f"Dari total {len(df_master)} jenis bahan terdaftar",
            kategori_warna="piutang" if len(df_kritis) > 0 else "keuntungan",
        )
    with k4:
        render_kpi_card(
            "Utang Belanja Bahan",
            format_rupiah(utang_belanja),
            f"Kas sudah dibayar: {format_rupiah(total_kas_belanja)}",
            kategori_warna="piutang" if utang_belanja > 0 else "keuntungan",
        )

    tab_catat, tab_harga, tab_stok, tab_riwayat = st.tabs(
        [
            "🛒 Catat Belanja Bahan Baku",
            "📈 Riwayat & Perbandingan Harga Bahan",
            "📦 Stok Gudang, Pemakaian Produksi & Master Bahan",
            "📋 Riwayat Nota Belanja & Ekspor Excel",
        ]
    )

    # ==========================================================================
    # TAB 1: FORM PENCATATAN BELANJA BAHAN BAKU
    # ==========================================================================
    with tab_catat:
        st.markdown("#### 🛒 Catat Pembelian Bahan Baku & Kemasan")
        st.caption(
            "Pembelian bahan baku dicatat sebagai pengeluaran kas dan penambahan stok persediaan. "
            "Biaya bahan baru dibebankan ke laba rugi (HPP) saat produk diproduksi dan terjual."
        )

        if df_master.empty:
            st.info("Belum ada master bahan baku.")
        else:
            cb1, cb2, cb3 = st.columns(3)
            with cb1:
                tgl_b = st.date_input("Tanggal Pembelian", value=date.today(), key="blj_tgl")
                nota_auto = buat_nomor_nota_belanja(tgl_b.isoformat(), db_path=db_path)
                no_nota_b = st.text_input("Nomor Nota Pembelian", value=nota_auto)
            with cb2:
                opsi_bhn = df_master["id_bahan"].tolist()
                peta_bhn = {
                    int(r["id_bahan"]): f"{r['kode_bahan']} - {r['nama_bahan']} ({r['kategori']} | Satuan: {r['satuan_beli']})"
                    for _, r in df_master.iterrows()
                }
                id_bhn_pilih = st.selectbox(
                    "Pilih Bahan Baku / Kemasan *",
                    options=opsi_bhn,
                    format_func=lambda x: peta_bhn[x],
                )
                row_bhn_aktif = df_master[df_master["id_bahan"] == id_bhn_pilih].iloc[0]
            with cb3:
                pemasok_b = st.text_input("Nama Toko / Pemasok", placeholder="Contoh: TBK Berkah Solo")
                metode_b = st.selectbox("Metode Pembayaran", ["Tunai", "Transfer Bank", "QRIS", "Tempo / Utang"])

            cq1, cq2, cq3, cq4 = st.columns(4)
            with cq1:
                qty_b = st.number_input(
                    f"Kuantitas Beli ({row_bhn_aktif['satuan_beli']}) *",
                    min_value=0.1,
                    value=1.0,
                    step=1.0,
                )
            with cq2:
                harga_sat_b = st.number_input(
                    f"Harga per {row_bhn_aktif['satuan_beli']} (Rp) *",
                    min_value=0.0,
                    value=float(row_bhn_aktif["harga_acuan"]),
                    step=500.0,
                )
            with cq3:
                total_b = round(float(qty_b) * float(harga_sat_b), 2)
                st.metric("Total Pembelian", format_rupiah(total_b))
            with cq4:
                selisih_acuan = float(harga_sat_b) - float(row_bhn_aktif["harga_acuan"])
                st.metric(
                    "Harga Acuan Sebelumnya",
                    format_rupiah(row_bhn_aktif["harga_acuan"]),
                    delta=f"{format_rupiah(selisih_acuan)} vs acuan" if selisih_acuan != 0 else "Tetap",
                    delta_color="inverse",
                )

            co1, co2, co3 = st.columns(3)
            with co1:
                lunas_b = st.checkbox("Pembayaran Lunas Penuh", value=True)
                dibayar_b = (
                    total_b
                    if lunas_b
                    else float(
                        st.number_input(
                            "Jumlah Dibayar Saat Ini (Rp)",
                            min_value=0.0,
                            max_value=float(total_b),
                            value=0.0,
                            step=1000.0,
                        )
                    )
                )
            with co2:
                up_stok_b = st.checkbox(
                    f"Tambahkan otomatis ke Stok Gudang (+{qty_b} {row_bhn_aktif['satuan_beli']})",
                    value=True,
                )
            with co3:
                up_harga_b = st.checkbox(
                    "Perbarui Harga Acuan Master Bahan dengan harga baru ini",
                    value=True,
                )

            cat_b = st.text_input("Catatan Pembelian", placeholder="Contoh: Beli kemasan 1 pak + tepung 6 kg...")

            if st.button("💾 Simpan Transaksi Belanja Bahan", type="primary", use_container_width=True):
                ok_blj, msg_blj = catat_pembelian_bahan(
                    tanggal=tgl_b.isoformat(),
                    nomor_nota=no_nota_b,
                    id_bahan=int(id_bhn_pilih),
                    qty_beli=float(qty_b),
                    harga_satuan=float(harga_sat_b),
                    pemasok=pemasok_b,
                    metode_bayar=metode_b,
                    jumlah_dibayar=float(dibayar_b),
                    tambah_ke_stok=up_stok_b,
                    perbarui_harga_acuan=up_harga_b,
                    catatan=cat_b,
                    db_path=db_path,
                )
                if ok_blj:
                    st.success(msg_blj)
                    st.rerun()
                else:
                    st.error(msg_blj)

    # ==========================================================================
    # TAB 2: RIWAYAT & PERBANDINGAN HARGA BAHAN ANTARPERIODE
    # ==========================================================================
    with tab_harga:
        st.markdown("#### 📈 Evaluasi Riwayat & Perbandingan Harga Bahan Antarperiode")
        if df_belanja_all.empty:
            st.info("Belum ada riwayat pembelian bahan baku untuk dianalisis.")
        else:
            df_urut = df_belanja_all.sort_values(["id_bahan", "tanggal", "id_belanja"], ascending=[True, True, True]).copy()
            df_urut["harga_sebelumnya"] = df_urut.groupby("id_bahan")["harga_satuan"].shift(1)
            df_urut["selisih_harga_rp"] = df_urut["harga_satuan"] - df_urut["harga_sebelumnya"]
            df_urut["perubahan_persen"] = (
                (df_urut["selisih_harga_rp"] / df_urut["harga_sebelumnya"]) * 100.0
            ).fillna(0.0)

            # Ringkasan perbandingan harga awal vs harga terakhir per bahan
            rekap_harga = (
                df_urut.groupby(["id_bahan", "kode_bahan", "nama_bahan", "kategori", "satuan_beli"], as_index=False)
                .agg(
                    jml_pembelian=("id_belanja", "count"),
                    harga_awal=("harga_satuan", "first"),
                    harga_terakhir=("harga_satuan", "last"),
                    harga_rata2=("harga_satuan", "mean"),
                    harga_min=("harga_satuan", "min"),
                    harga_max=("harga_satuan", "max"),
                    total_qty_dibeli=("qty_beli", "sum"),
                    total_pengeluaran=("total_pembelian", "sum"),
                )
            )
            rekap_harga["naik_turun_rp"] = rekap_harga["harga_terakhir"] - rekap_harga["harga_awal"]
            rekap_harga["naik_turun_persen"] = rekap_harga.apply(
                lambda r: round((r["naik_turun_rp"] / r["harga_awal"] * 100.0), 2) if r["harga_awal"] > 0 else 0.0,
                axis=1,
            )

            st.dataframe(
                rekap_harga.rename(
                    columns={
                        "kode_bahan": "Kode",
                        "nama_bahan": "Nama Bahan",
                        "kategori": "Kategori",
                        "satuan_beli": "Satuan",
                        "jml_pembelian": "Frekuensi Beli",
                        "harga_awal": "Harga Awal (Rp)",
                        "harga_terakhir": "Harga Terakhir (Rp)",
                        "harga_rata2": "Rata-rata (Rp)",
                        "harga_min": "Termurah (Rp)",
                        "harga_max": "Termahal (Rp)",
                        "naik_turun_rp": "Perubahan (Rp)",
                        "naik_turun_persen": "Perubahan (%)",
                        "total_qty_dibeli": "Total Qty",
                        "total_pengeluaran": "Total Belanja (Rp)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            fig_hrg = px.line(
                df_urut,
                x="tanggal",
                y="harga_satuan",
                color="nama_bahan",
                markers=True,
                title="Grafik Tren Pergerakan Harga Satuan Bahan Baku",
                labels={"tanggal": "Tanggal Belanja", "harga_satuan": "Harga Satuan (Rp)", "nama_bahan": "Bahan"},
            )
            st.plotly_chart(fig_hrg, use_container_width=True)

    # ==========================================================================
    # TAB 3: STOK GUDANG, MUTASI PEMAKAIAN PRODUKSI & TAMBAH MASTER BAHAN
    # ==========================================================================
    with tab_stok:
        st.markdown("#### 📦 Status Stok Bahan Baku Saat Ini")
        if not df_master.empty:
            df_stok_view = df_master.copy()
            df_stok_view["nilai_stok_rp"] = df_stok_view["stok_saat_ini"] * df_stok_view["harga_acuan"]
            df_stok_view["Status Stok"] = df_stok_view.apply(
                lambda r: "⚠️ PERLU RESTOCK" if r["stok_saat_ini"] <= r["stok_minimum"] else "✅ AMAN",
                axis=1,
            )
            st.dataframe(
                df_stok_view[
                    [
                        "kode_bahan",
                        "nama_bahan",
                        "kategori",
                        "stok_saat_ini",
                        "stok_minimum",
                        "satuan_beli",
                        "Status Stok",
                        "harga_acuan",
                        "nilai_stok_rp",
                        "isi_per_satuan",
                        "satuan_pakai",
                    ]
                ].rename(
                    columns={
                        "kode_bahan": "Kode",
                        "nama_bahan": "Nama Bahan",
                        "kategori": "Kategori",
                        "stok_saat_ini": "Stok Saat Ini",
                        "stok_minimum": "Batas Minimum",
                        "satuan_beli": "Satuan Beli",
                        "harga_acuan": "Harga Acuan (Rp)",
                        "nilai_stok_rp": "Nilai Persediaan (Rp)",
                        "isi_per_satuan": "Isi Konversi",
                        "satuan_pakai": "Satuan Resep",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.divider()
        col_mutasi, col_master_baru = st.columns(2)

        with col_mutasi:
            st.markdown("##### 🔄 Catat Pemakaian Bahan Produksi / Opname Stok")
            st.caption("Gunakan form ini saat bahan diambil dari gudang untuk produksi atau saat stok fisik disesuaikan.")
            with st.form("form_mutasi_stok"):
                id_b_mut = st.selectbox(
                    "Pilih Bahan Baku",
                    options=df_master["id_bahan"].tolist(),
                    format_func=lambda x: f"{df_master.loc[df_master['id_bahan']==x, 'nama_bahan'].iloc[0]} "
                    f"(Stok: {df_master.loc[df_master['id_bahan']==x, 'stok_saat_ini'].iloc[0]:.2f} "
                    f"{df_master.loc[df_master['id_bahan']==x, 'satuan_beli'].iloc[0]})",
                )
                tgl_mut = st.date_input("Tanggal Mutasi", value=date.today())
                jns_mut = st.selectbox(
                    "Jenis Mutasi Stok",
                    options=["KELUAR_PRODUKSI", "PENYESUAIAN"],
                    format_func=lambda x: (
                        "Keluar untuk Produksi (Kurangi Stok)"
                        if x == "KELUAR_PRODUKSI"
                        else "Penyesuaian / Stok Opname (Set Nilai Stok Aktual Baru)"
                    ),
                )
                qty_mut = st.number_input("Jumlah (dalam Satuan Beli)", min_value=0.01, value=1.0, step=0.5)
                ket_mut = st.text_input("Keterangan", placeholder="Contoh: Pemakaian produksi 1 batch taiyaki")
                btn_mut = st.form_submit_button("💾 Simpan Mutasi Stok", type="primary", use_container_width=True)
                if btn_mut:
                    ok_m, msg_m = catat_mutasi_pemakaian_bahan(
                        id_bahan=int(id_b_mut),
                        tanggal=tgl_mut.isoformat(),
                        jenis_mutasi=jns_mut,
                        qty_mutasi=float(qty_mut),
                        keterangan=ket_mut,
                        db_path=db_path,
                    )
                    if ok_m:
                        st.success(msg_m)
                        st.rerun()
                    else:
                        st.error(msg_m)

        with col_master_baru:
            st.markdown("##### ➕ Tambah Master Bahan Baku / Kemasan Baru")
            st.caption("Tambahkan jenis bahan baru secara dinamis jika belum ada di katalog 18 bahan awal.")
            with st.form("form_tambah_master_bahan", clear_on_submit=True):
                nm_bhn_baru = st.text_input("Nama Bahan Baru *", placeholder="Contoh: Keju Cheddar / Gas LPG 3 Kg")
                kat_bhn_baru = st.selectbox(
                    "Kategori Bahan",
                    ["Bahan Utama", "Bahan Isian", "Topping & Isian", "Kemasan", "Energi & Gas", "Bahan Tambahan"],
                )
                cm1, cm2 = st.columns(2)
                with cm1:
                    sat_beli_baru = st.text_input("Satuan Beli *", value="kg", placeholder="kg / pak / tabung")
                    hrg_acuan_baru = st.number_input("Harga Beli Acuan (Rp)", min_value=0.0, value=20000.0, step=1000.0)
                    stok_awal_baru = st.number_input("Stok Awal Saat Ini", min_value=0.0, value=0.0, step=1.0)
                with cm2:
                    sat_pakai_baru = st.text_input("Satuan Pakai di Resep *", value="gram", placeholder="gram / pcs / ml")
                    isi_konv_baru = st.number_input("Isi Satuan Pakai per 1 Satuan Beli", min_value=1.0, value=1000.0, step=100.0)
                    stok_min_baru = st.number_input("Batas Stok Minimum Peringatan", min_value=0.0, value=1.0, step=0.5)

                btn_bhn_baru = st.form_submit_button("➕ Simpan Bahan Baru", type="primary", use_container_width=True)
                if btn_bhn_baru:
                    ok_nb, msg_nb = tambah_master_bahan_baru(
                        nama_bahan=nm_bhn_baru,
                        kategori=kat_bhn_baru,
                        satuan_beli=sat_beli_baru,
                        isi_per_satuan=float(isi_konv_baru),
                        satuan_pakai=sat_pakai_baru,
                        harga_acuan=float(hrg_acuan_baru),
                        stok_awal=float(stok_awal_baru),
                        stok_minimum=float(stok_min_baru),
                        db_path=db_path,
                    )
                    if ok_nb:
                        st.success(msg_nb)
                        st.rerun()
                    else:
                        st.error(msg_nb)

    # ==========================================================================
    # TAB 4: RIWAYAT NOTA BELANJA & EKSPOR EXCEL
    # ==========================================================================
    with tab_riwayat:
        bounds_b = query_df(
            "SELECT MIN(tanggal) AS tmin, MAX(tanggal) AS tmax FROM belanja_bahan_baku;",
            db_path=db_path,
        )
        tmin_b = bounds_b.iloc[0]["tmin"] if not bounds_b.empty else None
        tmax_b = bounds_b.iloc[0]["tmax"] if not bounds_b.empty else None
        tgl_m_b, tgl_s_b = pilih_rentang_tanggal(tmin_b, tmax_b, key_prefix="belanja_bhn")

        df_blj_filter = (
            df_belanja_all[(df_belanja_all["tanggal"] >= tgl_m_b) & (df_belanja_all["tanggal"] <= tgl_s_b)]
            if not df_belanja_all.empty
            else pd.DataFrame()
        )

        if df_blj_filter.empty:
            st.info("Tidak ada nota belanja bahan baku pada rentang tanggal yang dipilih.")
        else:
            st.dataframe(
                df_blj_filter[
                    [
                        "nomor_nota",
                        "tanggal",
                        "kode_bahan",
                        "nama_bahan",
                        "kategori",
                        "qty_beli",
                        "satuan_beli",
                        "harga_satuan",
                        "total_pembelian",
                        "jumlah_dibayar",
                        "status_bayar",
                        "pemasok",
                        "metode_bayar",
                        "catatan",
                    ]
                ].rename(
                    columns={
                        "nomor_nota": "No. Nota",
                        "tanggal": "Tanggal",
                        "kode_bahan": "Kode",
                        "nama_bahan": "Nama Bahan",
                        "kategori": "Kategori",
                        "qty_beli": "Qty",
                        "satuan_beli": "Satuan",
                        "harga_satuan": "Harga Satuan (Rp)",
                        "total_pembelian": "Total Belanja (Rp)",
                        "jumlah_dibayar": "Dibayar (Rp)",
                        "status_bayar": "Status",
                        "pemasok": "Pemasok",
                        "metode_bayar": "Metode",
                        "catatan": "Catatan",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            ce1, ce2, _ = st.columns([1.4, 1.4, 2.5])
            with ce1:
                st.download_button(
                    "⬇️ Ekspor Belanja & Stok ke Excel (.xlsx)",
                    data=dfs_ke_excel_bytes(
                        {"Nota_Belanja_Bahan": df_blj_filter, "Stok_Master_Bahan": df_master}
                    ),
                    file_name="belanja_dan_stok_bahan_taiyaki.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with ce2:
                st.download_button(
                    "⬇️ Ekspor Nota Belanja ke CSV",
                    data=df_ke_csv_bytes(df_blj_filter),
                    file_name="nota_belanja_bahan_taiyaki.csv",
                    mime="text/csv",
                    use_container_width=True,
                )


render_halaman_belanja_bahan()