"""契约、读取会话、页段与冲突版本的共享运行状态。

归因规则服务和数据服务都会读写这里：
- 发布新契约时登记契约，并把旧契约的页段 / 会话置为失效；
- 归因规则换版时按契约引用的规则找到相关页段，提示重新开始；
- 并发发布只允许一个请求推进服务版本，另一个记入冲突版本。
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def internal_contract_key(service_code: str, contract_key: str) -> str:
    """契约按「服务编号 + 契约键」归档：同一服务内同契约只保留一份。"""
    return f"{service_code}::{contract_key}"


class ContractState:
    def __init__(self) -> None:
        self.lock = RLock()
        # internal_key -> 契约
        self.contracts: dict[str, dict[str, Any]] = {}
        # 服务编号 -> 当前契约键
        self.current: dict[str, str] = {}
        # 会话编号 -> 读取会话
        self.sessions: dict[str, dict[str, Any]] = {}
        # 页段结果
        self.segments: list[dict[str, Any]] = []
        # 冲突版本
        self.conflicts: list[dict[str, Any]] = []
        self._counters = {"session": 0, "segment": 0, "conflict": 0}

    def next_id(self, name: str, prefix: str) -> str:
        self._counters[name] = self._counters.get(name, 0) + 1
        return f"{prefix}-{self._counters[name]:06d}"

    def add_segment(self, **values: Any) -> dict[str, Any]:
        with self.lock:
            segment = {"页段编号": self.next_id("segment", "SEG"), **values}
            segment.setdefault("状态", "有效")
            segment.setdefault("失效原因", None)
            segment.setdefault("创建时间", now_text())
            self.segments.append(segment)
            return segment

    def invalidate_segments(self, predicate, reason: str) -> list[dict[str, Any]]:
        """按失效范围批量置为「已失效」，返回本次失效的页段。"""
        with self.lock:
            touched: list[dict[str, Any]] = []
            for segment in self.segments:
                if segment.get("状态") != "有效":
                    continue
                if predicate(segment):
                    segment["状态"] = "已失效"
                    segment["失效原因"] = reason
                    segment["失效时间"] = now_text()
                    touched.append(segment)
            return touched

    def invalidate_sessions(self, predicate, reason: str) -> list[dict[str, Any]]:
        with self.lock:
            touched: list[dict[str, Any]] = []
            for session in self.sessions.values():
                if session.get("状态") != "有效":
                    continue
                if predicate(session):
                    session["状态"] = "已失效"
                    session["失效原因"] = reason
                    session["失效时间"] = now_text()
                    touched.append(session)
            return touched

    def add_conflict(self, **values: Any) -> dict[str, Any]:
        with self.lock:
            conflict = {"冲突编号": self.next_id("conflict", "CFT"), **values}
            conflict.setdefault("时间", now_text())
            self.conflicts.append(conflict)
            return conflict

    def contracts_of_service(self, service_code: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                contract
                for contract in self.contracts.values()
                if contract.get("服务编号") == service_code
            ]

    def segments_for(
        self,
        *,
        service_code: str | None = None,
        contract_key: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.lock:
            rows = list(self.segments)
        if service_code:
            rows = [row for row in rows if row.get("服务编号") == service_code]
        if contract_key:
            rows = [row for row in rows if row.get("契约键") == contract_key]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return rows


state = ContractState()
