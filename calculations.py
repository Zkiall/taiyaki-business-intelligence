"""
calculations.py
Modul perhitungan bisnis murni (pure functions) untuk operasional Taiyaki:
- Perhitungan transaksi titip jual (omzet, setoran harapan, piutang, retur, status)
- Sell-through rate (STR) dan Return rate (RR)
- Kalkulasi HPP per batch dan per unit
- Perhitungan Laba Rugi (Akrual) vs Arus Kas (Cash Basis)
- Analisis Break-Even Point (BEP) dan simulasi skenario
"""

from typing import Dict, List, Optional, Union


def hitung_transaksi_item(
    qty_titip: int,
    qty_laku: Optional[int],
    qty_retur: Optional[int] = None,
    harga_jual_unit: float = 2000.0,
    komisi_unit: float = 0.0,
    uang_setoran: Optional[float] = None,
    status_konfirmasi_input: Optional[str] = None,
    is_rapel: bool = False,
) -> Dict[str, Union[int, float, str]]:
    """
    Menghitung metrik lengkap untuk satu baris transaksi titip jual tenongan.
    Membedakan dengan tegas antara barang belum terkonfirmasi vs sudah direkap,
    serta antara omzet penjualan vs uang yang benar-benar disetorkan.
    """
    titip = max(0, int(qty_titip or 0))
    harga = max(0.0, float(harga_jual_unit or 0.0))
    komisi = max(0.0, float(komisi_unit or 0.0))
    harga_bersih_usaha = max(0.0, harga - komisi)

    # Tentukan apakah transaksi masih menggantung (belum dikonfirmasi hasil lakunya)
    if status_konfirmasi_input == "BELUM_KONFIRMASI" or (qty_laku is None and titip > 0):
        status_konfirmasi = "BELUM_KONFIRMASI"
        laku_aktual = 0
        retur_aktual = 0
        omzet_kotor = 0.0
        setoran_harapan = 0.0
        setoran_aktual = max(0.0, float(uang_setoran or 0.0))
        piutang = 0.0
        status_pembayaran = "BELUM_BAYAR"
        return {
            "qty_titip": titip,
            "qty_laku": laku_aktual,
            "qty_retur": retur_aktual,
            "harga_jual_unit": harga,
            "komisi_unit": komisi,
            "omzet_kotor": omzet_kotor,
            "setoran_harapan": setoran_harapan,
            "uang_setoran": setoran_aktual,
            "piutang": piutang,
            "status_konfirmasi": status_konfirmasi,
            "status_pembayaran": status_pembayaran,
        }

    status_konfirmasi = "TERKONFIRMASI"
    laku_aktual = max(0, int(qty_laku or 0))

    # Hitung retur: jika tidak diisi eksplisit, hitung dari selisih (titip - laku)
    if qty_retur is not None:
        retur_aktual = max(0, int(qty_retur))
    else:
        retur_aktual = max(0, titip - laku_aktual)

    omzet_kotor = float(laku_aktual * harga)
    setoran_harapan = float(laku_aktual * harga_bersih_usaha)

    if uang_setoran is None:
        setoran_aktual = setoran_harapan
    else:
        setoran_aktual = max(0.0, float(uang_setoran))

    piutang = max(0.0, setoran_harapan - setoran_aktual)

    if setoran_harapan == 0:
        status_pembayaran = "LUNAS"
    elif setoran_aktual >= setoran_harapan - 1e-4:
        status_pembayaran = "LUNAS"
        piutang = 0.0
    elif setoran_aktual > 0:
        status_pembayaran = "SEBAGIAN"
    else:
        status_pembayaran = "BELUM_BAYAR"

    return {
        "qty_titip": titip,
        "qty_laku": laku_aktual,
        "qty_retur": retur_aktual,
        "harga_jual_unit": harga,
        "komisi_unit": komisi,
        "omzet_kotor": round(omzet_kotor, 2),
        "setoran_harapan": round(setoran_harapan, 2),
        "uang_setoran": round(setoran_aktual, 2),
        "piutang": round(piutang, 2),
        "status_konfirmasi": status_konfirmasi,
        "status_pembayaran": status_pembayaran,
    }


def hitung_sell_through_dan_retur(
    total_titip_terkonfirmasi: int,
    total_laku_terkonfirmasi: int,
    total_retur_terkonfirmasi: int,
) -> Dict[str, float]:
    """
    Menghitung Sell-Through Rate (STR) dan Return Rate (RR) hanya untuk
    transaksi yang sudah terkonfirmasi (valid).
    """
    titip = max(0, int(total_titip_terkonfirmasi or 0))
    laku = max(0, int(total_laku_terkonfirmasi or 0))
    retur = max(0, int(total_retur_terkonfirmasi or 0))

    if titip == 0:
        return {
            "sell_through_rate": 0.0,
            "return_rate": 0.0,
        }

    str_persen = (laku / titip) * 100.0
    rr_persen = (retur / titip) * 100.0
    return {
        "sell_through_rate": round(str_persen, 2),
        "return_rate": round(rr_persen, 2),
    }


