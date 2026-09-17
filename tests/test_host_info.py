"""Unit tests for HostInfo and host probe helpers."""

from __future__ import annotations

import unittest

from gpuwatch.models import HostInfo, ServerSnapshot
from gpuwatch.nvml_probe import (
    _is_physical_disk,
)


class TestHostInfo(unittest.TestCase):
    def test_from_probe_happy(self):
        h = HostInfo.from_probe(
            {
                "cpu_percent": 42.5,
                "mem_used_mb": 8192,
                "mem_total_mb": 32768,
                "load1": 1.25,
                "disk_read_mb_s": 10.0,
                "disk_write_mb_s": 2.5,
            }
        )
        assert h is not None
        self.assertAlmostEqual(h.cpu_percent, 42.5)
        self.assertEqual(h.mem_used_mb, 8192)
        self.assertAlmostEqual(h.mem_percent, 25.0)

    def test_from_probe_missing(self):
        self.assertIsNone(HostInfo.from_probe({}))
        self.assertIsNone(HostInfo.from_probe(None))  # type: ignore[arg-type]

    def test_snapshot_with_and_without_host(self):
        snap = ServerSnapshot.from_probe(
            "h1",
            "lab",
            {"gpus": [], "host": {"cpu_percent": 1, "mem_used_mb": 1, "mem_total_mb": 2, "load1": 0.1, "disk_read_mb_s": 0, "disk_write_mb_s": 0}},
            12.0,
        )
        self.assertIsNotNone(snap.host_info)
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


if __name__ == "__main__":
    unittest.main()
