# Deploy ke VPS Ubuntu

## 1. Siapkan VPS

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER
# logout + login lagi supaya grup docker aktif
```

## 2. Ambil kode

```bash
git clone https://github.com/hirotomasato/jiofarm.git
cd jiofarm
cp .env.example .env
nano .env   # isi GRIZZLY_API_KEY, TG_BOT_TOKEN, TG_CHAT_ID
```

Isi minimal `.env`:

```env
PROVIDER=grizzlysms
GRIZZLY_API_KEY=xxxx
MAX_PRICE=0.5
CONCURRENCY=2
DB_PATH=/data/results.db
RENT_RETRIES=600
RENT_RETRY_DELAY_SECONDS=5
TG_BOT_TOKEN=123456:ABCdef...
TG_CHAT_ID=987654321
```

## 3. Jalankan

```bash
# hunter saja
docker compose up -d --build hunter

# hunter + bot Telegram (cek subscribe, /balance, /stats)
docker compose --profile bot up -d --build
```

## 4. Operasional

```bash
docker compose logs -f hunter   # lihat dashboard/log
docker compose logs -f bot      # log bot
docker compose ps               # status container
docker compose down             # stop semua
docker compose down -v          # stop + HAPUS data (results.db hilang!)
```

Data (`results.db`) tersimpan di volume `jiofarm-data`, aman dari rebuild.
Backup: `docker run --rm -v jiofarm-data:/data -v $PWD:/b ubuntu cp /data/results.db /b/`.

## Bot Telegram

Kirim ke bot: `/start`, `/check 7995112495`, nomor langsung, `/balance`, `/stats`.
Kontrol hunt: `/hunt 5 --max-price 0.5`, `/hunt --target 3`, `/hunt --duration 2h`,
`/status`, `/stop`, `/maxprice 0.5`.
Bot kirim update tiap tahap (sewa → cek → OTP → buru) + ringkasan + saldo awal/akhir.
Kalau `TG_CHAT_ID` diset, bot hanya merespons chat itu.
