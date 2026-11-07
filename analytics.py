"""
analytics.py
Mesin Exploratory Data Analysis (EDA), Evaluasi Matriks Performa & Tren Tenongan,
Analisis Elastisitas Biaya Bahan Baku terhadap Margin, dan Generator Insight Otomatis
berbasis bukti statistik dari database SQLite.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from database import get_setting, query_df
from utils import NAMA_HARI_ID, format_angka, format_persen, format_rupiah


def get_dataset_eda_harian(
    tgl_mulai: Optional[str] = None,
    tgl_selesai: Optional[str] = None,
    hanya_terkonfirmasi: bool = True,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Mengambil agregasi penjualan harian untuk keperluan EDA dan analisis tren.
    Secara default hanya menyertakan transaksi berstatus TERKONFIRMASI agar
    tingkat retur dan sell-through rate tidak terdistorsi oleh barang yang belum direkap.
    """
    hpp_default = float(get_setting("hpp_unit_aktif", 999.2222, db_path=db_path) or 999.2222)
    mode_retur = str(get_setting("mode_beban_retur", "BEBANKAN_RETUR", db_path=db_path) or "BEBANKAN_RETUR")

    kondisi = []
    params: List[Any] = [hpp_default, hpp_default]

    if hanya_terkonfirmasi:
        kondisi.append("status_konfirmasi = 'TERKONFIRMASI'")
    if tgl_mulai:
        kondisi.append("tanggal >= ?")
        params.append(str(tgl_mulai)[:10])
    if tgl_selesai:
        kondisi.append("tanggal <= ?")
        params.append(str(tgl_selesai)[:10])

    where_sql = f"WHERE {' AND '.join(kondisi)}" if kondisi else ""

    sql = f"""
        SELECT
            tanggal,
            COUNT(DISTINCT id_tenongan) AS jml_tenongan_aktif,
            SUM(qty_titip) AS total_titip,
            SUM(qty_laku) AS total_laku,
            SUM(qty_retur) AS total_retur,
            SUM(omzet_kotor) AS omzet_kotor,
            SUM(setoran_harapan) AS setoran_harapan,
            SUM(uang_setoran) AS uang_setoran,
            SUM(piutang) AS total_piutang,
            SUM(qty_laku * COALESCE(hpp_unit_saat_itu, ?)) AS hpp_laku,
            SUM(qty_retur * COALESCE(hpp_unit_saat_itu, ?)) AS hpp_retur
        FROM transaksi_harian
        {where_sql}
        GROUP BY tanggal
        ORDER BY tanggal ASC;
    """
    df = query_df(sql, tuple(params), db_path=db_path)
    if df.empty:
        return df

    df["total_hpp"] = (
        df["hpp_laku"] + df["hpp_retur"]
        if mode_retur == "BEBANKAN_RETUR"
        else df["hpp_laku"]
    )
    df["laba_kotor"] = df["setoran_harapan"] - df["total_hpp"]
    df["sell_through_rate"] = np.where(
        df["total_titip"] > 0,
        (df["total_laku"] / df["total_titip"]) * 100.0,
        0.0,
    )
    df["return_rate"] = np.where(
        df["total_titip"] > 0,
        (df["total_retur"] / df["total_titip"]) * 100.0,
        0.0,
    )
    df["dt"] = pd.to_datetime(df["tanggal"])
    df["weekday"] = df["dt"].dt.weekday
    df["nama_hari"] = df["weekday"].map(NAMA_HARI_ID)
    df["minggu_iso"] = df["dt"].dt.strftime("%Y-W%W")
    df["bulan_iso"] = df["tanggal"].str.slice(0, 7)
    return df


