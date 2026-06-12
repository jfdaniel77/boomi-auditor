"""Boomi AtomSphere API client.

Pagination is handled transparently: paginate() follows the queryToken cursor
and returns the merged record list, so callers never deal with pages.
Retries 429/5xx with exponential backoff (1s → 2s → 4s by default).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn

from .config import Config
from .redaction import scrub_sensitive_value

err_console = Console(stderr=True)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BoomiAPIError(Exception):
    """Generic Boomi API failure with a human-readable message."""


class BoomiAuthError(BoomiAPIError):
    """Authentication failed (401)."""


class RateLimitError(BoomiAPIError):
    """Retries exhausted on 429/5xx responses."""


class BoomiClient:
    def __init__(
        self,
        config: Config,
        *,
        max_retries: int = 3,
        delay: float = 0.1,
        show_progress: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.delay = delay
        self.show_progress = show_progress
        # Per-run response cache: an audit treats the account as an immutable
        # snapshot, so identical queries (e.g. `all` running four sub-audits
        # that each need Environment/ComponentMetadata) are fetched once.
        # The lock matters because paginate() is called from worker threads.
        self._cache: dict[tuple, list[dict]] = {}
        self._cache_lock = threading.Lock()
        # Boomi API tokens authenticate as "BOOMI_TOKEN.<username>"; a plain
        # username with a token yields 403. Add the prefix if it's missing.
        username = config.username
        if not username.startswith("BOOMI_TOKEN."):
            username = f"BOOMI_TOKEN.{username}"
        self._http = httpx.Client(
            base_url=f"{config.base_url.rstrip('/')}/{config.account_id}",
            auth=(username, config.api_token),
            headers={"Accept": "application/json"},
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BoomiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        response: httpx.Response | None = None
        backoff = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                # Dropped connections, keep-alive races, and timeouts are as
                # transient as a 503 — retry them too (queries are read-only,
                # so resending is safe), then fail with a readable message.
                if attempt == self.max_retries:
                    raise BoomiAPIError(
                        f"❌ Network error talking to Boomi: {exc} — "
                        f"retried {self.max_retries} times. "
                        "Check connectivity and try again."
                    ) from exc
                time.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)
                continue
            if response.status_code not in RETRYABLE_STATUS:
                break
            if attempt == self.max_retries:
                if response.status_code == 429:
                    raise RateLimitError(
                        f"❌ Rate limit hit — retried {self.max_retries} times. "
                        "Try again later or use --delay flag"
                    )
                raise BoomiAPIError(
                    f"❌ Boomi API error {response.status_code} — "
                    f"retried {self.max_retries} times. Try again later."
                )
            # Boomi throttles long batch jobs with 429/503; prefer its own
            # Retry-After hint, otherwise back off exponentially (capped —
            # patience beats losing a 15-minute run to a transient blip).
            retry_after = response.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else backoff
            time.sleep(min(wait, 30.0))
            backoff = min(backoff * 2, 30.0)

        assert response is not None
        if response.status_code == 401:
            raise BoomiAuthError("❌ Auth failed — check your BOOMI_API_TOKEN")
        if response.status_code == 403:
            raise BoomiAuthError(
                "❌ Access denied (403) — check that your API token is valid, was created "
                "for this account, and that your user has the 'API Access' privilege in Boomi."
            )
        if response.status_code == 404:
            raise BoomiAPIError(f"❌ Not found: {path} — check your account ID and the API path")
        if response.status_code >= 400:
            # Boomi 4xx responses carry a useful explanation in the body — surface it.
            detail = scrub_sensitive_value(response.text.strip()[:300])
            suffix = f": {detail}" if detail else ""
            raise BoomiAPIError(f"❌ Boomi API error {response.status_code} for {path}{suffix}")
        try:
            return response.json()
        except ValueError as exc:
            raise BoomiAPIError(
                "❌ Malformed response from Boomi API — expected JSON. "
                "Check the Accept header and API availability."
            ) from exc

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def query(self, object_type: str, query_filter: dict | None = None) -> dict:
        body: dict = {"QueryFilter": query_filter} if query_filter else {}
        return self._request("POST", f"/{object_type}/query", json=body)

    def query_more(self, object_type: str, token: str) -> dict:
        return self._request(
            "POST",
            f"/{object_type}/queryMore",
            content=token,
            headers={"Content-Type": "text/plain"},
        )

    def paginate(
        self,
        object_type: str,
        query_filter: dict | None = None,
        *,
        max_records: int = 0,
        show_progress: bool | None = None,
        description: str | None = None,
    ) -> list[dict]:
        """Fetch every page of a query and return the merged record list.

        max_records=0 means unlimited. When the cap is hit a warning is
        printed to stderr so piped stdout stays clean. Repeated identical
        queries within one client lifetime are served from cache.

        description labels the spinner/progress bar; it defaults to the
        object type, which is ambiguous when one command queries the same
        object twice with different filters (e.g. ComponentMetadata for
        processes and then connectors).
        """
        if show_progress is None:
            show_progress = self.show_progress
        label = description or f"Fetching {object_type}..."

        cache_key = (object_type, json.dumps(query_filter, sort_keys=True), max_records)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        if show_progress:
            # The progress bar below only appears once pagination starts; the
            # spinner covers the first request so slow fetches never look hung.
            with err_console.status(label):
                first = self.query(object_type, query_filter)
        else:
            first = self.query(object_type, query_filter)
        records: list[dict] = list(first.get("result") or [])
        token = first.get("queryToken")
        total = int(first.get("numberOfResults") or len(records))

        progress: Progress | None = None
        task_id = None
        if token and show_progress:
            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed} records"),
                console=err_console,
                transient=True,
            )
            progress.start()
            task_id = progress.add_task(
                label, total=total or None, completed=len(records)
            )

        try:
            while token and (max_records == 0 or len(records) < max_records):
                time.sleep(self.delay)
                page = self.query_more(object_type, token)
                page_records = page.get("result") or []
                records.extend(page_records)
                next_token = page.get("queryToken")
                if next_token == token and not page_records:
                    break  # defensive: same cursor with no new data would loop forever
                token = next_token
                if progress is not None and task_id is not None:
                    progress.update(task_id, completed=len(records))
        finally:
            if progress is not None:
                progress.stop()

        if max_records and (token or len(records) > max_records):
            records = records[:max_records]
            err_console.print(
                f"⚠️  Results capped at {max_records}. Use --max-records 0 for all records."
            )
        with self._cache_lock:
            self._cache[cache_key] = records
        return list(records)
