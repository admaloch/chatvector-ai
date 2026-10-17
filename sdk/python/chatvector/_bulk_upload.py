"""Client-side bulk document upload orchestration."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ._common import retry_after_seconds
from .exceptions import ChatVectorAPIError, ChatVectorRateLimitError
from .models import BulkUploadResult, BulkUploadSummary, DocumentResponse

UploadFn = Callable[[str, str | None], DocumentResponse]
AsyncUploadFn = Callable[[str, str | None], Awaitable[DocumentResponse]]
ProgressFn = Callable[[BulkUploadResult, BulkUploadSummary], None]
IdempotencyKeyFn = Callable[[str], str | None]
WaitReadyFn = Callable[[str], None]
AsyncWaitReadyFn = Callable[[str], Awaitable[None]]


def collect_directory_upload_paths(
    directory_path: str,
    *,
    pattern: str = "*",
    recursive: bool = True,
) -> list[Path]:
    """
    List regular files under ``directory_path`` that are safe to upload.

    Rejects path segments containing ``..`` and symlinks whose targets escape
    the resolved directory root.
    """
    if ".." in Path(directory_path).parts:
        raise ValueError("directory_path must not contain '..' path segments.")

    directory = Path(directory_path)
    if not directory.is_dir():
        raise FileNotFoundError(f"Upload directory was not found: {directory_path}")

    root = directory.resolve()
    globber = root.rglob if recursive else root.glob
    paths: list[Path] = []
    for entry in sorted(globber(pattern)):
        if not entry.is_file():
            continue
        _assert_path_under_root(root, entry)
        paths.append(entry)
    return paths


def _assert_path_under_root(root: Path, path: Path) -> None:
    if ".." in path.parts:
        raise ValueError(f"Rejected path traversal in upload path: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Rejected upload path outside directory root: {path}",
        ) from exc


def _rate_limit_sleep_seconds(exc: ChatVectorRateLimitError) -> float:
    if exc.response is not None:
        return min(max(retry_after_seconds(exc.response), 0.0), 60.0)
    return 1.0


def _upload_with_optional_rate_limit_retry(
    upload_callable: Callable[[], DocumentResponse],
    *,
    retry_on_rate_limit: bool,
    max_rate_limit_retries: int,
) -> DocumentResponse:
    attempts = 0
    while True:
        try:
            return upload_callable()
        except ChatVectorRateLimitError as exc:
            if not retry_on_rate_limit or attempts >= max_rate_limit_retries:
                raise
            attempts += 1
            time.sleep(_rate_limit_sleep_seconds(exc))


async def _upload_with_optional_rate_limit_retry_async(
    upload_callable: Callable[[], Awaitable[DocumentResponse]],
    *,
    retry_on_rate_limit: bool,
    max_rate_limit_retries: int,
) -> DocumentResponse:
    attempts = 0
    while True:
        try:
            return await upload_callable()
        except ChatVectorRateLimitError as exc:
            if not retry_on_rate_limit or attempts >= max_rate_limit_retries:
                raise
            attempts += 1
            await asyncio.sleep(_rate_limit_sleep_seconds(exc))


def _build_summary(results: Sequence[BulkUploadResult]) -> BulkUploadSummary:
    success_count = sum(1 for item in results if item.success)
    failure_count = len(results) - success_count
    return BulkUploadSummary(
        count=len(results),
        success_count=success_count,
        failure_count=failure_count,
        results=list(results),
    )


def execute_bulk_uploads(
    file_paths: Sequence[str],
    upload_fn: UploadFn,
    *,
    concurrency: int,
    fail_fast: bool = False,
    retry_on_rate_limit: bool = False,
    max_rate_limit_retries: int = 1,
    idempotency_key_for: IdempotencyKeyFn | None = None,
    on_progress: ProgressFn | None = None,
    wait_ready_fn: WaitReadyFn | None = None,
) -> BulkUploadSummary:
    """Upload many files with bounded concurrency (sync)."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1.")
    if max_rate_limit_retries < 0:
        raise ValueError("max_rate_limit_retries must be non-negative.")

    normalized_paths = [str(Path(path)) for path in file_paths]
    results: list[BulkUploadResult | None] = [None] * len(normalized_paths)
    lock = threading.Lock()
    abort = threading.Event()

    def process_index(index: int, file_path: str) -> None:
        if fail_fast and abort.is_set():
            result = BulkUploadResult(
                file_path=file_path,
                success=False,
                error=ChatVectorAPIError("Bulk upload aborted after fail_fast failure."),
            )
            with lock:
                results[index] = result
                summary = _build_summary(
                    [item if item is not None else result for item in results],
                )
                if on_progress is not None:
                    on_progress(result, summary)
            return

        idempotency_key = (
            idempotency_key_for(file_path) if idempotency_key_for is not None else None
        )

        def single_upload() -> DocumentResponse:
            return upload_fn(file_path, idempotency_key)

        try:
            document = _upload_with_optional_rate_limit_retry(
                single_upload,
                retry_on_rate_limit=retry_on_rate_limit,
                max_rate_limit_retries=max_rate_limit_retries,
            )
            if wait_ready_fn is not None and document.document_id:
                wait_ready_fn(document.document_id)
            result = BulkUploadResult(
                file_path=file_path,
                success=True,
                document=document,
            )
        except ChatVectorAPIError as exc:
            result = BulkUploadResult(
                file_path=file_path,
                success=False,
                error=exc,
            )
            if fail_fast:
                abort.set()

        with lock:
            results[index] = result
            materialized = [
                item
                if item is not None
                else BulkUploadResult(
                    file_path=normalized_paths[i],
                    success=False,
                    error=ChatVectorAPIError("Bulk upload did not run."),
                )
                for i, item in enumerate(results)
            ]
            summary = _build_summary(materialized)
            if on_progress is not None:
                on_progress(result, summary)

    if not normalized_paths:
        return _build_summary([])

    worker_count = min(concurrency, len(normalized_paths))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(process_index, index, file_path)
            for index, file_path in enumerate(normalized_paths)
        ]
        for future in futures:
            future.result()

    finalized = [
        item
        if item is not None
        else BulkUploadResult(
            file_path=normalized_paths[i],
            success=False,
            error=ChatVectorAPIError("Bulk upload did not run."),
        )
        for i, item in enumerate(results)
    ]
    return _build_summary(finalized)


