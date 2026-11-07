"""
app.py
Entry point utama aplikasi Taiyaki Business Manager & Data Science Dashboard.
- Pengunjung publik langsung masuk ke Mode Demo Portofolio (Data Sintetis) tanpa login.
- Pemilik usaha dapat login melalui panel Administrator untuk mengakses Mode Produksi.
"""

from pathlib import Path
import streamlit as st

from auth import is_admin_authenticated, render_login_panel_sidebar
from components import get_active_db_path, terapkan_tema_ui
from database import get_ringkasan_cepat_db, get_setting
from utils import format_angka, format_rupiah, format_tanggal_id

st.set_page_config(
    page_title="Taiyaki Business Manager & Data Science Portfolio",
    page_icon="🐟",
    layout="wide",
    initial_sidebar_state="expanded",
)


def render_sidebar_global(db_path: Path) -> None:
    """Menampilkan panel autentikasi, status lingkungan database, dan ringkasan cepat di sidebar."""
    with st.sidebar:
        st.markdown("### 🐟 Taiyaki Manager")
        nama_usaha = get_setting("nama_usaha", "Taiyaki Consignment & Distribution", db_path=db_path)
        st.caption(f"{nama_usaha}")
        st.divider()

        # Panel Login Administrator / Informasi Mode Demo Publik
        render_login_panel_sidebar()
        st.divider()

        # Ringkasan Cepat Status Database yang Sedang Aktif
        ringkasan = get_ringkasan_cepat_db(db_path)
        label_status = (
            "📌 Status Operasional (Produksi)"
            if (is_admin_authenticated() and st.session_state.get("db_mode") == "PRODUCTION")
            else "📌 Ringkasan Dataset Demo"
        )
        st.markdown(f"#### {label_status}")
        col_s1, col_s2 = st.columns(2)
        col_s1.metric("Tenongan Aktif", f"{ringkasan['tenongan_aktif']}/{ringkasan['tenongan_total']}")
        col_s2.metric("Hari Tercatat", f"{ringkasan['total_hari_aktif']} hari")

        col_s3, col_s4 = st.columns(2)
        col_s3.metric("Total Titip", f"{format_angka(ringkasan['total_titip'])} pcs")
        col_s4.metric("Total Laku", f"{format_angka(ringkasan['total_laku'])} pcs")

        if ringkasan["total_piutang"] > 0:
            st.warning(f"Piutang Setoran: **{format_rupiah(ringkasan['total_piutang'])}**")
        if ringkasan["jml_unconfirmed"] > 0:
            st.info(f"Belum Dikonfirmasi: **{ringkasan['jml_unconfirmed']} transaksi**")

        if ringkasan["tgl_awal"] and ringkasan["tgl_akhir"]:
            st.caption(
                f"Rentang Data: {format_tanggal_id(ringkasan['tgl_awal'], singkat=True)} – "
                f"{format_tanggal_id(ringkasan['tgl_akhir'], singkat=True)}"
            )
        else:
            st.caption("Database belum memiliki transaksi harian.")

        st.divider()
        versi = get_setting("versi_aplikasi", "2.0.0", db_path=db_path)
        st.caption(f"Versi Aplikasi: v{versi} | SQLite WAL Persistent")


def main() -> None:
    terapkan_tema_ui()
    db_path = get_active_db_path()
    render_sidebar_global(db_path)

    pages_dir = Path(__file__).resolve().parent / "pages"
    daftar_halaman = [
        ("1_Dashboard_Utama.py", "Dashboard Utama", "📊"),
        ("8_Analisis_Data_Science.py", "Analisis Data Science", "🧠"),
        ("0_Tentang_Proyek.py", "Tentang Proyek (About)", "📖"),
        ("2_Data_Tenongan.py", "Data Tenongan", "🏪"),
        ("3_Input_Harian.py", "Input Harian: Titip & Laku", "📝"),
        ("4_Kulakan.py", "Transaksi Kulakan", "📦"),
        ("5_Belanja_Bahan_Baku.py", "Belanja Bahan Baku", "🥚"),
        ("6_Kalkulasi_Modal_HPP.py", "Kalkulasi Modal & HPP", "🧮"),
        ("7_Laporan_Keuangan.py", "Laporan Keuangan", "💰"),
        ("9_Pengaturan_Data.py", "Pengaturan & Manajemen Data", "⚙️"),
    ]

    nav_pages = []
    for filename, title, icon in daftar_halaman:
        file_path = pages_dir / filename
        if file_path.exists():
            nav_pages.append(st.Page(str(file_path), title=title, icon=icon))

    if nav_pages and hasattr(st, "navigation"):
        try:
            pg = st.navigation(nav_pages)
            pg.run()
        except PermissionError as exc:
            st.error(f"🔒 {exc}")
    else:
        st.title("🐟 Taiyaki Business Manager & Data Science Portfolio")
        st.info("Silakan pilih halaman di sidebar kiri.")


if __name__ == "__main__":
    main()