def hitung_hpp_batch(
    daftar_biaya_bahan: List[float],
    output_batch_pcs: int = 120,
    biaya_tenaga_kerja_batch: float = 0.0,
    biaya_energi_batch: float = 0.0,
    biaya_distribusi_batch: float = 0.0,
    biaya_lain_batch: float = 0.0,
    harga_jual_unit: float = 2000.0,
) -> Dict[str, float]:
    """
    Menghitung total biaya produksi per batch, HPP per buah, laba kotor per buah,
    dan margin keuntungan kotor sesuai rumus standar.
    """
    if output_batch_pcs <= 0:
        raise ValueError("Jumlah output produk layak jual per batch harus lebih besar dari 0.")

    total_bahan = sum(max(0.0, float(x or 0.0)) for x in daftar_biaya_bahan)
    total_overhead_batch = (
        max(0.0, float(biaya_tenaga_kerja_batch or 0.0))
        + max(0.0, float(biaya_energi_batch or 0.0))
        + max(0.0, float(biaya_distribusi_batch or 0.0))
        + max(0.0, float(biaya_lain_batch or 0.0))
    )
    total_biaya_batch = total_bahan + total_overhead_batch
    hpp_per_pcs = total_biaya_batch / float(output_batch_pcs)

    harga = max(0.0, float(harga_jual_unit or 0.0))
    laba_kotor_per_pcs = harga - hpp_per_pcs
    margin_kotor_persen = (laba_kotor_per_pcs / harga * 100.0) if harga > 0 else 0.0

    return {
        "total_biaya_bahan": round(total_bahan, 2),
        "total_overhead_batch": round(total_overhead_batch, 2),
        "total_biaya_batch": round(total_biaya_batch, 2),
        "output_batch_pcs": int(output_batch_pcs),
        "hpp_per_pcs": round(hpp_per_pcs, 4),
        "harga_jual_unit": round(harga, 2),
        "laba_kotor_per_pcs": round(laba_kotor_per_pcs, 4),
        "margin_kotor_persen": round(margin_kotor_persen, 2),
    }


def hitung_laba_rugi_periode(
    total_titip_terkonfirmasi: int,
    total_laku: int,
    total_retur: int,
    omzet_kotor: float,
    setoran_harapan: float,
    uang_setoran_diterima: float,
    hpp_per_pcs: float,
    biaya_operasional_lain: float = 0.0,
    belanja_bahan_dibayar_kas: float = 0.0,
    kas_masuk_kulakan: float = 0.0,
    kas_keluar_kulakan: float = 0.0,
    mode_beban_retur: str = "BEBANKAN_RETUR",
) -> Dict[str, float]:
    """
    Menghitung Laporan Laba Rugi (Berbasis Akrual Sederhana) dan Laporan Arus Kas (Berbasis Kas).
    - Pada mode 'BEBANKAN_RETUR' (default sesuai sheet kalkulasi roti basah):
      Total HPP Produksi mencakup HPP barang terjual + biaya kerugian retur (spoilage).
    - Pada mode 'RITEL_MURNI':
      Hanya HPP barang terjual yang mengurangi laba kotor.
    """
    laku = max(0, int(total_laku or 0))
    retur = max(0, int(total_retur or 0))
    hpp_unit = max(0.0, float(hpp_per_pcs or 0.0))

    hpp_barang_terjual = laku * hpp_unit
    biaya_kerugian_retur = retur * hpp_unit

    if mode_beban_retur == "BEBANKAN_RETUR":
        total_hpp_dibebankan = hpp_barang_terjual + biaya_kerugian_retur
    else:
        total_hpp_dibebankan = hpp_barang_terjual

    pendapatan_akrual = max(0.0, float(setoran_harapan or 0.0))
    laba_kotor = pendapatan_akrual - total_hpp_dibebankan
    biaya_ops = max(0.0, float(biaya_operasional_lain or 0.0))
    laba_bersih = laba_kotor - biaya_ops

    margin_kotor_persen = (laba_kotor / pendapatan_akrual * 100.0) if pendapatan_akrual > 0 else 0.0
    margin_bersih_persen = (laba_bersih / pendapatan_akrual * 100.0) if pendapatan_akrual > 0 else 0.0

    # Arus Kas (Cash Basis)
    kas_masuk_setoran = max(0.0, float(uang_setoran_diterima or 0.0))
    kas_masuk_total = kas_masuk_setoran + max(0.0, float(kas_masuk_kulakan or 0.0))
    kas_keluar_total = (
        max(0.0, float(belanja_bahan_dibayar_kas or 0.0))
        + biaya_ops
        + max(0.0, float(kas_keluar_kulakan or 0.0))
    )
    arus_kas_bersih = kas_masuk_total - kas_keluar_total
    piutang_periode = max(0.0, pendapatan_akrual - kas_masuk_setoran)

    return {
        "omzet_kotor": round(float(omzet_kotor or 0.0), 2),
        "pendapatan_akrual": round(pendapatan_akrual, 2),
        "uang_setoran_diterima": round(kas_masuk_setoran, 2),
        "piutang_periode": round(piutang_periode, 2),
        "hpp_barang_terjual": round(hpp_barang_terjual, 2),
        "biaya_kerugian_retur": round(biaya_kerugian_retur, 2),
        "total_hpp_dibebankan": round(total_hpp_dibebankan, 2),
        "laba_kotor": round(laba_kotor, 2),
        "biaya_operasional_lain": round(biaya_ops, 2),
        "laba_bersih": round(laba_bersih, 2),
        "margin_kotor_persen": round(margin_kotor_persen, 2),
        "margin_bersih_persen": round(margin_bersih_persen, 2),
        "kas_masuk_total": round(kas_masuk_total, 2),
        "kas_keluar_total": round(kas_keluar_total, 2),
        "arus_kas_bersih": round(arus_kas_bersih, 2),
    }


