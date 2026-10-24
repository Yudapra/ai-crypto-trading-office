# Arsitektur

```
[Frontend]  --(belum dibangun)-->  [Backend API]  --(belum dibangun)-->  ...
                                         |
        backend/app (sudah ada)          |        trading-engine (sudah ada)
  ┌────────────────────────────────┐     |   ┌──────────────────────────────┐
  │ market_data (antarmuka, MOCK)  │     └──►│ PaperBroker.submit()         │
  │ security  (engine, policy)     │◄────────│   validasi → idempotensi     │
  │ holder_analysis, liquidity_... │ SecurityGate  → harga → [BUY: gerbang │
  │ services (gate, robots)        │         │   keamanan → risiko → dana]  │
  └────────────────────────────────┘         │   → fill → SQLite + audit    │
                                             └──────────────────────────────┘
```

## Aturan dependensi

- `trading_engine` tidak mengimpor `backend`. Ia hanya mengenal antarmuka `SecurityGate` dan `PriceSource`.
- `backend` mengimpor `trading_engine` (adapter `EngineSecurityGate`).
- Satu-satunya jalur publik menuju fill adalah `PaperBroker.submit`. Tidak ada parameter untuk melewati gerbang keamanan.
- `LiveExecutor` selalu melempar `LiveTradingDisabled`; `TRADING_MODE != paper` menghentikan startup.

## Status robot

`RobotRegistry` hanya berubah ketika service backend memanggil `update` setelah pekerjaan nyata (mis. Security Analyst menjadi
`ANALYZING` saat evaluasi dimulai dan `COMPLETED`/`BLOCKED` sesuai keputusan). Robot yang tidak dilaporkan tetap `IDLE`.

## Tahap pembangunan

1. Kerangka proyek (selesai)
2. Mesin keamanan + paper trading + tes (selesai)
3. Backend FastAPI: endpoint REST, WebSocket, autentikasi API key (belum)
4. Provider data nyata setelah akses dan autentikasi diverifikasi (belum)
5. Frontend Next.js + kantor 3D (belum)
6. Deploy: frontend di host statis/serverless; API dan worker di host Python terpisah (belum)
