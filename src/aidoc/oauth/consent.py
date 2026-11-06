"""The 授權連線 consent page (spec 2026-11-05 §6): pure HTML + one inline <style>, no script, no external resource.

The page carries the whole authorization request in one hidden field, `request` = b64url(JSON) + "." +
HMAC-SHA256 (a key derived from the server secret), with a 10-minute `exp` and a random `nonce`. The POST reads only
that field (never the query), and each nonce is accepted once (`NonceSet`)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets
import threading
from dataclasses import dataclass

TEXT = {
    "title": "授權連線",
    "headline": "{name} 想連到 Doc4AI Studio",
    "client": "client",
    "unverified": "（自報名稱，未驗證）",
    "source": "來源",
    "dcr_source": "本機註冊的 client（{id}）",
    "redirect": "授權後會回到",
    "loopback_note": "回到這台電腦上的程式",
    "private_note": "會交給本機應用程式",
    "server": "這台伺服器",
    "scopes": "允許的權限",
    "scopes_note": "（client 請求了但你沒勾的權限不會給；至少要有 doc4ai:read）",
    "high_risk": "高風險",
    "server_token": "伺服器 token",
    "loopback_only": "只有在這台電腦的瀏覽器能看到這個頁面。",
    "deny": "拒絕",
    "allow": "允許連線",
    "error_title": "無法授權",
    "err_bad_redirect": "回呼位址不在這個 client 登記的清單內",
    "err_unknown_client": "找不到這個 client",
    "err_cimd": "無法取得 client 的描述文件（{reason}）",
    "err_missing_state": "缺少 state",
    "err_bad_host": "這個 Host 不是伺服器允許的位址",
    "err_bad_request": "授權請求無效或已被竄改，請回到應用程式重新開始。",
    "err_expired": "授權頁已逾時（10 分鐘），請回到應用程式重新開始。",
    "err_used": "這個授權頁已經用過了，請回到應用程式重新開始。",
    "err_scopes": "至少要允許 doc4ai:read，且只能允許 client 請求的權限。",
    "err_wrong_token": "伺服器 token 不正確。",
    "err_too_many": "嘗試太多次，請稍後再試。",
    "err_cross_site": "這個表單只能從授權頁本身送出。",
    "err_client": "client 設定有誤：{reason}",
    "err_param": "授權請求的參數有誤，請回到應用程式重新開始。",
}
SCOPE_TEXT = {
    "doc4ai:read": "讀取文件庫（搜尋、讀取、分段）",
    "doc4ai:convert": "上傳並轉換文件",
    "doc4ai:convert:local": "以本機路徑轉換",
    "doc4ai:manage": "取消工作、重新轉換文件",
}
HIGH_RISK = frozenset({"doc4ai:convert:local", "doc4ai:manage"})
CONSENT_HEADERS = {
    # no form-action: Chrome applies it to the 302 after the POST, which must go to the client's redirect URI (spec §1.2)
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; frame-ancestors 'none'; "
                               "base-uri 'none'",
    "X-Frame-Options": "DENY",
    # same-origin, not no-referrer: under no-referrer a browser sends `Origin: null` on the consent form's POST (Fetch
    # spec, non-CORS POST), which the CSRF check must refuse. same-origin still sends no Referer to the client's
    # callback (always another origin), so the code never leaks through it (plan deviation, Task 12)
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


class ConsentError(Exception):
    """A signed request that is malformed, forged or expired; `message` is shown on the error page."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _key(secret: bytes) -> bytes:
    return hmac.new(secret, b"doc4ai-oauth-consent-request-v1", hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def sign_request(secret: bytes, params: dict, exp: float) -> str:
    body = _b64(json.dumps({**params, "exp": exp, "nonce": secrets.token_hex(16)}, separators=(",", ":")).encode("utf-8"))
    return body + "." + hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).hexdigest()


def verify_request(secret: bytes, blob, now: float) -> dict:
    if not isinstance(blob, str) or blob.count(".") != 1:
        raise ConsentError(TEXT["err_bad_request"])
    body, sig = blob.split(".")
    want = hmac.new(_key(secret), body.encode("ascii", "replace"), hashlib.sha256).hexdigest()
    if not body or not hmac.compare_digest(want, sig):
        raise ConsentError(TEXT["err_bad_request"])
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        raise ConsentError(TEXT["err_bad_request"]) from None
    if not isinstance(data, dict) or not isinstance(data.get("exp"), (int, float)) or not isinstance(data.get("nonce"), str):
        raise ConsentError(TEXT["err_bad_request"])
    if data["exp"] <= now:
        raise ConsentError(TEXT["err_expired"])
    return data