def hitung_bep_dan_simulasi(
    harga_jual_unit: float,
    hpp_variabel_unit: float,
    biaya_tetap_periode: float,
    target_volume_laku: int = 1500,
    estimasi_return_rate_persen: float = 15.0,
) -> Dict[str, Union[float, Dict[str, float]]]:
    """
    Menghitung Break-Even Point (BEP) dalam unit dan Rupiah, serta menyediakan
    simulasi 3 skenario (Konservatif, Moderat, Optimistis).
    """
    harga = max(0.0, float(harga_jual_unit or 0.0))
    hpp_var = max(0.0, float(hpp_variabel_unit or 0.0))
    biaya_tetap = max(0.0, float(biaya_tetap_periode or 0.0))

    margin_kontribusi_unit = harga - hpp_var
    rasio_margin_kontribusi = (margin_kontribusi_unit / harga) if harga > 0 else 0.0

    if margin_kontribusi_unit <= 0:
        bep_unit = float("inf")
        bep_rupiah = float("inf")
    else:
        bep_unit = biaya_tetap / margin_kontribusi_unit
        bep_rupiah = bep_unit * harga

    rr_moderat = max(0.0, min(95.0, float(estimasi_return_rate_persen or 0.0))) / 100.0
    skenario_konfig = {
        "Konservatif": {"faktor_vol": 0.80, "rr": min(0.95, rr_moderat + 0.07)},
        "Moderat": {"faktor_vol": 1.00, "rr": rr_moderat},
        "Optimistis": {"faktor_vol": 1.20, "rr": max(0.0, rr_moderat - 0.05)},
    }

    hasil_skenario = {}
    for nama, cfg in skenario_konfig.items():
        vol_laku = int(round(target_volume_laku * cfg["faktor_vol"]))
        sell_through = 1.0 - cfg["rr"]
        vol_produksi = int(round(vol_laku / sell_through)) if sell_through > 0 else vol_laku
        vol_retur = max(0, vol_produksi - vol_laku)

        omzet = vol_laku * harga
        total_biaya_var = vol_produksi * hpp_var
        laba_bersih = omzet - total_biaya_var - biaya_tetap
        margin_persen = (laba_bersih / omzet * 100.0) if omzet > 0 else 0.0

        hasil_skenario[nama] = {
            "qty_produksi": vol_produksi,
            "qty_laku": vol_laku,
            "qty_retur": vol_retur,
            "return_rate_persen": round(cfg["rr"] * 100.0, 1),
            "omzet": round(omzet, 2),
            "biaya_variabel_total": round(total_biaya_var, 2),
            "biaya_tetap": round(biaya_tetap, 2),
            "laba_bersih": round(laba_bersih, 2),
            "margin_bersih_persen": round(margin_persen, 2),
        }

    return {
        "harga_jual_unit": round(harga, 2),
        "hpp_variabel_unit": round(hpp_var, 4),
        "margin_kontribusi_unit": round(margin_kontribusi_unit, 4),
        "rasio_margin_kontribusi_persen": round(rasio_margin_kontribusi * 100.0, 2),
        "biaya_tetap_periode": round(biaya_tetap, 2),
        "bep_unit": round(bep_unit, 2) if bep_unit != float("inf") else -1.0,
        "bep_rupiah": round(bep_rupiah, 2) if bep_rupiah != float("inf") else -1.0,
        "skenario": hasil_skenario,
    }