def hitung_statistik_deskriptif_eda(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Menghitung tabel statistik deskriptif (Count, Mean, Median, Min, Max, Std, Sum)
    untuk metrik utama harian.
    """
    if df_daily is None or df_daily.empty:
        return pd.DataFrame()

    kolom_target = {
        "total_titip": "Total Roti Dititipkan (pcs/hari)",
        "total_laku": "Total Roti Terjual / Laku (pcs/hari)",
        "total_retur": "Total Roti Retur / Sisa (pcs/hari)",
        "sell_through_rate": "Sell-Through Rate Harian (%)",
        "return_rate": "Return Rate Harian (%)",
        "omzet_kotor": "Omzet Penjualan Kotor (Rp/hari)",
        "laba_kotor": "Estimasi Laba Kotor (Rp/hari)",
    }
    baris = []
    for kol, label in kolom_target.items():
        if kol not in df_daily.columns:
            continue
        s = df_daily[kol].dropna()
        if s.empty:
            continue
        baris.append(
            {
                "Metrik Harian": label,
                "Jumlah Hari (Count)": int(s.count()),
                "Rata-rata (Mean)": round(float(s.mean()), 2),
                "Nilai Tengah (Median)": round(float(s.median()), 2),
                "Minimum (Min)": round(float(s.min()), 2),
                "Maksimum (Max)": round(float(s.max()), 2),
                "Simpangan Baku (Std)": round(float(s.std()) if len(s) > 1 else 0.0, 2),
                "Total Akumulasi (Sum)": round(float(s.sum()), 2)
                if "rate" not in kol
                else np.nan,
            }
        )
    return pd.DataFrame(baris)


def analisis_pola_hari_minggu(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Menganalisis rata-rata, median, simpangan baku, dan STR menurut hari dalam minggu."""
    if df_daily is None or df_daily.empty:
        return pd.DataFrame()

    df_aktif = df_daily[df_daily["total_titip"] > 0].copy()
    if df_aktif.empty:
        df_aktif = df_daily.copy()

    rekap = (
        df_aktif.groupby(["weekday", "nama_hari"], as_index=False)
        .agg(
            jumlah_hari=("tanggal", "count"),
            rata2_titip=("total_titip", "mean"),
            rata2_laku=("total_laku", "mean"),
            median_laku=("total_laku", "median"),
            std_laku=("total_laku", "std"),
            rata2_retur=("total_retur", "mean"),
            rata2_omzet=("omzet_kotor", "mean"),
            total_titip=("total_titip", "sum"),
            total_laku=("total_laku", "sum"),
            total_retur=("total_retur", "sum"),
        )
        .sort_values("weekday", ascending=True)
    )
    rekap["std_laku"] = rekap["std_laku"].fillna(0.0)
    rekap["sell_through_rate"] = np.where(
        rekap["total_titip"] > 0,
        (rekap["total_laku"] / rekap["total_titip"]) * 100.0,
        0.0,
    )
    rekap["return_rate"] = np.where(
        rekap["total_titip"] > 0,
        (rekap["total_retur"] / rekap["total_titip"]) * 100.0,
        0.0,
    )
    return rekap.round(2)


def analisis_hubungan_titip_laku(
    tgl_mulai: Optional[str] = None,
    tgl_selesai: Optional[str] = None,
    abaikan_outlier_agregat: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Menganalisis korelasi antara jumlah barang dititipkan (qty_titip) dan terjual (qty_laku),
    serta menghitung frekuensi kejadian habis terjual 100% (stockout / censored demand).
    """
    kondisi = ["tr.status_konfirmasi = 'TERKONFIRMASI'"]
    params: List[Any] = []
    if tgl_mulai:
        kondisi.append("tr.tanggal >= ?")
        params.append(str(tgl_mulai)[:10])
    if tgl_selesai:
        kondisi.append("tr.tanggal <= ?")
        params.append(str(tgl_selesai)[:10])

    sql = f"""
        SELECT
            tr.id_transaksi,
            tr.tanggal,
            tr.id_tenongan,
            t.nama_tenongan,
            tr.qty_titip,
            tr.qty_laku,
            tr.qty_retur,
            tr.is_rapel
        FROM transaksi_harian tr
        JOIN tenongan t ON tr.id_tenongan = t.id_tenongan
        WHERE {' AND '.join(kondisi)};
    """
    df = query_df(sql, tuple(params), db_path=db_path)
    if df.empty:
        return {"jumlah_data": 0, "korelasi_pearson": 0.0, "korelasi_spearman": 0.0, "df": df}

    if abaikan_outlier_agregat:
        df_clean = df[(df["qty_titip"] > 0) & (df["qty_titip"] < 30)].copy()
    else:
        df_clean = df[df["qty_titip"] > 0].copy()

    if len(df_clean) < 3:
        return {"jumlah_data": len(df_clean), "korelasi_pearson": 0.0, "korelasi_spearman": 0.0, "df": df_clean}

    r_pearson = float(df_clean["qty_titip"].corr(df_clean["qty_laku"], method="pearson"))
    r_spearman = float(df_clean["qty_titip"].corr(df_clean["qty_laku"], method="spearman"))
    jml_habis = int((df_clean["qty_laku"] >= df_clean["qty_titip"]).sum())
    persen_habis = round((jml_habis / len(df_clean)) * 100.0, 2)

    return {
        "jumlah_data": len(df_clean),
        "korelasi_pearson": round(r_pearson, 4) if not np.isnan(r_pearson) else 0.0,
        "korelasi_spearman": round(r_spearman, 4) if not np.isnan(r_spearman) else 0.0,
        "jml_habis_terjual": jml_habis,
        "persen_habis_terjual": persen_habis,
        "df": df_clean,
    }


def evaluasi_performa_dan_tren_tenongan(
    tgl_mulai: Optional[str] = None,
    tgl_selesai: Optional[str] = None,
    min_hari_valid: int = 5,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Mengevaluasi peringkat performa setiap tenongan pada transaksi terkonfirmasi:
    - Peringkat berdasarkan jumlah laku
    - Sell-Through Rate (STR) & Return Rate (RR)
    - Rata-rata penjualan per hari aktif & tingkat habis terjual (Sold-Out Rate)
    - Identifikasi tren performa (MENINGKAT, STABIL, MENURUN, atau DATA_TERBATAS)
      berdasarkan perbandingan paruh awal vs paruh akhir dan kemiringan regresi (slope).
    """
    kondisi = ["tr.status_konfirmasi = 'TERKONFIRMASI'", "tr.qty_titip < 30"]
    params: List[Any] = []
    if tgl_mulai:
        kondisi.append("tr.tanggal >= ?")
        params.append(str(tgl_mulai)[:10])
    if tgl_selesai:
        kondisi.append("tr.tanggal <= ?")
        params.append(str(tgl_selesai)[:10])

    sql = f"""
        SELECT
            tr.id_tenongan,
            t.kode_tenongan,
            t.nama_tenongan,
            t.pola_titip,
            t.kuota_default,
            t.status_aktif,
            tr.tanggal,
            tr.qty_titip,
            tr.qty_laku,
            tr.qty_retur,
            tr.omzet_kotor
        FROM transaksi_harian tr
        JOIN tenongan t ON tr.id_tenongan = t.id_tenongan
        WHERE {' AND '.join(kondisi)}
        ORDER BY tr.tanggal ASC;
    """
    df = query_df(sql, tuple(params), db_path=db_path)
    if df.empty:
        return pd.DataFrame()

    hasil_list = []
    for (id_t, kode_t, nama_t, pola_t, kuota_def, st_aktif), grp in df.groupby(
        ["id_tenongan", "kode_tenongan", "nama_tenongan", "pola_titip", "kuota_default", "status_aktif"]
    ):
        n_hari = len(grp)
        tot_titip = int(grp["qty_titip"].sum())
        tot_laku = int(grp["qty_laku"].sum())
        tot_retur = int(grp["qty_retur"].sum())
        tot_omzet = float(grp["omzet_kotor"].sum())

        str_pct = (tot_laku / tot_titip * 100.0) if tot_titip > 0 else 0.0
        rr_pct = (tot_retur / tot_titip * 100.0) if tot_titip > 0 else 0.0
        avg_titip = float(grp["qty_titip"].mean())
        avg_laku = float(grp["qty_laku"].mean())
        avg_retur = float(grp["qty_retur"].mean())
        sold_out_pct = float((grp["qty_laku"] >= grp["qty_titip"]).mean() * 100.0)

        # Deteksi tren meningkat / menurun (membutuhkan minimal max(6, min_hari_valid) observasi)
        if n_hari >= max(6, min_hari_valid):
            mid = n_hari // 2
            mean_awal = float(grp["qty_laku"].iloc[:mid].mean())
            mean_akhir = float(grp["qty_laku"].iloc[mid:].mean())
            delta_persen = ((mean_akhir - mean_awal) / mean_awal * 100.0) if mean_awal > 0 else 0.0
            x_idx = np.arange(n_hari, dtype=float)
            slope = float(np.polyfit(x_idx, grp["qty_laku"].values.astype(float), 1)[0])

            if delta_persen >= 8.0 or slope >= 0.03:
                status_tren = "MENINGKAT"
            elif delta_persen <= -8.0 or slope <= -0.03:
                status_tren = "MENURUN"
            else:
                status_tren = "STABIL"
        else:
            delta_persen = 0.0
            slope = 0.0
            status_tren = "DATA_TERBATAS"

        hasil_list.append(
            {
                "id_tenongan": int(id_t),
                "kode_tenongan": str(kode_t),
                "nama_tenongan": str(nama_t),
                "pola_titip": str(pola_t),
                "kuota_default": int(kuota_def),
                "status_aktif": int(st_aktif),
                "hari_valid": int(n_hari),
                "total_titip": tot_titip,
                "total_laku": tot_laku,
                "total_retur": tot_retur,
                "total_omzet": round(tot_omzet, 2),
                "rata2_titip_harian": round(avg_titip, 2),
                "rata2_laku_harian": round(avg_laku, 2),
                "rata2_retur_harian": round(avg_retur, 2),
                "sell_through_rate": round(str_pct, 2),
                "return_rate": round(rr_pct, 2),
                "sold_out_rate": round(sold_out_pct, 1),
                "perubahan_paruh_persen": round(delta_persen, 1),
                "slope_tren": round(slope, 4),
                "status_tren": status_tren,
            }
        )

    df_perf = pd.DataFrame(hasil_list).sort_values(
        ["total_laku", "sell_through_rate"], ascending=[False, False]
    ).reset_index(drop=True)
    df_perf["peringkat"] = df_perf.index + 1
    return df_perf


def analisis_sensitivitas_bahan_baku(db_path: Optional[Path] = None) -> pd.DataFrame:
    """
    Menganalisis kontribusi setiap bahan baku pada resep HPP aktif dan menghitung
    dampak kenaikan harga bahan sebesar +10% terhadap HPP per biji dan margin kotor.
    """
    harga_jual = float(get_setting("harga_jual_default", 2000.0, db_path=db_path) or 2000.0)
    df_resep = query_df("SELECT * FROM resep_hpp_batch ORDER BY id_resep ASC LIMIT 1;", db_path=db_path)
    if df_resep.empty:
        return pd.DataFrame()

    resep = df_resep.iloc[0]
    output_pcs = max(1, int(resep["output_batch_pcs"]))
    total_batch = float(resep["total_biaya_batch"])
    hpp_awal = total_batch / output_pcs
    margin_awal = ((harga_jual - hpp_awal) / harga_jual * 100.0) if harga_jual > 0 else 0.0

    df_items = query_df(
        "SELECT * FROM resep_hpp_item WHERE id_resep = ? ORDER BY biaya_item_batch DESC;",
        (int(resep["id_resep"]),),
        db_path=db_path,
    )
    if df_items.empty:
        return pd.DataFrame()

    baris = []
    for _, r in df_items.iterrows():
        biaya_komp = float(r["biaya_item_batch"])
        porsi_persen = (biaya_komp / total_batch * 100.0) if total_batch > 0 else 0.0
        biaya_per_biji = biaya_komp / output_pcs
        # Simulasi jika bahan ini naik 10%
        tambahan_hpp_biji = biaya_per_biji * 0.10
        margin_baru = ((harga_jual - (hpp_awal + tambahan_hpp_biji)) / harga_jual * 100.0) if harga_jual > 0 else 0.0
        penurunan_margin_poin = margin_awal - margin_baru

        baris.append(
            {
                "Komponen Bahan": str(r["nama_komponen"]),
                "Kategori": str(r["kategori"]),
                "Biaya per Batch (Rp)": round(biaya_komp, 2),
                "Biaya per Biji (Rp)": round(biaya_per_biji, 2),
                "Porsi Biaya Produksi (%)": round(porsi_persen, 2),
                "Dampak +10% Harga pada HPP/Biji (Rp)": round(tambahan_hpp_biji, 2),
                "Tekanan Margin Jika Naik +10% (Poin %)": round(penurunan_margin_poin, 3),
            }
        )
    return pd.DataFrame(baris)


def generate_insight_otomatis(
    tgl_mulai: Optional[str] = None,
    tgl_selesai: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """
    Menghasilkan ringkasan insight eksekutif otomatis yang 100% didukung oleh data aktual:
    1. Tenongan dengan performa terbaik & kontribusi pareto.
    2. Hari dalam minggu dengan permintaan tertinggi vs terendah.
    3. Perubahan penjualan 7 hari aktif terakhir dibanding 7 hari aktif sebelumnya.
    4. Bahan baku yang kenaikan harganya paling menekan margin.
    5. Risiko overproduction (retur tinggi) vs kekurangan stok (stockout / habis terjual).
    """
    insights: List[Dict[str, str]] = []
    df_daily = get_dataset_eda_harian(tgl_mulai, tgl_selesai, hanya_terkonfirmasi=True, db_path=db_path)

    if df_daily.empty or len(df_daily) < 3:
        return [
            {
                "judul": "Histori Data Belum Memadai untuk Insight Otomatis",
                "kategori": "peringatan",
                "isi": (
                    "Sistem membutuhkan minimal 3 hari transaksi terkonfirmasi untuk menghasilkan "
                    "insight statistik yang valid. Silakan tambah input harian atau impor data Excel."
                ),
            }
        ]

    df_perf = evaluasi_performa_dan_tren_tenongan(tgl_mulai, tgl_selesai, db_path=db_path)
    df_dow = analisis_pola_hari_minggu(df_daily)
    kor_info = analisis_hubungan_titip_laku(tgl_mulai, tgl_selesai, db_path=db_path)
    df_bhn_sens = analisis_sensitivitas_bahan_baku(db_path=db_path)

    # 1. Insight Tenongan Terbaik
    if not df_perf.empty:
        top1 = df_perf.iloc[0]
        top3_names = ", ".join(
            f"**{r['nama_tenongan']}** ({format_angka(r['total_laku'])} pcs, STR {format_persen(r['sell_through_rate'], 1)})"
            for _, r in df_perf.head(3).iterrows()
        )
        jml_meningkat = int((df_perf["status_tren"] == "MENINGKAT").sum())
        jml_menurun = int((df_perf["status_tren"] == "MENURUN").sum())
        insights.append(
            {
                "judul": f"🏆 Tenongan Performa Terbaik: {top1['nama_tenongan']}",
                "kategori": "positif",
                "isi": (
                    f"Tiga mitra dengan penjualan tertinggi adalah {top3_names}. "
                    f"Dari seluruh tenongan aktif, terdeteksi **{jml_meningkat} tenongan** memiliki tren penjualan meningkat "
                    f"dan **{jml_menurun} tenongan** mengalami tren menurun pada paruh kedua periode."
                ),
            }
        )

    # 2. Insight Hari Permintaan Tertinggi
    if not df_dow.empty:
        hari_max = df_dow.sort_values("rata2_laku", ascending=False).iloc[0]
        hari_min = df_dow.sort_values("rata2_laku", ascending=True).iloc[0]
        insights.append(
            {
                "judul": f"📅 Hari Permintaan Tertinggi: {hari_max['nama_hari']}",
                "kategori": "info",
                "isi": (
                    f"Permintaan rata-rata tertinggi terjadi pada hari **{hari_max['nama_hari']}** sebesar "
                    f"**{format_angka(hari_max['rata2_laku'], 1)} pcs/hari** (Sell-Through {format_persen(hari_max['sell_through_rate'], 1)}), "
                    f"sedangkan rata-rata terendah pada hari **{hari_min['nama_hari']}** sebesar "
                    f"**{format_angka(hari_min['rata2_laku'], 1)} pcs/hari**."
                ),
            }
        )

    # 3. Insight Perubahan Penjualan Minggu Terakhir vs Minggu Sebelumnya (6 hari aktif vs 6 hari aktif)
    df_aktif = df_daily[df_daily["total_titip"] > 0].reset_index(drop=True)
    if len(df_aktif) >= 12:
        w_kini = df_aktif.iloc[-6:]["total_laku"].sum()
        w_lalu = df_aktif.iloc[-12:-6]["total_laku"].sum()
        delta_w = ((w_kini - w_lalu) / w_lalu * 100.0) if w_lalu > 0 else 0.0
        arah = "meningkat" if delta_w >= 0 else "menurun"
        insights.append(
            {
                "judul": f"📈 Tren Mingguan: Penjualan {arah.capitalize()} {format_persen(abs(delta_w), 1)}",
                "kategori": "positif" if delta_w >= 0 else "peringatan",
                "isi": (
                    f"Total penjualan pada 6 hari produksi terakhir mencapai **{format_angka(w_kini)} pcs**, "
                    f"{arah} **{format_persen(abs(delta_w), 1)}** dibandingkan 6 hari produksi sebelumnya (**{format_angka(w_lalu)} pcs**)."
                ),
            }
        )

    # 4. Insight Risiko Overproduction (Retur) vs Kekurangan Stok (Stockout)
    if not df_perf.empty and kor_info["jumlah_data"] > 0:
        retur_tinggi = df_perf[(df_perf["hari_valid"] >= 5) & (df_perf["return_rate"] >= 28.0)]
        nama_retur = ", ".join(
            f"**{r['nama_tenongan']}** (Retur {format_persen(r['return_rate'], 1)})"
            for _, r in retur_tinggi.head(3).iterrows()
        )
        insights.append(
            {
                "judul": "⚖️ Risiko Overproduction vs Kekurangan Stok (Lost Sales)",
                "kategori": "peringatan",
                "isi": (
                    f"Sebanyak **{kor_info['jml_habis_terjual']} dari {kor_info['jumlah_data']} transaksi ({format_persen(kor_info['persen_habis_terjual'], 1)})** "
                    f"habis terjual 100% (`Laku == Titip`), menandakan adanya potensi *lost sales* (kekurangan kuota) pada tenongan unggulan. "
                    + (
                        f"Sebaliknya, risiko *overproduction* terkonsentrasi pada {nama_retur} yang perlu dikurangi kuota titipannya."
                        if nama_retur
                        else "Tingkat retur antar-tenongan secara umum masih terkendali."
                    )
                ),
            }
        )

    # 5. Insight Sensitivitas Harga Bahan Baku terhadap Margin
    if not df_bhn_sens.empty:
        bhn1 = df_bhn_sens.iloc[0]
        bhn2 = df_bhn_sens.iloc[1] if len(df_bhn_sens) > 1 else bhn1
        insights.append(
            {
                "judul": f"🥚 Sensitivitas Margin: Komponen {bhn1['Komponen Bahan']} & {bhn2['Komponen Bahan']}",
                "kategori": "info",
                "isi": (
                    f"Komponen biaya terbesar dalam 1 batch adalah **{bhn1['Komponen Bahan']}** "
                    f"({format_persen(bhn1['Porsi Biaya Produksi (%)'], 1)} dari HPP) dan **{bhn2['Komponen Bahan']}** "
                    f"({format_persen(bhn2['Porsi Biaya Produksi (%)'], 1)}). Kenaikan harga 10% pada {bhn1['Komponen Bahan']} "
                    f"akan menaikkan HPP sebesar Rp{format_angka(bhn1['Dampak +10% Harga pada HPP/Biji (Rp)'], 2)}/biji."
                ),
            }
        )

    return insights