# Aturan keamanan (diimplementasikan dan diuji)

Semua nilai ambang adalah **kebijakan yang bisa diatur**, bukan indikator universal penipuan.

## 1. Top-10 holder (wajib sebelum setiap BUY)

- Default maksimum: **25% dari total supply**. Tepat 25% lolos; di atas 25% diblokir.
- Data hilang, basi, tidak lengkap, tidak konsisten, atau berlabel MOCK (bila tidak diizinkan): **UNKNOWN**, BUY diblokir.
- Metodologi (lihat `backend/app/holder_analysis/concentration.py`):
  1. Akun token diduplikasi berdasarkan alamat; data bertentangan membuat hasil UNKNOWN.
  2. Saldo digabung per **pemilik**, jadi satu pemilik dengan banyak akun tidak bisa menyembunyikan saldo.
  3. **Raw** = 10 pemilik terbesar tanpa pengecualian. **Adjusted** = tanpa akun milik protokol
     (pool vault, bonding curve, burn, treasury) **hanya jika ada bukti tertulis**; setiap pengecualian dilaporkan beserta alasannya.
  4. Keduanya selalu dilaporkan. Aturan memakai `CONCENTRATION_BASIS` (default `raw`, paling konservatif).
  5. Perbandingan memakai aritmetika pecahan eksak (tanpa kesalahan pembulatan).
- Analisis dompet terkait: hanya bukti langsung (shared funder, transfer langsung) yang membentuk klaster;
  hubungan dugaan dilaporkan terpisah. Ini tidak menggantikan perhitungan raw top-10.

## 2. Anti-honeypot (statis + simulasi jual)

- Mint/freeze authority aktif, extension berbahaya (permanent delegate, transfer hook, non-transferable, pausable,
  default frozen), dan transfer fee di atas batas: **BLOCKED**.
- Simulasi jual gagal: **BLOCKED**. Simulasi tidak tersedia: **UNKNOWN** (bukan SAFE).
- Batasan: pengecekan statis tidak bisa membuktikan token bukan honeypot; setiap temuan mencantumkan batasannya.

## 3. Likuiditas

- Tipe pool tidak dikenal: UNKNOWN (tidak diasumsikan punya LP token).
- Status `LOCKED` hanya bila terverifikasi dengan metode **dan** bukti. Klaim tanpa verifikasi = `UNVERIFIED`.
- Likuiditas di bawah minimum, price impact tinggi, atau penarikan likuiditas mendadak: BLOCKED.

## 4. pump.fun

- Holding developer: < 5% lanjut, 5% sampai 10% (inklusif) REVIEW_REQUIRED, > 10% BLOCKED, tidak diketahui BLOCKED/UNKNOWN.
- Token yang masih di bonding curve butuh review manual (likuiditas bonding curve belum dianalisis mesin ini).

## 5. Keputusan terpadu

`BLOCKED` jika ada kegagalan kritis, `UNKNOWN` jika bukti kritis hilang, `REVIEW_REQUIRED` jika ada peringatan,
`PASS` hanya jika semua pengecekan wajib lolos. BUY hanya diizinkan pada `PASS`.
Bukti diambil ulang dan dievaluasi ulang **setiap** BUY. SELL tidak diblokir oleh keamanan.

## 6. Batasan yang diketahui

- Belum ada provider data nyata; semua demo memakai data MOCK berlabel.
- Pembatasan hanya sebaik data yang diberikan provider (terutama `complete` pada daftar holder).
- Kekuatan tes sudah diperiksa dengan uji mutasi manual (merusak aturan 25% dan gerbang keamanan membuat tes gagal).
