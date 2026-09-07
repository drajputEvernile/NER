"""Azure Document Intelligence prebuilt-read OCR."""

from __future__ import annotations

import json
import logging
import random
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Callable

logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from env_loader import load_sibling_config
from OCR import document as ocr_document

_cfg = load_sibling_config(__file__, "azure_ocr_folder_config")
enabled = bool(_cfg.enabled)
RAW_Read_Path = _cfg.RAW_Read_Path
Azure_OCR_Output_path = _cfg.Azure_OCR_Output_path
record_output_dir = _cfg.record_output_dir

_CLIENTS: dict[tuple[str, str, int], object] = {}
_CLIENTS_LOCK = threading.Lock()


@dataclass
class Settings:
    azure_document_intelligence_endpoint: str | None
    azure_document_intelligence_key: str | None
    azure_poll_timeout_seconds: int = 180

    @property
    def azure_configured(self) -> bool:
        endpoint = (self.azure_document_intelligence_endpoint or "").strip()
        key = (self.azure_document_intelligence_key or "").strip()
        return bool(endpoint and key)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        azure_document_intelligence_endpoint=_cfg.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
        azure_document_intelligence_key=_cfg.AZURE_DOCUMENT_INTELLIGENCE_KEY,
        azure_poll_timeout_seconds=int(_cfg.AZURE_POLL_TIMEOUT_SECONDS or 180),
    )


def create_document_intelligence_client(endpoint: str, key: str, *, pool_size: int = 32):
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential
    from azure.core.pipeline.transport import RequestsTransport

    cache_key = (endpoint, key, pool_size)
    client = _CLIENTS.get(cache_key)
    if client is not None:
        return client
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(cache_key)
        if client is None:
            transport = RequestsTransport(connection_pool_maxsize=max(16, pool_size))
            client = DocumentIntelligenceClient(
                endpoint=endpoint,
                credential=AzureKeyCredential(key),
                transport=transport,
            )
            _CLIENTS[cache_key] = client
        return client


def await_poller(poller, timeout_seconds: float, poll_interval: float = 2.0):
    if not timeout_seconds or timeout_seconds <= 0:
        return poller.result()

    deadline = time.monotonic() + float(timeout_seconds)
    while not poller.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Azure analyze operation did not complete within {timeout_seconds}s "
                f"(last status={poller.status()!r})."
            )
        poller.wait(min(poll_interval, remaining))
    return poller.result()


def with_azure_retries(request: Callable):
    from azure.core.exceptions import HttpResponseError

    max_retries = 5
    base_delay = 2.0

    for attempt in range(max_retries):
        try:
            return request()
        except HttpResponseError as err:
            if err.status_code == 429:
                retry_after = err.response.headers.get("Retry-After") or err.response.headers.get("retry-after")
                delay = float(retry_after) if retry_after else (base_delay * (2**attempt) + random.uniform(0.1, 1.0))
                logger.warning(
                    "Azure 429 rate limit hit. Waiting %.2fs before retry (attempt %s/%s).",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            logger.exception(
                "Azure Document Intelligence request failed with HTTP response error: status_code=%s",
                err.status_code,
            )
            raise
        except Exception:
            logger.exception("Azure Document Intelligence request failed.")
            raise

    raise RuntimeError("Exhausted retries calling Azure Document Intelligence due to rate limiting (429).")


class AzureReadOcrExtractor:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def available(self) -> bool:
        return self.settings.azure_configured

    def extract_record_outputs(
        self, image_paths: list[Path], output_dir: Path, record_id: str, *, force: bool = False
    ) -> dict:
        if not self.available:
            raise RuntimeError("Azure Document Intelligence is not configured.")

        json_file = ocr_document.json_path(output_dir, record_id)
        if not force and ocr_document.cached_document(output_dir, record_id) is not None:
            logger.info("event=azure_read_cache_hit record=%s path=%s", record_id, json_file)
            return {
                "model": "prebuilt-read",
                "features": {"barcodes": True, "languages": True},
                "outputs": {"json": str(json_file)},
                "cached": True,
            }

        features = ["languages", "barcodes"]
        pages_out: list[dict] = []
        azure_started_at = perf_counter()
        for page_number, image_path in enumerate(image_paths, start=1):
            legacy = output_dir / f"{image_path.stem}.json"
            if not force and legacy.is_file():
                logger.info("event=azure_read_legacy_page record=%s page=%s", record_id, image_path.name)
                pages_out.append(ocr_document.load_legacy_page_file(legacy, page_number, image_path.name))
                continue
            logger.info("event=azure_read_page record=%s page=%s", record_id, image_path.name)
            pages_out.append(
                ocr_document.page_from_azure(
                    self._call_read_model(image_path, features=features),
                    page_number,
                    image_path.name,
                )
            )

        document = {
            "recordId": record_id,
            "model": "prebuilt-read",
            "pageCount": len(pages_out),
            "pages": pages_out,
        }
        json_file = ocr_document.write_document(output_dir, record_id, document)
        azure_ocr_seconds = round(perf_counter() - azure_started_at, 3)
        return {
            "model": "prebuilt-read",
            "features": {"barcodes": True, "languages": True},
            "azure_ocr_seconds": azure_ocr_seconds,
            "outputs": {"json": str(json_file)},
        }

    def _call_read_model(self, image_path: Path, *, features: list[str]) -> dict:
        def request() -> dict:
            endpoint = (self.settings.azure_document_intelligence_endpoint or "").strip()
            key = (self.settings.azure_document_intelligence_key or "").strip()
            client = create_document_intelligence_client(endpoint, key)
            with image_path.open("rb") as image_file:
                poller = client.begin_analyze_document(
                    "prebuilt-read",
                    body=image_file,
                    features=features,
                )
            result = await_poller(poller, self.settings.azure_poll_timeout_seconds)
            return result.as_dict() if hasattr(result, "as_dict") else json.loads(result.to_json())

        return with_azure_retries(request)
