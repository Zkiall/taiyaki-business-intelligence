"""
database.py
Modul manajemen database SQLite persisten untuk Taiyaki Business Manager.
Memisahkan secara fisik dan logis antara:
- Database Produksi: data/production/taiyaki_production.db (Wajib Login Admin)
- Database Demo: data/demo/taiyaki_demo.db (Public Read-Only Portfolio)
Dilengkapi Hard Security Gate dan SQLite Read-Only Authorizer.
"""

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from auth import is_admin_authenticated, require_admin_access

try:
    import streamlit as st
except ImportError:
    st = None  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

PROD_DIR = DATA_DIR / "production"
BACKUP_DIR = PROD_DIR / "backups"
DEMO_DIR = DATA_DIR / "demo"

PROD_DB_PATH = PROD_DIR / "taiyaki_production.db"
DEMO_DB_PATH = DEMO_DIR / "taiyaki_demo.db"

# Path lama untuk migrasi otomatis tanpa kehilangan data yang sudah diinput
LEGACY_PROD_DB_PATH = DATA_DIR / "taiyaki_business.db"

INITIAL_TENONGAN = [
    (1, "TNG-001", "BHJK", "HARIAN", 4),
    (2, "TNG-002", "RESTIB", "SELANG_SELING", 3),
    (3, "TNG-003", "ALFI 1", "SELANG_SELING", 4),
    (4, "TNG-004", "ALFI 2", "SELANG_SELING", 4),
    (5, "TNG-005", "ALFI 3", "SELANG_SELING", 4),
    (6, "TNG-006", "AMANAH", "SELANG_SELING", 3),
    (7, "TNG-007", "AQILA", "HARIAN", 3),
    (8, "TNG-008", "MOOYA", "INSIDENTAL", 3),
    (9, "TNG-009", "BINTANG 1", "HARIAN", 4),
    (10, "TNG-010", "BINTANG 2", "INSIDENTAL", 3),
    (11, "TNG-011", "BROTHERS", "SELANG_SELING", 3),
    (12, "TNG-012", "DIENA", "SELANG_SELING", 4),
    (13, "TNG-013", "C.BIRU", "SELANG_SELING", 3),
    (14, "TNG-014", "FATHUR", "SELANG_SELING", 4),
    (15, "TNG-015", "ILYAS", "SELANG_SELING", 4),
    (16, "TNG-016", "IZZAH", "SELANG_SELING", 3),
    (17, "TNG-017", "KHUMAIRA", "HARIAN", 4),
    (18, "TNG-018", "KURNIA", "SELANG_SELING", 3),
    (19, "TNG-019", "LANGGENG 1", "SELANG_SELING", 3),
    (20, "TNG-020", "LANGGENG 2", "SELANG_SELING", 3),
    (21, "TNG-021", "HARUN", "SELANG_SELING", 4),
    (22, "TNG-022", "MAHRUS", "SELANG_SELING", 4),
    (23, "TNG-023", "MAJU", "HARIAN", 4),
    (24, "TNG-024", "MANUNGGAL", "HARIAN", 4),
    (25, "TNG-025", "MBARENG", "SELANG_SELING", 3),
    (26, "TNG-026", "NIDA", "HARIAN", 3),
    (27, "TNG-027", "PRASOJO", "HARIAN", 3),
    (28, "TNG-028", "RIFAI", "SELANG_SELING", 3),
    (29, "TNG-029", "SHOLEH", "SELANG_SELING", 4),
    (30, "TNG-030", "ZULFA", "SELANG_SELING", 4),
    (31, "TNG-031", "TIGPTR", "SELANG_SELING", 3),
    (32, "TNG-032", "barokah", "SELANG_SELING", 3),
    (33, "TNG-033", "sukaja", "INSIDENTAL", 3),
]

