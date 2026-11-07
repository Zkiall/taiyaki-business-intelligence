"""
tests/test_taiyaki_suite.py
Unit test menyeluruh (kompatibel dengan `unittest` bawaan Python maupun `pytest`)
untuk memverifikasi rumus bisnis, integritas SQLite, validasi input, edge-cases,
importer projectapp.xlsx, dan pipeline Data Science & Forecasting.
"""

from datetime import date
from pathlib import Path
import tempfile
import unittest
import pandas as pd

from calculations import (
    hitung_bep_dan_simulasi,
    hitung_hpp_batch,
    hitung_laba_rugi_periode,
    hitung_sell_through_dan_retur,
    hitung_transaksi_item,
)
from data_processing import (
    get_daftar_tenongan_lengkap,
    get_transaksi_harian_df,
    impor_daftar_tenongan_df,
    impor_workbook_projectapp,
    simpan_atau_update_transaksi_harian,
    tambah_tenongan_baru,
    update_data_tenongan,
)
from database import get_ringkasan_cepat_db, init_db, query_df
from forecasting import (
    hitung_rekomendasi_alokasi_tenongan,
    jalankan_evaluasi_dan_forecasting,
)
from utils import (
    format_rupiah,
    konversi_angka_aman,
    validasi_kuantitas_transaksi,
)


