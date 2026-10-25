# Sumber data pasar: status nyata (diperiksa 2026-10-25)

| Sumber | Dipakai untuk | Status |
|---|---|---|
| Solana JSON-RPC | authority mint/freeze, ekstensi Token-2022, 20 akun terbesar, saldo developer | Adapter ada (`market_data/solana_rpc.py`), diuji dengan fixture. Butuh `SOLANA_RPC_URL`. |
| Raydium API v3 (publik) | TVL pool, harga USD | Adapter ada (`raydium_api.py`), diuji dengan fixture. Endpoint: `/pools/info/mint`, `/mint/price`. |
| pump.fun | creator token | **Tidak resmi** (endpoint situs web). Adapter ada (`pumpfun_api.py`), nonaktif kecuali `ALLOW_UNOFFICIAL_PUMPFUN=true`. |
| GMGN | holder, security/honeypot, trending | **Belum ada adapter** (lihat di bawah). |

## GMGN

- GMGN menyediakan OpenAPI resmi untuk agen/AI (repo `GMGNAI/gmgn-skills`). Kunci API diajukan di https://gmgn.ai/ai
  (biaya dan persetujuan tidak dinyatakan di halaman yang dapat dibaca).
- Rute yang disebut di dokumentasi CLI: `GET /v1/token/info`, `/v1/token/security`, `/v1/token/pool_info`,
  `/v1/market/token_top_holders`, `/v1/market/token_top_traders` (limit laju: bucket 20, bobot holders = 5).
- **Tidak ditemukan** di sumber yang bisa diakses: host dasar untuk rute itu, nama parameter query, header autentikasi
  untuk endpoint token, dan bentuk JSON respons. (Dokumen "Callout OpenAPI" memakai `https://papi.gmgn.ai/callout/openapi/v1`
  dengan header `X-Ak`/`X-Timestamp`/`X-Signature` dan IP allowlist, tetapi itu hanya untuk callout, bukan data token.)
- Karena itu tidak ada adapter GMGN: menebak bentuk respons berarti bisa salah menilai keamanan token.
  Langkah berikutnya: ajukan kunci, baca `docs/cli-usage.md` di repo GMGN atau jalankan `gmgn-cli`, rekam respons asli,
  lalu tulis adapter + tes dari rekaman itu.

## Akibat pada aturan keamanan (mode live)

- Aturan top-10 holder <= 25% tidak dapat dibuktikan dari RPC saja (hanya 20 akun terbesar, bukan pemilik). Snapshot ditandai
  `complete=False` sehingga hasilnya UNKNOWN, bukan PASS.
- Honeypot: belum ada simulasi jual nyata, sehingga UNKNOWN.
- Developer > 10% (pump.fun), authority mint/freeze, dan likuiditas < minimum tetap bisa memblokir (BLOCKED) bila datanya tersedia.
- Hasil akhir: pada `DATA_PROVIDER=live` tidak ada BUY yang diizinkan sampai sumber holder lengkap dan simulasi jual tersedia.
