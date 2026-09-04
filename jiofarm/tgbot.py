"""JioFarm — Telegram bot: cek subscribe Jio + kontrol hunter.

Polling murni pakai ``requests`` (tanpa dependency baru).
Perintah yang didukung:
  /start            bantuan
  /check <nomor>    cek status subscribe Jio (cth: /check 7995112495)
  <nomor>           kirim nomor langsung, langsung dicek
  /balance          cek saldo provider
  /stats            statistik hunt dari SQLite
  /hunt [n] [--max-price X] [--target N] [--duration 2h]
                    cari nomor baru sampai dapat link (cth: /hunt 5 --max-price 0.5)
  /maxprice X       atur max harga default (cth: /maxprice 0.5)
  /status           status hunt yang jalan
  /stop             hentikan hunt yang jalan

Kalau TG_CHAT_ID diset di .env, bot hanya merespons chat itu.
"""

from __future__ import annotations

import re
import threading
import time

import requests

from jiofarm.config import Config
from jiofarm.jio.check import jio_check_detail, normalize_phone

HELP = (
    "🦁 JioFarm Bot\n\n"
    "/check <nomor> — cek subscribe Jio (cth: /check 7995112495)\n"
    "atau kirim nomornya langsung.\n\n"
    "/hunt [n] [--max-price X] [--target N] [--duration 2h]\n"
    "  cari nomor sampai dapat link (cth: /hunt 5 --max-price 0.5)\n"
    "/maxprice X — atur max harga (cth: /maxprice 0.5)\n"
    "/status — status hunt yang jalan\n"
    "/stop — hentikan hunt\n\n"
    "/balance — saldo provider\n"
    "/stats — statistik hunt"
)


def _call(token: str, method: str, payload: dict | None = None, timeout: int = 35) -> dict:
    res = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload or {},
        timeout=timeout,
    )
    res.raise_for_status()
    return res.json()


def _send(token: str, chat_id: int | str, text: str) -> None:
    try:
        _call(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=15)
    except Exception:
        pass


def check_reply(raw: str) -> str:
    phone = normalize_phone(raw)
    if len(phone) != 10:
        return f"Nomor tidak valid: {raw} (butuh 10 digit India)"
    ok, detail = jio_check_detail(phone)
    if ok:
        return f"✅ +91{phone} — SUBSCRIBED (pelanggan Jio aktif)"
    return f"❌ +91{phone} — {detail}"


def balance_reply(cfg: Config) -> str:
    from jiofarm.hunter import create_provider

    try:
        bal = create_provider(cfg).balance()
        return f"💰 Saldo {cfg.provider.upper()}: ${bal:,.2f}"
    except Exception as e:
        return f"Gagal cek saldo: {e}"


def stats_reply(cfg: Config) -> str:
    from jiofarm.storage.store import Store

    try:
        st = Store(cfg.db_path).stats()
        return (
            "📊 Statistik JioFarm\n"
            f"Percobaan: {st['hunts']} | Login: {st['logins']} | Link: {st['links']}"
        )
    except Exception as e:
        return f"Gagal baca stats: {e}"


# ------------------------------------------------------------------- hunt controller


def _parse_duration(s: str) -> float:
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s.strip())
    if m and any(m.groups()):
        h, mi, se = (int(g) if g else 0 for g in m.groups())
        return h * 3600 + mi * 60 + se
    raise ValueError(f"durasi tidak valid: '{s}' (cth: 2h, 30m, 1h30m)")


def parse_hunt_args(text: str) -> dict:
    """Parse '/hunt [n] [--max-price X] [--target N] [--duration 2h]'."""
    parts = text.split()[1:]
    out: dict = {"count": None, "max_price": None, "target": None, "duration": None}
    rest: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p == "--max-price" and i + 1 < len(parts):
            out["max_price"] = float(parts[i + 1])
            i += 2
        elif p == "--target" and i + 1 < len(parts):
            out["target"] = int(parts[i + 1])
            i += 2
        elif p == "--duration" and i + 1 < len(parts):
            out["duration"] = _parse_duration(parts[i + 1])
            i += 2
        else:
            rest.append(p)
            i += 1
    if rest:
        try:
            out["count"] = int(rest[0])
        except ValueError:
            raise ValueError(f"arg tidak dikenal: {rest[0]}")
    return out


