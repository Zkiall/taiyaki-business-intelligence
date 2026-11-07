"""
tests/test_security_and_integration.py
Pengujian Keamanan Autentikasi, Isolasi Database Produksi vs Demo,
Read-Only Authorizer Publik, dan Konsistensi Dataset Sintetis 1-6 Bulan.
"""

import os
from pathlib import Path
import tempfile
import unittest
import numpy as np

import auth
import database as db
import data_processing as dp
import synthetic_generator as sg


class TestSecurityAndIntegrationStage4(unittest.TestCase):
    """Rangkaian pengujian keamanan, isolasi database, dan generator sintetis 1-6 bulan."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_prod_db = Path(self.temp_dir.name) / "taiyaki_production.db"
        self.test_demo_db = Path(self.temp_dir.name) / "taiyaki_demo.db"

        self.admin_pass = "TaiyakiMaster2026!"
        self.admin_hash = auth.hash_password(self.admin_pass, prefer_bcrypt=False)
        os.environ["TAIYAKI_ADMIN_USERNAME"] = "zkiall"
        os.environ["TAIYAKI_ADMIN_PASSWORD_HASH"] = self.admin_hash
        auth.logout_admin()
        auth.reset_percobaan_login("test_bucket")

    def tearDown(self):
        auth.logout_admin()
        self.temp_dir.cleanup()

    def test_17_public_access_demo_without_login_and_readonly_enforcement(self):
        """Uji pengunjung dapat membuka demo tanpa login, namun tidak dapat mengubah/menghapus data demo."""
        self.assertFalse(auth.is_admin_authenticated())
        sg.pastikan_dataset_demo_tersedia(db.DEMO_DB_PATH, default_bulan=1, default_seed=42)
        ringkasan = db.get_ringkasan_cepat_db(db.DEMO_DB_PATH)
        self.assertGreater(ringkasan["total_baris_transaksi"], 400)

        with self.assertRaises(PermissionError):
            with db.get_connection(db.DEMO_DB_PATH) as conn:
                conn.execute("DELETE FROM transaksi_harian;")

    def test_18_unauthenticated_blocked_from_production_db(self):
        """Uji pengguna yang belum login ditolak keras saat membaca atau menulis ke database produksi."""
        self.assertFalse(auth.is_admin_authenticated())
        with self.assertRaises(PermissionError):
            db.get_db_path("PRODUCTION")

        with self.assertRaises(PermissionError):
            with db.get_connection(db.PROD_DB_PATH) as conn:
                conn.execute("SELECT * FROM tenongan;")

        with self.assertRaises(PermissionError):
            dp.get_daftar_tenongan_lengkap(db_path=self.test_prod_db)

    def test_19_admin_login_wrong_password_lockout_and_logout(self):
        """Uji login admin berhasil, password salah ditolak, lockout 5x aktif, dan logout bersih."""
        ok_wrong, _ = auth.authenticate_admin("zkiall", "SalahPass123", bucket_key="bucket_a")
        self.assertFalse(ok_wrong)
        self.assertFalse(auth.is_admin_authenticated())

        ok_right, _ = auth.authenticate_admin("zkiall", self.admin_pass, bucket_key="bucket_a")
        self.assertTrue(ok_right)
        self.assertTrue(auth.is_admin_authenticated())

        db.init_db(self.test_prod_db, seed_master=True)
        ok_trx, _, _ = dp.simpan_atau_update_transaksi_harian(
            id_tenongan=1, tanggal="2026-11-01", qty_titip=4, qty_laku=4, db_path=self.test_prod_db
        )
        self.assertTrue(ok_trx)

        auth.logout_admin()
        self.assertFalse(auth.is_admin_authenticated())
        with self.assertRaises(PermissionError):
            dp.get_transaksi_harian_df(db_path=self.test_prod_db)

        for _ in range(5):
            auth.authenticate_admin("zkiall", "SalahTerus", bucket_key="bucket_lock")
        terkunci, sisa_detik = auth.cek_status_lockout("bucket_lock")
        self.assertTrue(terkunci)
        self.assertGreater(sisa_detik, 0)

    def test_20_database_isolation_demo_reset_never_touches_production(self):
        """Uji database demo dan produksi benar-benar terpisah; reset demo tidak memengaruhi produksi."""
        auth.authenticate_admin("zkiall", self.admin_pass, bucket_key="bucket_iso")
        db.init_db(self.test_prod_db, seed_master=True)
        dp.simpan_atau_update_transaksi_harian(
            id_tenongan=1, tanggal="2026-11-02", qty_titip=5, qty_laku=5, db_path=self.test_prod_db
        )
        prod_before = db.get_ringkasan_cepat_db(self.test_prod_db)
        self.assertEqual(prod_before["total_baris_transaksi"], 1)
        self.assertEqual(prod_before["total_laku"], 5)

        res_demo = sg.generate_ulang_dataset_demo_admin(
            periode_bulan=2, seed=99, db_path=self.test_demo_db
        )
        self.assertGreater(res_demo["jml_transaksi_harian"], 900)

        with self.assertRaises(PermissionError):
            sg.generate_ulang_dataset_demo_admin(
                periode_bulan=1, seed=42, db_path=self.test_prod_db
            )

        prod_after = db.get_ringkasan_cepat_db(self.test_prod_db)
        self.assertEqual(prod_after["total_baris_transaksi"], 1)
        self.assertEqual(prod_after["total_laku"], 5)

    def test_21_synthetic_dataset_1_to_6_months_mathematical_consistency(self):
        """Uji pembuatan dataset sintetis 1-6 bulan dan konsistensi matematis seluruh tabel."""
        auth.authenticate_admin("zkiall", self.admin_pass, bucket_key="bucket_synth")
        for bln in [1, 3, 6]:
            meta = sg.generate_ulang_dataset_demo_admin(
                periode_bulan=bln, seed=42, db_path=self.test_demo_db
            )
            self.assertEqual(meta["periode_bulan"], bln)
            self.assertGreater(meta["jml_transaksi_harian"], bln * 450)
            self.assertGreater(meta["jml_belanja_bahan"], 0)
            self.assertGreater(meta["jml_kulakan"], 0)
            self.assertGreater(meta["jml_biaya_ops"], 0)

        df_trx = db.query_df("SELECT * FROM transaksi_harian;", db_path=self.test_demo_db)
        self.assertEqual(df_trx["id_transaksi"].nunique(), len(df_trx))
        self.assertTrue((df_trx["qty_laku"] <= df_trx["qty_titip"]).all())
        self.assertTrue((df_trx["qty_retur"] <= (df_trx["qty_titip"] - df_trx["qty_laku"])).all())
        self.assertTrue(
            np.allclose(df_trx["omzet_kotor"], df_trx["qty_laku"] * df_trx["harga_jual_unit"])
        )
        self.assertTrue(
            np.allclose(df_trx["piutang"], df_trx["setoran_harapan"] - df_trx["uang_setoran"])
        )
        self.assertTrue((df_trx["uang_setoran"] <= df_trx["setoran_harapan"]).all())


if __name__ == "__main__":
    unittest.main()