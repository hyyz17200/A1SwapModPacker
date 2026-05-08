from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Sequence

from .builder import build_packed_3mf
from .models import BuildOptions, BuildResult, PlateJob

INDIVIDUAL_BATCH_MAX_WORKERS = 8


@dataclass(frozen=True)
class IndividualBuildTask:
    job: PlateJob
    options: BuildOptions


@dataclass(frozen=True)
class IndividualBuildFailure:
    index: int
    job: PlateJob
    error: str


@dataclass(frozen=True)
class IndividualBatchBuildResult:
    results: tuple[BuildResult, ...]
    failures: tuple[IndividualBuildFailure, ...]
    worker_count: int


def individual_batch_worker_count(task_count: int, max_workers: int | None = None) -> int:
    if task_count <= 0:
        return 0
    if max_workers is not None:
        return min(task_count, max(1, int(max_workers)))
    detected_cpu_count = os.cpu_count() or 1
    return min(task_count, max(1, detected_cpu_count), INDIVIDUAL_BATCH_MAX_WORKERS)


def _build_individual_task(task: IndividualBuildTask) -> BuildResult:
    return build_packed_3mf([task.job], task.options)


def _run_individual_batch_serial(
    tasks: Sequence[IndividualBuildTask],
    worker_count: int,
) -> IndividualBatchBuildResult:
    results: list[BuildResult] = []
    failures: list[IndividualBuildFailure] = []
    for index, task in enumerate(tasks):
        try:
            results.append(_build_individual_task(task))
        except Exception as exc:
            failures.append(IndividualBuildFailure(index, task.job, str(exc)))
    return IndividualBatchBuildResult(tuple(results), tuple(failures), worker_count)


def run_individual_batch_builds(
    tasks: Sequence[IndividualBuildTask],
    max_workers: int | None = None,
) -> IndividualBatchBuildResult:
    task_list = list(tasks)
    worker_count = individual_batch_worker_count(len(task_list), max_workers)
    if worker_count <= 1:
        return _run_individual_batch_serial(task_list, worker_count)

    ordered_results: list[BuildResult | None] = [None] * len(task_list)
    failures: list[IndividualBuildFailure] = []
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(_build_individual_task, task): index
            for index, task in enumerate(task_list)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            task = task_list[index]
            try:
                ordered_results[index] = future.result()
            except Exception as exc:
                failures.append(IndividualBuildFailure(index, task.job, str(exc)))

    results = tuple(result for result in ordered_results if result is not None)
    return IndividualBatchBuildResult(
        tuple(results),
        tuple(sorted(failures, key=lambda item: item.index)),
        worker_count,
    )
