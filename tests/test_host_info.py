"""Unit tests for HostInfo and host probe helpers."""

from __future__ import annotations

import unittest

from gpuwatch.models import HostInfo, ServerSnapshot
from gpuwatch.nvml_probe import _collect_host, _is_physical_disk, _read_cpu_times_all


class TestHostInfo(unittest.TestCase):
    def test_from_probe_happy(self):
        h = HostInfo.from_probe(
            {
                "cpu_percent": 42.5,
                "cpu_per_core": [10.0, 20.0, 30.0, 40.0],
                "mem_used_mb": 8192,
                "mem_buffers_mb": 256,
                "mem_cached_mb": 4096,
                "mem_total_mb": 32768,
                "load1": 1.25,
                "disk_read_mb_s": 10.0,
                "disk_write_mb_s": 2.5,
            }
        )
        assert h is not None
        self.assertAlmostEqual(h.cpu_percent, 42.5)
        self.assertEqual(h.cpu_cores, 4)
        self.assertEqual(h.mem_used_mb, 8192)
        self.assertAlmostEqual(h.mem_percent, 25.0)

    def test_from_probe_missing(self):
        self.assertIsNone(HostInfo.from_probe({}))
        self.assertIsNone(HostInfo.from_probe(None))  # type: ignore[arg-type]

    def test_snapshot_with_and_without_host(self):
        snap = ServerSnapshot.from_probe(
            "h1",
            "lab",
            {
                "gpus": [],
                "host": {
                    "cpu_percent": 1,
                    "cpu_per_core": [1.0, 2.0],
                    "mem_used_mb": 1,
                    "mem_total_mb": 2,
                    "load1": 0.1,
                    "disk_read_mb_s": 0,
                    "disk_write_mb_s": 0,
                },
            },
            12.0,
        )
        self.assertIsNotNone(snap.host_info)
        self.assertEqual(snap.host_info.cpu_cores, 2)
        snap2 = ServerSnapshot.from_probe("h1", "lab", {"gpus": []}, 12.0)
        self.assertIsNone(snap2.host_info)


class TestDiskFilter(unittest.TestCase):
    def test_physical(self):
        self.assertTrue(_is_physical_disk("sda"))
        self.assertTrue(_is_physical_disk("nvme0n1"))
        self.assertFalse(_is_physical_disk("sda1"))
        self.assertFalse(_is_physical_disk("nvme0n1p1"))
        self.assertFalse(_is_physical_disk("loop0"))
        self.assertFalse(_is_physical_disk("dm-0"))


class TestCollectHost(unittest.TestCase):
    def test_local_collect_has_cores(self):
        agg, cores = _read_cpu_times_all()
        self.assertGreater(agg[1], 0)
        self.assertGreater(len(cores), 0)
        h = _collect_host()
        self.assertIsNotNone(h)
        assert h is not None
        self.assertIn("cpu_per_core", h)
        self.assertEqual(len(h["cpu_per_core"]), len(cores))
        self.assertIn("mem_buffers_mb", h)


if __name__ == "__main__":
    unittest.main()
