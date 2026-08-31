"""climate platform for Rheem EcoNet."""
from __future__ import annotations
import ssl
import pyeconet.api

try:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    pyeconet.api._SSL_CONTEXT = _ssl_ctx
except Exception:
    pass

try:
    from homeassistant.components.econet.climate import async_setup_entry
    __all__ = ["async_setup_entry"]
except ImportError:
    pass
