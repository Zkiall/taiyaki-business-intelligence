"""
data_processing.py
Modul pengolahan data, operasi CRUD Tenongan & Transaksi Harian,
importir khusus workbook projectapp.xlsx (Sheet TAIYAKI Blok 1 & 2, kulakan, kalkulasi),
impor daftar tenongan dari Excel/CSV, serta ekspor laporan ke Excel (.xlsx) dan CSV.
"""

import calendar
import io
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

import openpyxl
import pandas as pd

from calculations import hitung_transaksi_item
from database import (
    PROD_DB_PATH,
    catat_audit_log,
    get_connection,
    get_setting,
    query_df,
)
from utils import (
    bersihkan_teks,
    konversi_angka_aman,
    validasi_kuantitas_transaksi,
)


# ==============================================================================
# 1. OPERASI CRUD & RINGKASAN TENONGAN
# ==============================================================================

def get_daftar_tenongan_lengkap(
    db_path: Optional[Path] = None,
    hanya_aktif: bool = False,
    kata_kunci: str = "",
    filter_pola: str = "SEMUA",
) -> pd.DataFrame:
    """
    Mengambil daftar tenongan beserta agregasi ringkasan historisnya:
    total titip, total laku, total retur, omzet kotor, setoran diterima, dan piutang.
    """
    kondisi = []
    params: List[Any] = []

    if hanya_aktif:
        kondisi.append("t.status_aktif = 1")
    if filter_pola and filter_pola != "SEMUA":
        kondisi.append("t.pola_titip = ?")
        params.append(filter_pola)
    if kata_kunci.strip():
        kw = f"%{kata_kunci.strip()}%"
        kondisi.append(
            "(t.nama_tenongan LIKE ? OR t.kode_tenongan LIKE ? OR t.nama_pemilik LIKE ? OR t.alamat_lokasi LIKE ?)"
        )
        params.extend([kw, kw, kw, kw])

    where_sql = f"WHERE {' AND '.join(kondisi)}" if kondisi else ""

    sql = f"""
        SELECT
            t.id_tenongan,
            t.no_urut,
            t.kode_tenongan,
            t.nama_tenongan,
            t.nama_pemilik,
            t.kontak,
            t.alamat_lokasi,
            t.tgl_mulai,
            t.harga_default,
            t.komisi_default,
            t.pola_titip,
            t.kuota_default,
            t.status_aktif,
            t.catatan,
            COUNT(tr.id_transaksi) AS total_hari_transaksi,
            COALESCE(SUM(tr.qty_titip), 0) AS total_titip,
            COALESCE(SUM(CASE WHEN tr.status_konfirmasi = 'TERKONFIRMASI' THEN tr.qty_titip ELSE 0 END), 0) AS titip_terkonfirmasi,
            COALESCE(SUM(tr.qty_laku), 0) AS total_laku,
            COALESCE(SUM(tr.qty_retur), 0) AS total_retur,
            COALESCE(SUM(CASE WHEN tr.status_konfirmasi = 'BELUM_KONFIRMASI' THEN tr.qty_titip ELSE 0 END), 0) AS belum_konfirmasi_pcs,
            COALESCE(SUM(tr.omzet_kotor), 0.0) AS total_omzet,
            COALESCE(SUM(tr.setoran_harapan), 0.0) AS total_setoran_harapan,
            COALESCE(SUM(tr.uang_setoran), 0.0) AS total_uang_setoran,
            COALESCE(SUM(tr.piutang), 0.0) AS total_piutang
        FROM tenongan t
        LEFT JOIN transaksi_harian tr ON t.id_tenongan = tr.id_tenongan
        {where_sql}
        GROUP BY t.id_tenongan
        ORDER BY COALESCE(t.no_urut, 9999) ASC, t.id_tenongan ASC;
    """
    df = query_df(sql, tuple(params), db_path=db_path)
    if not df.empty:
        df["sell_through_persen"] = df.apply(
            lambda r: round((r["total_laku"] / r["titip_terkonfirmasi"] * 100.0), 2)
            if r["titip_terkonfirmasi"] > 0
            else 0.0,
            axis=1,
        )
        df["return_rate_persen"] = df.apply(
            lambda r: round((r["total_retur"] / r["titip_terkonfirmasi"] * 100.0), 2)
            if r["titip_terkonfirmasi"] > 0
            else 0.0,
            axis=1,
        )
    return df


