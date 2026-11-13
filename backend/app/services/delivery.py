"""数据服务业务规则：契约识别、服务版本发布、分页读取与失效范围。

发布链路：
    归因规则批量发布 -> DeliveryService.publish_contract
契约键 = 归因规则版本 + 返回字段 + 请求参数；相同契约复用同一服务版本，
契约变化才生成新的服务版本并让旧契约下的页段结果失效。

读取链路：
    请求入口先校验单次返回上限，再从首段建立读取会话；
后续页段按固定次序读取，规则换版时明确提示「请从首段重新开始」，
规则不可用或契约失效时不返回上一次的归因结果。
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from app.audit import audit
from app.contract_state import internal_contract_key, now_text, state
from app.contracts import (
    build_contract_key,
    normalize_request_params,
    normalize_return_fields,
    normalize_rule_versions,
    rule_versions_text,
)
from app.store import store

MODULE = "delivery"
REQUIRED_FIELDS = ["服务编号", "数据集名称", "查询参数定义"]
CONTRACT_REQUIRED = ["服务编号", "返回字段", "查询参数定义", "速率限制", "分页最大条数", "消费方应用"]
STATUS_ORDER = ["草稿", "服务评审中", "已发布", "已下线"]
ACTION_RULES = {"提交服务评审": "服务评审中", "发布服务": "已发布", "下线服务": "已下线"}
NEGATIVE_ACTIONS = ["下线服务"]
DEFAULT_TOTAL_ROWS = 1000
DEFAULT_PAGE_CAP = 200
UNSET = object()

_RULE_VERSION_PATTERN = re.compile(r"([A-Za-z0-9_\-]+)\s*@\s*v?(\d+)")

# 请求幂等与调用配额必须是进程级共享的：路由每次新建 service 实例，
# 状态放进实例会让重复请求绕过幂等判断。
_REQUEST_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_DAILY_CALLS: dict[tuple[str, str, str], int] = {}
# 内存仓库只保留近期的幂等结果，避免长时间运行后无限增长。
_REQUEST_CACHE_LIMIT = 2000


def _remember_request(key: tuple[str, str], value: dict[str, Any]) -> None:
    if len(_REQUEST_CACHE) >= _REQUEST_CACHE_LIMIT:
        _REQUEST_CACHE.pop(next(iter(_REQUEST_CACHE)))
    _REQUEST_CACHE[key] = value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_rule_versions(value: Any) -> list[dict[str, Any]]:
    """接受契约里的规则版本明细，或 ATTR-0003@v1 这样的展示文本。"""
    if not value:
        return []
    if isinstance(value, str):
        pairs = [(code, int(version)) for code, version in _RULE_VERSION_PATTERN.findall(value)]
        return normalize_rule_versions(pairs)
    pairs: list[tuple[str, int]] = []
    for item in value:
        if isinstance(item, dict):
            pairs.append((str(item.get("规则编号") or ""), _as_int(item.get("规则版本"), 1)))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append((str(item[0]), _as_int(item[1], 1)))
        elif isinstance(item, str):
            for code, version in _RULE_VERSION_PATTERN.findall(item):
                pairs.append((code, int(version)))
    return normalize_rule_versions(pairs)


def parse_daily_limit(values: dict[str, Any]) -> int | None:
    """从速率限制 / 每日调用上限里解析消费方每天调用次数。"""
    explicit = values.get("每日调用上限") or values.get("每天调用上限")
    if explicit not in (None, ""):
        return _as_int(explicit, 0) or None
    text = str(values.get("速率限制") or "")
    match = re.search(r"(\d+)\s*次\s*/\s*天", text)
    if not match:
        match = re.search(r"(?:每天|每日)\D{0,4}(\d+)\s*次", text)
    if not match:
        match = re.search(r"(\d+)", text)
    if not match:
        return None
    return int(match.group(1)) or None


def contract_fingerprint(values: dict[str, Any], rule_versions: list[dict[str, Any]]) -> str:
    payload = {
        "服务编号": str(values.get("服务编号") or "").strip(),
        "数据集名称": str(values.get("数据集名称") or "").strip(),
        "规则版本明细": rule_versions,
        "返回字段": normalize_return_fields(values.get("返回字段")),
        "请求参数": normalize_request_params(values.get("查询参数定义"))[0],
        "速率限制": str(values.get("速率限制") or "").strip(),
        "分页最大条数": _as_int(values.get("分页最大条数"), DEFAULT_PAGE_CAP),
        "消费方应用": str(values.get("消费方应用") or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DeliveryService:
    @property
    def _request_cache(self) -> dict[tuple[str, str], dict[str, Any]]:
        # 请求编号 -> 首次结果，用于幂等：重复请求不生成第二个版本 / 第二个页段。
        return _REQUEST_CACHE

    @property
    def _daily_calls(self) -> dict[tuple[str, str, str], int]:
        return _DAILY_CALLS

    # ------------------------------------------------------------ 基础列表
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("服务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def find_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("服务编号") == code:
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        code = str(values.get("服务编号") or "").strip()
        if self.find_by_code(code):
            return None, [f"服务编号 {code} 已存在"]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.setdefault("返回字段", values.get("返回字段"))
        entry.setdefault("速率限制", values.get("速率限制"))
        entry.setdefault("分页最大条数", _as_int(values.get("分页最大条数"), DEFAULT_PAGE_CAP))
        entry.setdefault("版本号", 1)
        entry.setdefault("消费方应用", values.get("消费方应用"))
        entry.setdefault("规则版本明细", parse_rule_versions(values.get("规则版本明细") or values.get("规则版本")))
        entry["契约键"] = None
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------------ 版本查询
    def current_service_version(self, service_code: str) -> int:
        with state.lock:
            internal = state.current.get(service_code)
            if internal and internal in state.contracts:
                return _as_int(state.contracts[internal].get("服务版本"))
            row = self.find_by_code(service_code)
            return _as_int(row.get("版本号"), 0) if row else 0

    def resolve_contract(self, service_code: str, contract_key: str | None = None) -> dict[str, Any] | None:
        with state.lock:
            if contract_key:
                internal = internal_contract_key(service_code, contract_key)
                return state.contracts.get(internal)
            internal = state.current.get(service_code)
            return state.contracts.get(internal) if internal else None

    # ------------------------------------------------------------ 发布契约
    def check_publish(
        self,
        values: dict[str, Any],
        *,
        base_version: Any = UNSET,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """发布前置校验：请求幂等与期望版本；通过返回 ok=True。

        必须在调用方持有 state.lock 时使用，保证与后续升版 / 建契约是原子的。
        """
        missing = [field for field in CONTRACT_REQUIRED if values.get(field) in (None, "")]
        if not values.get("规则版本明细") and not values.get("规则版本"):
            missing.append("规则版本明细")
        if missing:
            return {"ok": False, "code": "CONTRACT_INCOMPLETE", "message": f"整组契约缺少：{'、'.join(missing)}"}

        service_code = str(values.get("服务编号") or "").strip()
        if _as_int(values.get("分页最大条数"), 0) <= 0:
            return {"ok": False, "code": "CONTRACT_INCOMPLETE", "message": "分页最大条数必须为正整数"}

        rule_versions = parse_rule_versions(values.get("规则版本明细") or values.get("规则版本"))
        fingerprint = contract_fingerprint(values, rule_versions)
        if request_id:
            cached = self._request_cache.get(("publish", str(request_id)))
            if cached:
                if cached["指纹"] == fingerprint:
                    return {"ok": True, "duplicate": cached["结果"]}
                return {"ok": False, "code": "REQUEST_MISMATCH", "message": "重复请求携带了不同的契约内容，拒绝第二个版本"}

        current_version = self.current_service_version(service_code)
        expected = base_version if base_version not in (None, "", UNSET) else values.get("当前版本")
        if expected not in (None, "") and _as_int(expected, -1) != current_version:
            return {
                "ok": False,
                "code": "CONTRACT_CONFLICT",
                "expected": _as_int(expected, -1),
                "current": current_version,
                "message": f"服务当前版本为 v{current_version}，与期望 v{_as_int(expected, -1)} 不一致；不接受第二个版本",
            }

        # 并发保护：提交里带了期望版本时按版本号对账（上面已校验）；
        # 没带期望版本时，只有「相同契约复用」是安全的，新契约一律要求带上
        # 调用方读到的服务版本，避免两个并发请求各自生成一个版本。
        inline_key = build_contract_key(
            [(item["规则编号"], item["规则版本"]) for item in rule_versions],
            values.get("返回字段"),
            values.get("查询参数定义"),
        )
        exists = internal_contract_key(service_code, inline_key) in state.contracts
        if not exists and expected in (None, "") and state.current.get(service_code):
            return {
                "ok": False,
                "code": "CONTRACT_CONFLICT",
                "expected": None,
                "current": current_version,
                "message": (
                    f"契约变化时会生成新版本；服务当前为 v{current_version}，请携带 当前版本 再发布，"
                    "不接受并发生成的第二个版本"
                ),
            }
        return {"ok": True, "fingerprint": fingerprint, "rule_versions": rule_versions}

    def record_conflict(
        self,
        values: dict[str, Any],
        check: dict[str, Any],
        *,
        request_id: str | None = None,
        source: str = "并发发布",
    ) -> dict[str, Any]:
        """把被拒绝的第二个版本记入冲突版本，并写入审计。"""
        service_code = str(values.get("服务编号") or "").strip()
        conflict = state.add_conflict(
            服务编号=service_code,
            期望版本=check.get("expected"),
            当前版本=check.get("current", self.current_service_version(service_code)),
            来源=source,
            请求编号=request_id,
        )
        rule_versions = parse_rule_versions(values.get("规则版本明细") or values.get("规则版本"))
        contract_key = (
            build_contract_key(
                [(item["规则编号"], item["规则版本"]) for item in rule_versions],
                values.get("返回字段"),
                values.get("查询参数定义"),
            )
            if rule_versions
            else None
        )
        conflict.update({"契约键": contract_key, "规则版本": rule_versions_text(
            (item["规则编号"], item["规则版本"]) for item in rule_versions
        ) if rule_versions else None})
        audit.record(
            "发布冲突",
            service_code=service_code,
            contract_key=contract_key,
            rule_versions=conflict["规则版本"],
            conclusion=check.get("message") or "冲突版本被拒绝",
            detail={"冲突编号": conflict["冲突编号"], "请求编号": request_id},
        )
        return conflict

    def publish_contract(
        self,
        values: dict[str, Any],
        *,
        request_id: str | None = None,
        base_version: Any = UNSET,
    ) -> dict[str, Any]:
        with state.lock:
            check = self.check_publish(values, base_version=base_version, request_id=request_id)
            if check.get("duplicate"):
                result = {"ok": True, "reused": True, "duplicate": True, "message": "重复发布请求，返回首次发布结果", **check["duplicate"]}
                return result
            if not check.get("ok"):
                if check.get("code") == "CONTRACT_CONFLICT":
                    conflict = self.record_conflict(values, check, request_id=request_id)
                    return {**check, "reused": False, "conflict": conflict}
                if check.get("code") == "REQUEST_MISMATCH":
                    conflict = self.record_conflict(
                        values, check, request_id=request_id, source="重复请求"
                    )
                    return {**check, "reused": False, "conflict": conflict}
                return {**check, "reused": False}

            rule_versions = check["rule_versions"]
            fingerprint = check["fingerprint"]
            service_code = str(values.get("服务编号") or "").strip()
            contract_key = build_contract_key(
                [(item["规则编号"], item["规则版本"]) for item in rule_versions],
                values.get("返回字段"),
                values.get("查询参数定义"),
            )
            internal = internal_contract_key(service_code, contract_key)
            contract = state.contracts.get(internal)
            invalidated: list[dict[str, Any]] = []
            reused = contract is not None

            if contract is not None:
                contract["复用次数"] = _as_int(contract.get("复用次数")) + 1
                contract["最近复用时间"] = now_text()
                version = _as_int(contract.get("服务版本"), 1)
            else:
                version = self._next_version(service_code)
                contract = {
                    "契约键": contract_key,
                    "服务编号": service_code,
                    "服务版本": version,
                    "数据集名称": str(values.get("数据集名称") or self._service_name(service_code)),
                    "返回字段": normalize_return_fields(values.get("返回字段")),
                    "查询参数定义": normalize_request_params(values.get("查询参数定义"))[1],
                    "请求参数规范": normalize_request_params(values.get("查询参数定义"))[0],
                    "速率限制": str(values.get("速率限制") or ""),
                    "每日调用上限": parse_daily_limit(values),
                    "分页最大条数": _as_int(values.get("分页最大条数"), DEFAULT_PAGE_CAP),
                    "消费方应用": str(values.get("消费方应用") or ""),
                    "规则版本明细": rule_versions,
                    "规则版本": rule_versions_text(
                        (item["规则编号"], item["规则版本"]) for item in rule_versions
                    ),
                    "数据总条数": _as_int(values.get("总条数"), DEFAULT_TOTAL_ROWS),
                    "状态": "已发布",
                    "复用次数": 0,
                    "当前": False,
                    "创建时间": now_text(),
                    "更新时间": now_text(),
                    "首次发布请求": request_id,
                }
                state.contracts[internal] = contract

            # 运行属性（速率限制、单次返回上限、消费方应用）不参与契约键，
            # 复用契约时按本次提交更新，但仍只保留一份服务版本。
            if reused:
                contract.update({
                    "数据集名称": str(values.get("数据集名称") or contract.get("数据集名称") or ""),
                    "速率限制": str(values.get("速率限制") or contract.get("速率限制") or ""),
                    "每日调用上限": parse_daily_limit(values) or contract.get("每日调用上限"),
                    "分页最大条数": _as_int(values.get("分页最大条数"), contract.get("分页最大条数") or DEFAULT_PAGE_CAP),
                    "消费方应用": str(values.get("消费方应用") or contract.get("消费方应用") or ""),
                    "数据总条数": _as_int(values.get("总条数"), contract.get("数据总条数") or DEFAULT_TOTAL_ROWS),
                })

            previous_internal = state.current.get(service_code)
            previous = state.contracts.get(previous_internal) if previous_internal else None
            if previous_internal != internal:
                if previous is not None:
                    previous["当前"] = False
                    previous["状态"] = "已换版"
                contract["当前"] = True
                contract["状态"] = "已发布"
                state.current[service_code] = internal
                reason = self._switch_reason(previous, contract)
                invalidated = state.invalidate_segments(
                    lambda segment: segment.get("服务编号") == service_code
                    and segment.get("契约键") != contract_key,
                    reason,
                )
                state.invalidate_sessions(
                    lambda session: session.get("服务编号") == service_code
                    and session.get("契约键") != contract_key,
                    reason,
                )
            else:
                contract["当前"] = True

            contract["更新时间"] = now_text()
            self._sync_service_row(contract)

            message = (
                f"契约 {contract_key} 复用服务版本 v{version}（第 {_as_int(contract.get('复用次数'))} 次复用），未生成新版本"
                if reused
                else f"契约 {contract_key} 已发布为服务版本 v{version}"
            )
            if invalidated:
                message += f"；{len(invalidated)} 个页段结果已失效"
            audit.record(
                "契约复用" if reused else "契约发布",
                service_code=service_code,
                service_version=version,
                contract_key=contract_key,
                rule_versions=contract.get("规则版本"),
                reused=reused,
                conclusion=message,
                detail={"失效页段": len(invalidated), "请求编号": request_id},
            )
            result = {
                "ok": True,
                "reused": reused,
                "message": message,
                "contract_key": contract_key,
                "version": version,
                "service_code": service_code,
                "rule_versions": contract.get("规则版本"),
                "返回字段": contract.get("返回字段"),
                "分页最大条数": contract.get("分页最大条数"),
                "每日调用上限": contract.get("每日调用上限"),
                "invalidated": len(invalidated),
                "invalidated_segments": [segment.get("页段编号") for segment in invalidated],
            }
            if request_id:
                _remember_request(("publish", str(request_id)), {"指纹": fingerprint, "结果": result})
            return result

    def _next_version(self, service_code: str) -> int:
        versions = [
            _as_int(contract.get("服务版本"), 0)
            for contract in state.contracts_of_service(service_code)
        ]
        row = self.find_by_code(service_code)
        seeded = _as_int(row.get("版本号"), 0) if row else 0
        if not versions:
            # 服务还没有契约：沿用示例登记的服务版本号，首个契约不额外跳号。
            return max(seeded, 1)
        current = self.current_service_version(service_code)
        return max([current, seeded, *versions, 0]) + 1

    def _service_name(self, service_code: str) -> str:
        row = self.find_by_code(service_code)
        return str(row.get("数据集名称") or "") if row else ""

    def _switch_reason(self, previous: dict[str, Any] | None, contract: dict[str, Any]) -> str:
        if previous is None:
            return f"服务契约 {contract['契约键']} 生效，旧页段结果失效"
        rule_note = ""
        if previous.get("规则版本") != contract.get("规则版本"):
            rule_note = f"（规则版本 {previous.get('规则版本')} -> {contract.get('规则版本')}）"
        return (
            f"服务契约由 {previous.get('契约键')} 换为 {contract.get('契约键')}"
            f"{rule_note}，服务版本 v{contract.get('服务版本')} 生效，请从首段重新开始"
        )

    def _sync_service_row(self, contract: dict[str, Any]) -> None:
        service_code = str(contract.get("服务编号") or "")
        row = self.find_by_code(service_code)
        if row is None:
            rows = store.rows(MODULE)
            row = {"id": max((int(item.get("id", 0)) for item in rows), default=0) + 1}
            rows.append(row)
        row.update({
            "服务编号": service_code,
            "数据集名称": contract.get("数据集名称"),
            "查询参数定义": contract.get("查询参数定义"),
            "返回字段": "、".join(contract.get("返回字段") or []),
            "速率限制": contract.get("速率限制"),
            "分页最大条数": contract.get("分页最大条数"),
            "版本号": contract.get("服务版本"),
            "消费方应用": contract.get("消费方应用"),
            "契约键": contract.get("契约键"),
            "规则版本明细": contract.get("规则版本明细"),
            "规则版本": contract.get("规则版本"),
            "契约复用次数": contract.get("复用次数"),
            "status": "已发布",
            "pending": False,
            "abnormal": False,
        })

    # ------------------------------------------------------------ 契约查询
    def list_contracts(
        self,
        *,
        service_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with state.lock:
            rows = sorted(
                (dict(contract) for contract in state.contracts.values()),
                key=lambda item: (item.get("服务编号", ""), _as_int(item.get("服务版本"))),
            )
        if service_code:
            rows = [row for row in rows if row.get("服务编号") == service_code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def contract_stats(self) -> dict[str, Any]:
        with state.lock:
            contracts = list(state.contracts.values())
            segments = list(state.segments)
            conflicts = list(state.conflicts)
            sessions = list(state.sessions.values())
        return {
            "契约数量": len(contracts),
            "契约复用数量": sum(_as_int(contract.get("复用次数")) for contract in contracts),
            "复用契约数": sum(1 for contract in contracts if _as_int(contract.get("复用次数")) > 0),
            "失效页段": sum(1 for segment in segments if segment.get("状态") == "已失效"),
            "有效页段": sum(1 for segment in segments if segment.get("状态") == "有效"),
            "冲突版本": len(conflicts),
            "读取会话": len(sessions),
            "已失效会话": sum(1 for session in sessions if session.get("状态") == "已失效"),
        }

    # ------------------------------------------------------------ 读取契约
    def read_page(self, values: dict[str, Any]) -> dict[str, Any]:
        service_code = str(values.get("服务编号") or "").strip()
        contract_key = str(values.get("契约键") or "").strip() or None
        request_id = str(values.get("请求编号") or "").strip() or None
        page = _as_int(values.get("页码", values.get("page")), 1)
        session_id = str(values.get("会话编号") or values.get("session_id") or "").strip() or None

        with state.lock:
            contract = self.resolve_contract(service_code, contract_key) if service_code else None
            if contract is None:
                message = (
                    f"数据服务 {service_code or '（未指定）'} 尚未发布契约，读取不可用"
                    if service_code
                    else "读取请求缺少服务编号"
                )
                return self._failure("CONTRACT_NOT_PUBLISHED", message)

            if not contract.get("当前"):
                return self._failure(
                    "CONTRACT_SUPERSEDED",
                    f"契约 {contract.get('契约键')} 已被服务版本 v{self.current_service_version(service_code)} 取代；"
                    "规则或契约已换版，请从首段重新开始",
                    contract=contract,
                    extra={"需要重新开始": True},
                )
            if contract.get("状态") != "已发布":
                return self._failure(
                    "CONTRACT_UNAVAILABLE",
                    f"契约 {contract.get('契约键')} 当前不可用（{contract.get('状态')}），不会返回上一次归因结果",
                    contract=contract,
                )

            availability = self._rule_availability(contract)
            if not availability["ok"]:
                restart = page > 1 or bool(session_id)
                return self._failure(
                    "RULE_UNAVAILABLE",
                    availability["message"] + ("；请从首段重新开始" if restart else ""),
                    contract=contract,
                    extra={"需要重新开始": True} if restart else None,
                )

            raw_size = values.get("条数", values.get("size", values.get("每页条数")))
            cap = _as_int(contract.get("分页最大条数"), DEFAULT_PAGE_CAP)
            size = _as_int(raw_size, cap) if raw_size not in (None, "") else cap
            if size <= 0:
                return self._failure("INVALID_REQUEST", "条数必须为正整数", contract=contract)
            if size > cap:
                return self._failure(
                    "PAGE_SIZE_EXCEEDED",
                    f"单次返回上限为 {cap} 条，请求 {size} 条已在入口拒绝",
                    contract=contract,
                )
            if page <= 0:
                return self._failure("INVALID_REQUEST", "页码必须从 1 开始", contract=contract)

            signature = json.dumps(
                {"契约键": contract.get("契约键"), "页码": page, "条数": size, "会话编号": session_id},
                ensure_ascii=False,
                sort_keys=True,
            )
            if request_id:
                cached = self._request_cache.get(("read", request_id))
                if cached:
                    if cached["指纹"] == signature:
                        return {**cached["结果"], "重复请求": True, "message": "重复读取请求，返回首次页段结果"}
                    conflict = state.add_conflict(
                        服务编号=service_code,
                        期望版本=None,
                        当前版本=self.current_service_version(service_code),
                        来源="重复读取请求",
                        请求编号=request_id,
                    )
                    return self._failure(
                        "REQUEST_MISMATCH",
                        "重复请求携带了不同的读取参数，拒绝第二个页段结果",
                        contract=contract,
                        extra={"conflict": conflict},
                    )

            quota = self._consume_quota(contract, values.get("消费方应用"))
            if not quota["ok"]:
                return self._failure("QUOTA_EXCEEDED", quota["message"], contract=contract)

            if page == 1:
                # 首段总是开新会话：避免调用方残留的会话编号被覆盖，导致旧页段串读。
                session = self._start_session(contract, size, None)
                segment = self._emit_segment(contract, session, size, request_id)
            else:
                if not session_id:
                    return self._failure(
                        "SESSION_REQUIRED",
                        "后续页段必须携带会话编号并从首段开始读取",
                        contract=contract,
                    )
                session = state.sessions.get(session_id)
                if session is None:
                    return self._failure(
                        "SESSION_NOT_FOUND",
                        "读取会话不存在或已过期，请从首段重新开始",
                        contract=contract,
                    )
                if session.get("状态") != "有效":
                    return self._failure(
                        "SESSION_INVALID",
                        f"{session.get('失效原因') or '读取会话已失效'}，请从首段重新开始",
                        contract=contract,
                        extra={"需要重新开始": True},
                    )
                if session.get("契约键") != contract.get("契约键"):
                    session["状态"] = "已失效"
                    session["失效原因"] = "契约已换版，请从首段重新开始"
                    return self._failure(
                        "CONTRACT_CHANGED",
                        "契约已换版，请从首段重新开始",
                        contract=contract,
                        extra={"需要重新开始": True},
                    )
                if session.get("规则版本明细") != contract.get("规则版本明细"):
                    self._invalidate_session(session, "归因规则已换版，请从首段重新开始")
                    return self._failure(
                        "RULE_VERSION_CHANGED",
                        "归因规则已换版，请从首段重新开始",
                        contract=contract,
                        extra={"需要重新开始": True},
                    )
                if size != _as_int(session.get("每页条数")):
                    return self._failure(
                        "INVALID_REQUEST",
                        f"页段读取须保持每次条数一致（会话为 {session.get('每页条数')} 条）",
                        contract=contract,
                    )
                expected = _as_int(session.get("下一页"), 2)
                if page != expected:
                    return self._failure(
                        "PAGE_ORDER",
                        f"页段次序错误，请先读取第 {expected} 页",
                        contract=contract,
                    )
                segment = self._emit_segment(contract, session, size, request_id)

            result = {
                "ok": True,
                "可用": True,
                "message": "读取成功",
                "服务编号": service_code,
                "契约键": contract.get("契约键"),
                "服务版本": contract.get("服务版本"),
                "规则版本": contract.get("规则版本"),
                "返回字段": contract.get("返回字段"),
                "会话编号": session["会话编号"],
                "页码": page,
                "条数": size,
                "单次返回上限": cap,
                "下一页": session["下一页"],
                "总条数": session["总条数"],
                "页段": segment,
                "items": self._rows(contract, page, size, _as_int(session.get("总条数"), DEFAULT_TOTAL_ROWS)),
            }
            if request_id:
                _remember_request(("read", request_id), {"指纹": signature, "结果": result})
            return result

    def _rule_availability(self, contract: dict[str, Any]) -> dict[str, Any]:
        from app.services.attribution import AttributionService

        rules = AttributionService()
        for item in contract.get("规则版本明细") or []:
            code = str(item.get("规则编号"))
            expected_version = _as_int(item.get("规则版本"), 1)
            entry = rules.find_by_code(code)
            if entry is None:
                return {"ok": False, "message": f"归因规则 {code} v{expected_version} 不存在，读取不可用，不会返回上一次归因结果"}
            actual_version = _as_int(entry.get("规则版本"), 1)
            if entry.get("status") != "已发布" or actual_version != expected_version:
                return {
                    "ok": False,
                    "message": (
                        f"归因规则 {code} v{expected_version} 当前不可用"
                        f"（状态 {entry.get('status')} / 实际版本 v{actual_version}），"
                        "读取明确不可用，不会返回上一次归因结果；请重新发布归因规则后再读取"
                    ),
                }
            if (
                entry.get("版本状态") != "已发布"
                or entry.get("可用状态") != "可用"
            ):
                # 场景：规则被下线后又重新发布同一内容；契约仍指向旧版本，读取必须明确不可用。
                return {
                    "ok": False,
                    "message": (
                        f"归因规则 {code} v{expected_version} 当前不可用"
                        f"（版本状态 {entry.get('版本状态')} / 可用状态 {entry.get('可用状态')}），"
                        "读取明确不可用，不会返回上一次归因结果；请重新发布归因规则后再读取"
                    ),
                }
        return {"ok": True}

    def _consume_quota(self, contract: dict[str, Any], consumer: Any) -> dict[str, Any]:
        limit = contract.get("每日调用上限")
        if not limit:
            return {"ok": True}
        name = str(consumer or contract.get("消费方应用") or "未标记消费方")
        key = (name, date.today().isoformat(), str(contract.get("服务编号")))
        calls = self._daily_calls.get(key, 0)
        if calls >= _as_int(limit):
            return {
                "ok": False,
                "message": f"消费方 {name} 今日调用已达上限 {limit} 次（速率限制：{contract.get('速率限制')}）",
            }
        self._daily_calls[key] = calls + 1
        return {"ok": True}

    def _start_session(self, contract: dict[str, Any], size: int, session_id: str | None) -> dict[str, Any]:
        session = {
            "会话编号": session_id or state.next_id("session", "SES"),
            "服务编号": contract.get("服务编号"),
            "契约键": contract.get("契约键"),
            "服务版本": contract.get("服务版本"),
            "规则版本明细": contract.get("规则版本明细"),
            "规则版本": contract.get("规则版本"),
            "每页条数": size,
            "总条数": _as_int(contract.get("数据总条数"), DEFAULT_TOTAL_ROWS),
            "下一页": 2,
            "已读页段": 1,
            "状态": "有效",
            "创建时间": now_text(),
            "页段": [],
        }
        state.sessions[session["会话编号"]] = session
        return session

    def _emit_segment(
        self,
        contract: dict[str, Any],
        session: dict[str, Any],
        size: int,
        request_id: str | None,
    ) -> dict[str, Any]:
        page = _as_int(session.get("已读页段"), 1)
        total = _as_int(session.get("总条数"), DEFAULT_TOTAL_ROWS)
        start = (page - 1) * size
        segment = state.add_segment(
            服务编号=contract.get("服务编号"),
            服务版本=contract.get("服务版本"),
            契约键=contract.get("契约键"),
            规则版本=contract.get("规则版本"),
            规则版本明细=contract.get("规则版本明细"),
            会话编号=session.get("会话编号"),
            页码=page,
            条数=size,
            起始位置=start + 1,
            结束位置=min(start + size, total),
            请求编号=request_id,
            状态="有效",
        )
        session["页段"].append(segment.get("页段编号"))
        session["已读页段"] = page + 1
        session["下一页"] = page + 1
        return dict(segment)

    def _invalidate_session(self, session: dict[str, Any], reason: str) -> None:
        session["状态"] = "已失效"
        session["失效原因"] = reason
        session["失效时间"] = now_text()
        state.invalidate_segments(
            lambda segment: segment.get("会话编号") == session.get("会话编号"),
            reason,
        )

    def _rows(self, contract: dict[str, Any], page: int, size: int, total: int) -> list[dict[str, Any]]:
        fields = contract.get("返回字段") or []
        rules = contract.get("规则版本明细") or []
        start = (page - 1) * size
        rows: list[dict[str, Any]] = []
        for index in range(start, min(start + size, total)):
            row: dict[str, Any] = {}
            for offset, field in enumerate(fields):
                if field in ("规则编号", "归因规则编号") and rules:
                    row[field] = rules[index % len(rules)].get("规则编号")
                elif "渠道" in field:
                    row[field] = f"渠道-{(index % 6) + 1}"
                elif "金额" in field:
                    row[field] = round(120.5 + index * 3.7, 2)
                elif any(token in field for token in ("数", "量")):
                    row[field] = 40 + (index * 7 + offset) % 260
                elif "日期" in field:
                    row[field] = f"2026-10-{(index % 28) + 1:02d}"
                else:
                    row[field] = f"{field}-{index + 1}"
            rows.append(row)
        return rows

    def _failure(
        self,
        code: str,
        message: str,
        *,
        contract: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "可用": False,
            "code": code,
            "message": message,
            "服务编号": contract.get("服务编号") if contract else None,
            "契约键": contract.get("契约键") if contract else None,
            "服务版本": contract.get("服务版本") if contract else None,
            "规则版本": contract.get("规则版本") if contract else None,
            "items": [],
        }
        if extra:
            result.update(extra)
        audit.record(
            "读取不可用",
            service_code=result.get("服务编号"),
            service_version=result.get("服务版本"),
            contract_key=result.get("契约键"),
            rule_versions=result.get("规则版本"),
            conclusion=message,
            detail={"code": code},
        )
        return result

    # ------------------------------------------------------------ 动作流转
    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"服务版本 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据服务可执行范围"
        if action == "发布服务":
            merged = {**entry, **(values or {})}
            merged.setdefault("规则版本明细", entry.get("规则版本明细"))
            result = self.publish_contract(
                merged,
                request_id=(values or {}).get("请求编号"),
                base_version=(values or {}).get("当前版本", UNSET),
            )
            if not result.get("ok"):
                return None, result.get("message") or "服务发布失败"
            entry = store.find(MODULE, entry_id) or entry
            return entry, result.get("message") or "服务版本已发布"
        if action == "下线服务":
            service_code = str(entry.get("服务编号") or "")
            entry["status"] = "已下线"
            entry["pending"] = False
            entry["abnormal"] = True
            reason = f"数据服务 {service_code} 已下线"
            state.invalidate_segments(
                lambda segment: segment.get("服务编号") == service_code,
                reason,
            )
            state.invalidate_sessions(
                lambda session: session.get("服务编号") == service_code,
                reason,
            )
            audit.record("服务下线", service_code=service_code, contract_key=entry.get("契约键"), conclusion=reason)
            return entry, f"服务版本已{action}，读取将明确返回不可用"

        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"服务版本已{action}"

    # ------------------------------------------------------------ 读取视图
    def list_segments(
        self,
        *,
        service_code: str | None = None,
        contract_key: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = state.segments_for(service_code=service_code, contract_key=contract_key, status=status)
        rows = sorted(rows, key=lambda item: _as_int(str(item.get("页段编号", "")).split("-")[-1]), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_conflicts(
        self,
        *,
        service_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with state.lock:
            rows = list(reversed(state.conflicts))
        if service_code:
            rows = [row for row in rows if row.get("服务编号") == service_code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total