class TestTaiyakiBusinessAndDataScience(unittest.TestCase):
    """Rangkaian pengujian unit bisnis, database, dan Data Science Taiyaki Manager."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_taiyaki.db"
        init_db(self.db_path, seed_master=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_omzet_retur_setoran_lunas(self):
        """Uji perhitungan omzet, retur otomatis, dan setoran lunas penuh."""
        res = hitung_transaksi_item(qty_titip=4, qty_laku=3, harga_jual_unit=2000.0)
        self.assertEqual(res["qty_titip"], 4)
        self.assertEqual(res["qty_laku"], 3)
        self.assertEqual(res["qty_retur"], 1)
        self.assertEqual(res["omzet_kotor"], 6000.0)
        self.assertEqual(res["setoran_harapan"], 6000.0)
        self.assertEqual(res["uang_setoran"], 6000.0)
        self.assertEqual(res["piutang"], 0.0)
        self.assertEqual(res["status_konfirmasi"], "TERKONFIRMASI")
        self.assertEqual(res["status_pembayaran"], "LUNAS")

    def test_02_pembayaran_sebagian_dan_piutang(self):
        """Uji skenario pembayaran sebagian (cicil) dan perhitungan piutang tenongan."""
        res = hitung_transaksi_item(
            qty_titip=5,
            qty_laku=4,
            harga_jual_unit=2000.0,
            uang_setoran=5000.0,
        )
        self.assertEqual(res["omzet_kotor"], 8000.0)
        self.assertEqual(res["setoran_harapan"], 8000.0)
        self.assertEqual(res["uang_setoran"], 5000.0)
        self.assertEqual(res["piutang"], 3000.0)
        self.assertEqual(res["status_pembayaran"], "SEBAGIAN")

    def test_03_transaksi_belum_dikonfirmasi(self):
        """Uji barang baru dititipkan (BELUM_KONFIRMASI) tidak boleh dianggap retur atau piutang."""
        res = hitung_transaksi_item(
            qty_titip=4,
            qty_laku=None,
            status_konfirmasi_input="BELUM_KONFIRMASI",
        )
        self.assertEqual(res["status_konfirmasi"], "BELUM_KONFIRMASI")
        self.assertEqual(res["qty_laku"], 0)
        self.assertEqual(res["qty_retur"], 0)
        self.assertEqual(res["omzet_kotor"], 0.0)
        self.assertEqual(res["piutang"], 0.0)

    def test_04_kalkulasi_hpp_batch_dan_margin_sesuai_excel(self):
        """Uji presisi rumus HPP batch 120 pcs terhadap angka sheet 'kalkulasi' projectapp.xlsx."""
        biaya_bahan = [21000, 18000, 18666.6667, 7440, 27000, 300, 1200, 3300, 23000]
        hpp = hitung_hpp_batch(
            daftar_biaya_bahan=biaya_bahan, output_batch_pcs=120, harga_jual_unit=2000.0
        )
        self.assertAlmostEqual(hpp["total_biaya_batch"], 119906.67, places=1)
        self.assertAlmostEqual(hpp["hpp_per_pcs"], 999.2222, places=3)
        self.assertAlmostEqual(hpp["laba_kotor_per_pcs"], 1000.7778, places=3)
        self.assertAlmostEqual(hpp["margin_kotor_persen"], 50.04, places=1)

    def test_05_laba_bersih_dan_beban_retur_sesuai_excel(self):
        """Uji perhitungan laba bersih pada simulasi sheet 'kalkulasi' (Titip 66, Sisa 8, Laku 58)."""
        lr = hitung_laba_rugi_periode(
            total_titip_terkonfirmasi=66,
            total_laku=58,
            total_retur=8,
            omzet_kotor=116000.0,
            setoran_harapan=116000.0,
            uang_setoran_diterima=116000.0,
            hpp_per_pcs=119906.6667 / 120.0,
            biaya_operasional_lain=0.0,
            mode_beban_retur="BEBANKAN_RETUR",
        )
        self.assertEqual(lr["omzet_kotor"], 116000.0)
        self.assertAlmostEqual(lr["total_hpp_dibebankan"], 65948.67, places=1)
        self.assertAlmostEqual(lr["laba_bersih"], 50051.33, places=1)
        self.assertAlmostEqual(lr["margin_bersih_persen"], 43.15, places=1)

    def test_06_sell_through_rate_dan_return_rate(self):
        """Uji perhitungan Sell-Through Rate (STR) dan Return Rate (RR) termasuk saat titip = 0."""
        normal = hitung_sell_through_dan_retur(100, 84, 16)
        self.assertEqual(normal["sell_through_rate"], 84.0)
        self.assertEqual(normal["return_rate"], 16.0)

        kosong = hitung_sell_through_dan_retur(0, 0, 0)
        self.assertEqual(kosong["sell_through_rate"], 0.0)
        self.assertEqual(kosong["return_rate"], 0.0)

    def test_07_break_even_point_dan_simulasi_skenario(self):
        """Uji rumus titik impas (BEP) unit & Rupiah serta 3 skenario simulasi."""
        bep = hitung_bep_dan_simulasi(
            harga_jual_unit=2000.0,
            hpp_variabel_unit=1000.0,
            biaya_tetap_periode=150000.0,
            target_volume_laku=1500,
        )
        self.assertEqual(bep["margin_kontribusi_unit"], 1000.0)
        self.assertEqual(bep["bep_unit"], 150.0)
        self.assertEqual(bep["bep_rupiah"], 300000.0)
        self.assertEqual(set(bep["skenario"].keys()), {"Konservatif", "Moderat", "Optimistis"})
        self.assertGreater(
            bep["skenario"]["Optimistis"]["laba_bersih"],
            bep["skenario"]["Konservatif"]["laba_bersih"],
        )

    def test_08_transaksi_kosong_dan_nilai_nol(self):
        """Uji penolakan transaksi kosong (Titip=0, Laku=0, Retur=0)."""
        ok, msg = validasi_kuantitas_transaksi(0, 0, 0)
        self.assertFalse(ok)
        self.assertIn("kosong", msg.lower())

    def test_09_input_negatif_ditolak(self):
        """Uji penolakan input bernilai negatif pada titip, laku, maupun retur."""
        ok1, _ = validasi_kuantitas_transaksi(-1, 2, 0)
        ok2, _ = validasi_kuantitas_transaksi(4, -2, 0)
        ok3, _ = validasi_kuantitas_transaksi(4, 2, -1)
        self.assertFalse(ok1)
        self.assertFalse(ok2)
        self.assertFalse(ok3)

    def test_10_laku_melebihi_titip_tanpa_rapel_ditolak(self):
        """Uji validasi Laku > Titip ditolak kecuali mode Rapel diaktifkan."""
        ok_normal, _ = validasi_kuantitas_transaksi(4, 5, 0, izinkan_rapel=False)
        ok_rapel, _ = validasi_kuantitas_transaksi(4, 5, 0, izinkan_rapel=True)
        self.assertFalse(ok_normal)
        self.assertTrue(ok_rapel)

    def test_11_pencegahan_transaksi_ganda_dan_audit_log(self):
        """Uji pencegahan duplikasi transaksi pada tenongan & tanggal yang sama serta Audit Log."""
        ok1, _, id1 = simpan_atau_update_transaksi_harian(
            id_tenongan=1, tanggal="2026-10-01", qty_titip=4, qty_laku=3, db_path=self.db_path
        )
        self.assertTrue(ok1)
        self.assertIsNotNone(id1)

        ok_dup, msg_dup, _ = simpan_atau_update_transaksi_harian(
            id_tenongan=1,
            tanggal="2026-10-01",
            qty_titip=4,
            qty_laku=4,
            izinkan_timpa=False,
            db_path=self.db_path,
        )
        self.assertFalse(ok_dup)
        self.assertIn("sudah ada", msg_dup.lower())

        ok_upd, _, id_upd = simpan_atau_update_transaksi_harian(
            id_tenongan=1,
            tanggal="2026-10-01",
            qty_titip=4,
            qty_laku=4,
            izinkan_timpa=True,
            db_path=self.db_path,
        )
        self.assertTrue(ok_upd)
        self.assertEqual(id_upd, id1)

        df_cek = get_transaksi_harian_df(
            tgl_mulai="2026-10-01", tgl_selesai="2026-10-01", id_tenongan=1, db_path=self.db_path
        )
        self.assertEqual(len(df_cek), 1)
        self.assertEqual(int(df_cek.iloc[0]["qty_laku"]), 4)

        df_audit = query_df(
            "SELECT * FROM audit_log WHERE tabel_target='transaksi_harian';", db_path=self.db_path
        )
        self.assertGreaterEqual(len(df_audit), 2)

    def test_12_harga_berubah_dan_tenongan_baru_serta_nonaktif(self):
        """Uji penambahan tenongan baru, perubahan harga jual, dan penonaktifan tanpa kehilangan riwayat."""
        ok_new, _, id_baru = tambah_tenongan_baru(
            nama_tenongan="MITRA BARU SOLO",
            harga_default=2500.0,
            kuota_default=5,
            db_path=self.db_path,
        )
        self.assertTrue(ok_new)
        self.assertIsNotNone(id_baru)

        simpan_atau_update_transaksi_harian(
            id_tenongan=id_baru,
            tanggal="2026-10-05",
            qty_titip=5,
            qty_laku=4,
            harga_jual_unit=2500.0,
            db_path=self.db_path,
        )

        ok_ed, _ = update_data_tenongan(
            id_tenongan=id_baru,
            nama_tenongan="MITRA BARU SOLO",
            harga_default=3000.0,
            status_aktif=0,
            db_path=self.db_path,
        )
        self.assertTrue(ok_ed)

        df_all = get_daftar_tenongan_lengkap(db_path=self.db_path, hanya_aktif=False)
        baris_m = df_all[df_all["id_tenongan"] == id_baru].iloc[0]
        self.assertEqual(int(baris_m["status_aktif"]), 0)
        self.assertEqual(float(baris_m["total_omzet"]), 10000.0)

    def test_13_impor_file_kolom_tidak_valid_ditolak(self):
        """Uji impor file dengan kolom tidak valid tidak merusak database."""
        df_salah = pd.DataFrame([{"kolom_acak": "ABC", "nilai": 123}])
        ok_imp, msg_imp, _ = impor_daftar_tenongan_df(df_salah, db_path=self.db_path)
        self.assertFalse(ok_imp)
        self.assertIn("tidak ditemukan", msg_imp.lower())

    def test_14_database_kosong_dan_persistensi_restart(self):
        """Uji perilaku saat database belum memiliki transaksi dan persistensi setelah koneksi dibuka ulang."""
        ringkasan_awal = get_ringkasan_cepat_db(self.db_path)
        self.assertEqual(ringkasan_awal["total_baris_transaksi"], 0)
        self.assertEqual(ringkasan_awal["tenongan_total"], 33)

        fc_kosong = jalankan_evaluasi_dan_forecasting(db_path=self.db_path)
        self.assertEqual(fc_kosong["status"], "DATA_KOSONG")

        simpan_atau_update_transaksi_harian(1, "2026-10-10", 4, 4, db_path=self.db_path)
        init_db(self.db_path, seed_master=True)

        ringkasan_restart = get_ringkasan_cepat_db(self.db_path)
        self.assertEqual(ringkasan_restart["total_baris_transaksi"], 1)
        self.assertEqual(ringkasan_restart["total_laku"], 4)

    def test_15_impor_projectapp_dan_pipeline_forecasting_end_to_end(self):
        """Uji impor workbook projectapp.xlsx asli dan jalankan evaluasi forecasting serta rekomendasi kuota."""
        excel_path = Path(__file__).resolve().parent.parent / "projectapp.xlsx"
        if not excel_path.exists():
            excel_path = Path("projectapp.xlsx")
        ok_w, _, det = impor_workbook_projectapp(excel_path, db_path=self.db_path)
        self.assertTrue(ok_w)
        self.assertEqual(det["total_inserted"], 819)

        ringkasan = get_ringkasan_cepat_db(self.db_path)
        self.assertEqual(ringkasan["total_titip"], 2876)
        self.assertEqual(ringkasan["total_laku"], 2283)

        hasil_fc = jalankan_evaluasi_dan_forecasting(horizon_hari=7, db_path=self.db_path)
        self.assertEqual(hasil_fc["status"], "OK")
        self.assertGreater(hasil_fc["mae_terbaik"], 0.0)
        self.assertEqual(len(hasil_fc["df_forecast"]), 7)

        df_rek = hitung_rekomendasi_alokasi_tenongan(
            target_tanggal=date(2026, 10, 30),
            kapasitas_produksi_maks=80,
            db_path=self.db_path,
        )
        self.assertEqual(len(df_rek), 33)
        self.assertLessEqual(int(df_rek["Saran Kuota Baru (pcs)"].sum()), 80)

    def test_16_utilitas_konversi_formula_excel_dan_format_rupiah(self):
        """Uji parser formula sederhana Excel (=4, =4+4, spasi kosong) dan format Rupiah."""
        self.assertEqual(konversi_angka_aman("=4"), 4.0)
        self.assertEqual(konversi_angka_aman("=4+4"), 8.0)
        self.assertIsNone(konversi_angka_aman(" "))
        self.assertEqual(format_rupiah(2000), "Rp2.000")
        self.assertEqual(format_rupiah(1250000), "Rp1.250.000")


if __name__ == "__main__":
    unittest.main(verbosity=2)