class NonceSet:
    """Nonces of consent requests already used, each remembered for `ttl_s` (the request's own lifetime)."""

    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def consume(self, nonce: str, now: float) -> bool:
        with self._lock:
            for k in [k for k, t in self._seen.items() if now - t >= self.ttl_s]:
                del self._seen[k]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True

    def is_used(self, nonce: str, now: float) -> bool:
        with self._lock:
            t = self._seen.get(nonce)
            return t is not None and now - t < self.ttl_s

    def __len__(self) -> int:
        return len(self._seen)


class ConsentThrottle:
    """Token-mode consent guessing (verifier F4, review 2026-11-06). Two limits, neither of which lets an address lock
    the owner out:

    * per consent page (nonce) — the hard limit: from the 2nd wrong server token a pause of 1, 2, 4 … s before the
      next try, and the NONCE_LIMIT-th wrong guess uses the page up (the user reloads for a new one);
    * per address (the socket peer; no forwarded headers are trusted) — a growing delay, never a refusal: after
      FREE_FAILS wrong tokens every attempt from the address waits 1, 2, 4, 8 s (MAX_DELAY_S) before it is judged, and
      attempts from one address are judged one at a time, so guesses cannot get faster however many pages are opened.
      The delay decays after QUIET_S without failures and resets on a success.

    All arithmetic is clamped (exponents at EXP_CAP, counters stop at their cap) and fails closed: any error answers
    with the longest delay."""

    FREE_FAILS, EXP_CAP, MAX_DELAY_S, QUIET_S = 2, 6, 8.0, 300.0
    NONCE_LIMIT = 5
    MAX_KEYS = 10000

    def __init__(self):
        self._ip: dict[str, dict] = {}                     # ip -> {fails, last}
        self._nonce: dict[str, tuple[int, float]] = {}     # nonce -> (wrong guesses, not before)
        self._lock = threading.Lock()

    def delay_for(self, ip: str | None, now: float) -> float:
        with self._lock:
            st = self._ip.get(ip or "?")
            if st is None:
                return 0.0
            try:
                if now - st["last"] >= self.QUIET_S:
                    return 0.0
                if st["fails"] <= self.FREE_FAILS:
                    return 0.0
                return min(self.MAX_DELAY_S, float(2 ** min(st["fails"] - self.FREE_FAILS - 1, self.EXP_CAP)))
            except Exception:  # noqa: BLE001  fail closed: never skip the delay
                return self.MAX_DELAY_S

    def nonce_retry(self, nonce: str, now: float) -> int | None:
        with self._lock:
            n = self._nonce.get(nonce)
        if n is None or n[1] <= now:
            return None
        return max(1, int(n[1] - now + 0.999))

    def failure(self, ip: str | None, nonce: str, now: float) -> bool:
        """Count one wrong token; True when this page is now used up."""
        cap = self.FREE_FAILS + self.EXP_CAP + 1
        with self._lock:
            if len(self._ip) > self.MAX_KEYS:
                self._ip = {k: v for k, v in self._ip.items() if isinstance(v.get("last"), float) and now - v["last"] < self.QUIET_S}
            if len(self._nonce) > self.MAX_KEYS:
                self._nonce = {k: v for k, v in self._nonce.items() if v[1] > now}
            st = self._ip.setdefault(ip or "?", {"fails": 0, "last": now})
            try:
                fails = 0 if now - st["last"] >= self.QUIET_S else int(st["fails"])
                st["fails"] = min(fails + 1, cap)
            except Exception:  # noqa: BLE001  fail closed: the longest delay
                st["fails"] = cap
            st["last"] = now
            wrong = min(self._nonce.get(nonce, (0, 0.0))[0] + 1, self.NONCE_LIMIT + self.EXP_CAP)
            pause = 2 ** min(wrong - 2, self.EXP_CAP) if wrong >= 2 else 0
            self._nonce[nonce] = (wrong, now + pause)
            return wrong >= self.NONCE_LIMIT

    def success(self, ip: str | None, nonce: str) -> None:
        with self._lock:
            self._nonce.pop(nonce, None)
            self._ip.pop(ip or "?", None)


@dataclass
class ConsentView:
    client_name: str
    source_kind: str            # "cimd" | "dcr"
    source_label: str           # CIMD: the client_id's host; DCR: the client id
    redirect_uri: str
    redirect_kind: str          # "https" | "loopback" | "private"
    issuer: str
    requested_scopes: list
    checked_scopes: list
    needs_token: bool
    request_blob: str


