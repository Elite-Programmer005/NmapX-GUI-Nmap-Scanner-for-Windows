from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class ScanDatabase:
    def __init__(self) -> None:
        self.db_path = Path(__file__).resolve().parent / "nmapx_history.db"
        self._create_table()

    def _create_table(self) -> None:
        with sqlite3.connect(str(self.db_path), check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    target TEXT,
                    profile TEXT,
                    ports TEXT,
                    result_summary TEXT,
                    full_output TEXT
                )
                """
            )
            conn.commit()

    def save_scan(
        self,
        target: str,
        profile: str,
        ports: str,
        result_summary: str,
        full_output: str,
    ) -> None:
        with sqlite3.connect(str(self.db_path), check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scans (timestamp, target, profile, ports, result_summary, full_output)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    target,
                    profile,
                    ports,
                    result_summary,
                    full_output,
                ),
            )
            conn.commit()

    def get_all_scans(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(str(self.db_path), check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp, target, profile, ports, result_summary, full_output
                FROM scans
                ORDER BY id DESC
                """
            )
            rows = cursor.fetchall()

        return [
            {
                "id": scan_id,
                "timestamp": timestamp,
                "target": target,
                "profile": profile,
                "ports": ports,
                "summary": summary,
            }
            for scan_id, timestamp, target, profile, ports, summary, _full_output in rows
        ]

    def delete_scan(self, scan_id: int) -> None:
        with sqlite3.connect(str(self.db_path), check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
            conn.commit()

    def clear_all(self) -> None:
        with sqlite3.connect(str(self.db_path), check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scans")
            conn.commit()