async def execute_bulk_uploads_async(
    file_paths: Sequence[str],
    upload_fn: AsyncUploadFn,
    *,
    concurrency: int,
    fail_fast: bool = False,
    retry_on_rate_limit: bool = False,
    max_rate_limit_retries: int = 1,
    idempotency_key_for: IdempotencyKeyFn | None = None,
    on_progress: ProgressFn | None = None,
    wait_ready_fn: AsyncWaitReadyFn | None = None,
) -> BulkUploadSummary:
    """Upload many files with bounded concurrency (async)."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1.")
    if max_rate_limit_retries < 0:
        raise ValueError("max_rate_limit_retries must be non-negative.")

    normalized_paths = [str(Path(path)) for path in file_paths]
    if not normalized_paths:
        return _build_summary([])

    results: list[BulkUploadResult | None] = [None] * len(normalized_paths)
    lock = asyncio.Lock()
    abort = asyncio.Event()
    semaphore = asyncio.Semaphore(min(concurrency, len(normalized_paths)))

    async def process_index(index: int, file_path: str) -> None:
        async with semaphore:
            if fail_fast and abort.is_set():
                result = BulkUploadResult(
                    file_path=file_path,
                    success=False,
                    error=ChatVectorAPIError(
                        "Bulk upload aborted after fail_fast failure.",
                    ),
                )
                async with lock:
                    results[index] = result
                    if on_progress is not None:
                        on_progress(result, _build_summary(_materialize_results()))
                return

            idempotency_key = (
                idempotency_key_for(file_path)
                if idempotency_key_for is not None
                else None
            )

            async def single_upload() -> DocumentResponse:
                return await upload_fn(file_path, idempotency_key)

            try:
                document = await _upload_with_optional_rate_limit_retry_async(
                    single_upload,
                    retry_on_rate_limit=retry_on_rate_limit,
                    max_rate_limit_retries=max_rate_limit_retries,
                )
                if wait_ready_fn is not None and document.document_id:
                    await wait_ready_fn(document.document_id)
                result = BulkUploadResult(
                    file_path=file_path,
                    success=True,
                    document=document,
                )
            except ChatVectorAPIError as exc:
                result = BulkUploadResult(
                    file_path=file_path,
                    success=False,
                    error=exc,
                )
                if fail_fast:
                    abort.set()

            async with lock:
                results[index] = result
                if on_progress is not None:
                    on_progress(result, _build_summary(_materialize_results()))

    def _materialize_results() -> list[BulkUploadResult]:
        return [
            item
            if item is not None
            else BulkUploadResult(
                file_path=normalized_paths[i],
                success=False,
                error=ChatVectorAPIError("Bulk upload did not run."),
            )
            for i, item in enumerate(results)
        ]

    await asyncio.gather(
        *(process_index(index, file_path) for index, file_path in enumerate(normalized_paths)),
    )
    return _build_summary(_materialize_results())
