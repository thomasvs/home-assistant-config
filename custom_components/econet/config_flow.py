"""Config flow for Rheem EcoNet with SSL workaround."""
from __future__ import annotations
import logging
import ssl
import pyeconet.api

_LOGGER = logging.getLogger(__name__)

# Ensure pyeconet SSL context is patched during config flow
try:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    pyeconet.api._SSL_CONTEXT = _ssl_ctx
except Exception:
    pass

from homeassistant.components.econet.config_flow import EcoNetFlowHandler

__all__ = ["EcoNetFlowHandler"]
