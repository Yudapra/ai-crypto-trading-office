# AI Crypto Trading Office

Dashboard trading AI dengan karyawan robot beranimasi di kantor virtual 3D.

> **Mode: PAPER TRADING saja.** Tidak ada transaksi uang asli sampai sistem diuji dan diizinkan secara eksplisit.

## Tim robot

| Robot | Tugas |
|---|---|
| CEO | Memantau seluruh sistem |
| Research | Mencari token (data GMGN bila akses tersedia) |
| Analyst | Menganalisis harga, volume, likuiditas, aktivitas pasar |
| Risk Manager | Menjalankan batas risiko dan stop-loss |
| Trader | Mengelola jual-beli simulasi |
| Accountant | Mencatat saldo, profit, dan loss |

## Struktur

```
frontend/        Antarmuka web ramah ponsel (kantor 3D + dashboard)
backend/         API dan penjadwal, jalan terus di cloud
trading-engine/  Logika agen, risiko, dan paper trading
docs/            Dokumentasi arsitektur
```

Ketiga modul dipisah: frontend hanya bicara dengan backend lewat API.

## Aturan keamanan

- Kunci API dan private key wallet tidak boleh ada di kode frontend atau di Git.
- Semua rahasia ada di environment variable backend (lihat `.env.example`).
- `TRADING_MODE` default `paper`. Mode live butuh persetujuan eksplisit.
