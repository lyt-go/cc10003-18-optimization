"""审计日志：发布、换版和可用性变更都留下带规则版本的记录。

审计入口只追加不修改，接口按时间倒序返回，便于验收时核对
「哪一次发布用了哪一版归因规则」。
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = RLock()
        self._seq = 0

    def record(
        self,
        action: str,
        *,
        service_code: str | None = None,
        service_version: int | None = None,
        contract_key: str | None = None,
        rule_versions: str | None = None,
        conclusion: str = "",
        reused: bool | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            entry: dict[str, Any] = {
                "id": self._seq,
                "时间": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "动作": action,
                "服务编号": service_code,
                "服务版本": service_version,
                "契约键": contract_key,
                "规则版本": rule_versions,
                "复用": reused,
                "结论": conclusion,
            }
            if detail:
                entry["详情"] = detail
            self._entries.append(entry)
            return dict(entry)

    def list_entries(
        self,
        *,
        action: str | None = None,
        service_code: str | None = None,
        rule_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = list(reversed(self._entries))
        if action:
            rows = [row for row in rows if row.get("动作") == action]
        if service_code:
            rows = [row for row in rows if row.get("服务编号") == service_code]
        if rule_code:
            rows = [row for row in rows if rule_code in str(row.get("规则版本") or "")]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total


audit = AuditLog()
