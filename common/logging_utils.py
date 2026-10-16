"""Thread-safe CSV logging helpers."""
from __future__ import annotations

import csv
import os
import threading
from typing import Sequence


class CsvLog:
    """Append rows to a CSV file, writing the header once. Safe across threads."""

    def __init__(self, path: str, header: Sequence[str]):
        self.path = path
        self.header = list(header)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(self.header)

    def write(self, row: Sequence) -> None:
        """Append one row."""
        with self._lock, open(self.path, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(row)


def write_csv(path: str, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    """Write a whole CSV file (used by experiments)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path: str) -> list[dict]:
    """Read a CSV file into a list of dicts."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
