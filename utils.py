"""
utils.py
Modul utilitas untuk pemformatan mata uang Rupiah, tanggal Bahasa Indonesia,
validasi input numerik, dan pengelolaan state aplikasi.
"""

from datetime import date, datetime
from typing import Any, Optional, Tuple, Union
import math

NAMA_HARI_ID = {
    0: "Senin",
    1: "Selasa",
    2: "Rabu",
    3: "Kamis",
    4: "Jumat",
    5: "Sabtu",
    6: "Minggu",
}

NAMA_BULAN_ID = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}

NAMA_BULAN_SINGKAT_ID = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "Mei",
    6: "Jun",
    7: "Jul",
    8: "Agu",
    9: "Sep",
    10: "Okt",
    11: "Nov",
    12: "Des",
}


def format_rupiah(nominal: Optional[Union[int, float]], gunakan_desimal: bool = False) -> str:
    """
    Memformat angka menjadi format Rupiah Indonesia standar (contoh: Rp2.000 atau -Rp15.000).
    """
    if nominal is None:
        return "Rp0"
    try:
        val = float(nominal)
        if math.isnan(val) or math.isinf(val):
            return "Rp0"
    except (ValueError, TypeError):
        return "Rp0"

    tanda_negatif = "-" if val < 0 else ""
    val_abs = abs(val)

    if gunakan_desimal:
        teks = f"{val_abs:,.2f}"
        bagian_bulat, bagian_desimal = teks.split(".")
        bagian_bulat = bagian_bulat.replace(",", ".")
        return f"{tanda_negatif}Rp{bagian_bulat},{bagian_desimal}"
    else:
        bulat = int(round(val_abs))
        bagian_bulat = f"{bulat:,}".replace(",", ".")
        return f"{tanda_negatif}Rp{bagian_bulat}"


def format_angka(nilai: Optional[Union[int, float]], desimal: int = 0) -> str:
    """
    Memformat angka ribuan dengan titik dan desimal dengan koma sesuai standar Indonesia.
    """
    if nilai is None:
        return "0"
    try:
        val = float(nilai)
        if math.isnan(val) or math.isinf(val):
            return "0"
    except (ValueError, TypeError):
        return "0"

    if desimal <= 0:
        return f"{int(round(val)):,}".replace(",", ".")
    teks = f"{val:,.{desimal}f}"
    bulat, pecah = teks.split(".")
    bulat = bulat.replace(",", ".")
    return f"{bulat},{pecah}"


def format_persen(nilai: Optional[Union[int, float]], desimal: int = 1) -> str:
    """
    Memformat nilai persentase dengan koma desimal Indonesia (contoh: 85,5%).
    """
    return f"{format_angka(nilai, desimal=desimal)}%"


def parse_tanggal(tgl: Union[str, date, datetime]) -> date:
    """
    Mengonversi string ISO (YYYY-MM-DD), datetime, atau date menjadi objek datetime.date.
    """
    if isinstance(tgl, datetime):
        return tgl.date()
    if isinstance(tgl, date):
        return tgl
    if isinstance(tgl, str):
        teks = tgl.strip()[:10]
        return datetime.strptime(teks, "%Y-%m-%d").date()
    raise ValueError(f"Format tanggal tidak dikenali: {tgl}")


def format_tanggal_id(
    tgl: Optional[Union[str, date, datetime]],
    sertakan_hari: bool = False,
    singkat: bool = False,
) -> str:
    """
    Memformat tanggal ke dalam Bahasa Indonesia (contoh: 'Kamis, 01 Oktober 2026' atau '01 Okt 2026').
    """
    if tgl is None or tgl == "":
        return "-"
    try:
        obj_tgl = parse_tanggal(tgl)
    except Exception:
        return str(tgl)

    nama_bulan = NAMA_BULAN_SINGKAT_ID[obj_tgl.month] if singkat else NAMA_BULAN_ID[obj_tgl.month]
    teks_utama = f"{obj_tgl.day:02d} {nama_bulan} {obj_tgl.year}"
    if sertakan_hari:
        nama_hari = NAMA_HARI_ID[obj_tgl.weekday()]
        return f"{nama_hari}, {teks_utama}"
    return teks_utama


def bersihkan_teks(teks: Any) -> str:
    """
    Membersihkan string dari spasi berlebih di awal/akhir dan menangani nilai None/NaN.
    """
    if teks is None:
        return ""
    if isinstance(teks, float) and math.isnan(teks):
        return ""
    return " ".join(str(teks).strip().split())


def konversi_angka_aman(nilai: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Mengonversi input (termasuk string kosong atau spasi seperti pada Excel) menjadi float.
    """
    if nilai is None:
        return default
    if isinstance(nilai, (int, float)):
        if math.isnan(float(nilai)) or math.isinf(float(nilai)):
            return default
        return float(nilai)
    if isinstance(nilai, str):
        bersih = nilai.strip().replace("Rp", "").replace(" ", "")
        if bersih == "" or bersih == "-":
            return default
        # Tangani formula sederhana seperti '=4' atau '=4+4'
        if bersih.startswith("="):
            ekspresi = bersih[1:]
            try:
                if all(ch in "0123456789+-*/.() " for ch in ekspresi):
                    return float(eval(ekspresi, {"__builtins__": None}, {}))
            except Exception:
                return default
        try:
            return float(bersih)
        except ValueError:
            return default
    return default


def validasi_kuantitas_transaksi(
    qty_titip: Optional[Union[int, float]],
    qty_laku: Optional[Union[int, float]],
    qty_retur: Optional[Union[int, float]] = None,
    izinkan_rapel: bool = False,
) -> Tuple[bool, str]:
    """
    Memvalidasi aturan bisnis untuk input jumlah titip, laku, dan retur.
    Mengembalikan tuple (is_valid, pesan_error).
    """
    titip = 0 if qty_titip is None else int(qty_titip)
    laku = 0 if qty_laku is None else int(qty_laku)
    retur = 0 if qty_retur is None else int(qty_retur)

    if titip < 0:
        return False, "Jumlah roti dititipkan tidak boleh bernilai negatif."
    if laku < 0:
        return False, "Jumlah roti terjual (laku) tidak boleh bernilai negatif."
    if retur < 0:
        return False, "Jumlah retur/sisa tidak boleh bernilai negatif."
    if titip == 0 and laku == 0 and retur == 0:
        return False, "Transaksi kosong: minimal salah satu dari Titip atau Laku harus lebih dari 0."

    if not izinkan_rapel:
        if laku > titip:
            return (
                False,
                f"Jumlah laku ({laku} pcs) melebihi jumlah yang dititipkan ({titip} pcs). "
                "Aktifkan opsi 'Transaksi Rapel/Gabungan' jika ini mencakup sisa hari sebelumnya.",
            )
        sisa_maksimal = titip - laku
        if retur > sisa_maksimal:
            return (
                False,
                f"Jumlah retur ({retur} pcs) melebihi sisa barang yang tersedia ({sisa_maksimal} pcs).",
            )

    return True, "Valid"