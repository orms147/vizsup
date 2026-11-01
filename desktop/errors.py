"""One sentence for an error a person reads: the network, a refused key and a busy server in
plain Vietnamese ("Lỗi rút gọn: APIConnectionError: Connection error." meant nothing to the
owner, 2026-11-01); our own messages — already Vietnamese — as they are; anything else keeps
its type name, which is what makes it debuggable."""
from __future__ import annotations

NETWORK = ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
           "ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException", "RemoteProtocolError",
           "ConnectionError", "ConnectionResetError", "ConnectionRefusedError", "gaierror")
REFUSED_KEY = ("AuthenticationError", "PermissionDeniedError")
BUSY = ("RateLimitError", "InternalServerError", "ServiceUnavailableError", "OverloadedError")

OFFLINE = "Không kết nối được máy chủ — kiểm tra mạng rồi thử lại."
BAD_KEY = "Khóa API bị từ chối — kiểm tra ở Cài đặt → Khóa API."
SERVER_BUSY = "Máy chủ AI đang bận hoặc giới hạn lượt gọi — đợi một lát rồi thử lại."


def user_message(exc: BaseException) -> str:
    """The sentence to show for ``exc``. Pure."""
    name = type(exc).__name__
    text = str(exc).strip()
    low = text.casefold()
    if name in NETWORK or "getaddrinfo" in low or "name or service not known" in low:
        return OFFLINE
    if name in REFUSED_KEY or "status code 401" in low or "invalid api key" in low:
        return BAD_KEY
    if name in BUSY or "status code 429" in low or "status code 503" in low:
        return SERVER_BUSY
    if type(exc) in (RuntimeError, ValueError) and text:
        return text
    return f"{name}: {text}" if text else name
