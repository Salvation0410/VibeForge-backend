from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import socket
import tempfile
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from ai_service.config import Settings


_knowledge_request_active = contextvars.ContextVar("knowledge_request_active", default=False)


class _SuppressSignedUrlLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _knowledge_request_active.get()


for _logger_name in (
    "httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
    "httpcore.proxy", "httpcore.socks",
):
    logging.getLogger(_logger_name).addFilter(_SuppressSignedUrlLogs())


class KnowledgeDownloadError(RuntimeError):
    """Stable public error; never carries a signed URL or provider exception."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DownloadedKnowledgeFile:
    """Caller owns the temporary file and must call cleanup or use async with."""

    def __init__(self, path: Path, sha256: str, size: int):
        self.path = path
        self.sha256 = sha256
        self.size = size

    def __repr__(self) -> str:
        return f"DownloadedKnowledgeFile(sha256={self.sha256!r}, size={self.size})"

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)

    async def __aenter__(self) -> "DownloadedKnowledgeFile":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.cleanup()


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [entry[4][0] for entry in addresses]


class KnowledgeDownloader:
    MAX_REDIRECTS = 3

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], Awaitable[list[str]]] | None = None,
    ):
        self._settings = settings
        self._transport = transport
        self._resolver = resolver or _resolve
        self._allowed_hosts = {
            host.strip().lower() for host in settings.rag_oss_allowed_hosts.split(",") if host.strip()
        }

    async def _validated_target(self, url: str) -> tuple[str, str]:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TARGET_REJECTED") from None
        if (
            parsed.scheme != "https" or not host or host != host.lower()
            or host not in self._allowed_hosts or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)
            or not parsed.netloc or "\\" in url or any(ord(c) < 32 for c in url)
        ):
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TARGET_REJECTED")
        try:
            addresses = await self._resolver(host)
        except (OSError, ValueError, asyncio.TimeoutError):
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_DNS_FAILED") from None
        if not addresses:
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_DNS_FAILED")
        try:
            ips = [ipaddress.ip_address(address) for address in addresses]
        except ValueError:
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_DNS_FAILED") from None
        if not all(ip.is_global and not ip.is_multicast and not ip.is_reserved for ip in ips):
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TARGET_REJECTED")
        selected = ips[0]
        target_host = f"[{selected.compressed}]" if selected.version == 6 else selected.compressed
        target = f"https://{target_host}{':' + str(port) if port else ''}{parsed.path or '/'}"
        if parsed.query:
            target += f"?{parsed.query}"
        return host, target

    async def download(
        self, url: str, *, expected_sha256: str, max_bytes: int
    ) -> DownloadedKnowledgeFile:
        if (
            not isinstance(url, str)
            or not url
            or not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None
            or not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
            or max_bytes < 1 or max_bytes > self._settings.rag_download_max_bytes
        ):
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_INVALID_REQUEST")
        path: Path | None = None
        completed = False
        log_token = _knowledge_request_active.set(True)
        timeout = httpx.Timeout(
            connect=self._settings.rag_download_connect_timeout_seconds,
            read=self._settings.rag_download_read_timeout_seconds,
            write=self._settings.rag_download_connect_timeout_seconds,
            pool=self._settings.rag_download_connect_timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=timeout, follow_redirects=False, trust_env=False
            ) as client:
                for redirects in range(self.MAX_REDIRECTS + 1):
                    host, target = await self._validated_target(url)
                    request = client.build_request(
                        "GET", target, headers={"Host": host, "Accept-Encoding": "identity"},
                        extensions={"sni_hostname": host},
                    )
                    async with client.stream("GET", request.url, headers=request.headers,
                                             extensions=request.extensions) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if redirects == self.MAX_REDIRECTS:
                                raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_REDIRECT_LIMIT")
                            location = response.headers.get("Location")
                            if not location:
                                raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TARGET_REJECTED")
                            try:
                                url = urljoin(url, location)
                            except ValueError:
                                raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TARGET_REJECTED") from None
                            continue
                        if response.status_code != 200:
                            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_HTTP_ERROR")
                        declared = response.headers.get("Content-Length")
                        if declared is not None:
                            try:
                                declared_size = int(declared)
                            except ValueError:
                                raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_HTTP_ERROR") from None
                            if declared_size < 0:
                                raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_HTTP_ERROR")
                            if declared_size > max_bytes:
                                raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TOO_LARGE")
                        fd, name = tempfile.mkstemp(prefix="knowledge-", suffix=".tmp")
                        path = Path(name)
                        digest = hashlib.sha256()
                        size = 0
                        with os.fdopen(fd, "wb") as output:
                            # MockTransport can return an already-buffered Response; wire responses use raw bytes.
                            blocks = (response.aiter_bytes(chunk_size=65536) if response.is_stream_consumed
                                      else response.aiter_raw(chunk_size=65536))
                            async for block in blocks:
                                size += len(block)
                                if size > max_bytes:
                                    raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TOO_LARGE")
                                output.write(block)
                                digest.update(block)
                        actual = digest.hexdigest()
                        if not hmac.compare_digest(actual, expected_sha256.lower()):
                            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_HASH_MISMATCH")
                        completed = True
                        return DownloadedKnowledgeFile(path, actual, size)
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_REDIRECT_LIMIT")
        except KnowledgeDownloadError:
            raise
        except httpx.TimeoutException:
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TIMEOUT") from None
        except (httpx.RequestError, OSError):
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_NETWORK_ERROR") from None
        except (httpx.InvalidURL, ValueError, TypeError):
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TARGET_REJECTED") from None
        finally:
            _knowledge_request_active.reset(log_token)
            if path is not None and not completed:
                path.unlink(missing_ok=True)