def phase_line(provider: str, ws) -> str:
    """Satu baris status tahap worker, bahasa manusia."""
    from jiofarm.hunter import Phase

    phone = f" +91{ws.phone}" if ws.phone else ""
    err = f" — {ws.error}" if ws.error else ""
    table = {
        Phase.IDLE: "⏳ Idle",
        Phase.RENTING: f"📱 Menyewa nomor {provider}{err}",
        Phase.CHECKING: f"🔍 Cek subscriber Jio{phone}",
        Phase.SENDING_OTP: f"📤 Kirim OTP ke{phone}",
        Phase.WAITING_OTP: f"⏳ Tunggu OTP masuk{phone} (maks 10 mnt)",
        Phase.VALIDATING: f"🔐 Validasi OTP{phone}",
        Phase.HUNTING: f"🎯 Buru link Google{phone}",
        Phase.DONE: f"✅ Selesai{phone}{err}",
        Phase.ERROR: f"❌ Gagal{phone}{err}",
    }
    return table.get(ws.phase, f"{ws.phase.name}{phone}{err}")


def attempt_reason(ws) -> str:
    """Ringkasan hasil satu attempt, bahasa manusia."""
    err = ws.error or "gagal tanpa keterangan"
    phone = f"+91{ws.phone}" if ws.phone else "tanpa nomor"
    if "bukan pelanggan Jio" in err:
        return f"{phone} → ❌ bukan pelanggan Jio (nomor mati/recycled, refund otomatis)"
    if "OTP gagal dikirim" in err:
        return f"{phone} → ❌ OTP Jio gagal dikirim"
    if "OTP timeout" in err:
        return f"{phone} → ❌ OTP tidak masuk (10 mnt)"
    if "OTP invalid" in err:
        return f"{phone} → ❌ OTP salah"
    if "tidak ada promo" in err:
        return f"{phone} → ⚠️ login OK tapi tidak ada promo Google (dana terpakai)"
    if "stok kosong" in err:
        return "❌ stok kosong setelah 600x coba"
    if "dihentikan" in err:
        return "dihentikan"
    return f"{phone} → ❌ {err}"


