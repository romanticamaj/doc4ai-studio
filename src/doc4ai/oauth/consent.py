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
    "headline": "{name} 想連到你的 Doc4AI 文件庫",
    "intro": "按下「允許連線」後，它可以做下面勾選的事：",
    "details": "連線資訊",
    "client": "用戶端",
    "unverified": "（自報名稱，未驗證）",
    "source": "來源",
    "dcr_source": "本機註冊的用戶端（{id}）",
    "redirect": "授權後會回到",
    "loopback_note": "回到這台電腦上的程式",
    "private_note": "會交給本機應用程式",
    "server": "這台伺服器",
    "scopes": "它可以做什麼",
    "scopes_note": "沒勾的項目不會開放給它。",
    # test_limits_text_matches_the_tools_that_exist fails if a tool that breaks this promise is added
    "limits": "不管勾哪些，它都不能刪除文件、更改設定或管理授權。你可以隨時到 Doc4AI 的 MCP 頁面撤銷這個連線。",
    "attention": "這項權限影響比較大，勾選前請確認",
    "required": "必要",
    "server_token": "伺服器 token",
    "loopback_only": "只有在這台電腦的瀏覽器能看到這個頁面。",
    "deny": "拒絕",
    "allow": "允許連線",
    "error_title": "無法授權",
    "err_bad_redirect": "回呼位址不在這個用戶端登記的清單內",
    "err_unknown_client": "找不到這個用戶端",
    "err_cimd": "無法取得用戶端的描述文件（{reason}）",
    "err_missing_state": "缺少 state",
    "err_bad_host": "這個 Host 不是伺服器允許的位址",
    "err_bad_request": "授權請求無效或已被竄改，請回到應用程式重新開始。",
    "err_expired": "授權頁已逾時（10 分鐘），請回到應用程式重新開始。",
    "err_used": "這個授權頁已經用過了，請回到應用程式重新開始。",
    "err_scopes": "至少要允許「搜尋和閱讀文件庫」，且只能允許用戶端請求的權限。",
    "err_wrong_token": "伺服器 token 不正確。",
    "err_too_many": "嘗試太多次，請稍後再試。",
    "err_cross_site": "這個表單只能從授權頁本身送出。",
    "err_client": "用戶端設定有誤：{reason}",
    "err_param": "授權請求的參數有誤，請回到應用程式重新開始。",
}
SCOPE_TEXT = {
    "doc4ai:read": "搜尋和閱讀文件庫",
    "doc4ai:convert": "上傳文件並轉檔",
    "doc4ai:convert:local": "直接轉換電腦上的檔案",
    "doc4ai:manage": "取消工作、重新轉檔",
}
SCOPE_DETAIL = {
    "doc4ai:read": "搜尋、閱讀已經轉好的文件，查看文件資訊和轉檔進度。只能看，不會改動任何內容。",
    "doc4ai:convert": "把文件傳進 Doc4AI 轉成 Markdown，轉好的文件會加入你的文件庫。",
    "doc4ai:convert:local": "不用上傳，直接讀取這台電腦上的檔案來轉換。只限設定裡允許的資料夾。",
    "doc4ai:manage": "可以中止排隊或進行中的轉檔，也可以重新轉換文件庫裡的文件，轉好後會取代舊的結果。",
}
HIGH_RISK = frozenset({"doc4ai:convert:local", "doc4ai:manage"})
REQUIRED_SCOPE = "doc4ai:read"
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


