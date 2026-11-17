"""分析模块。

历史上这里导出 ``PositionAnalyzer``（依赖 src.storage 的 SQLite 存储层）。
该链路随 P3 重构下线（生产采集直接产出 4 契约 JSON，不再落 crawl_data.db），
2026-11-17 复核确认无生产消费者后，连同 ``src/storage/`` 一并删除。
"""