class HuntManager:
    """Satu hunt background per bot. Thread-safe, bisa di-stop kapan saja."""

    PROGRESS_INTERVAL = 120  # kirim heartbeat tiap N detik kalau fase tidak berubah

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.info: dict = {}

    def running(self) -> bool:
        with self._lock:
            return self.thread is not None and self.thread.is_alive()

    def start(self, cfg: Config, args: dict, notify) -> str:
        if args["max_price"] is not None and args["max_price"] > cfg.price_cap:
            return (
                f"❌ --max-price ${args['max_price']} melebihi plafon ${cfg.price_cap}. "
                f"Pakai maksimal ${cfg.price_cap}."
            )
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return "⏳ Hunt sudah jalan. /status untuk pantau, /stop untuk hentikan."
            self.stop = threading.Event()
            t = threading.Thread(
                target=self._run, args=(cfg, args, notify), daemon=True
            )
            self.thread = t
            t.start()
        mode = (
            f"target {args['target']} link"
            if args["target"]
            else (f"{args['count']} nomor" if args["count"] else "sampai dapat 1 link")
        )
        mp = min(
            args["max_price"] if args["max_price"] is not None else cfg.effective_max_price,
            cfg.price_cap,
        )
        extra = ""
        if args["duration"]:
            extra = f", durasi {args['duration'] / 3600:.1f} jam"
        return f"🦁 Hunt mulai: {mode}, max ${mp}{extra}. /stop untuk hentikan."

    def stop_hunt(self) -> str:
        with self._lock:
            t = self.thread
        if t is None or not t.is_alive():
            return "Tidak ada hunt yang jalan."
        self.stop.set()
        return "🛑 Stop dikirim — menunggu worker selesai, ringkasan dikirim otomatis."

    def status(self) -> str:
        with self._lock:
            info = dict(self.info)
            alive = self.thread is not None and self.thread.is_alive()
        if not alive:
            return "Tidak ada hunt yang jalan."
        if not info:
            return "🦁 Hunt baru mulai..."
        el = int(time.time() - info.get("start", time.time()))
        st = info.get("state")
        phase = st.phase.name if st else "-"
        phone = (st.phone or "-") if st else "-"
        err = (st.error or "") if st else ""
        return (
            "🦁 Hunt jalan\n"
            f"⏱ {el}s | attempt {info.get('done', 0)} | 🎯 {info.get('found', 0)} link\n"
            f"Fase: {phase} | {phone} {err}"
        )

    def _run(self, cfg: Config, args: dict, notify) -> None:
        from jiofarm.console import tg_send  # noqa: F401 (keep import local, avoid cycle)
        from jiofarm.grizzly.refund import RefundWorker
        from jiofarm.hunter import WorkerState, create_provider, hunt_once
        from jiofarm.storage.store import Store

        if args["max_price"] is not None:
            cfg.max_price_override = args["max_price"]
        provider = create_provider(cfg)
        store = Store(cfg.db_path)
        refunds = RefundWorker(provider, log=lambda _: None)
        slots = threading.Semaphore(cfg.effective_concurrency)
        ws = WorkerState(wid=1)
        found: list[str] = []
        done = 0
        start = time.time()
        stop = self.stop
        is_count = not (args["target"] or args["duration"])
        total = args["count"] or (1 if is_count and not args["target"] else 0)

        def hit_stop() -> bool:
            if stop.is_set():
                return True
            if args["duration"] and time.time() - start >= args["duration"]:
                return True
            if args["target"] and len(found) >= args["target"]:
                return True
            if is_count and done >= (total or 1):
                return True
            return False

        with self._lock:
            self.info = {"start": start, "done": 0, "found": 0, "state": ws}
        try:
            bal0 = provider.balance()
            notify(f"💰 Saldo awal: ${bal0:,.2f}")
        except Exception:
            bal0 = None
        stopped = False

        def watcher() -> None:
            last_key = None
            last_sent = 0.0
            while not stop.is_set() and self.running():
                key = (ws.phase, ws.phone)
                now = time.time()
                if key != last_key or now - last_sent >= self.PROGRESS_INTERVAL:
                    last_key = key
                    last_sent = now
                    line = phase_line(provider=cfg.provider.upper(), ws=ws)
                    notify(f"📡 Attempt {done + 1}\n{line}")
                stop.wait(5)

        threading.Thread(target=watcher, daemon=True).start()
        while not hit_stop():
            try:
                link = hunt_once(provider, store, refunds, slots, cfg, ws, stop=stop)
            except Exception as e:
                ws.error = str(e)
                link = None
            done += 1
            with self._lock:
                self.info.update({"done": done, "found": len(found)})
            notify(f"📋 Attempt {done}: {attempt_reason(ws)}")
            if link:
                found.append(link)
                with self._lock:
                    self.info["found"] = len(found)
                notify(f"🎯 Link ketemu!\n\nNomor: {ws.phone}\n{link}")
            if stop.is_set():
                stopped = True
                break
            if not hit_stop():
                if stop.wait(3):
                    stopped = True
                    break
        refunds.wait_all()
        el = int(time.time() - start)
        why = "dihentikan 🛑" if stopped else "selesai ✅"
        try:
            bal1 = provider.balance()
            bal_line = f"\n💰 Saldo: ${bal0:,.2f} → ${bal1:,.2f}" if bal0 is not None else ""
        except Exception:
            bal_line = ""
        summary = (
            f"🦁 Hunt {why} ({el}s)\n"
            f"📱 Diproses: {done} | 🎯 Link: {len(found)}{bal_line}"
        )
        if found:
            summary += "\n\n" + "\n".join(found)
        notify(summary)
        with self._lock:
            self.info = {}


