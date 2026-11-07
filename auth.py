"""
auth.py
Modul autentikasi Administrator, manajemen sesi aman (session timeout & rate-limiting),
hashing password kuat (mendukung bcrypt dan memory-hard hashlib.scrypt),
serta utilitas CLI untuk membuat hash password tanpa menyimpan plaintext di kode.
"""

import getpass
import hashlib
import hmac
import os
import secrets
import sys
import time
from typing import Dict, Optional, Tuple

try:
    import bcrypt  # type: ignore
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False

try:
    import streamlit as st
except ImportError:
    st = None  # type: ignore

DEFAULT_SESSION_TIMEOUT_MINUTES = 60
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 300  # 5 menit

_FAILED_ATTEMPTS_STORE: Dict[str, Dict[str, float]] = {}


def hash_password(password: str, prefer_bcrypt: bool = True) -> str:
    """
    Menghasilkan hash password kriptografis yang kuat.
    - Jika library `bcrypt` terinstal dan `prefer_bcrypt=True`, menghasilkan format `$2b$12$...`
    - Jika tidak, menggunakan memory-hard KDF `hashlib.scrypt` (RFC 7914) bawaan Python
      dengan format `scrypt$n$r$p$salt_hex$hash_hex`.
    """
    if not password or len(password) < 8:
        raise ValueError("Password minimal harus terdiri dari 8 karakter.")

    if prefer_bcrypt and HAS_BCRYPT:
        garam = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password.encode("utf-8"), garam)
        return hashed.decode("utf-8")

    n_param, r_param, p_param = 16384, 8, 1
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n_param,
        r=r_param,
        p=p_param,
        dklen=64,
    )
    return f"scrypt${n_param}${r_param}${p_param}${salt.hex()}${derived.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """
    Memverifikasi password terhadap hash yang tersimpan secara constant-time
    untuk mencegah timing attack. Mendukung format `bcrypt` ($2b$/$2a$) dan `scrypt$`.
    """
    if not plain_password or not stored_hash:
        return False

    hash_bersih = stored_hash.strip()

    try:
        if hash_bersih.startswith(("$2b$", "$2a$", "$2y$")):
            if not HAS_BCRYPT:
                return False
            return bool(
                bcrypt.checkpw(
                    plain_password.encode("utf-8"),
                    hash_bersih.encode("utf-8"),
                )
            )

        if hash_bersih.startswith("scrypt$"):
            parts = hash_bersih.split("$")
            if len(parts) != 6:
                return False
            _, n_str, r_str, p_str, salt_hex, expected_hex = parts
            salt = bytes.fromhex(salt_hex)
            expected_bytes = bytes.fromhex(expected_hex)
            computed = hashlib.scrypt(
                plain_password.encode("utf-8"),
                salt=salt,
                n=int(n_str),
                r=int(r_str),
                p=int(p_str),
                dklen=len(expected_bytes),
            )
            return hmac.compare_digest(computed, expected_bytes)
    except Exception:
        return False

    return False


def _get_admin_credentials_config() -> Tuple[Optional[str], Optional[str], int]:
    """
    Membaca kredensial admin dari Environment Variables atau Streamlit Secrets.
    Tidak menyimpan password atau hash di dalam kode sumber.
    """
    username = os.environ.get("TAIYAKI_ADMIN_USERNAME", "").strip()
    password_hash = os.environ.get("TAIYAKI_ADMIN_PASSWORD_HASH", "").strip()
    timeout_str = os.environ.get(
        "TAIYAKI_SESSION_TIMEOUT_MINUTES", str(DEFAULT_SESSION_TIMEOUT_MINUTES)
    ).strip()

    if st is not None and (not username or not password_hash):
        try:
            if "auth" in st.secrets:
                auth_sec = st.secrets["auth"]
                username = username or str(auth_sec.get("admin_username", "")).strip()
                password_hash = password_hash or str(auth_sec.get("admin_password_hash", "")).strip()
                if "session_timeout_minutes" in auth_sec:
                    timeout_str = str(auth_sec["session_timeout_minutes"])
        except Exception:
            pass

    try:
        timeout_menit = max(5, int(timeout_str))
    except ValueError:
        timeout_menit = DEFAULT_SESSION_TIMEOUT_MINUTES

    return (username or None), (password_hash or None), timeout_menit


def is_auth_configured() -> bool:
    """Memeriksa apakah kredensial admin sudah dikonfigurasi di server."""
    user, pwd_hash, _ = _get_admin_credentials_config()
    return bool(user and pwd_hash)


def _get_session_bucket_key() -> str:
    """Mendapatkan kunci identifikasi sesi untuk rate-limiting percobaan login."""
    if st is not None and hasattr(st, "session_state"):
        if "_client_rate_key" not in st.session_state:
            st.session_state["_client_rate_key"] = secrets.token_hex(8)
        return str(st.session_state["_client_rate_key"])
    return "global_cli_session"


def cek_status_lockout(bucket_key: Optional[str] = None) -> Tuple[bool, int]:
    """
    Memeriksa apakah sesi saat ini sedang dikunci sementara akibat gagal login berulang.
    Mengembalikan tuple (is_locked, sisa_detik_lockout).
    """
    key = bucket_key or _get_session_bucket_key()
    rec = _FAILED_ATTEMPTS_STORE.get(key)
    if not rec:
        return False, 0

    lock_until = rec.get("lock_until", 0.0)
    now = time.time()
    if lock_until > now:
        return True, int(round(lock_until - now))

    if lock_until > 0 and now >= lock_until:
        _FAILED_ATTEMPTS_STORE.pop(key, None)
    return False, 0


def catat_percobaan_login_gagal(bucket_key: Optional[str] = None) -> int:
    """Mencatat percobaan login gagal dan mengaktifkan lockout jika mencapai batas."""
    key = bucket_key or _get_session_bucket_key()
    now = time.time()
    rec = _FAILED_ATTEMPTS_STORE.get(key, {"count": 0.0, "last_fail": 0.0, "lock_until": 0.0})

    if now - rec.get("last_fail", 0.0) > 900:
        rec["count"] = 0.0

    rec["count"] = float(rec.get("count", 0.0)) + 1.0
    rec["last_fail"] = now

    if rec["count"] >= MAX_LOGIN_ATTEMPTS:
        rec["lock_until"] = now + LOCKOUT_DURATION_SECONDS

    _FAILED_ATTEMPTS_STORE[key] = rec
    return max(0, int(MAX_LOGIN_ATTEMPTS - rec["count"]))


def reset_percobaan_login(bucket_key: Optional[str] = None) -> None:
    """Menghapus catatan kegagalan login setelah berhasil autentikasi."""
    key = bucket_key or _get_session_bucket_key()
    _FAILED_ATTEMPTS_STORE.pop(key, None)


def authenticate_admin(
    input_username: str,
    input_password: str,
    bucket_key: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Memvalidasi username dan password administrator di sisi server.
    Pesan error dibuat generik agar tidak membocorkan informasi sensitif.
    """
    terkunci, sisa_detik = cek_status_lockout(bucket_key)
    if terkunci:
        return (
            False,
            f"Terlalu banyak percobaan login yang gagal. Silakan tunggu {sisa_detik} detik sebelum mencoba kembali.",
        )

    cfg_user, cfg_hash, timeout_menit = _get_admin_credentials_config()
    if not cfg_user or not cfg_hash:
        return (
            False,
            "Autentikasi Administrator belum dikonfigurasi di server (Environment Variable belum diatur).",
        )

    user_bersih = (input_username or "").strip()
    user_cocok = hmac.compare_digest(user_bersih.encode("utf-8"), cfg_user.encode("utf-8"))
    pass_cocok = verify_password(input_password or "", cfg_hash)

    if user_cocok and pass_cocok:
        reset_percobaan_login(bucket_key)
        now = time.time()
        if st is not None and hasattr(st, "session_state"):
            st.session_state["auth_is_admin"] = True
            st.session_state["auth_username"] = cfg_user
            st.session_state["auth_login_at"] = now
            st.session_state["auth_last_active"] = now
            st.session_state["auth_token"] = secrets.token_urlsafe(32)
            st.session_state["auth_timeout_minutes"] = timeout_menit
        return True, "Autentikasi berhasil. Selamat datang kembali, Administrator."

    sisa = catat_percobaan_login_gagal(bucket_key)
    if sisa == 0:
        return (
            False,
            f"Kredensial tidak valid. Akun dikunci sementara selama {LOCKOUT_DURATION_SECONDS // 60} menit demi keamanan.",
        )
    return (
        False,
        f"Username atau password tidak valid. Sisa percobaan sebelum penguncian sementara: {sisa} kali.",
    )


