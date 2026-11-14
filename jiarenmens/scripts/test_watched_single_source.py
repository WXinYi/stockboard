#!/usr/bin/env python3
"""QA · MJ-01 收口：关注名单「单一真源」落盘回归守卫（BMAD QA）。

背景（07-dual-source-review.md §8 / MJ-01 / §11）：
    现有 246 个 Python 测试全部以 ``ps.WATCHED_PLAYERS`` **自身**为期望值
    （self-referential）。若有人把内联名单重新写回 player_snapshot.py，
    全套旧测试仍会全绿，双真源会静默复活。本文件补两类「非自引用」断言，
    把 AQ2=A 真正锁进 CI：

    1) 对象 identity：``player_snapshot`` / ``watchdog`` 两个消费方绑定的
       ``WATCHED_PLAYERS`` 必须与真源 ``src.config.watched.WATCHED_PLAYERS``
       是「同一个对象」(is)，而非内容相等的副本。
    2) 静态源码守卫（AST）：``player_snapshot.py`` 不得再出现把
       ``WATCHED_PLAYERS`` 作为赋值目标的语句（内联副本），且必须存在
       「且仅存在一处」``from src.config.watched import WATCHED_PLAYERS``。

设计说明（长度从真源取、人数不硬编码为唯一判据）：
    「成员 / 顺序 / 长度」类断言一律从真源 ``src/config/watched.py`` 取期望值
    （运行时对象 + AST 字面量双重比对），绝不写死名单字面量。因此真源名单
    经批准增删成员或调整顺序后，identity 与逐项对齐断言会**自动跟随**，
    无需改测试。仅 ``test_baseline_snapshot_length9_first_id`` 把「9 人 /
    首个 900456476」作为当前已批准基线快照单独锚定（2026-11-14 取消关注
    900438148 后），用于捕捉「真源本身被意外篡改」；它不是判定单一真源的
    唯一判据，核心判据是 identity + AST 守卫。若未来经批准变更名单，需
    同步更新该锚值（有意让它变红，提示复核）。

负向守卫有效性（07 §8 P0-1 验收方式）：
    ``analyze_watched_source`` / ``assert_no_inline_watched_assignment`` /
    ``count_true_source_imports`` 是只吃源码文本的纯函数，不碰文件系统、
    不 import 业务模块，可在 /tmp 的污染副本上复用**同一套 AST 断言逻辑**：
    把 ``WATCHED_PLAYERS = []`` 插回 player_snapshot.py 源码即 raise
    AssertionError → ``test_no_inline_watched_assignment_in_player_snapshot``
    变红。注意 AST 守卫只针对**消费方** player_snapshot.py；真源文件
    watched.py 本身当然含 ``WATCHED_PLAYERS = [...]``，另用
    ``extract_watched_literal`` 读取其字面量，二者用途不同勿混。

本轮约束：仅新建本文件；不改任何既有测试 / 实现 / 规格 / 数据 /
前端 / workflows / scf，不 commit / push。
"""
import ast
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
# 与 test_shared_imports.py 同款 sys.path 处理：保证 src 包与 scripts 入口
# 在任意 cwd 下都可 import（纯内存操作，零文件 / 零网络副作用）。
for _p in (str(SCRIPTS.parent), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PLAYER_SNAPSHOT_PATH = SCRIPTS / "player_snapshot.py"
WATCHED_SOURCE_PATH = SCRIPTS.parent / "src" / "config" / "watched.py"
TRUE_SOURCE_MODULE = "src.config.watched"
TRUE_SOURCE_NAME = "WATCHED_PLAYERS"


def _target_names(target):
    """递归展开赋值目标，返回其中的 Name id 列表（覆盖元组解包赋值）。"""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def analyze_watched_source(source_text):
    """AST 分析一段源码文本，返回 ``(assignments, imports)``。

    assignments: 所有把 ``WATCHED_PLAYERS`` 作为赋值目标的语句节点
                 （``=`` / ``AnnAssign`` / ``AugAssign``；含模块级内联副本）。
                 用 ``ast.walk`` 扫全树，内联副本塞在任何层级都会被捕获。
    imports:     ``from src.config.watched import WATCHED_PLAYERS`` 的
                 ImportFrom 节点列表。
    纯函数：不读文件、不 import 业务模块、无副作用 —— 便于 /tmp 负向验证复用。
    """
    tree = ast.parse(source_text)
    assignments = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            if TRUE_SOURCE_NAME in _target_names(t):
                assignments.append(node)
                break
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == TRUE_SOURCE_MODULE:
            if any(alias.name == TRUE_SOURCE_NAME for alias in node.names):
                imports.append(node)
    return assignments, imports


def assert_no_inline_watched_assignment(source_text):
    """负向守卫核心：源码内不得存在 ``WATCHED_PLAYERS = ...`` 这类赋值。

    命中即 raise AssertionError（unittest 记为 FAIL；/tmp 负向验证据此判红）。
    """
    assignments, _ = analyze_watched_source(source_text)
    if assignments:
        lines = ", ".join(str(getattr(n, "lineno", "?")) for n in assignments)
        raise AssertionError(
            f"检测到 {TRUE_SOURCE_NAME} 内联赋值（疑似双真源复活），行号: {lines}"
        )


def count_true_source_imports(source_text):
    """返回 ``from src.config.watched import WATCHED_PLAYERS`` 的出现次数。"""
    _, imports = analyze_watched_source(source_text)
    return len(imports)


def extract_watched_literal(source_text):
    """从源码读取「模块级 ``WATCHED_PLAYERS = [...]``」的字面量（静态期望值）。

    仅用于读取真源 watched.py / 构造静态顺序比对，不用于消费方反内联检测。
    """
    tree = ast.parse(source_text)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if TRUE_SOURCE_NAME in _target_names(t):
                    return ast.literal_eval(node.value)
    raise AssertionError(f"未找到模块级 {TRUE_SOURCE_NAME} 字面量")


def _read(path):
    return path.read_text(encoding="utf-8")


class TestWatchedSingleSourceIdentity(unittest.TestCase):
    """MJ-01 · 落盘的单一真源 identity 断言（非 self-referential）。"""

    def test_player_snapshot_is_true_source_object(self):
        import player_snapshot as ps
        import src.config.watched as watched_mod
        # 字面复现：player_snapshot.WATCHED_PLAYERS is src.config.watched.WATCHED_PLAYERS
        self.assertIs(
            ps.WATCHED_PLAYERS, watched_mod.WATCHED_PLAYERS,
            "ps.WATCHED_PLAYERS 必须是真源对象本身（is），而非内容相等的副本",
        )
        from src.config.watched import WATCHED_PLAYERS as SRC
        self.assertIs(ps.WATCHED_PLAYERS, SRC)

    def test_watchdog_is_same_true_source_object(self):
        import player_snapshot as ps
        import watchdog
        import src.config.watched as watched_mod
        self.assertIs(
            watchdog.WATCHED_PLAYERS, watched_mod.WATCHED_PLAYERS,
            "watchdog.WATCHED_PLAYERS 亦须指向同一真源",
        )
        self.assertIs(
            ps.WATCHED_PLAYERS, watchdog.WATCHED_PLAYERS,
            "两个消费方共享同一 list 对象（AQ2=A 单一真源）",
        )

    def test_members_and_order_equal_watched_py_literal(self):
        import player_snapshot as ps
        import watchdog
        import src.config.watched as watched_mod
        # 期望值取自真源文件字面量 + 运行时对象：不写死名单，真源变化自动跟随。
        expected = extract_watched_literal(_read(WATCHED_SOURCE_PATH))
        self.assertEqual(list(ps.WATCHED_PLAYERS), expected,
                         "ps 名单成员与顺序须与 src/config/watched.py 完全一致")
        self.assertEqual(list(watchdog.WATCHED_PLAYERS), expected,
                         "watchdog 名单成员与顺序须与 src/config/watched.py 完全一致")
        # 长度从真源取（不是硬编码人数）：
        self.assertEqual(len(ps.WATCHED_PLAYERS), len(expected))
        self.assertEqual(len(ps.WATCHED_PLAYERS), len(watched_mod.WATCHED_PLAYERS))
        self.assertEqual([zh for zh, _ in ps.WATCHED_PLAYERS],
                         [zh for zh, _ in expected], "id 顺序须逐项一致")

    def test_baseline_snapshot_length9_first_id(self):
        """当前已批准基线快照（非单一真源唯一判据，见模块 docstring）。"""
        import player_snapshot as ps
        import src.config.watched as watched_mod
        self.assertEqual(len(watched_mod.WATCHED_PLAYERS), 9,
                         "2026-11-14 取消关注 900438148 后基线为 9 人")
        self.assertEqual(watched_mod.WATCHED_PLAYERS[0][0], "900456476",
                         "首个 id = 置顶顺序首位")
        self.assertEqual(ps.WATCHED_PLAYERS[0][0], "900456476")


class TestWatchedSingleSourceAstGuard(unittest.TestCase):
    """MJ-01 · 静态反内联守卫 + import 唯一性（负向可拦截双真源复活）。"""

    def test_no_inline_watched_assignment_in_player_snapshot(self):
        # 与 /tmp 负向验证共用同一套 AST 断言逻辑；内联副本回灌即在此变红。
        assert_no_inline_watched_assignment(_read(PLAYER_SNAPSHOT_PATH))

    def test_exactly_one_true_source_import_in_player_snapshot(self):
        source = _read(PLAYER_SNAPSHOT_PATH)
        self.assertEqual(
            count_true_source_imports(source), 1,
            "必须存在且仅存在一处 from src.config.watched import WATCHED_PLAYERS",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
