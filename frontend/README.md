# Frontend (Next.js + React Three Fiber)

Kantor virtual 3D: enam robot humanoid duduk di depan komputer (CEO, Research Analyst, Market Analyst,
Security Analyst, Trader, Accountant). **Hanya membaca** `/api/state` dan `/api/events` dari backend (polling 2 detik).
Tidak ada kunci API atau private key di frontend; satu-satunya variabel publik adalah `NEXT_PUBLIC_API_BASE_URL`.

## Menjalankan

```
cp .env.example .env.local     # isi alamat backend
npm install
npm run dev                    # http://localhost:3000
npm test                       # tes logika murni (Node 22+, tanpa dependensi)
```

## Hubungan tampilan dengan backend (tidak ada aktivitas karangan)

- Pose robot = fungsi dari `state` yang dilaporkan backend (`src/lib/motion.ts`): WORKING mengetik, ANALYZING mengetik + menoleh,
  BLOCKED menggeleng + berkedip, ERROR lunglai + berkedip, COMPLETED mengangguk, IDLE diam.
- Backend tidak terjangkau => semua robot abu-abu dan diam (`OFFLINE`), spanduk merah muncul.
- Layar dinding menampilkan teks asli dari backend (detail robot, equity/PnL simulasi, jumlah PASS/BLOCKED). Garis pindai hanya
  bergerak saat robot berstatus WORKING/ANALYZING.
- Satu paket data terbang dari robot ke robot berikutnya HANYA ketika event `COMPLETED` baru tiba dari backend.
- Banner permanen: SIMULASI / PAPER TRADING, dan lencana sumber data (MOCK / nyata / terputus).

## Status verifikasi

Diuji: logika murni (`src/lib/*`) dengan `node --test` (11 tes), sintaks semua `.ts/.tsx` dengan esbuild, dan tipe modul murni.
**Belum diuji:** `npm install`, `next build`, dan tampilan 3D di browser (registry npm tidak dapat dijangkau dari lingkungan pengembangan).
Jalankan `npm run typecheck && npm run build` pertama kali di mesin yang punya akses npm; kemungkinan ada penyesuaian kecil pada komponen R3F.