INITIAL_BAHAN = [
    ("BHN-001", "Tepung Terigu", "Bahan Utama", "kg", 1000.0, "gram", 11500.0, 6.0, 2.0),
    ("BHN-002", "Gula Pasir", "Bahan Utama", "kg", 1000.0, "gram", 18000.0, 3.0, 1.5),
    ("BHN-003", "Susu (3 Sapi)", "Bahan Utama", "kaleng", 450.0, "gram", 14000.0, 4.0, 2.0),
    ("BHN-004", "Telur Ayam (ndog)", "Bahan Utama", "kg", 16.0, "butir", 27000.0, 2.0, 1.0),
    ("BHN-005", "Margarin (Simas)", "Bahan Utama", "kg", 1000.0, "gram", 25000.0, 1.0, 0.5),
    ("BHN-006", "Selai Coklat", "Bahan Isian", "kg", 1000.0, "gram", 35000.0, 2.0, 1.0),
    ("BHN-007", "Plastik OPP", "Kemasan", "pak", 1000.0, "lembar", 44000.0, 1.0, 0.5),
    ("BHN-008", "Mika 7A", "Kemasan", "pak", 360.0, "pcs", 8000.0, 1.0, 0.5),
    ("BHN-009", "Soda Kue (sk)", "Bahan Tambahan", "pak", 250.0, "gram", 4000.0, 1.0, 0.5),
    ("BHN-010", "Double Acting Baking Powder (da/bh)", "Bahan Tambahan", "pak", 250.0, "gram", 13500.0, 1.0, 0.5),
    ("BHN-011", "Perisa Kopi", "Bahan Isian", "pcs", 100.0, "gram", 10000.0, 0.0, 0.5),
    ("BHN-012", "Tepung Bintang", "Bahan Utama", "sak", 5000.0, "gram", 42000.0, 0.0, 1.0),
    ("BHN-013", "Pewarna Makanan (warna)", "Bahan Tambahan", "botol", 100.0, "ml", 22000.0, 0.0, 0.5),
    ("BHN-014", "Galetto P", "Bahan Isian", "pak", 500.0, "gram", 16000.0, 0.0, 0.5),
    ("BHN-015", "Shaula", "Bahan Isian", "kg", 1000.0, "gram", 33000.0, 0.0, 0.5),
    ("BHN-016", "Sprinkle (springkel)", "Topping & Isian", "pak", 500.0, "gram", 18000.0, 0.0, 0.5),
    ("BHN-017", "Crumb Oreo", "Topping & Isian", "pak", 500.0, "gram", 21000.0, 0.0, 0.5),
    ("BHN-018", "Chocochip", "Topping & Isian", "pak", 500.0, "gram", 21000.0, 0.0, 0.5),
]

INITIAL_SETTINGS = [
    ("nama_usaha", "Taiyaki Consignment & Distribution", "str", "Nama bisnis yang tampil di laporan"),
    ("harga_jual_default", "2000", "float", "Harga setoran default per buah taiyaki (Rp)"),
    ("komisi_default", "0", "float", "Komisi potongan tenongan default per buah (Rp)"),
    ("output_batch_default", "120", "int", "Jumlah taiyaki layak jual per 1 batch resep"),
    ("hpp_unit_aktif", "999.2222", "float", "HPP standar aktif per buah sesuai resep kalkulasi (Rp)"),
    ("mode_beban_retur", "BEBANKAN_RETUR", "str", "BEBANKAN_RETUR (roti sisa membebani HPP) atau RITEL_MURNI"),
    ("target_penjualan_bulanan_pcs", "1500", "int", "Target jumlah penjualan taiyaki per bulan (pcs)"),
    ("biaya_tetap_bulanan_default", "150000", "float", "Estimasi biaya tetap bulanan untuk simulasi BEP (Rp)"),
    ("versi_aplikasi", "2.0.0", "str", "Versi rilis aplikasi Taiyaki Business Manager"),
]