_HUNT = HuntManager()


def hunt_reply(cfg: Config, text: str, notify) -> str:
    try:
        args = parse_hunt_args(text)
    except ValueError as e:
        return str(e)
    if args["target"] is None and args["count"] is None:
        args["target"] = 1
    return _HUNT.start(cfg, args, notify)


def maxprice_reply(cfg: Config, text: str) -> str:
    parts = text.split()
    if len(parts) < 2:
        return (
            f"Harga jalan: ${cfg.effective_max_price} "
            f"(default ${cfg.max_price}, plafon ${cfg.price_cap}) — atur: /maxprice 0.47"
        )
    try:
        v = float(parts[1])
    except ValueError:
        return f"Harga tidak valid: {parts[1]}"
    if v <= 0:
        return "Harga harus > 0."
    if v > cfg.price_cap:
        return f"❌ Melebihi plafon ${cfg.price_cap}. Maksimal yang boleh: ${cfg.price_cap}."
    cfg.max_price = v
    cfg.max_price_override = None
    return f"✅ Max price diset ${v} (plafon ${cfg.price_cap})."


def handle_text(cfg: Config, text: str, notify=None) -> str | None:
    low = text.strip().lower()
    if low.startswith("/start"):
        return HELP
    if low.startswith("/check"):
        parts = text.split()
        if len(parts) < 2:
            return "Kirim: /check 7995112495"
        return check_reply(parts[1])
    if low.startswith("/balance"):
        return balance_reply(cfg)
    if low.startswith("/stats"):
        return stats_reply(cfg)
    if low.startswith("/status"):
        return _HUNT.status()
    if low.startswith("/stop"):
        return _HUNT.stop_hunt()
    if low.startswith("/maxprice"):
        return maxprice_reply(cfg, text)
    if low.startswith("/hunt"):
        if notify is None:
            return "Hunt via bot butuh sesi bot aktif."
        return hunt_reply(cfg, text, notify)
    digits = "".join(c for c in text if c.isdigit())
    if digits and len(digits) >= 10:
        return check_reply(digits)
    return None


def run_bot(cfg: Config) -> None:
    """Long-polling loop. Berhenti pakai Ctrl+C."""
    if not cfg.tg_bot_token:
        raise SystemExit("TG_BOT_TOKEN belum diset di .env")
    token = cfg.tg_bot_token
    try:
        me = _call(token, "getMe", timeout=15).get("result", {})
        print(f"[bot] login @{me.get('username')} (id={me.get('id')})", flush=True)
    except Exception as e:
        print(f"[bot] getMe gagal: {e}", flush=True)
    allowed = str(cfg.tg_chat_id or "")
    print(f"[bot] chat filter: {allowed or '(semua)'}", flush=True)
    offset = 0
    idle_log = time.time()
    while True:
        try:
            data = _call(token, "getUpdates", {"offset": offset, "timeout": 30}, timeout=40)
        except Exception as e:
            print(f"[bot] poll error: {e}", flush=True)
            time.sleep(5)
            continue
        results = data.get("result", [])
        if not results and time.time() - idle_log >= 300:
            idle_log = time.time()
            print("[bot] polling... (belum ada pesan)", flush=True)
        for upd in results:
            offset = upd.get("update_id", offset) + 1
            msg = upd.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            text = (msg.get("text") or "").strip()
            if not chat_id or not text:
                continue
            print(f"[bot] pesan dari {chat_id}: {text[:60]}", flush=True)
            if allowed and str(chat_id) != allowed:
                print(f"[bot] chat {chat_id} diabaikan (filter {allowed})", flush=True)
                continue
            notify = lambda msg, _c=chat_id: _send(token, _c, msg)
            try:
                reply = handle_text(cfg, text, notify=notify)
            except Exception as e:
                reply = f"Error: {e}"
            if reply:
                _send(token, chat_id, reply)
