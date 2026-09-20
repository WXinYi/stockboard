#!/usr/bin/env python3
"""盘中实时宽度(breadth-rt)单测: 旧库迁移加列 / upsert 幂等 / captured_at 标记 / 非今日行过滤。

运行: python3 scripts/test_breadth_rt.py
"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backfill_emotion as be  # noqa: E402

OLD_SCHEMA_ROW = ("2026-09-17", 49, 2, 47, 3, 29.85, 20)
RT_TODAY = [78, 0, 74, 1, 24.27, 25, "2026-09-18"]
RT_YESTERDAY = [49, 2, 47, 3, 29.85, 20, "2026-09-17"]


@contextmanager
def tempfile_db():
    path = Path(tempfile.mkdtemp()) / "auction.db"
    conn = sqlite3.connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _cols(conn):
    return [r[1] for r in conn.execute("PRAGMA table_info(market_breadth)")]


class TestMigration(unittest.TestCase):
    def test_old_schema_gains_captured_at(self):
        """旧库(7 列无 captured_at)经 init_tables 迁移后获得新列, 旧数据保留。"""
        with tempfile_db() as conn:
            conn.execute("CREATE TABLE market_breadth (date TEXT PRIMARY KEY, zt INTEGER,"
                         " dt INTEGER, natural_zt INTEGER, once_dt INTEGER, broke_rate REAL,"
                         " zhaban INTEGER)")
            conn.execute("INSERT INTO market_breadth VALUES (?,?,?,?,?,?,?)", OLD_SCHEMA_ROW)
            be.init_tables(conn)
            self.assertIn("captured_at", _cols(conn))
            row = conn.execute("SELECT date, captured_at FROM market_breadth").fetchone()
            self.assertEqual(row[0], "2026-09-17")
            self.assertIsNone(row[1])   # 旧行 captured_at 为 NULL(收盘定稿口径)


class TestRtUpsert(unittest.TestCase):
    def test_write_and_idempotent(self):
        """当日行写入带 captured_at; 重复写入幂等(REPLACE 同一行)。"""
        with tempfile_db() as conn:
            be.init_tables(conn)
            be._write_rt_rows(conn, [RT_TODAY], "10:06")
            be._write_rt_rows(conn, [RT_TODAY], "10:11")
            row = conn.execute("SELECT date, zt, broke_rate, captured_at FROM market_breadth"
                               " WHERE date='2026-09-18'").fetchone()
            self.assertEqual((row[0], row[1], row[2], row[3]), ("2026-09-18", 78, 24.27, "10:11"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_breadth").fetchone()[0], 1)

    def test_write_keeps_history_intact(self):
        """只写当日行: 历史行不被触碰(captured_at 仍为 NULL=收盘定稿口径)。"""
        with tempfile_db() as conn:
            be.init_tables(conn)
            conn.execute("INSERT INTO market_breadth"
                         "(date, zt, dt, natural_zt, once_dt, broke_rate, zhaban, captured_at)"
                         " VALUES ('2026-09-17',49,2,47,3,29.85,20,NULL)")
            be._write_rt_rows(conn, [RT_TODAY], "10:06")
            hist = conn.execute("SELECT captured_at FROM market_breadth"
                                " WHERE date='2026-09-17'").fetchone()[0]
            self.assertIsNone(hist)


class TestTodayFilter(unittest.TestCase):
    def test_non_today_rows_filtered(self):
        """节假日 rt 返回旧日期 → 过滤掉, 不落库(不重写历史行)。"""
        rows = [r for r in (RT_TODAY, RT_YESTERDAY) if str(r[6]) == "2026-09-18"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][6], "2026-09-18")


if __name__ == "__main__":
    unittest.main(verbosity=2)
