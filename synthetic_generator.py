"""
synthetic_generator.py
Generator dataset sintetis multi-tabel (1 s.d. 6 bulan, default 6 bulan)
untuk mengisi database Mode Demo Portofolio (data/demo/taiyaki_demo.db).
Menjamin konsistensi matematis lintas 11 tabel SQLite hingga tanggal hari ini (date.today()).
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from auth import require_admin_access
from calculations import hitung_transaksi_item
from database import DEMO_DB_PATH, get_connection, init_db, is_production_db_path

LOKASI_DEMO_SAMPLE = [
    "Pasar Gede",
    "Pasar Klewer",
    "Kartasura",
    "UMS Pabelan",
    "UNS Kentingan",
    "Manahan",
    "Banjarsari",
    "Laweyan",
    "Serengan",
    "Jebres",
    "Pasar Legi",
    "Colomadu",
    "Mojosongo",
    "Purwosari",
    "Palur",
    "Solo Baru",
    "Ngemplak",
]

KATALOG_BELANJA_RUTIN = [
    (1, "BHN-001", "Tepung Terigu", "kg", 11500.0, 12.0, 2.0),
    (2, "BHN-002", "Gula Pasir", "kg", 18000.0, 6.0, 1.0),
    (3, "BHN-003", "Susu (3 Sapi)", "kaleng", 14000.0, 8.0, 1.33),
    (4, "BHN-004", "Telur Ayam (ndog)", "kg", 27000.0, 6.0, 1.0),
    (5, "BHN-005", "Margarin (Simas)", "kg", 25000.0, 2.0, 0.15),
    (6, "BHN-006", "Selai Coklat", "kg", 35000.0, 4.0, 0.65),
    (7, "BHN-007", "Plastik OPP", "pak", 44000.0, 1.0, 0.12),
    (8, "BHN-008", "Mika 7A", "pak", 8000.0, 2.0, 0.33),
]


def _eksekusi_generator_sintetis(
    db_path: Path,
    periode_bulan: int = 6,
    seed: int = 42,
    tgl_akhir_acuan: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Fungsi internal pembangkit dataset sintetis hingga tanggal hari ini (date.today()).
    Ditolak keras apabila db_path mengarah ke database produksi.
    """
    target = Path(db_path)
    if is_production_db_path(target):
        raise PermissionError(
            "Keamanan Database: Generator data sintetis dilarang dijalankan pada database produksi!"
        )

    init_db(target, seed_master=True, allow_system_init=True)

    periode_bulan = max(1, min(6, int(periode_bulan)))
    rng = np.random.default_rng(int(seed))
    tgl_akhir = tgl_akhir_acuan or date.today()
    total_hari_kalender = periode_bulan * 30
    tgl_mulai = tgl_akhir - timedelta(days=total_hari_kalender - 1)

    with get_connection(target, allow_system_init=True) as conn:
        cur = conn.cursor()

        cur.execute("DELETE FROM transaksi_harian;")
        cur.execute("DELETE FROM belanja_bahan_baku;")
        cur.execute("DELETE FROM mutasi_stok_bhn;")
        cur.execute("DELETE FROM transaksi_kulakan;")
        cur.execute("DELETE FROM biaya_operasional;")
        cur.execute("DELETE FROM audit_log;")

        tenongan_rows = cur.execute(
            "SELECT id_tenongan, no_urut, kode_tenongan, nama_tenongan, pola_titip, kuota_default FROM tenongan ORDER BY id_tenongan;"
        ).fetchall()

        profil_tenongan: Dict[int, Dict[str, Any]] = {}
        for idx, r in enumerate(tenongan_rows):
            id_t = int(r["id_tenongan"])
            pola = str(r["pola_titip"])

            if idx % 3 == 0:
                tier = "TIER_A"
                base_prob = float(rng.uniform(0.88, 0.96))
                kuota_awal = int(rng.choice([4, 5]))
            elif idx % 3 == 1:
                tier = "TIER_B"
                base_prob = float(rng.uniform(0.76, 0.87))
                kuota_awal = int(rng.choice([3, 4]))
            else:
                tier = "TIER_C"
                base_prob = float(rng.uniform(0.60, 0.74))
                kuota_awal = int(rng.choice([3, 4]))

            if idx % 5 == 0:
                tren_bulanan = 0.025
            elif idx % 7 == 0:
                tren_bulanan = -0.025
            else:
                tren_bulanan = 0.0

            lokasi = LOKASI_DEMO_SAMPLE[idx % len(LOKASI_DEMO_SAMPLE)]
            pemilik = f"Mitra {r['nama_tenongan'].title()}"
            cur.execute(
                """
                UPDATE tenongan
                SET nama_pemilik = ?, alamat_lokasi = ?, kuota_default = ?,
                    tgl_mulai = ?, status_aktif = 1
                WHERE id_tenongan = ?;
                """,
                (pemilik, lokasi, kuota_awal, tgl_mulai.isoformat(), id_t),
            )
            profil_tenongan[id_t] = {
                "nama": r["nama_tenongan"],
                "pola": pola,
                "tier": tier,
                "base_prob": base_prob,
                "kuota_berjalan": kuota_awal,
                "tren_bulanan": tren_bulanan,
                "histori_str": [],
            }

        hpp_dasar_unit = 119906.67 / 120.0
        jml_trx_harian = 0
        jml_belanja = 0
        jml_mutasi = 0
        jml_kulakan = 0
        jml_biaya_ops = 0
        hari_aktif_counter = 0

        stok_simulasi = {item[0]: float(item[5]) for item in KATALOG_BELANJA_RUTIN}

        for idx_hari in range(total_hari_kalender):
            tgl_curr = tgl_mulai + timedelta(days=idx_hari)
            tgl_iso = tgl_curr.isoformat()
            dow = tgl_curr.weekday()
            indeks_bulan = idx_hari // 30
            faktor_inflasi = 1.0 + (indeks_bulan * 0.012)
            hpp_hari_ini = round(hpp_dasar_unit * faktor_inflasi, 4)

            if idx_hari % 12 == 0:
                for id_b, _, _, sat_b, hrg_awal, qty_standar, _ in KATALOG_BELANJA_RUTIN:
                    fluktuasi = float(rng.uniform(0.98, 1.03))
                    hrg_beli = round((hrg_awal * faktor_inflasi * fluktuasi) / 100.0) * 100.0
                    qty_b = max(1.0, float(qty_standar + rng.choice([-1, 0, 0, 1])))
                    tot_b = round(qty_b * hrg_beli, 2)
                    no_nota = f"DEMO-BLJ-{tgl_iso.replace('-', '')}-{id_b:02d}"

                    cur.execute(
                        """
                        INSERT INTO belanja_bahan_baku (
                            nomor_nota, tanggal, id_bahan, qty_beli, satuan_beli,
                            harga_satuan, total_pembelian, pemasok, metode_bayar,
                            jumlah_dibayar, status_bayar, update_ke_stok, catatan
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Grosir Bahan Kue Solo (Demo)', 'Tunai', ?, 'LUNAS', 1, '[DATA SINTETIS] Restock berkala');
                        """,
                        (no_nota, tgl_iso, id_b, qty_b, sat_b, hrg_beli, tot_b, tot_b),
                    )
                    jml_belanja += 1
                    stok_simulasi[id_b] = stok_simulasi.get(id_b, 0.0) + qty_b

                    cur.execute(
                        """
                        INSERT INTO mutasi_stok_bhn (id_bahan, tanggal, jenis_mutasi, qty_mutasi, keterangan)
                        VALUES (?, ?, 'MASUK_BELANJA', ?, ?);
                        """,
                        (id_b, tgl_iso, qty_b, f"Restock Nota {no_nota}"),
                    )
                    jml_mutasi += 1

            if dow == 6:
                continue

            hari_aktif_counter += 1
            peta_faktor_hari = {0: 0.93, 1: 0.97, 2: 1.00, 3: 1.05, 4: 1.07, 5: 1.06}
            faktor_dow = peta_faktor_hari.get(dow, 1.0)
            faktor_cuaca = float(rng.normal(1.0, 0.05))
            faktor_musiman = 1.0 + (indeks_bulan * 0.015)

            total_titip_hari_ini = 0
            apakah_hari_terakhir = idx_hari == total_hari_kalender - 1

            for id_t, prof in profil_tenongan.items():
                pola = prof["pola"]
                if pola == "SELANG_SELING" and ((idx_hari + id_t) % 2 != 0):
                    continue
                if pola == "INSIDENTAL" and (rng.random() > 0.28):
                    continue

                if len(prof["histori_str"]) >= 8 and hari_aktif_counter % 12 == 0:
                    mean_str_akhir = float(np.mean(prof["histori_str"][-8:]))
                    if mean_str_akhir >= 0.92 and prof["kuota_berjalan"] < 6:
                        prof["kuota_berjalan"] += 1
                    elif mean_str_akhir <= 0.62 and prof["kuota_berjalan"] > 2:
                        prof["kuota_berjalan"] -= 1

                kuota_hari = max(2, int(prof["kuota_berjalan"] + rng.choice([-1, 0, 0, 0, 1])))
                prob_efektif = (
                    prof["base_prob"] + (indeks_bulan * prof["tren_bulanan"])
                ) * faktor_dow * faktor_cuaca * faktor_musiman
                prob_efektif = float(np.clip(prob_efektif, 0.35, 0.98))

                laku_pcs = int(rng.binomial(kuota_hari, prob_efektif))
                retur_pcs = max(0, kuota_hari - laku_pcs)
                prof["histori_str"].append(laku_pcs / float(kuota_hari))
                total_titip_hari_ini += kuota_hari

                if apakah_hari_terakhir and rng.random() < 0.18:
                    status_konf = "BELUM_KONFIRMASI"
                    uang_setor = 0.0
                else:
                    status_konf = "TERKONFIRMASI"
                    tagihan = float(laku_pcs * 2000.0)
                    if tagihan >= 4000.0 and rng.random() < 0.04:
                        uang_setor = tagihan - 2000.0
                    elif tagihan >= 4000.0 and apakah_hari_terakhir and rng.random() < 0.08:
                        uang_setor = 0.0
                    else:
                        uang_setor = tagihan

                hasil = hitung_transaksi_item(
                    qty_titip=kuota_hari,
                    qty_laku=None if status_konf == "BELUM_KONFIRMASI" else laku_pcs,
                    qty_retur=retur_pcs,
                    harga_jual_unit=2000.0,
                    komisi_unit=0.0,
                    uang_setoran=uang_setor,
                    status_konfirmasi_input=status_konf,
                    is_rapel=False,
                )

                cur.execute(
                    """
                    INSERT INTO transaksi_harian (
                        id_tenongan, tanggal, qty_titip, qty_laku, qty_retur,
                        harga_jual_unit, komisi_unit, omzet_kotor, setoran_harapan,
                        uang_setoran, piutang, hpp_unit_saat_itu, status_konfirmasi,
                        status_pembayaran, is_rapel, catatan
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '[DATA SINTETIS] Operasional Harian');
                    """,
                    (
                        id_t,
                        tgl_iso,
                        hasil["qty_titip"],
                        hasil["qty_laku"],
                        hasil["qty_retur"],
                        hasil["harga_jual_unit"],
                        hasil["komisi_unit"],
                        hasil["omzet_kotor"],
                        hasil["setoran_harapan"],
                        hasil["uang_setoran"],
                        hasil["piutang"],
                        hpp_hari_ini,
                        hasil["status_konfirmasi"],
                        hasil["status_pembayaran"],
                    ),
                )
                jml_trx_harian += 1

            rasio_batch = total_titip_hari_ini / 120.0
            if rasio_batch > 0:
                for id_b, _, _, _, _, _, pakai_per_batch in KATALOG_BELANJA_RUTIN:
                    qty_pakai_hari = round(rasio_batch * pakai_per_batch, 3)
                    if qty_pakai_hari > 0:
                        stok_simulasi[id_b] = max(0.2, stok_simulasi.get(id_b, 0.0) - qty_pakai_hari)
                        cur.execute(
                            """
                            INSERT INTO mutasi_stok_bhn (id_bahan, tanggal, jenis_mutasi, qty_mutasi, keterangan)
                            VALUES (?, ?, 'KELUAR_PRODUKSI', ?, ?);
                            """,
                            (
                                id_b,
                                tgl_iso,
                                -qty_pakai_hari,
                                f"Pemakaian produksi {total_titip_hari_ini} pcs ({rasio_batch:.2f} batch)",
                            ),
                        )
                        jml_mutasi += 1

            if hari_aktif_counter % 3 == 0:
                cur.execute(
                    """
                    INSERT INTO biaya_operasional (tanggal, kategori_biaya, deskripsi, nominal, jenis_beban, metode_bayar, catatan)
                    VALUES (?, 'Distribusi & Transportasi', 'BBM Keliling Distribusi Tenongan', 15000.0, 'VARIABEL', 'Tunai', '[DATA SINTETIS]');
                    """,
                    (tgl_iso,),
                )
                jml_biaya_ops += 1
            if hari_aktif_counter % 6 == 0:
                cur.execute(
                    """
                    INSERT INTO biaya_operasional (tanggal, kategori_biaya, deskripsi, nominal, jenis_beban, metode_bayar, catatan)
                    VALUES (?, 'Gas & Energi Produksi', 'Isi Ulang Gas LPG 3 Kg Produksi', 22000.0, 'VARIABEL', 'Tunai', '[DATA SINTETIS]');
                    """,
                    (tgl_iso,),
                )
                jml_biaya_ops += 1
            if hari_aktif_counter % 24 == 0:
                cur.execute(
                    """
                    INSERT INTO biaya_operasional (tanggal, kategori_biaya, deskripsi, nominal, jenis_beban, metode_bayar, catatan)
                    VALUES (?, 'Perawatan Alat & Cetakan', 'Perawatan Rutin Loyang Cetakan & Kompor', 45000.0, 'TETAP', 'Tunai', '[DATA SINTETIS]');
                    """,
                    (tgl_iso,),
                )
                jml_biaya_ops += 1

            if hari_aktif_counter % 10 == 0:
                no_klk = f"DEMO-KLK-{tgl_iso.replace('-', '')}-01"
                qty_grosir = int(rng.choice([50, 75, 100]))
                hrg_grosir = 1800.0
                tot_grosir = round(qty_grosir * hrg_grosir, 2)
                cur.execute(
                    """
                    INSERT INTO transaksi_kulakan (
                        nomor_transaksi, tanggal, kategori_transaksi, nama_item,
                        qty, satuan, harga_unit, total_transaksi, mitra_nama,
                        metode_bayar, jumlah_dibayar, status_bayar, catatan
                    ) VALUES (?, ?, 'JUAL_GROSIR_TAIYAKI', 'Pesanan Taiyaki Acara Komunitas/Pengajian',
                              ?, 'pcs', ?, ?, 'Pelanggan Acara (Demo)', 'Transfer Bank', ?, 'LUNAS', '[DATA SINTETIS] Penjualan grosir');
                    """,
                    (no_klk, tgl_iso, qty_grosir, hrg_grosir, tot_grosir, tot_grosir),
                )
                jml_kulakan += 1

        for id_b, stok_akhir in stok_simulasi.items():
            stok_set = 1.2 if id_b in (2, 5) else round(max(2.5, stok_akhir), 2)
            cur.execute("UPDATE master_bahan SET stok_saat_ini = ? WHERE id_bahan = ?;", (stok_set, id_b))

        waktu_buat = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for k_meta, v_meta, t_meta in [
            ("demo_periode_bulan", str(periode_bulan), "int"),
            ("demo_generated_at", waktu_buat, "str"),
            ("demo_total_transaksi", str(jml_trx_harian), "int"),
            ("demo_tgl_mulai", tgl_mulai.isoformat(), "str"),
            ("demo_tgl_akhir", tgl_akhir.isoformat(), "str"),
            ("demo_seed", str(seed), "int"),
        ]:
            cur.execute(
                """
                INSERT INTO app_settings (setting_key, setting_value, tipe_data, deskripsi, updated_at)
                VALUES (?, ?, ?, 'Metadata Dataset Sintetis Demo', datetime('now', 'localtime'))
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = datetime('now', 'localtime');
                """,
                (k_meta, v_meta, t_meta),
            )

    return {
        "periode_bulan": periode_bulan,
        "tgl_mulai": tgl_mulai.isoformat(),
        "tgl_akhir": tgl_akhir.isoformat(),
        "jml_transaksi_harian": jml_trx_harian,
        "jml_belanja_bahan": jml_belanja,
        "jml_mutasi_stok": jml_mutasi,
        "jml_kulakan": jml_kulakan,
        "jml_biaya_ops": jml_biaya_ops,
        "generated_at": waktu_buat,
        "seed": seed,
    }