_ALLOWED_READONLY_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _sqlite_readonly_authorizer(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
    """
    Callback otorisasi level C-engine SQLite untuk memastikan pengunjung publik
    hanya dapat mengeksekusi perintah SELECT/READ tanpa bisa mengubah isi database demo.
    """
    if action in _ALLOWED_READONLY_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in (
        "table_info",
        "index_list",
        "foreign_key_list",
        "journal_mode",
        "foreign_keys",
        "busy_timeout",
    ):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def ensure_directories() -> None:
    """
    Memastikan direktori data/production/backups dan data/demo tersedia,
    serta memigrasikan file database produksi lama secara aman jika ditemukan.
    """
    PROD_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    if LEGACY_PROD_DB_PATH.exists() and not PROD_DB_PATH.exists():
        shutil.copy2(LEGACY_PROD_DB_PATH, PROD_DB_PATH)


def is_production_db_path(target_path: Path) -> bool:
    """Memeriksa apakah suatu path mengarah ke direktori atau file database produksi."""
    resolved = Path(target_path).resolve()
    if resolved == PROD_DB_PATH.resolve() or resolved == LEGACY_PROD_DB_PATH.resolve():
        return True
    if PROD_DIR.resolve() in resolved.parents:
        return True
    if "production" in resolved.name.lower() or "business" in resolved.name.lower():
        return True
    return False


def get_db_path(mode: str = "DEMO") -> Path:
    """
    Mengembalikan path file database berdasarkan mode aplikasi.
    Akses ke mode PRODUCTION wajib melalui pemeriksaan autentikasi Administrator.
    """
    ensure_directories()
    if mode.upper() == "PRODUCTION":
        require_admin_access()
        return PROD_DB_PATH
    return DEMO_DB_PATH


def resolve_db_path(db_path: Optional[Path] = None) -> Path:
    """
    Menentukan path database secara aman tanpa pernah melakukan fallback otomatis
    dari Demo ke Produksi bagi pengunjung publik.
    """
    ensure_directories()
    if db_path is not None:
        target = Path(db_path)
        if is_production_db_path(target):
            require_admin_access()
        return target

    if st is not None and hasattr(st, "session_state"):
        if st.session_state.get("db_mode") == "PRODUCTION" and is_admin_authenticated():
            return PROD_DB_PATH
    return DEMO_DB_PATH


@contextmanager
def get_connection(db_path: Optional[Path] = None, allow_system_init: bool = False):
    """
    Context manager koneksi SQLite dengan Hard Security Gate:
    - Menolak akses ke database produksi jika belum login sebagai Administrator.
    - Mengaktifkan SQLite Read-Only Authorizer pada database demo bagi pengunjung publik.
    """
    ensure_directories()
    if db_path is not None:
        target_path = Path(db_path)
        if is_production_db_path(target_path) and not allow_system_init:
            require_admin_access()
    else:
        target_path = resolve_db_path(None)

    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        is_demo_target = target_path.resolve() == DEMO_DB_PATH.resolve()
        if is_demo_target and not allow_system_init and not is_admin_authenticated():
            conn.set_authorizer(_sqlite_readonly_authorizer)

        yield conn
        conn.commit()
    except sqlite3.DatabaseError as exc:
        conn.rollback()
        if "not authorized" in str(exc).lower():
            raise PermissionError(
                "Mode Demo Publik bersifat Read-Only. Pengunjung tidak diizinkan mengubah atau menghapus data."
            ) from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(
    db_path: Optional[Path] = None,
    seed_master: bool = True,
    allow_system_init: bool = True,
) -> None:
    """
    Membuat seluruh skema 11 tabel relasional jika belum ada dan melakukan seeding
    data master awal (33 tenongan, 18 bahan baku, resep HPP 120 pcs, dan settings).
    """
    target = Path(db_path) if db_path is not None else DEMO_DB_PATH
    with get_connection(target, allow_system_init=allow_system_init) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenongan (
                id_tenongan INTEGER PRIMARY KEY AUTOINCREMENT,
                no_urut INTEGER,
                kode_tenongan TEXT NOT NULL UNIQUE,
                nama_tenongan TEXT NOT NULL UNIQUE,
                nama_pemilik TEXT DEFAULT '',
                kontak TEXT DEFAULT '',
                alamat_lokasi TEXT DEFAULT '',
                tgl_mulai TEXT DEFAULT (date('now')),
                harga_default REAL NOT NULL DEFAULT 2000.0,
                komisi_default REAL NOT NULL DEFAULT 0.0,
                pola_titip TEXT NOT NULL DEFAULT 'HARIAN',
                kuota_default INTEGER NOT NULL DEFAULT 4,
                status_aktif INTEGER NOT NULL DEFAULT 1,
                catatan TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transaksi_harian (
                id_transaksi INTEGER PRIMARY KEY AUTOINCREMENT,
                id_tenongan INTEGER NOT NULL,
                tanggal TEXT NOT NULL,
                qty_titip INTEGER NOT NULL DEFAULT 0 CHECK (qty_titip >= 0),
                qty_laku INTEGER NOT NULL DEFAULT 0 CHECK (qty_laku >= 0),
                qty_retur INTEGER NOT NULL DEFAULT 0 CHECK (qty_retur >= 0),
                harga_jual_unit REAL NOT NULL DEFAULT 2000.0 CHECK (harga_jual_unit >= 0),
                komisi_unit REAL NOT NULL DEFAULT 0.0 CHECK (komisi_unit >= 0),
                omzet_kotor REAL NOT NULL DEFAULT 0.0,
                setoran_harapan REAL NOT NULL DEFAULT 0.0,
                uang_setoran REAL NOT NULL DEFAULT 0.0 CHECK (uang_setoran >= 0),
                piutang REAL NOT NULL DEFAULT 0.0,
                hpp_unit_saat_itu REAL NOT NULL DEFAULT 999.2222,
                status_konfirmasi TEXT NOT NULL DEFAULT 'TERKONFIRMASI',
                status_pembayaran TEXT NOT NULL DEFAULT 'LUNAS',
                is_rapel INTEGER NOT NULL DEFAULT 0,
                catatan TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(id_tenongan, tanggal),
                FOREIGN KEY (id_tenongan) REFERENCES tenongan(id_tenongan) ON DELETE RESTRICT
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id_log INTEGER PRIMARY KEY AUTOINCREMENT,
                tabel_target TEXT NOT NULL,
                id_record INTEGER NOT NULL,
                aksi TEXT NOT NULL,
                data_lama_json TEXT,
                data_baru_json TEXT,
                keterangan TEXT DEFAULT '',
                waktu_perubahan TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS master_bahan (
                id_bahan INTEGER PRIMARY KEY AUTOINCREMENT,
                kode_bahan TEXT NOT NULL UNIQUE,
                nama_bahan TEXT NOT NULL UNIQUE,
                kategori TEXT NOT NULL DEFAULT 'Bahan Utama',
                satuan_beli TEXT NOT NULL DEFAULT 'kg',
                isi_per_satuan REAL NOT NULL DEFAULT 1000.0,
                satuan_pakai TEXT NOT NULL DEFAULT 'gram',
                harga_acuan REAL NOT NULL DEFAULT 0.0,
                stok_saat_ini REAL NOT NULL DEFAULT 0.0,
                stok_minimum REAL NOT NULL DEFAULT 1.0,
                status_aktif INTEGER NOT NULL DEFAULT 1,
                catatan TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS belanja_bahan_baku (
                id_belanja INTEGER PRIMARY KEY AUTOINCREMENT,
                nomor_nota TEXT NOT NULL UNIQUE,
                tanggal TEXT NOT NULL,
                id_bahan INTEGER NOT NULL,
                qty_beli REAL NOT NULL CHECK (qty_beli > 0),
                satuan_beli TEXT NOT NULL,
                harga_satuan REAL NOT NULL CHECK (harga_satuan >= 0),
                total_pembelian REAL NOT NULL CHECK (total_pembelian >= 0),
                pemasok TEXT DEFAULT '',
                metode_bayar TEXT NOT NULL DEFAULT 'Tunai',
                jumlah_dibayar REAL NOT NULL DEFAULT 0.0,
                status_bayar TEXT NOT NULL DEFAULT 'LUNAS',
                update_ke_stok INTEGER NOT NULL DEFAULT 1,
                catatan TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (id_bahan) REFERENCES master_bahan(id_bahan) ON DELETE RESTRICT
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mutasi_stok_bhn (
                id_mutasi INTEGER PRIMARY KEY AUTOINCREMENT,
                id_bahan INTEGER NOT NULL,
                tanggal TEXT NOT NULL,
                jenis_mutasi TEXT NOT NULL CHECK (jenis_mutasi IN ('MASUK_BELANJA', 'KELUAR_PRODUKSI', 'PENYESUAIAN')),
                qty_mutasi REAL NOT NULL,
                keterangan TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (id_bahan) REFERENCES master_bahan(id_bahan) ON DELETE RESTRICT
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resep_hpp_batch (
                id_resep INTEGER PRIMARY KEY AUTOINCREMENT,
                nama_resep TEXT NOT NULL UNIQUE,
                output_batch_pcs INTEGER NOT NULL DEFAULT 120 CHECK (output_batch_pcs > 0),
                biaya_tenaga_kerja REAL NOT NULL DEFAULT 0.0,
                biaya_energi_batch REAL NOT NULL DEFAULT 0.0,
                biaya_distribusi_batch REAL NOT NULL DEFAULT 0.0,
                biaya_lain_batch REAL NOT NULL DEFAULT 0.0,
                total_biaya_bahan REAL NOT NULL DEFAULT 0.0,
                total_biaya_batch REAL NOT NULL DEFAULT 0.0,
                hpp_per_pcs REAL NOT NULL DEFAULT 0.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resep_hpp_item (
                id_resep_item INTEGER PRIMARY KEY AUTOINCREMENT,
                id_resep INTEGER NOT NULL,
                nama_komponen TEXT NOT NULL,
                kategori TEXT NOT NULL DEFAULT 'Bahan Utama',
                qty_pakai REAL NOT NULL DEFAULT 0.0,
                satuan_pakai TEXT NOT NULL DEFAULT 'gram',
                harga_acuan_satuan REAL NOT NULL DEFAULT 0.0,
                ukuran_acuan REAL NOT NULL DEFAULT 1000.0,
                biaya_item_batch REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (id_resep) REFERENCES resep_hpp_batch(id_resep) ON DELETE CASCADE
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transaksi_kulakan (
                id_kulakan INTEGER PRIMARY KEY AUTOINCREMENT,
                nomor_transaksi TEXT NOT NULL UNIQUE,
                tanggal TEXT NOT NULL,
                kategori_transaksi TEXT NOT NULL CHECK (
                    kategori_transaksi IN (
                        'BELI_BARANG_JUAL_KEMBALI',
                        'JUAL_GROSIR_TAIYAKI',
                        'BELI_GROSIR_BAHAN',
                        'BAYAR_UTANG_KULAKAN',
                        'TERIMA_PIUTANG_KULAKAN'
                    )
                ),
                nama_item TEXT NOT NULL,
                qty REAL NOT NULL CHECK (qty > 0),
                satuan TEXT NOT NULL DEFAULT 'pcs',
                harga_unit REAL NOT NULL CHECK (harga_unit >= 0),
                total_transaksi REAL NOT NULL CHECK (total_transaksi >= 0),
                mitra_nama TEXT DEFAULT '',
                metode_bayar TEXT NOT NULL DEFAULT 'Tunai',
                jumlah_dibayar REAL NOT NULL DEFAULT 0.0,
                status_bayar TEXT NOT NULL DEFAULT 'LUNAS',
                catatan TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS biaya_operasional (
                id_biaya_ops INTEGER PRIMARY KEY AUTOINCREMENT,
                tanggal TEXT NOT NULL,
                kategori_biaya TEXT NOT NULL,
                deskripsi TEXT NOT NULL,
                nominal REAL NOT NULL CHECK (nominal >= 0),
                jenis_beban TEXT NOT NULL DEFAULT 'VARIABEL' CHECK (jenis_beban IN ('TETAP', 'VARIABEL')),
                metode_bayar TEXT NOT NULL DEFAULT 'Tunai',
                catatan TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                tipe_data TEXT NOT NULL DEFAULT 'str',
                deskripsi TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trx_tanggal ON transaksi_harian(tanggal);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trx_tenongan ON transaksi_harian(id_tenongan);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_belanja_tanggal ON belanja_bahan_baku(tanggal);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_kulakan_tanggal ON transaksi_kulakan(tanggal);")

    if seed_master:
        seed_initial_data(target, allow_system_init=allow_system_init)


def seed_initial_data(db_path: Optional[Path] = None, allow_system_init: bool = True) -> None:
    """Mengisi data master awal (33 tenongan, 18 bahan baku, resep kalkulasi 120 pcs, dan pengaturan default)."""
    target = Path(db_path) if db_path is not None else DEMO_DB_PATH
    with get_connection(target, allow_system_init=allow_system_init) as conn:
        cursor = conn.cursor()

        for key, val, tipe, desc in INITIAL_SETTINGS:
            cursor.execute(
                """
                INSERT OR IGNORE INTO app_settings (setting_key, setting_value, tipe_data, deskripsi)
                VALUES (?, ?, ?, ?);
                """,
                (key, val, tipe, desc),
            )

        cursor.execute("SELECT COUNT(*) as cnt FROM tenongan;")
        if cursor.fetchone()["cnt"] == 0:
            for no_urut, kode, nama, pola, kuota in INITIAL_TENONGAN:
                cursor.execute(
                    """
                    INSERT INTO tenongan (
                        no_urut, kode_tenongan, nama_tenongan, harga_default,
                        komisi_default, pola_titip, kuota_default, status_aktif
                    ) VALUES (?, ?, ?, 2000.0, 0.0, ?, ?, 1);
                    """,
                    (no_urut, kode, nama, pola, kuota),
                )

        cursor.execute("SELECT COUNT(*) as cnt FROM master_bahan;")
        if cursor.fetchone()["cnt"] == 0:
            for kode, nama, kat, sat_beli, isi, sat_pakai, harga, stok, stok_min in INITIAL_BAHAN:
                cursor.execute(
                    """
                    INSERT INTO master_bahan (
                        kode_bahan, nama_bahan, kategori, satuan_beli,
                        isi_per_satuan, satuan_pakai, harga_acuan, stok_saat_ini, stok_minimum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (kode, nama, kat, sat_beli, isi, sat_pakai, harga, stok, stok_min),
                )

        cursor.execute("SELECT COUNT(*) as cnt FROM resep_hpp_batch;")
        if cursor.fetchone()["cnt"] == 0:
            total_bahan_default = 119906.67
            hpp_pcs_default = round(total_bahan_default / 120.0, 4)
            cursor.execute(
                """
                INSERT INTO resep_hpp_batch (
                    nama_resep, output_batch_pcs, biaya_tenaga_kerja, biaya_energi_batch,
                    biaya_distribusi_batch, biaya_lain_batch, total_biaya_bahan,
                    total_biaya_batch, hpp_per_pcs, is_active
                ) VALUES (?, 120, 0.0, 0.0, 0.0, 0.0, ?, ?, ?, 1);
                """,
                (
                    "Resep Standar Taiyaki (120 pcs - Sheet Kalkulasi)",
                    total_bahan_default,
                    total_bahan_default,
                    hpp_pcs_default,
                ),
            )
            id_resep = cursor.lastrowid
            komponen_kalkulasi = [
                ("Tepung Terigu", "Bahan Utama", 2000.0, "gram", 10500.0, 1000.0, 21000.0),
                ("Gula Pasir", "Bahan Utama", 1000.0, "gram", 18000.0, 1000.0, 18000.0),
                ("Susu (3 Sapi)", "Bahan Utama", 600.0, "gram", 14000.0, 450.0, 18666.67),
                ("Plastik OPP & Mika (40+22)", "Kemasan", 120.0, "set", 62.0, 1.0, 7440.0),
                ("Telur Ayam (endok)", "Bahan Utama", 16.0, "butir", 27000.0, 16.0, 27000.0),
                ("Soda Kue (sk)", "Bahan Tambahan", 25.0, "gram", 3000.0, 250.0, 300.0),
                ("Pengembang (bh/da)", "Bahan Tambahan", 25.0, "gram", 12000.0, 250.0, 1200.0),
                ("Margarin (Simas)", "Bahan Utama", 150.0, "gram", 22000.0, 1000.0, 3300.0),
                ("Bahan Isian Campur", "Bahan Isian", 1.0, "paket", 23000.0, 1.0, 23000.0),
            ]
            for nama_k, kat_k, qty_k, sat_k, hrg_k, ukr_k, biaya_k in komponen_kalkulasi:
                cursor.execute(
                    """
                    INSERT INTO resep_hpp_item (
                        id_resep, nama_komponen, kategori, qty_pakai,
                        satuan_pakai, harga_acuan_satuan, ukuran_acuan, biaya_item_batch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (id_resep, nama_k, kat_k, qty_k, sat_k, hrg_k, ukr_k, biaya_k),
                )


def get_setting(key: str, default: Any = None, db_path: Optional[Path] = None) -> Any:
    """Mengambil nilai pengaturan dari tabel app_settings dengan konversi tipe otomatis."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT setting_value, tipe_data FROM app_settings WHERE setting_key = ?;",
            (key,),
        ).fetchone()
        if not row:
            return default
        val, tipe = row["setting_value"], row["tipe_data"]
        try:
            if tipe == "int":
                return int(float(val))
            if tipe == "float":
                return float(val)
            if tipe == "bool":
                return val.lower() in ("1", "true", "yes")
            return val
        except Exception:
            return default


def set_setting(
    key: str,
    value: Any,
    tipe_data: str = "str",
    deskripsi: str = "",
    db_path: Optional[Path] = None,
    allow_system_init: bool = False,
) -> None:
    """Menyimpan atau memperbarui pengaturan di tabel app_settings (Wajib Admin kecuali init sistem)."""
    if not allow_system_init:
        require_admin_access()
    with get_connection(db_path, allow_system_init=allow_system_init) as conn:
        conn.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value, tipe_data, deskripsi, updated_at)
            VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = datetime('now', 'localtime');
            """,
            (key, str(value), tipe_data, deskripsi),
        )


def catat_audit_log(
    conn: sqlite3.Connection,
    tabel_target: str,
    id_record: int,
    aksi: str,
    data_lama: Optional[Dict[str, Any]] = None,
    data_baru: Optional[Dict[str, Any]] = None,
    keterangan: str = "",
) -> None:
    """Mencatat jejak audit perubahan data ke dalam tabel audit_log."""
    conn.execute(
        """
        INSERT INTO audit_log (tabel_target, id_record, aksi, data_lama_json, data_baru_json, keterangan)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            tabel_target,
            int(id_record),
            aksi,
            json.dumps(data_lama, ensure_ascii=False) if data_lama else None,
            json.dumps(data_baru, ensure_ascii=False) if data_baru else None,
            keterangan,
        ),
    )


def query_df(sql: str, params: Tuple = (), db_path: Optional[Path] = None) -> pd.DataFrame:
    """Menjalankan query SELECT dan mengembalikan hasilnya sebagai pandas DataFrame."""
    with get_connection(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_ringkasan_cepat_db(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Mengambil ringkasan status database untuk indikator di Sidebar aplikasi."""
    with get_connection(db_path) as conn:
        jml_tenongan_aktif = conn.execute(
            "SELECT COUNT(*) as c FROM tenongan WHERE status_aktif = 1;"
        ).fetchone()["c"]
        jml_tenongan_total = conn.execute("SELECT COUNT(*) as c FROM tenongan;").fetchone()["c"]
        row_trx = conn.execute(
            """
            SELECT
                COUNT(*) as total_baris,
                COUNT(DISTINCT tanggal) as total_hari,
                COALESCE(SUM(qty_titip), 0) as total_titip,
                COALESCE(SUM(qty_laku), 0) as total_laku,
                COALESCE(SUM(piutang), 0) as total_piutang,
                MIN(tanggal) as tgl_awal,
                MAX(tanggal) as tgl_akhir
            FROM transaksi_harian;
            """
        ).fetchone()
        jml_unconfirmed = conn.execute(
            "SELECT COUNT(*) as c FROM transaksi_harian WHERE status_konfirmasi = 'BELUM_KONFIRMASI';"
        ).fetchone()["c"]

        return {
            "tenongan_aktif": int(jml_tenongan_aktif),
            "tenongan_total": int(jml_tenongan_total),
            "total_baris_transaksi": int(row_trx["total_baris"]),
            "total_hari_aktif": int(row_trx["total_hari"]),
            "total_titip": int(row_trx["total_titip"]),
            "total_laku": int(row_trx["total_laku"]),
            "total_piutang": float(row_trx["total_piutang"]),
            "tgl_awal": row_trx["tgl_awal"],
            "tgl_akhir": row_trx["tgl_akhir"],
            "jml_unconfirmed": int(jml_unconfirmed),
        }


def buat_backup_database(db_path: Optional[Path] = None) -> Path:
    """Membuat salinan cadangan file database SQLite ke folder data/production/backups/ (Khusus Admin)."""
    require_admin_access()
    ensure_directories()
    sumber = resolve_db_path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nama_backup = f"{sumber.stem}_backup_{timestamp}.db"
    tujuan = BACKUP_DIR / nama_backup
    shutil.copy2(sumber, tujuan)
    return tujuan