def is_admin_authenticated() -> bool:
    """
    Memeriksa apakah sesi saat ini terautentikasi sebagai Administrator aktif
    dan belum melewati batas waktu kedaluwarsa sesi (session timeout).
    """
    if st is None or not hasattr(st, "session_state"):
        return False

    if not st.session_state.get("auth_is_admin", False):
        return False
    if not st.session_state.get("auth_token"):
        return False

    now = time.time()
    last_active = float(st.session_state.get("auth_last_active", 0.0))
    timeout_menit = int(st.session_state.get("auth_timeout_minutes", DEFAULT_SESSION_TIMEOUT_MINUTES))

    if last_active <= 0 or (now - last_active) > (timeout_menit * 60):
        logout_admin(alasan_timeout=True)
        return False

    st.session_state["auth_last_active"] = now
    return True


def logout_admin(alasan_timeout: bool = False) -> None:
    """
    Menghapus seluruh status autentikasi dari session_state dan mengembalikan
    lingkungan aplikasi ke Mode Demo Publik secara aman.
    """
    if st is None or not hasattr(st, "session_state"):
        return

    for k in [
        "auth_is_admin",
        "auth_username",
        "auth_login_at",
        "auth_last_active",
        "auth_token",
    ]:
        st.session_state.pop(k, None)

    st.session_state["db_mode"] = "DEMO"
    if alasan_timeout:
        st.session_state["auth_timeout_notice"] = (
            "Sesi Administrator Anda telah berakhir karena tidak ada aktivitas. Silakan login kembali."
        )


