"""
forecasting.py
Mesin Time-Series Demand Forecasting & Sistem Rekomendasi Alokasi Kuota Distribusi.
Fitur utama:
1. Evaluasi Walk-Forward Expanding Window tanpa Data Leakage (SMA-3, SMA-6, EWMA,
   Ridge Regression, dan Random Forest Regressor) lengkap dengan metrik MAE, RMSE, dan MAPE.
2. Prediksi permintaan multi-hari ke depan secara autoregresif (memperhitungkan hari libur Minggu).
3. Rekomendasi alokasi kuota titipan per tenongan berdasarkan histori, tingkat retur,
   pola hari dalam minggu, dan kapasitas produksi target.
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error

from analytics import evaluasi_performa_dan_tren_tenongan, get_dataset_eda_harian
from database import query_df
from utils import NAMA_HARI_ID


def _bangun_vektor_fitur(history_y: np.ndarray, dt_target: pd.Timestamp, idx_urut: int) -> List[float]:
    """
    Membangun fitur time-series murni dari data masa lalu (history_y) sebelum dt_target
    untuk menjamin 0% data leakage saat training maupun inferensi.
    """
    lag_1 = float(history_y[-1])
    lag_2 = float(history_y[-2]) if len(history_y) >= 2 else lag_1
    lag_3 = float(history_y[-3]) if len(history_y) >= 3 else lag_2
    lag_6 = float(history_y[-6]) if len(history_y) >= 6 else float(np.mean(history_y))
    roll_mean_3 = float(np.mean(history_y[-3:]))
    roll_mean_6 = float(np.mean(history_y[-6:]))
    roll_std_6 = float(np.std(history_y[-6:])) if len(history_y) >= 3 else 0.0
    dow = int(dt_target.weekday())
    is_akhir_pekan = 1.0 if dow in (4, 5) else 0.0
    return [
        lag_1,
        lag_2,
        lag_3,
        lag_6,
        roll_mean_3,
        roll_mean_6,
        roll_std_6,
        float(dow),
        is_akhir_pekan,
        float(idx_urut),
    ]


def jalankan_evaluasi_dan_forecasting(
    horizon_hari: int = 7,
    rasio_test: float = 0.25,
    margin_cadangan_persen: float = 12.0,
    tgl_mulai: Optional[str] = None,
    tgl_selesai: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Menjalankan evaluasi Walk-Forward Backtesting pada data historis dan menghasilkan
    prediksi permintaan untuk `horizon_hari` ke depan.
    Jika histori < 10 hari aktif, hanya menjalankan model Baseline Moving Average.
    """
    df_daily = get_dataset_eda_harian(tgl_mulai, tgl_selesai, hanya_terkonfirmasi=True, db_path=db_path)
    if df_daily.empty:
        return {"status": "DATA_KOSONG", "pesan": "Belum ada transaksi terkonfirmasi untuk diprediksi."}

    # Gunakan hari produksi reguler (total_titip > 0) agar hari penarikan sisa tanpa penitipan tidak mendistorsi model
    df_ts = df_daily[df_daily["total_titip"] > 0].copy().reset_index(drop=True)
    n_obs = len(df_ts)

    if n_obs < 7:
        return {
            "status": "DATA_KURANG",
            "n_observasi": n_obs,
            "pesan": (
                f"Saat ini baru terdapat {n_obs} hari produksi terkonfirmasi. "
                "Dibutuhkan minimal 7 hari aktif untuk evaluasi Moving Average dan minimal 14 hari untuk model Machine Learning."
            ),
        }

    y_vals = df_ts["total_laku"].values.astype(float)
    dt_vals = pd.to_datetime(df_ts["tanggal"]).tolist()

    # Tentukan ukuran jendela uji (test set) secara kronologis di bagian akhir deret waktu
    n_test = max(3, min(14, int(round(n_obs * rasio_test))))
    if n_obs - n_test < 6:
        n_test = max(2, n_obs - 6)
    n_train_awal = n_obs - n_test

    # Siapkan matriks fitur untuk model ML bila n_obs >= 14
    gunakan_ml = n_obs >= 14
    X_all, y_all, idx_all = [], [], []
    if gunakan_ml:
        for i in range(6, n_obs):
            X_all.append(_bangun_vektor_fitur(y_vals[:i], dt_vals[i], i))
            y_all.append(y_vals[i])
            idx_all.append(i)
        X_all_arr = np.array(X_all)
        y_all_arr = np.array(y_all)

    # Walk-Forward Expanding Window Backtesting pada indeks [n_train_awal ... n_obs-1]
    catatan_backtest = []
    for t_idx in range(n_train_awal, n_obs):
        hist_y = y_vals[:t_idx]
        aktual = float(y_vals[t_idx])
        tgl_uji = str(df_ts["tanggal"].iloc[t_idx])

        pred_sma3 = float(np.mean(hist_y[-3:]))
        pred_sma6 = float(np.mean(hist_y[-6:]))
        pred_ewma = float(pd.Series(hist_y).ewm(alpha=0.35, adjust=False).mean().iloc[-1])

        baris_bt = {
            "tanggal": tgl_uji,
            "Aktual Laku (pcs)": aktual,
            "SMA-3": round(pred_sma3, 2),
            "SMA-6": round(pred_sma6, 2),
            "EWMA": round(pred_ewma, 2),
        }

        if gunakan_ml:
            mask_tr = np.array(idx_all) < t_idx
            X_tr = X_all_arr[mask_tr]
            y_tr = y_all_arr[mask_tr]
            x_te = np.array(_bangun_vektor_fitur(hist_y, dt_vals[t_idx], t_idx)).reshape(1, -1)

            m_ridge = Ridge(alpha=10.0)
            m_ridge.fit(X_tr, y_tr)
            baris_bt["Ridge Regression"] = round(float(m_ridge.predict(x_te)[0]), 2)

            m_rf = RandomForestRegressor(
                n_estimators=100, max_depth=4, min_samples_leaf=2, random_state=42
            )
            m_rf.fit(X_tr, y_tr)
            baris_bt["Random Forest"] = round(float(m_rf.predict(x_te)[0]), 2)

        catatan_backtest.append(baris_bt)

    df_backtest = pd.DataFrame(catatan_backtest)
    aktual_arr = df_backtest["Aktual Laku (pcs)"].values

    daftar_kandidat_model = ["SMA-3", "SMA-6", "EWMA"] + (
        ["Ridge Regression", "Random Forest"] if gunakan_ml else []
    )
    label_lengkap_model = {
        "SMA-3": "Moving Average 3 Hari (SMA-3)",
        "SMA-6": "Moving Average 6 Hari (SMA-6)",
        "EWMA": "Exponential Smoothing (EWMA α=0,35)",
        "Ridge Regression": "Ridge Regression (Fitur Lag + Kalender)",
        "Random Forest": "Random Forest Regressor (Ensemble)",
    }

    tabel_metrik = []
    for kode_m in daftar_kandidat_model:
        pred_arr = df_backtest[kode_m].values
        mae = float(mean_absolute_error(aktual_arr, pred_arr))
        rmse = float(np.sqrt(mean_squared_error(aktual_arr, pred_arr)))
        mape = float(np.mean(np.abs((aktual_arr - pred_arr) / np.maximum(1.0, aktual_arr))) * 100.0)
        tabel_metrik.append(
            {
                "kode_model": kode_m,
                "Nama Model": label_lengkap_model[kode_m],
                "MAE (pcs)": round(mae, 2),
                "RMSE (pcs)": round(rmse, 2),
                "MAPE (%)": round(mape, 2),
            }
        )

    df_metrik = pd.DataFrame(tabel_metrik).sort_values("MAE (pcs)", ascending=True).reset_index(drop=True)
    model_terbaik_kode = str(df_metrik.iloc[0]["kode_model"])
    model_terbaik_nama = str(df_metrik.iloc[0]["Nama Model"])
    mae_terbaik = float(df_metrik.iloc[0]["MAE (pcs)"])

    # Latih model ML penuh pada seluruh data historis untuk prediksi masa depan
    if gunakan_ml:
        ridge_full = Ridge(alpha=10.0).fit(X_all_arr, y_all_arr)
        rf_full = RandomForestRegressor(
            n_estimators=100, max_depth=4, min_samples_leaf=2, random_state=42
        ).fit(X_all_arr, y_all_arr)

    # Prediksi Autoregresif untuk `horizon_hari` ke depan
    tgl_terakhir = dt_vals[-1].date()
    baris_prediksi_depan = []
    hist_sim = {m: list(y_vals) for m in daftar_kandidat_model}
    idx_lanjut = n_obs

    for step in range(1, horizon_hari + 1):
        tgl_depan = tgl_terakhir + timedelta(days=step)
        dow_depan = tgl_depan.weekday()
        nama_hari_depan = NAMA_HARI_ID[dow_depan]

        # Hari Minggu adalah hari libur operasional rutin berdasarkan pola historis
        if dow_depan == 6:
            baris_prediksi_depan.append(
                {
                    "Tanggal": tgl_depan.isoformat(),
                    "Hari": nama_hari_depan,
                    "Status Operasional": "Libur Rutin Minggu",
                    "SMA-3": 0,
                    "SMA-6": 0,
                    "EWMA": 0,
                    "Ridge Regression": 0 if gunakan_ml else np.nan,
                    "Random Forest": 0 if gunakan_ml else np.nan,
                    "Prediksi Model Terbaik (pcs)": 0,
                    "Batas Bawah (pcs)": 0,
                    "Batas Atas (pcs)": 0,
                    "Saran Kuota Produksi/Titip (pcs)": 0,
                }
            )
            continue

        dt_ts = pd.Timestamp(tgl_depan)
        p_sma3 = float(np.mean(hist_sim["SMA-3"][-3:]))
        p_sma6 = float(np.mean(hist_sim["SMA-6"][-6:]))
        p_ewma = float(pd.Series(hist_sim["EWMA"]).ewm(alpha=0.35, adjust=False).mean().iloc[-1])

        hist_sim["SMA-3"].append(p_sma3)
        hist_sim["SMA-6"].append(p_sma6)
        hist_sim["EWMA"].append(p_ewma)

        hasil_step = {
            "SMA-3": p_sma3,
            "SMA-6": p_sma6,
            "EWMA": p_ewma,
        }

        if gunakan_ml:
            x_r = np.array(_bangun_vektor_fitur(np.array(hist_sim["Ridge Regression"]), dt_ts, idx_lanjut)).reshape(1, -1)
            p_ridge = max(0.0, float(ridge_full.predict(x_r)[0]))
            hist_sim["Ridge Regression"].append(p_ridge)
            hasil_step["Ridge Regression"] = p_ridge

            x_rf = np.array(_bangun_vektor_fitur(np.array(hist_sim["Random Forest"]), dt_ts, idx_lanjut)).reshape(1, -1)
            p_rf = max(0.0, float(rf_full.predict(x_rf)[0]))
            hist_sim["Random Forest"].append(p_rf)
            hasil_step["Random Forest"] = p_rf

        idx_lanjut += 1
        pred_utama = int(round(hasil_step[model_terbaik_kode]))
        batas_bawah = max(0, int(round(pred_utama - mae_terbaik)))
        batas_atas = int(round(pred_utama + mae_terbaik))
        saran_produksi = int(np.ceil(pred_utama * (1.0 + margin_cadangan_persen / 100.0)))

        baris_prediksi_depan.append(
            {
                "Tanggal": tgl_depan.isoformat(),
                "Hari": nama_hari_depan,
                "Status Operasional": "Operasional Aktif",
                "SMA-3": int(round(p_sma3)),
                "SMA-6": int(round(p_sma6)),
                "EWMA": int(round(p_ewma)),
                "Ridge Regression": int(round(hasil_step["Ridge Regression"])) if gunakan_ml else np.nan,
                "Random Forest": int(round(hasil_step["Random Forest"])) if gunakan_ml else np.nan,
                "Prediksi Model Terbaik (pcs)": pred_utama,
                "Batas Bawah (pcs)": batas_bawah,
                "Batas Atas (pcs)": batas_atas,
                "Saran Kuota Produksi/Titip (pcs)": saran_produksi,
            }
        )

    return {
        "status": "OK",
        "n_observasi": n_obs,
        "n_train": n_train_awal,
        "n_test": n_test,
        "gunakan_ml": gunakan_ml,
        "model_terbaik_kode": model_terbaik_kode,
        "model_terbaik_nama": model_terbaik_nama,
        "mae_terbaik": mae_terbaik,
        "df_metrik": df_metrik,
        "df_backtest": df_backtest,
        "df_forecast": pd.DataFrame(baris_prediksi_depan),
        "df_historis": df_ts,
    }


