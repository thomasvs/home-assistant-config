"""Rheem EcoNet integration with SSL certificate patch for ClearBlade backend."""
from __future__ import annotations
import logging
import ssl
import pyeconet.api

_LOGGER = logging.getLogger(__name__)

# Monkey-patch pyeconet SSL context to bypass distrusted legacy DigiCert G1 root
try:
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    pyeconet.api._SSL_CONTEXT = _ssl_ctx
    _LOGGER.info("Successfully patched pyeconet.api._SSL_CONTEXT for EcoNet / Friedrich")
except Exception as err:
    _LOGGER.warning("Could not patch pyeconet.api._SSL_CONTEXT: %s", err)

from homeassistant.components.econet import (
    DOMAIN,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)

__all__ = ["DOMAIN", "async_setup", "async_setup_entry", "async_unload_entry"]