# the web app's tokens (web/src/index.css) and BrandMark, copied: this page may load nothing, so it cannot share the bundle
_STYLE = """
:root{color-scheme:light dark;--canvas:oklch(0.975 0.002 250);--fg:oklch(0.2 0.01 260);--card:oklch(1 0 0);
--primary:oklch(0.51 0.19 270);--on-primary:oklch(0.99 0 0);--muted:oklch(0.955 0.004 260);--muted-fg:oklch(0.5 0.012 260);
--border:oklch(0.915 0.004 260);--input:oklch(0.885 0.005 260);--ring:oklch(0.51 0.19 270 / 55%);--warn:oklch(0.58 0.13 65);
--shadow:0 1px 0 oklch(0 0 0 / 2.5%),0 1px 2px oklch(0 0 0 / 4%)}
@media (prefers-color-scheme: dark){:root{--canvas:oklch(0.155 0.004 260);--fg:oklch(0.93 0.004 260);--card:oklch(0.19 0.005 260);
--primary:oklch(0.72 0.14 272);--on-primary:oklch(0.16 0.02 270);--muted:oklch(0.235 0.006 260);--muted-fg:oklch(0.68 0.01 260);
--border:oklch(1 0 0 / 8.5%);--input:oklch(1 0 0 / 13%);--ring:oklch(0.72 0.14 272 / 55%);--warn:oklch(0.82 0.13 80);
--shadow:0 0 0 1px oklch(1 0 0 / 1.5%)}}
*{box-sizing:border-box}html,body{margin:0;background:var(--canvas);color:var(--fg)}
body{font:14px/1.55 "Geist Variable",ui-sans-serif,system-ui,"Segoe UI","PingFang TC","Microsoft JhengHei UI","Microsoft JhengHei","Noto Sans TC",sans-serif;
-webkit-font-smoothing:antialiased}
main{max-width:30rem;margin:0 auto;padding:clamp(1rem,6vh,3.5rem) 1rem 2rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;box-shadow:var(--shadow);overflow:hidden}
.head{padding:1.75rem 1.5rem 1.25rem;text-align:center;border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;justify-content:center;margin-bottom:1rem}
.tile{display:flex;align-items:center;justify-content:center;width:44px;height:44px;border:1px solid var(--border);border-radius:10px;background:var(--card)}
.tile svg{width:26px;height:26px}.peer{background:var(--muted);font-size:18px;font-weight:600}
.wire{width:36px;border-top:1.5px dashed var(--input)}
h1{font-size:18px;line-height:1.3;font-weight:600;letter-spacing:-.01em;margin:0 0 .25rem}
.lead{margin:0;color:var(--muted-fg);overflow-wrap:anywhere}
.body{padding:1.25rem 1.5rem}
h2{font-size:14px;font-weight:600;margin:0}.note{color:var(--muted-fg);font-size:13px;margin:0;overflow-wrap:anywhere}
.scopes{list-style:none;margin:.75rem 0 0;padding:0;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.scopes li+li{border-top:1px solid var(--border)}
.scopes label{display:flex;gap:.625rem;align-items:flex-start;padding:.625rem .75rem;cursor:pointer}
.scopes label:hover{background:var(--muted)}.scopes label.fixed{cursor:default}.scopes label.fixed:hover{background:none}
input[type=checkbox]{width:16px;height:16px;margin:2px 0 0;flex:none;accent-color:var(--primary)}
.scope-title{font-weight:500}.scope-detail{display:block;color:var(--muted-fg);font-size:13px;line-height:1.5}
.scope-id{display:block;margin-top:.125rem;color:var(--muted-fg);font-size:11px;opacity:.8}
.tag{margin-left:.375rem;padding:0 .375rem;border-radius:4px;background:var(--muted);color:var(--muted-fg);font-size:11px}
.attn{display:inline-block;margin-left:.3rem;color:var(--warn);vertical-align:-2px;line-height:0}.attn svg{width:14px;height:14px}
.limits{margin-top:.75rem}
.tech{margin-top:1rem;padding-top:.875rem;border-top:1px solid var(--border);font-size:12px;color:var(--muted-fg)}
.tech h3{font-size:12px;font-weight:500;margin:0 0 .375rem}
dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:.25rem .875rem}dd{margin:0;color:var(--fg);overflow-wrap:anywhere;word-break:break-word}
dd .note{font-size:12px}code{font-family:ui-monospace,"Cascadia Mono",Consolas,"Microsoft JhengHei UI",monospace;font-size:.95em;overflow-wrap:anywhere}
.foot{padding:1rem 1.5rem;background:var(--canvas);border-top:1px solid var(--border)}
.foot label{display:block;font-size:13px;font-weight:500;margin-bottom:.25rem}
input[type=password]{width:100%;height:36px;padding:0 .625rem;margin-bottom:.75rem;border:1px solid var(--input);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:.625rem}
button{height:36px;font:inherit;font-weight:500;border-radius:8px;border:1px solid var(--input);background:var(--card);color:var(--fg);cursor:pointer}
button:hover{background:var(--muted)}button.primary{background:var(--primary);border-color:var(--primary);color:var(--on-primary)}
button.primary:hover{background:color-mix(in oklch,var(--primary) 85%,var(--card))}
button:focus-visible,input:focus-visible{outline:2px solid var(--ring);outline-offset:2px}
.foot .note{margin-top:.625rem;text-align:center;font-size:12px}
.made{margin:1rem 0 0;text-align:center;font-size:12px;color:var(--muted-fg)}.made b{font-weight:600;color:var(--fg)}
@media (max-width:420px){.head,.body,.foot{padding-left:1rem;padding-right:1rem}dl{grid-template-columns:1fr;gap:0}dd{margin-bottom:.375rem}}
"""
_MARK = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
         '<path d="M6 2.5h8.2L19 7.3V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 20V4A1.5 1.5 0 0 1 6.5 2.5Z" fill="var(--primary)"/>'
         '<path d="M14 2.5V6a1.5 1.5 0 0 0 1.5 1.5H19" fill="none" stroke="var(--on-primary)" stroke-opacity=".55" stroke-width="1.2"/>'
         '<path d="M8.5 11.5h7M8.5 14.5h7M8.5 17.5h4" stroke="var(--on-primary)" stroke-width="1.5" stroke-linecap="round"/></svg>')
