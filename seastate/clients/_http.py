"""HTTP transport via QGIS's own network stack.

Uses QgsBlockingNetworkRequest so requests honor the user's proxy and SSL
settings and can run on background (QgsTask) threads. Avoids urllib, which
static scanners flag for permitting non-HTTP schemes.
"""

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest


def fetch_bytes(url, timeout_ms=30000):
    """GET a URL and return the response body as bytes. Raises on failure."""
    request = QgsBlockingNetworkRequest()
    req = QNetworkRequest(QUrl(url))
    req.setTransferTimeout(timeout_ms)
    request.get(req)
    reply = request.reply()
    content = bytes(reply.content()) if reply is not None else b""
    if not content:
        raise RuntimeError(request.errorMessage() or f"empty response from {url}")
    return content
