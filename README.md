# AI Crypto Trading Office

Dashboard trading AI dengan karyawan robot beranimasi di kantor virtual 3D.

> **PAPER TRADING SAJA. Live trading dinonaktifkan** dan tidak bisa diaktifkan lewat konfigurasi
> (`TRADING_MODE` selain `paper` membuat aplikasi gagal start).

## Status jujur (apa yang benar-benar ada)

| Bagian | Status |
|---|---|
| Aturan top-10 holder (default 25%, bisa diatur) | Selesai, diuji |
| Pengecekan keamanan token (authority, extension, transfer fee) | Selesai, diuji |
| Simulasi jual gagal memblokir BUY | Selesai, diuji |
| Likuiditas Raydium (lock hanya LOCKED bila terverifikasi) | Selesai, diuji |
| Kebijakan developer pump.fun (<5 lolos, 5-10 review, >10 blokir) | Selesai, diuji |
| Keputusan terpadu PASS / REVIEW_REQUIRED / BLOCKED / UNKNOWN | Selesai, diuji |
| Mesin paper trading (fee, slippage, limit risiko, stop-loss, idempotensi, SQLite, audit berantai hash) | Selesai, diuji |
| Konfigurasi `.env` tervalidasi | Selesai, diuji |
| API backend (REST, tanpa framework; `uvicorn app.asgi:app`), auth API key, CORS eksplisit, jadwal siklus | Selesai, diuji (tes unit + uji server uvicorn nyata dengan data MOCK) |
| Aktivitas robot nyata (status robot + feed event dari backend) | Selesai, diuji |
| Provider live: Solana RPC (authority, ekstensi token, saldo developer), Raydium (TVL, harga), pump.fun (opsional, tidak resmi) | Ditulis, diuji **hanya dengan fixture**; belum pernah memanggil layanan asli (jaringan sandbox memblokirnya) |
| Sumber top-10 holder lengkap, simulasi jual (honeypot), verifikasi lock LP | **Belum ada** → pada mode live semua BUY berstatus UNKNOWN (diblokir) |
| GMGN | **Belum ada adapter.** API resmi ada (kunci dari gmgn.ai/ai) tetapi path/parameter HTTP-nya tidak terdokumentasi di sumber yang bisa diakses; lihat [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) |
| Frontend Next.js + kantor 3D 6 robot (`frontend/`) | Kode ditulis; logika murni diuji. **Belum di-build/dijalankan** (npm tidak terjangkau) |


Detail aturan: [docs/SECURITY_RULES.md](docs/SECURITY_RULES.md). Arsitektur: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Menjalankan tes

Tidak butuh paket tambahan (hanya Python 3.11+):

```
python3 -m unittest discover -s tests -t .
(cd frontend && npm test)     # logika murni frontend, Node 22+
```

Tes ditulis dengan `unittest` sehingga juga bisa dijalankan dengan `pytest`.

## Struktur

```
backend/app/            holder_analysis, security, liquidity_analysis, market_data, services, config
trading-engine/         trading_engine: orders, paper_broker, portfolio, risk, audit, storage
tests/                  tes otomatis
docs/                   dokumentasi
frontend/               Next.js + React Three Fiber (lihat frontend/README.md)
```

## Aturan keamanan

- Tidak ada private key wallet dan tidak ada kunci API di Git atau di frontend.
- Data MOCK tidak pernah dianggap data produksi: ditolak kecuali `ALLOW_MOCK_DATA=true`, dan selalu berlabel `MOCK`.
- Data yang hilang, basi, atau tidak lengkap menghasilkan `UNKNOWN` dan memblokir BUY.
- Hasil keamanan dihitung deterministik; teks AI tidak boleh mengubah keputusan.
