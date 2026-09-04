"""JioFarm — pipeline 2 tahap: prefilter cepat + hunt lambat.

Tahap 1 (prefilter, N worker paralel): hanya sewa + cek subscriber.
Nomor busuk langsung refund + buang dalam hitungan detik.
Tahap 2 (hunt, K slot): hanya nomor lolos cek yang masuk OTP + buru link.

Slot mahal (tunggu OTP sampai 10 menit) tidak pernah diblokir nomor busuk.
"""

from __future__ import annotations

import queue
import threading
import time

import requests

from jiofarm.config import Config
from jiofarm.console import tg_send
from jiofarm.grizzly.refund import RefundWorker
from jiofarm.hunter import Phase, WorkerState, create_provider, hunt_once, poll_otp
from jiofarm.jio.auth import UA, jio_send_otp, jio_validate_otp
from jiofarm.jio.check import jio_check_detail, normalize_phone
from jiofarm.jio.hunt import hunt_link
from jiofarm.storage.store import Store


def prefilter_once(
    provider,
    refunds: RefundWorker,
    cfg: Config,
    stop: threading.Event | None = None,
    report=None,
) -> tuple[str, str] | None:
    """Sewa 1 nomor + cek subscriber. Kembalikan (act_id, phone) jika lolos.

    *report*, kalau diisi, dipanggil sebagai report(event, phone, detail)
    dengan event: "wait" (stok kosong, throttling urusan pemanggil),
    "empty" (menyerah setelah rent_retries), "fail" (cek subscriber gagal).
    """
    def _report(ev: str, phone: str = "", detail: str = "") -> None:
        if report is not None:
            try:
                report(ev, phone, detail)
            except Exception:
                pass

    for attempt in range(1, cfg.rent_retries + 1):
        if stop is not None and stop.is_set():
            return None
        try:
            act_id, raw = provider.rent(cfg.effective_max_price)
            break
        except Exception as e:
            if "NO_NUMBERS" not in str(e).upper().replace(" ", "_"):
                raise
            _report("wait", "", f"attempt {attempt}/{cfg.rent_retries}")
            if stop is not None:
                if stop.wait(cfg.rent_retry_delay):
                    return None
            else:
                time.sleep(cfg.rent_retry_delay)
    else:
        _report("empty")
        return None

    phone = normalize_phone(raw)
    ok, detail = jio_check_detail(phone)
    if not ok:
        # Grizzly butuh >2 menit dari activation time sebelum bisa cancel
        # langsung vs schedule refund agresif
        refunds.schedule(act_id, 120, aggressive=True)
        _report("fail", phone, f"TIDAK SUBSCRIBED ({detail[:30]})")
        return None
    return act_id, phone


def hunt_number(
    provider,
    store: Store,
    refunds: RefundWorker,
    cfg: Config,
    act_id: str,
    phone: str,
    state: WorkerState,
    stop: threading.Event | None = None,
) -> str | None:
    """OTP + validasi + buru link untuk nomor yang sudah lolos prefilter."""
    state.phase = Phase.SENDING_OTP
    state.phone = phone
    state.act_id = act_id
    state.otp = ""
    state.logged_in = False
    state.link = None
    state.error = ""

    try:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.jio.com",
                "Referer": "https://www.jio.com/selfcare/login/",
            }
        )

        if not jio_send_otp(s, phone):
            state.phase = Phase.ERROR
            state.error = "OTP gagal dikirim"
            refunds.schedule(act_id, cfg.otp_fail_delay)
            return None

        provider.ready(act_id)

        state.phase = Phase.WAITING_OTP
        state.otp = poll_otp(provider, act_id, timeout=cfg.otp_timeout, stop=stop)

        state.phase = Phase.VALIDATING
        if not jio_validate_otp(s, state.otp):
            state.phase = Phase.ERROR
            state.error = "OTP invalid"
            refunds.schedule(act_id, cfg.cancel_delay)
            return None

        state.logged_in = True

        state.phase = Phase.HUNTING
        link = hunt_link(s)
        provider.complete(act_id)

        if link:
            state.link = link
            state.phase = Phase.DONE
            tg_send(
                f"🎯 Google AI Pro link!\n\nNomor : {phone}\nLink :\n{link}",
                cfg.tg_bot_token,
                cfg.tg_chat_id,
            )
        else:
            state.phase = Phase.DONE
            state.error = "login sukses tapi tidak ada promo Google aktif"

        return link

    except KeyboardInterrupt:
        raise
    except Exception as e:
        state.phase = Phase.ERROR
        state.error = str(e)
        if act_id:
            refunds.schedule(act_id, cfg.cancel_delay)
        return None
    finally:
        store.save(phone, act_id, state.otp, state.logged_in, state.link)
        state.attempts += 1


