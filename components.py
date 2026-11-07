"""
components.py
Komponen UI/UX reusable untuk aplikasi Streamlit Taiyaki Business Manager.
Menyediakan tema CSS minimalis profesional, KPI Cards berwarna semantik,
Alert Banners, pemilih rentang tanggal, guard cek_izin_tulis_ui, dan
auto-seeding dataset sintetis 6 bulan pada Mode Demo.
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Tuple

import streamlit as st

from auth import is_admin_authenticated
from database import DEMO_DB_PATH, PROD_DB_PATH, init_db
from synthetic_generator import pastikan_dataset_demo_tersedia
from utils import format_tanggal_id

WARNA_SEMANTIK = {
    "pendapatan": {"border": "#2563eb", "bg": "#eff6ff", "text": "#1e40af"},
    "keuntungan": {"border": "#16a34a", "bg": "#f0fdf4", "text": "#166534"},
    "pengeluaran": {"border": "#dc2626", "bg": "#fef2f2", "text": "#991b1b"},
    "piutang": {"border": "#d97706", "bg": "#fffbeb", "text": "#92400e"},
    "netral": {"border": "#475569", "bg": "#f8fafc", "text": "#1e293b"},
}


def get_active_db_path() -> Path:
    """
    Mengembalikan path database SQLite yang sedang aktif.
    - Jika pengguna belum login sebagai Administrator, paksa mode ke 'DEMO'
      dan pastikan dataset sintetis 6 bulan sudah tersedia di DEMO_DB_PATH.
    - Jika Administrator sudah login, ikuti pilihan mode ('PRODUCTION' atau 'DEMO').
    """
    admin_logged_in = is_admin_authenticated()

    if not admin_logged_in:
        st.session_state["db_mode"] = "DEMO"
        mode = "DEMO"
        target_path = DEMO_DB_PATH
    else:
        if "db_mode" not in st.session_state:
            st.session_state["db_mode"] = "PRODUCTION"
        mode = st.session_state["db_mode"]
        target_path = PROD_DB_PATH if mode == "PRODUCTION" else DEMO_DB_PATH

    init_key = f"db_initialized_{mode}"
    if not st.session_state.get(init_key, False) or not target_path.exists():
        init_db(target_path, seed_master=True, allow_system_init=True)
        st.session_state[init_key] = True

    # Pastikan database DEMO selalu langsung terisi dataset sintetis 6 bulan jika masih kosong
    if mode == "DEMO":
        pastikan_dataset_demo_tersedia(target_path, default_bulan=6, default_seed=42)

    return target_path


def cek_izin_tulis_ui(tampilkan_peringatan: bool = True) -> bool:
    """
    Memeriksa apakah sesi saat ini memiliki hak tulis (Administrator).
    Digunakan pada halaman form operasional untuk memberi tahu pengunjung publik
    bahwa aplikasi sedang berada dalam Mode Demo Portofolio (Read-Only).
    """
    if is_admin_authenticated():
        return True
    if tampilkan_peringatan:
        st.info(
            "🔒 **Mode Pratinjau Portofolio (Public Read-Only):** Anda dapat menjelajahi seluruh fitur, "
            "filter, dan simulasi, namun penyimpanan/penghapusan ke database dikunci untuk menjaga keutuhan "
            "data demo. Pemilik usaha dapat login melalui panel **🔐 Login Pemilik Usaha** di sidebar kiri."
        )
    return False


def terapkan_tema_ui() -> None:
    """Menyuntikkan CSS modern, bersih, dan responsif ke halaman Streamlit."""
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2.5rem;
            max-width: 1380px;
        }
        .kpi-card {
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 12px;
            border-left: 5px solid #2563eb;
            background-color: #f8fafc;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }
        .kpi-title {
            font-size: 0.82rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            color: #475569;
            margin-bottom: 4px;
        }
        .kpi-value {
            font-size: 1.45rem;
            font-weight: 700;
            color: #0f172a;
            line-height: 1.25;
        }
        .kpi-sub {
            font-size: 0.78rem;
            color: #64748b;
            margin-top: 4px;
        }
        .mode-badge-prod {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 700;
            background-color: #dcfce7;
            color: #166534;
            border: 1px solid #86efac;
        }
        .mode-badge-demo {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 700;
            background-color: #fef3c7;
            color: #92400e;
            border: 1px solid #fde68a;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def tampilkan_header_halaman(judul: str, subjudul: str = "", ikon: str = "📊") -> None:
    """Menampilkan header halaman yang konsisten beserta lencana mode database & status akses."""
    terapkan_tema_ui()
    admin_aktif = is_admin_authenticated()
    mode = st.session_state.get("db_mode", "DEMO") if admin_aktif else "DEMO"

    if mode == "PRODUCTION" and admin_aktif:
        badge_html = '<span class="mode-badge-prod">🟢 MODE PRODUKSI — DATA BISNIS ASLI (ADMIN)</span>'
    elif admin_aktif:
        badge_html = '<span class="mode-badge-demo">🛠️ DEMO PORTOFOLIO — DATA SINTETIS (MODE ADMIN)</span>'
    else:
        badge_html = '<span class="mode-badge-demo">🧪 DEMO PORTOFOLIO — DATA SINTETIS</span>'

    col_kiri, col_kanan = st.columns([3.3, 1.7])
    with col_kiri:
        st.markdown(f"## {ikon} {judul}")
        if subjudul:
            st.caption(subjudul)
    with col_kanan:
        st.markdown(
            f"<div style='text-align: right; padding-top: 12px;'>{badge_html}</div>",
            unsafe_allow_html=True,
        )
    st.divider()


def render_kpi_card(
    judul: str,
    nilai_utama: str,
    subteks: str = "",
    kategori_warna: str = "netral",
) -> None:
    """Menampilkan kartu KPI dengan skema warna semantik yang konsisten."""
    palet = WARNA_SEMANTIK.get(kategori_warna, WARNA_SEMANTIK["netral"])
    st.markdown(
        f"""
        <div class="kpi-card" style="border-left-color: {palet['border']}; background-color: {palet['bg']};">
            <div class="kpi-title">{judul}</div>
            <div class="kpi-value" style="color: {palet['text']};">{nilai_utama}</div>
            <div class="kpi-sub">{subteks}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pilih_rentang_tanggal(
    tgl_min_db: Optional[str],
    tgl_max_db: Optional[str],
    key_prefix: str = "global",
) -> Tuple[str, str]:
    """Komponen pemilih rentang tanggal interaktif dengan preset cepat."""
    hari_ini = date.today()
    try:
        awal_db = date.fromisoformat(tgl_min_db[:10]) if tgl_min_db else hari_ini.replace(day=1)
        akhir_db = date.fromisoformat(tgl_max_db[:10]) if tgl_max_db else hari_ini
    except Exception:
        awal_db = hari_ini.replace(day=1)
        akhir_db = hari_ini

    col_preset, col_mulai, col_akhir = st.columns([1.6, 1.2, 1.2])
    with col_preset:
        preset = st.selectbox(
            "Rentang Periode",
            options=[
                "Semua Data Historis",
                "Bulan Terakhir di Database",
                "7 Hari Terakhir (Database)",
                "Pilih Tanggal Manual",
            ],
            index=0,
            key=f"{key_prefix}_preset_tgl",
        )

    if preset == "Semua Data Historis":
        default_start, default_end = awal_db, akhir_db
    elif preset == "Bulan Terakhir di Database":
        default_start = akhir_db.replace(day=1)
        default_end = akhir_db
    elif preset == "7 Hari Terakhir (Database)":
        default_start = max(awal_db, akhir_db - timedelta(days=6))
        default_end = akhir_db
    else:
        default_start, default_end = awal_db, akhir_db

    with col_mulai:
        tgl_mulai = st.date_input(
            "Dari Tanggal",
            value=default_start,
            disabled=(preset != "Pilih Tanggal Manual"),
            key=f"{key_prefix}_tgl_mulai",
        )
    with col_akhir:
        tgl_selesai = st.date_input(
            "Sampai Tanggal",
            value=default_end,
            disabled=(preset != "Pilih Tanggal Manual"),
            key=f"{key_prefix}_tgl_selesai",
        )

    if tgl_mulai > tgl_selesai:
        st.warning("Tanggal mulai lebih besar dari tanggal akhir. Rentang disesuaikan otomatis.")
        tgl_mulai = tgl_selesai

    st.caption(
        f"Periode aktif: **{format_tanggal_id(tgl_mulai, sertakan_hari=True)}** s.d. "
        f"**{format_tanggal_id(tgl_selesai, sertakan_hari=True)}**"
    )
    return tgl_mulai.isoformat(), tgl_selesai.isoformat()