def hitung_rekomendasi_alokasi_tenongan(
    target_tanggal: date,
    kapasitas_produksi_maks: int = 80,
    min_hari_histori: int = 5,
    db_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Menghasilkan rekomendasi jumlah titipan (pcs) untuk setiap tenongan aktif:
    - Mempertimbangkan pola penjualan historis, tingkat retur (return rate),
      frekuensi habis terjual (sold-out rate), pola kunjungan (HARIAN vs SELANG_SELING),
      dan batas kapasitas produksi harian.
    - Menandai tenongan yang historinya masih terlalu sedikit (< min_hari_histori).
    """
    df_perf = evaluasi_performa_dan_tren_tenongan(min_hari_valid=min_hari_histori, db_path=db_path)
    df_tng = query_df(
        "SELECT id_tenongan, kode_tenongan, nama_tenongan, pola_titip, kuota_default, status_aktif FROM tenongan WHERE status_aktif = 1 ORDER BY COALESCE(no_urut, 9999) ASC;",
        db_path=db_path,
    )
    if df_tng.empty:
        return pd.DataFrame()

    peta_perf = {}
    if not df_perf.empty:
        for _, r in df_perf.iterrows():
            peta_perf[int(r["id_tenongan"])] = r

    rekomendasi_list = []
    for _, tng in df_tng.iterrows():
        id_t = int(tng["id_tenongan"])
        kuota_def = int(tng["kuota_default"])
        pola = str(tng["pola_titip"])

        if id_t not in peta_perf:
            rekomendasi_list.append(
                {
                    "id_tenongan": id_t,
                    "Kode": str(tng["kode_tenongan"]),
                    "Nama Tenongan": str(tng["nama_tenongan"]),
                    "Pola Titip": pola,
                    "Hari Histori Valid": 0,
                    "Rata-rata Laku (pcs)": 0.0,
                    "Sell-Through (%)": 0.0,
                    "Return Rate (%)": 0.0,
                    "Tren": "BELUM_ADA_DATA",
                    "Kuota Saat Ini (pcs)": kuota_def,
                    "Saran Kuota Baru (pcs)": kuota_def,
                    "Perubahan (pcs)": 0,
                    "Skor Prioritas": 30.0,
                    "Alasan Rekomendasi": "Belum ada histori transaksi terkonfirmasi; gunakan kuota awal dengan pemantauan.",
                }
            )
            continue

        p = peta_perf[id_t]
        n_hari = int(p["hari_valid"])
        avg_laku = float(p["rata2_laku_harian"])
        str_pct = float(p["sell_through_rate"])
        rr_pct = float(p["return_rate"])
        sold_out = float(p["sold_out_rate"])
        tren = str(p["status_tren"])
        kuota_hist = max(1, int(round(float(p["rata2_titip_harian"]))))

        if n_hari < min_hari_histori:
            saran_kuota = kuota_def
            alasan = f"Histori baru {n_hari} hari (< {min_hari_histori} hari minimum); pertahankan kuota {kuota_def} pcs."
        elif str_pct >= 90.0 and sold_out >= 65.0:
            saran_kuota = kuota_hist + 1
            alasan = f"Sering habis terjual ({sold_out:.0f}% hari, STR {str_pct:.1f}%); disarankan tambah +1 pcs untuk cegah lost sales."
        elif rr_pct >= 30.0 and avg_laku <= kuota_hist - 0.8:
            saran_kuota = max(2, int(np.ceil(avg_laku)))
            alasan = f"Return rate tinggi ({rr_pct:.1f}%, rata-rata laku {avg_laku:.1f} pcs); kurangi titipan untuk tekan kerugian retur."
        else:
            saran_kuota = kuota_hist
            alasan = f"Performa optimal & seimbang (STR {str_pct:.1f}%, rata-rata laku {avg_laku:.1f} pcs); pertahankan {saran_kuota} pcs."

        skor_prioritas = round((str_pct * 0.6) + (avg_laku * 10.0) - (rr_pct * 0.4), 2)
        rekomendasi_list.append(
            {
                "id_tenongan": id_t,
                "Kode": str(tng["kode_tenongan"]),
                "Nama Tenongan": str(tng["nama_tenongan"]),
                "Pola Titip": pola,
                "Hari Histori Valid": n_hari,
                "Rata-rata Laku (pcs)": avg_laku,
                "Sell-Through (%)": str_pct,
                "Return Rate (%)": rr_pct,
                "Tren": tren,
                "Kuota Saat Ini (pcs)": kuota_hist,
                "Saran Kuota Baru (pcs)": int(saran_kuota),
                "Perubahan (pcs)": int(saran_kuota) - int(kuota_hist),
                "Skor Prioritas": skor_prioritas,
                "Alasan Rekomendasi": alasan,
            }
        )

    df_rek = pd.DataFrame(rekomendasi_list).sort_values("Skor Prioritas", ascending=False).reset_index(drop=True)

    # Jika total saran kuota melebihi kapasitas_produksi_maks, sesuaikan proporsional dari skor terendah
    total_saran = int(df_rek["Saran Kuota Baru (pcs)"].sum())
    if kapasitas_produksi_maks > 0 and total_saran > kapasitas_produksi_maks:
        kelebihan = total_saran - kapasitas_produksi_maks
        for idx in reversed(df_rek.index.tolist()):
            if kelebihan <= 0:
                break
            curr_q = int(df_rek.loc[idx, "Saran Kuota Baru (pcs)"])
            pengurangan = min(curr_q, kelebihan)
            df_rek.loc[idx, "Saran Kuota Baru (pcs)"] = curr_q - pengurangan
            df_rek.loc[idx, "Perubahan (pcs)"] = int(df_rek.loc[idx, "Saran Kuota Baru (pcs)"]) - int(
                df_rek.loc[idx, "Kuota Saat Ini (pcs)"]
            )
            df_rek.loc[idx, "Alasan Rekomendasi"] += " (Disesuaikan batas kapasitas produksi harian)."
            kelebihan -= pengurangan

    return df_rek