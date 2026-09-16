"""操作记录仓储（统一提交协议的对外别名）。

真实实现位于 ``operation_record_repository``，本模块提供稳定的导入入口，
避免调用方散落到多个命名。业务代码请从本模块导入：

    from backend.db.repositories.operation_repository import (
        OperationRecordRepository,
        build_request_hash,
        operation_repo,
    )
"""

from __future__ import annotations

from backend.db.repositories.operation_record_repository import (
    OPERATION_STATUS,
    OperationRecordRepository,
    build_request_hash,
    operation_record_repo,
)

# 稳定别名：与文件名一致的仓储单例
operation_repo = operation_record_repo

__all__ = [
    "OPERATION_STATUS",
    "OperationRecordRepository",
    "build_request_hash",
    "operation_repo",
    "operation_record_repo",
]