def pastikan_dataset_demo_tersedia(
    db_path: Optional[Path] = None,
    default_bulan: int = 6,
    default_seed: int = 42,
) -> bool:
    """
    Memeriksa apakah database demo (taiyaki_demo.db) sudah memiliki transaksi harian.
    Jika masih kosong (pertama kali dijalankan), otomatis membangkitkan dataset sintetis
    6 bulan satu kali saja. Tidak melakukan regenerasi saat Streamlit rerun.
    """
    target = Path(db_path) if db_path is not None else DEMO_DB_PATH
    if is_production_db_path(target):
        return False

    init_db(target, seed_master=True, allow_system_init=True)
    with get_connection(target, allow_system_init=True) as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM transaksi_harian;").fetchone()["c"]
        if int(cnt) > 0:
            return False

    _eksekusi_generator_sintetis(
        db_path=target,
        periode_bulan=default_bulan,
        seed=default_seed,
    )
    return True


def generate_ulang_dataset_demo_admin(
    periode_bulan: int = 6,
    seed: int = 42,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Membuat ulang atau mereset dataset demo dengan pilihan durasi 1-6 bulan.
    Wajib melalui autentikasi Administrator (require_admin_access).
    """
    require_admin_access()
    target = Path(db_path) if db_path is not None else DEMO_DB_PATH
    if is_production_db_path(target):
        raise PermissionError("Tidak dapat menjalankan generator sintetis pada database produksi.")
    return _eksekusi_generator_sintetis(db_path=target, periode_bulan=periode_bulan, seed=seed)


def get_metadata_dataset_demo(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Mengambil informasi periode, jumlah transaksi, dan waktu pembuatan dataset demo."""
    target = Path(db_path) if db_path is not None else DEMO_DB_PATH
    with get_connection(target) as conn:
        rows = conn.execute(
            "SELECT setting_key, setting_value FROM app_settings WHERE setting_key LIKE 'demo_%';"
        ).fetchall()
        meta = {r["setting_key"]: r["setting_value"] for r in rows}
        cnt_trx = conn.execute("SELECT COUNT(*) AS c FROM transaksi_harian;").fetchone()["c"]
        cnt_hari = conn.execute("SELECT COUNT(DISTINCT tanggal) AS h FROM transaksi_harian;").fetchone()["h"]
        return {
            "periode_bulan": int(meta.get("demo_periode_bulan", 6)),
            "generated_at": meta.get("demo_generated_at", "-"),
            "total_transaksi": int(cnt_trx),
            "total_hari_aktif": int(cnt_hari),
            "tgl_mulai": meta.get("demo_tgl_mulai", "-"),
            "tgl_akhir": meta.get("demo_tgl_akhir", "-"),
            "seed": int(meta.get("demo_seed", 42)),
        }