_ALERT = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true">'
          '<circle cx="12" cy="12" r="9.5"/><path d="M12 7.5v5.5M12 16.5h.01"/></svg>')


def _page(title: str, inner: str) -> str:
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} · Doc4AI Studio</title>'
            f'<style>{_STYLE}</style></head><body><main>{inner}<p class="made"><b>Doc4AI</b> Studio</p></main></body></html>')


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
    def scope_text(s: str, tag: str) -> str:
        """What the app may do first, in plain words; the scope id is a footnote for whoever needs it."""
        return (f'<span><span class="scope-title">{e(SCOPE_TEXT.get(s, s))}</span>{tag}'
                f'<span class="scope-detail">{e(SCOPE_DETAIL.get(s, ""))}</span><code class="scope-id">{e(s)}</code></span>')

    rows = []
    for s in v.requested_scopes:
        mark = f'<span class="attn" role="img" title="{e(TEXT["attention"])}">{_ALERT}</span>' if s in HIGH_RISK else ""
        if s == REQUIRED_SCOPE:
            # every connection needs read: shown ticked and fixed, always sent (no JS on this page; a disabled
            # checkbox is never submitted, so a hidden field carries it) - unticking the rest cannot end on a 400
            required = f'<span class="tag">{e(TEXT["required"])}</span>'
            rows.append(f'<li><label class="fixed"><input type="checkbox" value="{e(s)}" checked disabled>{scope_text(s, required)}</label>'
                        f'<input type="hidden" name="scopes" value="{e(s)}"></li>')
            continue
        checked = " checked" if s in v.checked_scopes else ""
        rows.append(f'<li><label><input type="checkbox" name="scopes" value="{e(s)}"{checked}>{scope_text(s, mark)}</label></li>')
    if v.needs_token:
        cred = (f'<label for="server_token">{e(TEXT["server_token"])}</label>'
                f'<input type="password" name="server_token" id="server_token" autocomplete="current-password" required>')
        after = ""
    else:
        cred = ""
        after = f'<p class="note">{e(TEXT["loopback_only"])}</p>'
    tile = e(v.client_name.strip()[:1].upper() or "?")
    inner = (f'<div class="card"><header class="head"><div class="brand"><span class="tile peer" aria-hidden="true">{tile}</span>'
             f'<span class="wire"></span><span class="tile">{_MARK}</span></div>'
             f'<h1>{e(TEXT["title"])}</h1><p class="lead">{e(TEXT["headline"].format(name=v.client_name))}</p></header>'
             f'<form method="post" action="/oauth/authorize">'
             f'<input type="hidden" name="request" value="{e(v.request_blob)}">'
             f'<div class="body"><h2>{e(TEXT["scopes"])}</h2><p class="note">{e(TEXT["intro"])}</p><ul class="scopes">{"".join(rows)}</ul>'
             f'<p class="note limits">{e(TEXT["scopes_note"])}{e(TEXT["limits"])}</p>'
             # small print, never collapsed: the source and the callback are how a user spots a request that lies
             f'<div class="tech"><h3>{e(TEXT["details"])}</h3>'
             f'<dl><dt>{e(TEXT["client"])}</dt><dd>{name}{e(TEXT["unverified"])}</dd>'
             f'<dt>{e(TEXT["source"])}</dt><dd>{source}</dd>'
             f'<dt>{e(TEXT["redirect"])}</dt><dd><code>{e(v.redirect_uri)}</code>{note}</dd>'
             f'<dt>{e(TEXT["server"])}</dt><dd><code>{e(v.issuer)}</code></dd></dl></div></div>'
             f'<div class="foot">{cred}<div class="actions"><button type="submit" name="decision" value="deny">{e(TEXT["deny"])}</button>'
             f'<button type="submit" name="decision" value="allow" class="primary">{e(TEXT["allow"])}</button></div>{after}</div>'
             f"</form></div>")
    return _page(TEXT["title"], inner)


def render_error(title: str, reason: str | None = None) -> str:
    e = html.escape
    detail = f'<p class="note">{e(reason)}</p>' if reason else ""
    return _page(TEXT["error_title"], f'<div class="card"><header class="head"><div class="brand"><span class="tile">{_MARK}</span></div>'
                                      f'<h1>{e(TEXT["error_title"])}</h1><p class="lead">{e(title)}</p>{detail}</header></div>')