def require_admin_access() -> None:
    """
    Guard level fungsi/database: melempar PermissionError jika fungsi yang mengakses
    data produksi dipanggil tanpa autentikasi Administrator yang sah.
    """
    if not is_admin_authenticated():
        raise PermissionError(
            "Akses ditolak: Operasi atau database produksi hanya dapat diakses oleh Administrator terautentikasi."
        )


def render_login_panel_sidebar() -> None:
    """
    Menampilkan panel kontrol autentikasi Administrator dan pemilih lingkungan database
    di dalam sidebar Streamlit.
    """
    if st is None:
        return

    if st.session_state.get("auth_timeout_notice"):
        st.sidebar.warning(st.session_state.pop("auth_timeout_notice"))

    if is_admin_authenticated():
        admin_user = st.session_state.get("auth_username", "Admin")
        st.sidebar.success(f"🔐 **Admin Aktif:** `{admin_user}`")

        mode_sekarang = st.session_state.get("db_mode", "PRODUCTION")
        mode_pilihan = st.sidebar.radio(
            "Lingkungan Database (Khusus Admin)",
            options=["PRODUCTION", "DEMO"],
            format_func=lambda x: (
                "🟢 Produksi (Data Bisnis Asli)"
                if x == "PRODUCTION"
                else "🧪 Demo Portofolio (Kelola Sintetis)"
            ),
            index=0 if mode_sekarang == "PRODUCTION" else 1,
            help="Sebagai Administrator, Anda dapat beralih antara data produksi asli dan pengelolaan dataset demo.",
        )
        if mode_pilihan != mode_sekarang:
            st.session_state["db_mode"] = mode_pilihan
            st.rerun()

        if st.sidebar.button("🚪 Logout Administrator", use_container_width=True, type="secondary"):
            logout_admin(alasan_timeout=False)
            st.rerun()
    else:
        st.session_state["db_mode"] = "DEMO"
        st.sidebar.info(
            "🌐 **Mode Publik:** `DEMO PORTOFOLIO`\n\n"
            "Menampilkan data sintetis untuk demonstrasi Data Science & BI."
        )
        with st.sidebar.expander("🔐 Login Pemilik Usaha (Admin)", expanded=False):
            st.caption("Khusus pemilik usaha untuk mengakses Mode Produksi.")
            with st.form("form_login_admin_sidebar", clear_on_submit=False):
                u_in = st.text_input("Username", placeholder="Masukkan username admin")
                p_in = st.text_input("Password", type="password", placeholder="Masukkan password")
                submit_login = st.form_submit_button(
                    "Masuk ke Mode Produksi", type="primary", use_container_width=True
                )

                if submit_login:
                    ok, pesan = authenticate_admin(u_in, p_in)
                    if ok:
                        st.session_state["db_mode"] = "PRODUCTION"
                        st.success(pesan)
                        st.rerun()
                    else:
                        st.error(pesan)


if __name__ == "__main__":
    print("=== Taiyaki Business Manager - Generator Hash Password Admin ===")
    if len(sys.argv) > 1 and sys.argv[1] != "--generate-hash":
        pwd = sys.argv[1]
    else:
        pwd = getpass.getpass("Masukkan password baru admin (min. 8 karakter): ")
    try:
        hasil_hash = hash_password(pwd)
        print("\nHash Password Anda (simpan di file .env sebagai TAIYAKI_ADMIN_PASSWORD_HASH):")
        print(hasil_hash)
    except Exception as exc:
        print(f"Gagal membuat hash: {exc}", file=sys.stderr)
        sys.exit(1)