def tambah_tenongan_baru(
    nama_tenongan: str,
    kode_tenongan: str = "",
    nama_pemilik: str = "",
    kontak: str = "",
    alamat_lokasi: str = "",
    tgl_mulai: Optional[str] = None,
    harga_default: float = 2000.0,
    komisi_default: float = 0.0,
    pola_titip: str = "HARIAN",
    kuota_default: int = 4,
    status_aktif: int = 1,
    catatan: str = "",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str, Optional[int]]:
    """
    Menambahkan tenongan baru secara dinamis ke database tanpa mengubah kode program.
    Jika kode_tenongan kosong, dibuat otomatis dengan format TNG-XXX.
    """
    nama_bersih = bersihkan_teks(nama_tenongan)
    if not nama_bersih:
        return False, "Nama tenongan tidak boleh kosong.", None
    if harga_default < 0 or komisi_default < 0:
        return False, "Harga jual dan komisi tidak boleh bernilai negatif.", None
    if kuota_default < 0:
        return False, "Kuota titip default tidak boleh negatif.", None

    tgl_str = tgl_mulai or date.today().isoformat()

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        # Cek duplikasi nama (case-insensitive)
        cek = cursor.execute(
            "SELECT id_tenongan, kode_tenongan FROM tenongan WHERE LOWER(TRIM(nama_tenongan)) = LOWER(?);",
            (nama_bersih,),
        ).fetchone()
        if cek:
            return False, f"Tenongan dengan nama '{nama_bersih}' sudah terdaftar ({cek['kode_tenongan']}).", None

        max_row = cursor.execute(
            "SELECT COALESCE(MAX(no_urut), 0) AS max_no, COALESCE(MAX(id_tenongan), 0) AS max_id FROM tenongan;"
        ).fetchone()
        no_urut_baru = int(max_row["max_no"]) + 1
        next_num = max(no_urut_baru, int(max_row["max_id"]) + 1)

        kode_bersih = bersihkan_teks(kode_tenongan).upper()
        if not kode_bersih:
            kode_bersih = f"TNG-{next_num:03d}"
            while cursor.execute("SELECT 1 FROM tenongan WHERE kode_tenongan = ?;", (kode_bersih,)).fetchone():
                next_num += 1
                kode_bersih = f"TNG-{next_num:03d}"
        else:
            if cursor.execute("SELECT 1 FROM tenongan WHERE kode_tenongan = ?;", (kode_bersih,)).fetchone():
                return False, f"Kode tenongan '{kode_bersih}' sudah digunakan.", None

        cursor.execute(
            """
            INSERT INTO tenongan (
                no_urut, kode_tenongan, nama_tenongan, nama_pemilik, kontak,
                alamat_lokasi, tgl_mulai, harga_default, komisi_default,
                pola_titip, kuota_default, status_aktif, catatan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                no_urut_baru,
                kode_bersih,
                nama_bersih,
                bersihkan_teks(nama_pemilik),
                bersihkan_teks(kontak),
                bersihkan_teks(alamat_lokasi),
                tgl_str,
                float(harga_default),
                float(komisi_default),
                pola_titip,
                int(kuota_default),
                1 if status_aktif else 0,
                bersihkan_teks(catatan),
            ),
        )
        new_id = cursor.lastrowid
        catat_audit_log(
            conn,
            tabel_target="tenongan",
            id_record=new_id,
            aksi="INSERT",
            data_baru={"kode": kode_bersih, "nama": nama_bersih, "harga_default": harga_default},
            keterangan="Penambahan tenongan baru",
        )
        return True, f"Tenongan '{nama_bersih}' ({kode_bersih}) berhasil ditambahkan.", new_id


def update_data_tenongan(
    id_tenongan: int,
    nama_tenongan: str,
    nama_pemilik: str = "",
    kontak: str = "",
    alamat_lokasi: str = "",
    tgl_mulai: str = "",
    harga_default: float = 2000.0,
    komisi_default: float = 0.0,
    pola_titip: str = "HARIAN",
    kuota_default: int = 4,
    status_aktif: int = 1,
    catatan: str = "",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Memperbarui atribut tenongan dan status aktif/nonaktif tanpa menghapus
    riwayat transaksi historisnya.
    """
    nama_bersih = bersihkan_teks(nama_tenongan)
    if not nama_bersih:
        return False, "Nama tenongan tidak boleh kosong."

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        lama = cursor.execute(
            "SELECT * FROM tenongan WHERE id_tenongan = ?;",
            (int(id_tenongan),),
        ).fetchone()
        if not lama:
            return False, "Data tenongan tidak ditemukan."

        duplikat = cursor.execute(
            """
            SELECT id_tenongan FROM tenongan
            WHERE LOWER(TRIM(nama_tenongan)) = LOWER(?) AND id_tenongan != ?;
            """,
            (nama_bersih, int(id_tenongan)),
        ).fetchone()
        if duplikat:
            return False, f"Nama tenongan '{nama_bersih}' sudah dipakai oleh tenongan lain."

        cursor.execute(
            """
            UPDATE tenongan SET
                nama_tenongan = ?,
                nama_pemilik = ?,
                kontak = ?,
                alamat_lokasi = ?,
                tgl_mulai = ?,
                harga_default = ?,
                komisi_default = ?,
                pola_titip = ?,
                kuota_default = ?,
                status_aktif = ?,
                catatan = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id_tenongan = ?;
            """,
            (
                nama_bersih,
                bersihkan_teks(nama_pemilik),
                bersihkan_teks(kontak),
                bersihkan_teks(alamat_lokasi),
                tgl_mulai or lama["tgl_mulai"],
                float(harga_default),
                float(komisi_default),
                pola_titip,
                int(kuota_default),
                1 if status_aktif else 0,
                bersihkan_teks(catatan),
                int(id_tenongan),
            ),
        )
        catat_audit_log(
            conn,
            tabel_target="tenongan",
            id_record=int(id_tenongan),
            aksi="UPDATE",
            data_lama=dict(lama),
            data_baru={
                "nama_tenongan": nama_bersih,
                "harga_default": harga_default,
                "status_aktif": 1 if status_aktif else 0,
            },
            keterangan="Perubahan profil/status tenongan",
        )
        return True, f"Data tenongan '{nama_bersih}' berhasil diperbarui."


def impor_daftar_tenongan_df(
    df_input: pd.DataFrame,
    db_path: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, int]]:
    """
    Mengimpor daftar tenongan dari DataFrame (hasil baca Excel/CSV).
    Memvalidasi kolom wajib ('nama_tenongan' atau 'nama').
    """
    if df_input is None or df_input.empty:
        return False, "File impor kosong atau tidak memiliki baris data.", {"ditambahkan": 0, "dilewati": 0}

    peta_kolom = {str(c).strip().lower(): c for c in df_input.columns}
    kol_nama = None
    for kandidat in ["nama_tenongan", "nama", "tenongan", "nama tenongan"]:
        if kandidat in peta_kolom:
            kol_nama = peta_kolom[kandidat]
            break

    if not kol_nama:
        return (
            False,
            "Kolom wajib 'nama_tenongan' atau 'Nama' tidak ditemukan pada file yang diunggah.",
            {"ditambahkan": 0, "dilewati": 0},
        )

    ditambahkan = 0
    dilewati = 0
    for _, row in df_input.iterrows():
        nama = bersihkan_teks(row.get(kol_nama, ""))
        if not nama or nama.upper() in ("JUMLAH", "SISA", "BAYAR", "TOTAL SETOR", "TOTAL SISA", "TOTAL LAKU", "BERSIH"):
            continue
        pemilik = bersihkan_teks(row.get(peta_kolom.get("nama_pemilik", ""), ""))
        kontak = bersihkan_teks(row.get(peta_kolom.get("kontak", ""), ""))
        lokasi = bersihkan_teks(row.get(peta_kolom.get("alamat_lokasi", peta_kolom.get("lokasi", "")), ""))
        harga = konversi_angka_aman(row.get(peta_kolom.get("harga_default", ""), 2000.0), 2000.0) or 2000.0
        kuota = int(konversi_angka_aman(row.get(peta_kolom.get("kuota_default", ""), 4), 4) or 4)
        pola = bersihkan_teks(row.get(peta_kolom.get("pola_titip", ""), "HARIAN")).upper()
        if pola not in ("HARIAN", "SELANG_SELING", "INSIDENTAL"):
            pola = "HARIAN"

        ok, _, _ = tambah_tenongan_baru(
            nama_tenongan=nama,
            nama_pemilik=pemilik,
            kontak=kontak,
            alamat_lokasi=lokasi,
            harga_default=harga,
            pola_titip=pola,
            kuota_default=kuota,
            db_path=db_path,
        )
        if ok:
            ditambahkan += 1
        else:
            dilewati += 1

    return (
        True,
        f"Proses impor selesai: {ditambahkan} tenongan baru ditambahkan, {dilewati} sudah ada/dilewati.",
        {"ditambahkan": ditambahkan, "dilewati": dilewati},
    )


