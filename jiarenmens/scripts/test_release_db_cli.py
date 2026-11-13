#!/usr/bin/env python3
"""release_db.py CLI 裁剪后的 auction 子命令覆盖（F1 / T2.3.4）。

F1：只保留 ``--what auction --download-latest | --upload-latest``；
``--sync`` / ``--download-fallback`` / ``--cache-db`` 已裁掉，必须被 argparse 拒绝。
零网络（dispatch 目标全部 mock）。
"""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestAuctionCliDispatch(unittest.TestCase):
    def _run(self, argv):
        import release_db
        old = sys.argv
        sys.argv = ["release_db.py"] + argv
        try:
            release_db.main()
        finally:
            sys.argv = old

    def test_download_latest_dispatch_auction(self):
        import release_db
        with unittest.mock.patch.object(release_db, "cmd_download_what",
                                        return_value=0) as m:
            with self.assertRaises(SystemExit) as cm:
                self._run(["--what", "auction", "--download-latest"])
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(m.call_args[0][0], "auction")

    def test_upload_latest_dispatch_auction(self):
        import release_db
        with unittest.mock.patch.object(release_db, "cmd_upload_what",
                                        return_value=None) as m:
            self._run(["--what", "auction", "--upload-latest"])
        self.assertEqual(m.call_args[0][0], "auction")

    def test_removed_flags_rejected(self):
        for argv in (["--sync"], ["--download-fallback"], ["--cache-db", "x.gz"]):
            with self.assertRaises(SystemExit, msg=f"{argv} 应已被裁剪"), \
                 unittest.mock.patch("sys.stderr"):
                self._run(argv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