class Pipeline:
    """Orkestrasi prefilter + hunt. Satu instans per sesi hunt bot."""

    def __init__(self, cfg: Config, notify) -> None:
        self.cfg = cfg
        self.notify = notify
        self.stop = threading.Event()
        self.provider = create_provider(cfg)
        self.store = Store(cfg.db_path)
        self.refunds = RefundWorker(self.provider, log=lambda _: None)
        self.ready: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.checked = 0
        self.failed = 0
        self.passed = 0
        self.done = 0
        self.found: list[str] = []
        self.waiting_stock = False
        self.start = time.time()

    def prefilter_loop(self, target_checks: int | None) -> None:
        def report(ev: str, phone: str = "", detail: str = "") -> None:
            msg = None
            with self.lock:
                if ev == "fail":
                    self.failed += 1
                    fails = self.failed
                    if "TUNAI" in detail:
                        msg = f"♻️ +91{phone} bukan subscriber ({detail[:30]}) — refund tunai!"
                    else:
                        msg = f"❌ +91{phone} bukan subscriber ({detail}) — gagal #{fails}"
                elif ev == "wait":
                    if not self.waiting_stock:
                        self.waiting_stock = True
                        msg = f"⏳ Stok kosong, menunggu... ({detail})"
                elif ev == "empty":
                    msg = "❌ Stok kosong — menyerah, coba lagi nanti"
            if msg:
                self.notify(msg)

        while not self.stop.is_set():
            if target_checks is not None:
                with self.lock:
                    if self.checked >= target_checks:
                        return
            try:
                got = prefilter_once(
                    self.provider, self.refunds, self.cfg, self.stop, report=report
                )
            except Exception as e:
                if "NO_NUMBERS" not in str(e).upper().replace(" ", "_"):
                    self.notify(f"⚠️ Prefilter error: {e}")
                    self.stop.wait(30)
                    continue
                continue
            with self.lock:
                self.checked += 1
            if got is None:
                if self.stop.is_set():
                    return
                continue
            act_id, phone = got
            with self.lock:
                self.passed += 1
                was_waiting = self.waiting_stock
                self.waiting_stock = False
            if was_waiting:
                self.notify("📦 Stok ada lagi — lanjut sewa")
            self.notify(f"✅ +91{phone} subscribed — masuk antrean OTP")
            self.ready.put((act_id, phone))

    def hunt_loop(self, ws: WorkerState) -> None:
        from jiofarm.hunter import Phase

        while not self.stop.is_set():
            try:
                act_id, phone = self.ready.get(timeout=5)
            except queue.Empty:
                continue
            try:
                link = hunt_number(
                    self.provider, self.store, self.refunds,
                    self.cfg, act_id, phone, ws, self.stop,
                )
            except Exception as e:
                ws.phase = Phase.ERROR
                ws.error = str(e)
                link = None
            with self.lock:
                self.done += 1
                if link:
                    self.found.append(link)
            from jiofarm.tgbot import attempt_reason

            self.notify(f"📋 Hunt {self.done}: {attempt_reason(ws)}")
            if link:
                self.notify(f"🎯 Link ketemu!\n\nNomor: {phone}\n{link}")
            self.ready.task_done()

    def stats_line(self) -> str:
        el = int(time.time() - self.start)
        with self.lock:
            return (
                f"⏱ {el}s | dicek {self.checked} (gagal {self.failed}) | "
                f"lolos {self.passed} | hunt {self.done} | "
                f"🎯 {len(self.found)} link | antre {self.ready.qsize()}"
            )

    def drain(self) -> int:
        """Batalkan semua nomor antre yang belum di-hunt (refund)."""
        n = 0
        while True:
            try:
                act_id, _ = self.ready.get_nowait()
            except queue.Empty:
                break
            try:
                self.provider.cancel(act_id)
                n += 1
            except Exception:
                pass
            self.ready.task_done()
        return n
