import asyncio
import hashlib
import ssl
from contextlib import suppress


class CameraTLSContext(ssl.SSLContext):
    def wrap_socket(self, *args, **kwargs):
        raise ssl.SSLError("Camera connections require the async TLS transport")


def _tls_context() -> ssl.SSLContext:
    context = CameraTLSContext(ssl.PROTOCOL_TLS_CLIENT)
    # Camera identity is its explicitly approved leaf fingerprint, independent
    # of issuer and host name (cameras commonly have self-signed certificates).
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _peer_fingerprint(connection) -> str:
    cert = connection.getpeercert(binary_form=True) if connection else None
    if not cert:
        raise ssl.SSLCertVerificationError("Camera did not present a certificate")
    return hashlib.sha256(cert).hexdigest()


async def discover_fingerprint(host: str, port: int) -> str:
    # Pairing only. Never send an HTTP request over this untrusted connection.
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=_tls_context()), timeout=5,
    )
    try:
        return _peer_fingerprint(writer.get_extra_info("ssl_object"))
    finally:
        writer.close()
        with suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), timeout=1)


def pinned_context(fingerprint: str) -> ssl.SSLContext:
    # httpx's async transport negotiates TLS with wrap_bio. Checking inside
    # the handshake covers every new connection before any HTTP bytes are sent.
    class PinnedConnection(ssl.SSLObject):
        def do_handshake(self):
            super().do_handshake()
            if _peer_fingerprint(self) != fingerprint:
                raise ssl.SSLCertVerificationError(
                    "Camera certificate changed. Review the fingerprint in Settings."
                )

    context = _tls_context()
    context.sslobject_class = PinnedConnection
    return context
