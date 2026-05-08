from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import a1_swap_mod_packer.batch as batch
from a1_swap_mod_packer.models import BuildOptions, BuildResult, PlateJob


class IndividualBatchBuildTest(unittest.TestCase):
    def test_worker_count_uses_task_count_and_caps_cpu_count(self) -> None:
        self.assertEqual(batch.individual_batch_worker_count(0), 0)
        self.assertEqual(batch.individual_batch_worker_count(3, max_workers=99), 3)
        self.assertEqual(batch.individual_batch_worker_count(3, max_workers=0), 1)
        with patch.object(batch.os, "cpu_count", return_value=32):
            self.assertEqual(batch.individual_batch_worker_count(20), batch.INDIVIDUAL_BATCH_MAX_WORKERS)
        with patch.object(batch.os, "cpu_count", return_value=None):
            self.assertEqual(batch.individual_batch_worker_count(20), 1)

    def test_serial_runner_keeps_success_order_and_collects_failures(self) -> None:
        tasks = [
            self.task("one.3mf", "one.out.3mf", copies=2),
            self.task("fail.3mf", "fail.out.3mf", copies=3),
            self.task("two.3mf", "two.out.3mf", copies=4),
        ]

        def fake_build(jobs: list[PlateJob], options: BuildOptions) -> BuildResult:
            job = jobs[0]
            if job.source_3mf.name == "fail.3mf":
                raise ValueError("planned failure")
            return BuildResult(
                output_3mf=options.output_3mf,
                plate_count=job.copies,
                total_prediction_seconds=None,
                total_weight_grams=None,
                gcode_md5=job.source_3mf.stem,
            )

        with patch.object(batch, "build_packed_3mf", side_effect=fake_build):
            result = batch.run_individual_batch_builds(tasks, max_workers=1)

        self.assertEqual(result.worker_count, 1)
        self.assertEqual(
            [item.output_3mf.name for item in result.results],
            ["one.out.3mf", "two.out.3mf"],
        )
        self.assertEqual([item.plate_count for item in result.results], [2, 4])
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].index, 1)
        self.assertEqual(result.failures[0].job.source_3mf.name, "fail.3mf")
        self.assertIn("planned failure", result.failures[0].error)

    @staticmethod
    def task(source_name: str, output_name: str, copies: int) -> batch.IndividualBuildTask:
        return batch.IndividualBuildTask(
            PlateJob(Path(source_name), copies),
            BuildOptions(swap_gcode=Path("swap.gcode"), output_3mf=Path(output_name)),
        )


if __name__ == "__main__":
    unittest.main()