# ==============================================================================
# 2. OPERASI CRUD & BATCH UPSERT TRANSAKSI HARIAN (TITIP & LAKU)
# ==============================================================================

def simpan_atau_update_transaksi_harian(
    id_tenongan: int,
    tanggal: str,
    qty_titip: int,
    qty_laku: Optional[int],
    qty_retur: Optional[int] = None,
    harga_jual_unit: float = 2000.0,
    komisi_unit: float = 0.0,
    uang_setoran: Optional[float] = None,
    status_konfirmasi: str = "TERKONFIRMASI",
    is_rapel: bool = False,
    catatan: str = "",
    izinkan_timpa: bool = True,
    db_path: Optional[Path] = None,
) -> Tuple[bool, str, Optional[int]]:
    """
    Menyimpan atau memperbarui satu transaksi harian tenongan dengan validasi ketat,
    perhitungan otomatis omzet/piutang/HPP snapshot, dan pencatatan Audit Log.
    """
    tgl_bersih = str(tanggal).strip()[:10]

    # Jika status BELUM_KONFIRMASI, qty_laku dianggap belum ada (0 sementara)
    laku_val = None if status_konfirmasi == "BELUM_KONFIRMASI" else int(qty_laku or 0)
    retur_val = 0 if status_konfirmasi == "BELUM_KONFIRMASI" else (
        int(qty_retur) if qty_retur is not None else max(0, int(qty_titip or 0) - int(laku_val or 0))
    )

    is_valid, pesan_validasi = validasi_kuantitas_transaksi(
        qty_titip=qty_titip,
        qty_laku=0 if laku_val is None else laku_val,
        qty_retur=retur_val,
        izinkan_rapel=is_rapel,
    )
    if not is_valid:
        return False, pesan_validasi, None

    hasil_hitung = hitung_transaksi_item(
        qty_titip=int(qty_titip or 0),
        qty_laku=laku_val,
        qty_retur=retur_val,
        harga_jual_unit=harga_jual_unit,
        komisi_unit=komisi_unit,
        uang_setoran=uang_setoran,
        status_konfirmasi_input=status_konfirmasi,
        is_rapel=is_rapel,
    )

    hpp_aktif = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        eksisting = cursor.execute(
            "SELECT * FROM transaksi_harian WHERE id_tenongan = ? AND tanggal = ?;",
            (int(id_tenongan), tgl_bersih),
        ).fetchone()

        if eksisting and not izinkan_timpa:
            return (
                False,
                f"Transaksi untuk tenongan ini pada tanggal {tgl_bersih} sudah ada. "
                "Centang opsi 'Izinkan Koreksi/Update Transaksi' jika ingin memperbarui.",
                eksisting["id_transaksi"],
            )

        if eksisting:
            id_trx = int(eksisting["id_transaksi"])
            cursor.execute(
                """
                UPDATE transaksi_harian SET
                    qty_titip = ?,
                    qty_laku = ?,
                    qty_retur = ?,
                    harga_jual_unit = ?,
                    komisi_unit = ?,
                    omzet_kotor = ?,
                    setoran_harapan = ?,
                    uang_setoran = ?,
                    piutang = ?,
                    status_konfirmasi = ?,
                    status_pembayaran = ?,
                    is_rapel = ?,
                    catatan = ?,
                    updated_at = datetime('now', 'localtime')
                WHERE id_transaksi = ?;
                """,
                (
                    hasil_hitung["qty_titip"],
                    hasil_hitung["qty_laku"],
                    hasil_hitung["qty_retur"],
                    hasil_hitung["harga_jual_unit"],
                    hasil_hitung["komisi_unit"],
                    hasil_hitung["omzet_kotor"],
                    hasil_hitung["setoran_harapan"],
                    hasil_hitung["uang_setoran"],
                    hasil_hitung["piutang"],
                    hasil_hitung["status_konfirmasi"],
                    hasil_hitung["status_pembayaran"],
                    1 if is_rapel else 0,
                    bersihkan_teks(catatan),
                    id_trx,
                ),
            )
            catat_audit_log(
                conn,
                tabel_target="transaksi_harian",
                id_record=id_trx,
                aksi="UPDATE",
                data_lama={
                    "tanggal": eksisting["tanggal"],
                    "qty_titip": eksisting["qty_titip"],
                    "qty_laku": eksisting["qty_laku"],
                    "qty_retur": eksisting["qty_retur"],
                    "uang_setoran": eksisting["uang_setoran"],
                    "status_konfirmasi": eksisting["status_konfirmasi"],
                },
                data_baru=hasil_hitung,
                keterangan=f"Koreksi transaksi tanggal {tgl_bersih}",
            )
            return True, "Transaksi berhasil diperbarui dan dicatat di Audit Log.", id_trx
        else:
            cursor.execute(
                """
                INSERT INTO transaksi_harian (
                    id_tenongan, tanggal, qty_titip, qty_laku, qty_retur,
                    harga_jual_unit, komisi_unit, omzet_kotor, setoran_harapan,
                    uang_setoran, piutang, hpp_unit_saat_itu, status_konfirmasi,
                    status_pembayaran, is_rapel, catatan
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    int(id_tenongan),
                    tgl_bersih,
                    hasil_hitung["qty_titip"],
                    hasil_hitung["qty_laku"],
                    hasil_hitung["qty_retur"],
                    hasil_hitung["harga_jual_unit"],
                    hasil_hitung["komisi_unit"],
                    hasil_hitung["omzet_kotor"],
                    hasil_hitung["setoran_harapan"],
                    hasil_hitung["uang_setoran"],
                    hasil_hitung["piutang"],
                    hpp_aktif,
                    hasil_hitung["status_konfirmasi"],
                    hasil_hitung["status_pembayaran"],
                    1 if is_rapel else 0,
                    bersihkan_teks(catatan),
                ),
            )
            id_trx = int(cursor.lastrowid)
            catat_audit_log(
                conn,
                tabel_target="transaksi_harian",
                id_record=id_trx,
                aksi="INSERT",
                data_baru=hasil_hitung,
                keterangan=f"Input transaksi baru tanggal {tgl_bersih}",
            )
            return True, "Transaksi baru berhasil disimpan.", id_trx


def simpan_batch_transaksi_harian(
    tanggal: str,
    daftar_input: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, int]]:
    """
    Menyimpan banyak transaksi tenongan dalam satu tanggal sekaligus secara atomik.
    Hanya memproses baris yang memiliki qty_titip > 0 atau qty_laku > 0.
    """
    tgl_bersih = str(tanggal).strip()[:10]
    hpp_aktif = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)

    disimpan = 0
    diperbarui = 0
    dilewati = 0
    error_list: List[str] = []

    # Validasi seluruh baris terlebih dahulu sebelum menyentuh database
    baris_siap_simpan = []
    for item in daftar_input:
        id_tng = int(item["id_tenongan"])
        nama_tng = item.get("nama_tenongan", f"ID-{id_tng}")
        titip = int(konversi_angka_aman(item.get("qty_titip", 0), 0) or 0)
        laku = int(konversi_angka_aman(item.get("qty_laku", 0), 0) or 0)
        retur_raw = item.get("qty_retur", None)
        status_konf = item.get("status_konfirmasi", "TERKONFIRMASI")
        is_rapel = bool(item.get("is_rapel", False))

        if titip == 0 and laku == 0:
            dilewati += 1
            continue

        if status_konf == "BELUM_KONFIRMASI":
            laku_opt = None
            retur_opt = 0
        else:
            laku_opt = laku
            retur_opt = int(konversi_angka_aman(retur_raw, max(0, titip - laku)) or 0) if retur_raw is not None else max(0, titip - laku)

        ok_val, msg_val = validasi_kuantitas_transaksi(
            qty_titip=titip,
            qty_laku=0 if laku_opt is None else laku_opt,
            qty_retur=retur_opt,
            izinkan_rapel=is_rapel,
        )
        if not ok_val:
            error_list.append(f"[{nama_tng}]: {msg_val}")
            continue

        harga = float(konversi_angka_aman(item.get("harga_jual_unit", 2000.0), 2000.0) or 2000.0)
        komisi = float(konversi_angka_aman(item.get("komisi_unit", 0.0), 0.0) or 0.0)
        setoran_input = item.get("uang_setoran", None)
        uang_setoran_val = None if setoran_input is None else float(konversi_angka_aman(setoran_input, 0.0) or 0.0)

        hasil = hitung_transaksi_item(
            qty_titip=titip,
            qty_laku=laku_opt,
            qty_retur=retur_opt,
            harga_jual_unit=harga,
            komisi_unit=komisi,
            uang_setoran=uang_setoran_val,
            status_konfirmasi_input=status_konf,
            is_rapel=is_rapel,
        )
        baris_siap_simpan.append(
            {
                "id_tenongan": id_tng,
                "nama_tenongan": nama_tng,
                "hasil": hasil,
                "is_rapel": is_rapel,
                "catatan": bersihkan_teks(item.get("catatan", "")),
            }
        )

    if error_list:
        return (
            False,
            "Terdapat kesalahan validasi pada baris berikut:\n- " + "\n- ".join(error_list),
            {"disimpan": 0, "diperbarui": 0, "dilewati": dilewati},
        )

    if not baris_siap_simpan:
        return (
            False,
            "Tidak ada baris transaksi aktif yang diisi (semua baris bernilai 0).",
            {"disimpan": 0, "diperbarui": 0, "dilewati": dilewati},
        )

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        for b in baris_siap_simpan:
            id_tng = b["id_tenongan"]
            h = b["hasil"]
            eks = cursor.execute(
                "SELECT * FROM transaksi_harian WHERE id_tenongan = ? AND tanggal = ?;",
                (id_tng, tgl_bersih),
            ).fetchone()

            if eks:
                id_trx = int(eks["id_transaksi"])
                cursor.execute(
                    """
                    UPDATE transaksi_harian SET
                        qty_titip = ?, qty_laku = ?, qty_retur = ?,
                        harga_jual_unit = ?, komisi_unit = ?, omzet_kotor = ?,
                        setoran_harapan = ?, uang_setoran = ?, piutang = ?,
                        status_konfirmasi = ?, status_pembayaran = ?,
                        is_rapel = ?, catatan = ?,
                        updated_at = datetime('now', 'localtime')
                    WHERE id_transaksi = ?;
                    """,
                    (
                        h["qty_titip"],
                        h["qty_laku"],
                        h["qty_retur"],
                        h["harga_jual_unit"],
                        h["komisi_unit"],
                        h["omzet_kotor"],
                        h["setoran_harapan"],
                        h["uang_setoran"],
                        h["piutang"],
                        h["status_konfirmasi"],
                        h["status_pembayaran"],
                        1 if b["is_rapel"] else 0,
                        b["catatan"],
                        id_trx,
                    ),
                )
                catat_audit_log(
                    conn,
                    tabel_target="transaksi_harian",
                    id_record=id_trx,
                    aksi="UPDATE_BATCH",
                    data_lama={
                        "qty_titip": eks["qty_titip"],
                        "qty_laku": eks["qty_laku"],
                        "uang_setoran": eks["uang_setoran"],
                    },
                    data_baru=h,
                    keterangan=f"Update massal tanggal {tgl_bersih} ({b['nama_tenongan']})",
                )
                diperbarui += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO transaksi_harian (
                        id_tenongan, tanggal, qty_titip, qty_laku, qty_retur,
                        harga_jual_unit, komisi_unit, omzet_kotor, setoran_harapan,
                        uang_setoran, piutang, hpp_unit_saat_itu, status_konfirmasi,
                        status_pembayaran, is_rapel, catatan
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        id_tng,
                        tgl_bersih,
                        h["qty_titip"],
                        h["qty_laku"],
                        h["qty_retur"],
                        h["harga_jual_unit"],
                        h["komisi_unit"],
                        h["omzet_kotor"],
                        h["setoran_harapan"],
                        h["uang_setoran"],
                        h["piutang"],
                        hpp_aktif,
                        h["status_konfirmasi"],
                        h["status_pembayaran"],
                        1 if b["is_rapel"] else 0,
                        b["catatan"],
                    ),
                )
                id_trx = int(cursor.lastrowid)
                catat_audit_log(
                    conn,
                    tabel_target="transaksi_harian",
                    id_record=id_trx,
                    aksi="INSERT_BATCH",
                    data_baru=h,
                    keterangan=f"Input massal tanggal {tgl_bersih} ({b['nama_tenongan']})",
                )
                disimpan += 1

    return (
        True,
        f"Berhasil memproses transaksi tanggal {tgl_bersih}: {disimpan} baru disimpan, {diperbarui} diperbarui.",
        {"disimpan": disimpan, "diperbarui": diperbarui, "dilewati": dilewati},
    )


def hapus_transaksi_harian(
    id_transaksi: int,
    alasan: str = "Dihapus oleh pengguna",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Menghapus satu baris transaksi harian dan mencatat data terakhirnya ke audit_log."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        eks = cursor.execute(
            "SELECT * FROM transaksi_harian WHERE id_transaksi = ?;",
            (int(id_transaksi),),
        ).fetchone()
        if not eks:
            return False, "Data transaksi tidak ditemukan."

        catat_audit_log(
            conn,
            tabel_target="transaksi_harian",
            id_record=int(id_transaksi),
            aksi="DELETE",
            data_lama=dict(eks),
            keterangan=alasan,
        )
        cursor.execute("DELETE FROM transaksi_harian WHERE id_transaksi = ?;", (int(id_transaksi),))
        return True, f"Transaksi ID #{id_transaksi} pada tanggal {eks['tanggal']} berhasil dihapus."


def get_transaksi_harian_df(
    tgl_mulai: Optional[str] = None,
    tgl_selesai: Optional[str] = None,
    id_tenongan: Optional[int] = None,
    status_konfirmasi: str = "SEMUA",
    status_pembayaran: str = "SEMUA",
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Mengambil daftar transaksi harian dengan join informasi tenongan."""
    kondisi = []
    params: List[Any] = []

    if tgl_mulai:
        kondisi.append("tr.tanggal >= ?")
        params.append(str(tgl_mulai)[:10])
    if tgl_selesai:
        kondisi.append("tr.tanggal <= ?")
        params.append(str(tgl_selesai)[:10])
    if id_tenongan:
        kondisi.append("tr.id_tenongan = ?")
        params.append(int(id_tenongan))
    if status_konfirmasi and status_konfirmasi != "SEMUA":
        kondisi.append("tr.status_konfirmasi = ?")
        params.append(status_konfirmasi)
    if status_pembayaran and status_pembayaran != "SEMUA":
        kondisi.append("tr.status_pembayaran = ?")
        params.append(status_pembayaran)

    where_sql = f"WHERE {' AND '.join(kondisi)}" if kondisi else ""

    sql = f"""
        SELECT
            tr.id_transaksi,
            tr.tanggal,
            tr.id_tenongan,
            t.no_urut,
            t.kode_tenongan,
            t.nama_tenongan,
            t.pola_titip,
            tr.qty_titip,
            tr.qty_laku,
            tr.qty_retur,
            tr.harga_jual_unit,
            tr.komisi_unit,
            tr.omzet_kotor,
            tr.setoran_harapan,
            tr.uang_setoran,
            tr.piutang,
            tr.hpp_unit_saat_itu,
            tr.status_konfirmasi,
            tr.status_pembayaran,
            tr.is_rapel,
            tr.catatan,
            tr.updated_at
        FROM transaksi_harian tr
        JOIN tenongan t ON tr.id_tenongan = t.id_tenongan
        {where_sql}
        ORDER BY tr.tanggal DESC, COALESCE(t.no_urut, 9999) ASC;
    """
    return query_df(sql, tuple(params), db_path=db_path)


# ==============================================================================
# 3. PARSER & IMPORTER KHUSUS WORKBOOK AWAL (projectapp.xlsx)
# ==============================================================================

def _buat_tanggal_valid(tahun: int, bulan: int, hari_ke: int) -> str:
    """Membuat string tanggal ISO YYYY-MM-DD yang aman terhadap jumlah hari dalam bulan."""
    max_hari = calendar.monthrange(tahun, bulan)[1]
    hari_aman = max(1, min(hari_ke, max_hari))
    return date(tahun, bulan, hari_aman).isoformat()


def _ekstrak_blok_taiyaki(
    ws: Any,
    baris_awal_tenongan: int,
    baris_akhir_tenongan: int,
    tahun_target: int,
    bulan_target: int,
    label_blok: str,
    abaikan_outlier_agregat: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Mengekstrak satu blok tabel crosstab (33 tenongan x 31 hari) dari sheet 'TAIYAKI'.
    Menangani sel formula (=4, =4+4), spasi kosong, transaksi belum konfirmasi,
    dan transaksi rapel (laku > titip atau titip kosong).
    """
    records: List[Dict[str, Any]] = []
    catatan_anomali: List[str] = []
    max_hari_bulan = calendar.monthrange(tahun_target, bulan_target)[1]

    for idx_hari, col_titip in enumerate(range(3, 65, 2), start=1):
        if idx_hari > max_hari_bulan:
            break
        col_laku = col_titip + 1
        tgl_iso = _buat_tanggal_valid(tahun_target, bulan_target, idx_hari)

        for r in range(baris_awal_tenongan, baris_akhir_tenongan + 1):
            nama_raw = ws.cell(r, 2).value
            nama_tenongan = bersihkan_teks(nama_raw)
            if not nama_tenongan:
                continue

            raw_t = ws.cell(r, col_titip).value
            raw_l = ws.cell(r, col_laku).value
            val_t = konversi_angka_aman(raw_t, None)
            val_l = konversi_angka_aman(raw_l, None)

            if val_t is None and val_l is None:
                continue

            titip = int(round(val_t)) if val_t is not None else 0
            laku = int(round(val_l)) if val_l is not None else None

            if titip == 0 and (laku is None or laku == 0):
                continue

            # Deteksi outlier input agregat satu hari di satu baris (misal barokah Titip=65, Laku=65)
            if abaikan_outlier_agregat and titip >= 30:
                catatan_anomali.append(
                    f"[{label_blok} - {tgl_iso}] Baris '{nama_tenongan}' memiliki nilai agregat "
                    f"(Titip={titip}, Laku={laku}) dan ditandai khusus di catatan."
                )

            # Tentukan status konfirmasi & apakah transaksi rapel
            if val_t is not None and val_l is None:
                status_konfirmasi = "BELUM_KONFIRMASI"
                is_rapel = False
                catatan = f"Impor {label_blok}: Titipan belum dikonfirmasi laku"
            elif (val_t is None and val_l is not None) or (laku is not None and laku > titip):
                status_konfirmasi = "TERKONFIRMASI"
                is_rapel = True
                catatan = f"Impor {label_blok}: Transaksi rapel/gabungan (Titip={titip}, Laku={laku})"
                catatan_anomali.append(
                    f"[{label_blok} - {tgl_iso}] '{nama_tenongan}': Titip={titip}, Laku={laku} (Ditandai sebagai Rapel)"
                )
            else:
                status_konfirmasi = "TERKONFIRMASI"
                is_rapel = False
                catatan = f"Impor {label_blok}"

            records.append(
                {
                    "nama_tenongan": nama_tenongan,
                    "tanggal": tgl_iso,
                    "qty_titip": titip,
                    "qty_laku": laku,
                    "status_konfirmasi": status_konfirmasi,
                    "is_rapel": is_rapel,
                    "catatan": catatan,
                }
            )

    return records, catatan_anomali


def impor_workbook_projectapp(
    file_source: Union[str, Path, BinaryIO],
    tahun_blok1: int = 2026,
    bulan_blok1: int = 8,
    tahun_blok2: int = 2026,
    bulan_blok2: int = 9,
    impor_belanja_kulakan: bool = True,
    tanggal_belanja_awal: str = "2026-08-01",
    db_path: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Memeriksa struktur dan mengimpor seluruh data historis dari workbook 'projectapp.xlsx'
    (Sheet 'TAIYAKI' Blok 1 & Blok 2, Sheet 'kulakan') ke dalam SQLite secara atomik.
    Tidak menimpa atau merusak database jika file tidak valid.
    """
    try:
        wb = openpyxl.load_workbook(file_source, data_only=True)
    except Exception as exc:
        return False, f"Gagal membuka file Excel: {exc}", {}

    sheet_names = wb.sheetnames
    if "TAIYAKI" not in sheet_names:
        return (
            False,
            f"Sheet 'TAIYAKI' tidak ditemukan di dalam workbook. Sheet yang ada: {sheet_names}",
            {},
        )

    ws_taiyaki = wb["TAIYAKI"]

    # Ekstrak Blok 1 (Baris 4 s.d. 36) dan Blok 2 (Baris 52 s.d. 84)
    records_b1, anomali_b1 = _ekstrak_blok_taiyaki(
        ws_taiyaki,
        baris_awal_tenongan=4,
        baris_akhir_tenongan=36,
        tahun_target=tahun_blok1,
        bulan_target=bulan_blok1,
        label_blok=f"Blok-1 ({tahun_blok1}-{bulan_blok1:02d})",
    )
    records_b2, anomali_b2 = _ekstrak_blok_taiyaki(
        ws_taiyaki,
        baris_awal_tenongan=52,
        baris_akhir_tenongan=84,
        tahun_target=tahun_blok2,
        bulan_target=bulan_blok2,
        label_blok=f"Blok-2 ({tahun_blok2}-{bulan_blok2:02d})",
    )

    semua_records = records_b1 + records_b2
    if not semua_records:
        return False, "Tidak ditemukan data transaksi valid pada Sheet 'TAIYAKI'.", {}

    hpp_aktif = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)
    jml_insert = 0
    jml_update = 0
    jml_belanja = 0

    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Petakan nama_tenongan (lowercase) -> (id_tenongan, harga_default, komisi_default)
            rows_tng = cursor.execute(
                "SELECT id_tenongan, nama_tenongan, harga_default, komisi_default FROM tenongan;"
            ).fetchall()
            peta_tenongan = {
                bersihkan_teks(r["nama_tenongan"]).lower(): (
                    int(r["id_tenongan"]),
                    float(r["harga_default"]),
                    float(r["komisi_default"]),
                )
                for r in rows_tng
            }

            for rec in semua_records:
                kunci_nama = rec["nama_tenongan"].lower()
                if kunci_nama not in peta_tenongan:
                    # Tambahkan tenongan otomatis jika belum ada di master
                    max_no = cursor.execute("SELECT COALESCE(MAX(no_urut), 0) as m FROM tenongan;").fetchone()["m"]
                    no_baru = int(max_no) + 1
                    kode_baru = f"TNG-{no_baru:03d}"
                    cursor.execute(
                        """
                        INSERT INTO tenongan (no_urut, kode_tenongan, nama_tenongan, harga_default, komisi_default, status_aktif)
                        VALUES (?, ?, ?, 2000.0, 0.0, 1);
                        """,
                        (no_baru, kode_baru, rec["nama_tenongan"]),
                    )
                    peta_tenongan[kunci_nama] = (int(cursor.lastrowid), 2000.0, 0.0)

                id_tng, harga_def, komisi_def = peta_tenongan[kunci_nama]
                hasil = hitung_transaksi_item(
                    qty_titip=rec["qty_titip"],
                    qty_laku=rec["qty_laku"],
                    harga_jual_unit=harga_def,
                    komisi_unit=komisi_def,
                    uang_setoran=None,
                    status_konfirmasi_input=rec["status_konfirmasi"],
                    is_rapel=rec["is_rapel"],
                )

                eks = cursor.execute(
                    "SELECT id_transaksi FROM transaksi_harian WHERE id_tenongan = ? AND tanggal = ?;",
                    (id_tng, rec["tanggal"]),
                ).fetchone()

                if eks:
                    cursor.execute(
                        """
                        UPDATE transaksi_harian SET
                            qty_titip = ?, qty_laku = ?, qty_retur = ?,
                            harga_jual_unit = ?, komisi_unit = ?, omzet_kotor = ?,
                            setoran_harapan = ?, uang_setoran = ?, piutang = ?,
                            status_konfirmasi = ?, status_pembayaran = ?,
                            is_rapel = ?, catatan = ?,
                            updated_at = datetime('now', 'localtime')
                        WHERE id_transaksi = ?;
                        """,
                        (
                            hasil["qty_titip"],
                            hasil["qty_laku"],
                            hasil["qty_retur"],
                            hasil["harga_jual_unit"],
                            hasil["komisi_unit"],
                            hasil["omzet_kotor"],
                            hasil["setoran_harapan"],
                            hasil["uang_setoran"],
                            hasil["piutang"],
                            hasil["status_konfirmasi"],
                            hasil["status_pembayaran"],
                            1 if rec["is_rapel"] else 0,
                            rec["catatan"],
                            int(eks["id_transaksi"]),
                        ),
                    )
                    jml_update += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO transaksi_harian (
                            id_tenongan, tanggal, qty_titip, qty_laku, qty_retur,
                            harga_jual_unit, komisi_unit, omzet_kotor, setoran_harapan,
                            uang_setoran, piutang, hpp_unit_saat_itu, status_konfirmasi,
                            status_pembayaran, is_rapel, catatan
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            id_tng,
                            rec["tanggal"],
                            hasil["qty_titip"],
                            hasil["qty_laku"],
                            hasil["qty_retur"],
                            hasil["harga_jual_unit"],
                            hasil["komisi_unit"],
                            hasil["omzet_kotor"],
                            hasil["setoran_harapan"],
                            hasil["uang_setoran"],
                            hasil["piutang"],
                            hpp_aktif,
                            hasil["status_konfirmasi"],
                            hasil["status_pembayaran"],
                            1 if rec["is_rapel"] else 0,
                            rec["catatan"],
                        ),
                    )
                    jml_insert += 1

            # Impor data belanja bahan awal dari sheet 'kulakan' jika diminta
            if impor_belanja_kulakan and "kulakan" in sheet_names:
                ws_kul = wb["kulakan"]
                peta_alias_bahan = {
                    "tepung": "BHN-001",
                    "gula": "BHN-002",
                    "3 sapi": "BHN-003",
                    "ndog": "BHN-004",
                    "simas": "BHN-005",
                    "selai coklat": "BHN-006",
                    "opp": "BHN-007",
                    "mika 7a": "BHN-008",
                    "sk": "BHN-009",
                    "da": "BHN-010",
                    "kopi": "BHN-011",
                    "bintang": "BHN-012",
                    "warna": "BHN-013",
                    "galetto p": "BHN-014",
                    "shaula": "BHN-015",
                    "springkel": "BHN-016",
                    "oreo": "BHN-017",
                    "chocochip": "BHN-018",
                }
                for r in range(4, 22):
                    nama_b = bersihkan_teks(ws_kul.cell(r, 1).value).lower()
                    total_hrg = konversi_angka_aman(ws_kul.cell(r, 2).value, 0.0) or 0.0
                    qty_b = konversi_angka_aman(ws_kul.cell(r, 3).value, 0.0) or 0.0
                    if nama_b in peta_alias_bahan and total_hrg > 0 and qty_b > 0:
                        kode_b = peta_alias_bahan[nama_b]
                        row_bhn = cursor.execute(
                            "SELECT id_bahan, satuan_beli FROM master_bahan WHERE kode_bahan = ?;",
                            (kode_b,),
                        ).fetchone()
                        if row_bhn:
                            no_nota = f"IMP-KUL-{kode_b}-{tanggal_belanja_awal.replace('-', '')}"
                            hrg_satuan = round(total_hrg / qty_b, 2)
                            cursor.execute(
                                """
                                INSERT OR IGNORE INTO belanja_bahan_baku (
                                    nomor_nota, tanggal, id_bahan, qty_beli, satuan_beli,
                                    harga_satuan, total_pembelian, pemasok, metode_bayar,
                                    jumlah_dibayar, status_bayar, update_ke_stok, catatan
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pemasok Awal (Impor Excel)', 'Tunai', ?, 'LUNAS', 0, 'Impor otomatis dari sheet kulakan projectapp.xlsx');
                                """,
                                (
                                    no_nota,
                                    tanggal_belanja_awal,
                                    int(row_bhn["id_bahan"]),
                                    qty_b,
                                    row_bhn["satuan_beli"],
                                    hrg_satuan,
                                    total_hrg,
                                    total_hrg,
                                ),
                            )
                            if cursor.rowcount > 0:
                                jml_belanja += 1

            catat_audit_log(
                conn,
                tabel_target="transaksi_harian",
                id_record=0,
                aksi="IMPORT_EXCEL",
                data_baru={
                    "blok1_periode": f"{tahun_blok1}-{bulan_blok1:02d}",
                    "blok2_periode": f"{tahun_blok2}-{bulan_blok2:02d}",
                    "inserted": jml_insert,
                    "updated": jml_update,
                    "belanja_imported": jml_belanja,
                },
                keterangan="Impor workbook projectapp.xlsx berhasil",
            )
    except Exception as exc:
        return False, f"Impor dibatalkan (rollback otomatis) karena terjadi kesalahan: {exc}", {}

    ringkasan_impor = {
        "blok1_records": len(records_b1),
        "blok2_records": len(records_b2),
        "total_inserted": jml_insert,
        "total_updated": jml_update,
        "belanja_imported": jml_belanja,
        "catatan_anomali": anomali_b1 + anomali_b2,
    }
    pesan = (
        f"Impor projectapp.xlsx berhasil! "
        f"{jml_insert} transaksi baru ditambahkan, {jml_update} transaksi diperbarui, "
        f"dan {jml_belanja} item belanja bahan baku diimpor."
    )
    return True, pesan, ringkasan_impor


# ==============================================================================
# 4. UTILITAS EKSPOR EXCEL (.XLSX) & CSV
# ==============================================================================

def df_ke_csv_bytes(df: pd.DataFrame) -> bytes:
    """Mengonversi DataFrame ke bytes CSV ber-encoding UTF-8 dengan BOM agar rapi di Excel."""
    return df.to_csv(index=False).encode("utf-8-sig")


def dfs_ke_excel_bytes(sheets_dict: Dict[str, pd.DataFrame]) -> bytes:
    """
    Mengonversi satu atau beberapa DataFrame menjadi file Excel (.xlsx) multi-sheet
    di dalam memori (bytes) dengan lebar kolom yang disesuaikan otomatis.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nama_sheet, df in sheets_dict.items():
            nama_aman = str(nama_sheet)[:31]
            df.to_excel(writer, sheet_name=nama_aman, index=False)
            ws = writer.sheets[nama_aman]
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = col[0].column_letter
                ws.column_dimensions[col_letter].width = min(max(max_len + 3, 10), 42)
    buffer.seek(0)
    return buffer.getvalue()