_STYLE = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e2dc;--accent:#2457c5;--danger:#b42318;--btn:#eceae4}
@media (prefers-color-scheme: dark){:root{--bg:#141413;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;
--accent:#7ea2ff;--danger:#ff8a80;--btn:#2b2a27}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg)}
body{font:15px/1.55 system-ui,-apple-system,"Segoe UI","Microsoft JhengHei","PingFang TC",sans-serif}
main{max-width:32rem;margin:0 auto;padding:1rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1.25rem}
h1{font-size:1.35rem;margin:0 0 .25rem}h2{font-size:1rem;margin:1.25rem 0 .5rem}
.lead{margin:0 0 1rem;font-size:1.05rem;overflow-wrap:anywhere}
dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:.35rem .75rem}dt{color:var(--muted)}
dd{margin:0;overflow-wrap:anywhere;word-break:break-word}code{font-size:.92em;overflow-wrap:anywhere}
.note{color:var(--muted);font-size:.88rem}.danger{color:var(--danger);font-weight:600;font-size:.85rem;margin-left:.35rem}
ul{list-style:none;margin:0;padding:0}li{padding:.4rem 0;border-top:1px solid var(--line)}
label{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap}label code{font-weight:600}
input[type=password]{width:100%;padding:.55rem;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
.actions{display:flex;gap:.75rem;justify-content:flex-end;margin-top:1.25rem}
button{font:inherit;padding:.6rem 1.1rem;border-radius:8px;border:1px solid var(--line);background:var(--btn);color:var(--fg);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
@media (max-width:420px){.actions{flex-direction:column-reverse}.actions button{width:100%}dl{grid-template-columns:1fr}dt{margin-top:.35rem}}
"""


def _page(title: str, inner: str) -> str:
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} · Doc4AI Studio</title>'
            f"<style>{_STYLE}</style></head><body><main>{inner}</main></body></html>")


def render_consent(v: ConsentView) -> str:
    e = html.escape
    name = e(v.client_name)
    if v.source_kind == "cimd":
        source = f"<strong>{e(v.source_label)}</strong>"
    else:
        source = e(TEXT["dcr_source"].format(id=v.source_label))
    note = ""
    if v.redirect_kind == "loopback":
        note = f'<br><span class="note">{e(TEXT["loopback_note"])}</span>'
    elif v.redirect_kind == "private":
        scheme = v.redirect_uri.split(":", 1)[0]
        note = f'<br><span class="note">{e(TEXT["private_note"])} {e(scheme)}://…</span>'
    rows = []
    for s in v.requested_scopes:
        checked = " checked" if s in v.checked_scopes else ""
        risk = f'<span class="danger">{e(TEXT["high_risk"])}</span>' if s in HIGH_RISK else ""
        rows.append(f'<li><label><input type="checkbox" name="scopes" value="{e(s)}"{checked}> <code>{e(s)}</code> '
                    f'<span>{e(SCOPE_TEXT.get(s, s))}</span>{risk}</label></li>')
    if v.needs_token:
        cred = (f'<h2><label for="server_token">{e(TEXT["server_token"])}</label></h2>'
                f'<input type="password" name="server_token" id="server_token" autocomplete="current-password" required>')
    else:
        cred = f'<p class="note">{e(TEXT["loopback_only"])}</p>'
    inner = (f'<div class="card"><h1>{e(TEXT["title"])}</h1>'
             f'<p class="lead">{e(TEXT["headline"].format(name=v.client_name))}</p>'
             f'<dl><dt>{e(TEXT["client"])}</dt><dd>{name}{e(TEXT["unverified"])}</dd>'
             f'<dt>{e(TEXT["source"])}</dt><dd>{source}</dd>'
             f'<dt>{e(TEXT["redirect"])}</dt><dd><code>{e(v.redirect_uri)}</code>{note}</dd>'
             f'<dt>{e(TEXT["server"])}</dt><dd><code>{e(v.issuer)}</code></dd></dl>'
             f'<form method="post" action="/oauth/authorize">'
             f'<input type="hidden" name="request" value="{e(v.request_blob)}">'
             f'<h2>{e(TEXT["scopes"])}</h2><ul>{"".join(rows)}</ul><p class="note">{e(TEXT["scopes_note"])}</p>'
             f'{cred}<div class="actions"><button type="submit" name="decision" value="deny">{e(TEXT["deny"])}</button>'
             f'<button type="submit" name="decision" value="allow" class="primary">{e(TEXT["allow"])}</button></div>'
             f"</form></div>")
    return _page(TEXT["title"], inner)


def render_error(title: str, reason: str | None = None) -> str:
    e = html.escape
    detail = f'<p class="note">{e(reason)}</p>' if reason else ""
    return _page(TEXT["error_title"], f'<div class="card"><h1>{e(TEXT["error_title"])}</h1><p class="lead">{e(title)}</p>{detail}</div>')
