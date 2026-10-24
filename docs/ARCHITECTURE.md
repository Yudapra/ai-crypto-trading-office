# Arsitektur

```
[Frontend] --HTTP/WebSocket--> [Backend API] --> [Trading Engine]
                                    |                  |
                                 Database          Data pasar (GMGN, dll)
```

## Alur kerja robot

1. Research menemukan token kandidat.
2. Analyst menilai harga, volume, likuiditas, aktivitas.
3. Risk Manager menyetujui atau menolak berdasarkan batas risiko.
4. Trader mengeksekusi order simulasi (paper).
5. Accountant mencatat saldo, profit, loss.
6. CEO memantau status semua robot dan menandai masalah.

## Tahap pembangunan

1. Kerangka proyek (selesai)
2. Trading engine paper: saldo, order simulasi, aturan risiko
3. Backend API
4. Dashboard ponsel sederhana
5. Kantor 3D dan animasi robot
6. Integrasi data pasar nyata
7. Deploy cloud untuk operasi terus-menerus
8. Evaluasi (live trading hanya setelah pengujian dan izin eksplisit)
