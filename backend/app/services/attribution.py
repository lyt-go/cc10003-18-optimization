"""归因模型业务规则：状态流转、规则版本、批量发布与契约联动。

一条归因规则被发布后才具备对外服务能力；规则内容再次发布时规则版本 +1，
并按规则版本找到引用了旧版本的页段结果置为失效。规则下线或回到评审中时
读取侧必须明确报不可用，不允许回退到上一次的归因结果。
"""
from __future__ import annotations

import json
from typing import Any

from app.audit import audit
from app.contract_state import now_text, state
from app.contracts import rule_versions_text
from app.store import store

MODULE = "attribution"
REQUIRED_FIELDS = ["规则编号", "归因类型", "回溯窗口天"]
RULE_CONTENT_FIELDS = ["归因类型", "回溯窗口天", "触点权重", "首次触点权重", "末次触点权重", "中间触点衰减", "关联渠道"]
STATUS_ORDER = ["草稿", "规则评审中", "已发布", "已下线"]
ACTION_RULES = {"提交规则评审": "规则评审中", "发布规则": "已发布", "下线规则": "已下线"}
NEGATIVE_ACTIONS = ["下线规则"]
CONTRACT_REQUIRED = ["服务编号", "返回字段", "查询参数定义", "速率限制", "分页最大条数", "消费方应用"]


def _signature(entry: dict[str, Any]) -> str:
    payload = {field: entry.get(field) for field in RULE_CONTENT_FIELDS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AttributionService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def find_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("规则编号") == code:
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in RULE_CONTENT_FIELDS if field in values})
        entry["规则编号"] = values.get("规则编号")
        for field in REQUIRED_FIELDS:
            entry.setdefault(field, values.get(field))
        entry["规则版本"] = 1
        entry["版本状态"] = "未发布"
        entry["可用状态"] = "未发布"
        entry["发布签名"] = None
        entry["版本历史"] = [{"规则版本": 1, "动作": "登记", "时间": now_text()}]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    # ---------------------------------------------------------------- 发布
    def _prospective_version(self, entry: dict[str, Any]) -> int:
        """本次发布会得到的规则版本（不落库）。"""
        version = max(_as_int(entry.get("规则版本"), 1), 1)
        if entry.get("发布签名") is not None and entry.get("发布签名") != _signature(entry):
            return version + 1
        return version

    def _publish_rule(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        """把一条规则推进到已发布；内容变化时升版。返回（规则, 说明, 是否升版）。"""
        code = str(entry.get("规则编号") or "")
        signature = _signature(entry)
        version = max(_as_int(entry.get("规则版本"), 1), 1)
        # 只和「上一次真正发布过的内容」比较：规则先回到评审中再改内容发布，
        # 也必须升版并让引用旧版本的页段失效，不能原地改写已发布的版本。
        published_before = entry.get("发布签名") is not None
        unchanged = published_before and entry.get("发布签名") == signature
        already_published = entry.get("版本状态") == "已发布" and entry.get("status") == "已发布"

        if already_published and unchanged:
            audit.record(
                "规则发布",
                rule_versions=f"{code}@v{version}",
                service_code=None,
                conclusion="规则未变化，版本保持",
            )
            return entry, f"归因规则 {code} 已发布，规则版本保持 v{version}", False

        previous_version = version
        bumped = published_before and not unchanged
        if bumped:
            version += 1
        entry["规则版本"] = version
        entry["版本状态"] = "已发布"
        entry["可用状态"] = "可用"
        entry["发布签名"] = signature
        entry["status"] = "已发布"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["发布时间"] = now_text()
        history = entry.setdefault("版本历史", [])
        history.append({"规则版本": version, "动作": "发布", "时间": entry["发布时间"]})

        if bumped:
            reason = f"归因规则 {code} 由 v{previous_version} 升至 v{version}"
            invalidated = state.invalidate_segments(
                lambda segment: any(
                    item.get("规则编号") == code
                    and _as_int(item.get("规则版本")) != version
                    for item in segment.get("规则版本明细", [])
                ),
                reason,
            )
            state.invalidate_sessions(
                lambda session: any(
                    item.get("规则编号") == code
                    and _as_int(item.get("规则版本")) != version
                    for item in session.get("规则版本明细", [])
                ),
                reason,
            )
            audit.record(
                "规则换版",
                rule_versions=f"{code}@v{version}",
                conclusion=reason,
                detail={"失效页段": len(invalidated)},
            )
        else:
            audit.record("规则发布", rule_versions=f"{code}@v{version}", conclusion="首次发布")

        return entry, f"归因规则 {code} 已发布，规则版本 v{version}", bumped

    def _take_offline(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        code = str(entry.get("规则编号") or "")
        version = max(_as_int(entry.get("规则版本"), 1), 1)
        entry["status"] = "已下线"
        entry["版本状态"] = "已下线"
        entry["可用状态"] = "不可用"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["下线时间"] = now_text()
        reason = f"归因规则 {code} v{version} 已下线"
        invalidated = state.invalidate_segments(
            lambda segment: any(item.get("规则编号") == code for item in segment.get("规则版本明细", [])),
            reason,
        )
        state.invalidate_sessions(
            lambda session: any(item.get("规则编号") == code for item in session.get("规则版本明细", [])),
            reason,
        )
        audit.record(
            "规则下线",
            rule_versions=f"{code}@v{version}",
            conclusion=reason,
            detail={"失效页段": len(invalidated)},
        )
        return entry, f"归因规则 {code} 已下线，对外读取将明确返回不可用"

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        with state.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"归因规则 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于归因模型可执行范围"
            if action == "发布规则":
                entry, message, _ = self._publish_rule(entry)
                return entry, message
            if action == "下线规则":
                return self._take_offline(entry)

            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            if target == "规则评审中":
                entry["版本状态"] = "评审中"
                entry["可用状态"] = "不可用"
            return entry, f"归因规则已{action}"

    def batch_actions(self, ids: list[Any], action: str) -> dict[str, Any]:
        """多选批量动作：逐条给结论，单条失败不影响其他条目。"""
        with state.lock:
            results: list[dict[str, Any]] = []
            succeeded = 0
            for raw_id in ids:
                entry = store.find(MODULE, _as_int(raw_id))
                if entry is None:
                    results.append({"id": raw_id, "规则编号": None, "ok": False, "message": f"归因规则 {raw_id} 不存在"})
                    continue
                updated, message = self.run_action(int(entry["id"]), action)
                ok = updated is not None
                succeeded += 1 if ok else 0
                results.append({
                    "id": entry["id"],
                    "规则编号": entry.get("规则编号"),
                    "规则版本": entry.get("规则版本"),
                    "ok": ok,
                    "message": message,
                })
            return {
                "ok": bool(results) and succeeded == len(results),
                "message": f"批量{action}：成功 {succeeded}/{len(results)} 条",
                "results": results,
            }

    # ------------------------------------------------------------ 批量发布
    def _rule_conclusion(self, identifier: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        entry = None
        if isinstance(identifier, dict):
            code = str(identifier.get("规则编号") or "").strip()
            entry = self.find_by_code(code) if code else None
            if entry is None and identifier.get("id") is not None:
                entry = store.find(MODULE, _as_int(identifier.get("id")))
        else:
            text = str(identifier).strip()
            entry = self.find_by_code(text) if text else None
            if entry is None and text.isdigit():
                entry = store.find(MODULE, int(text))
        if entry is None:
            return None, {"规则编号": str(identifier), "ok": False, "message": f"归因规则 {identifier} 不存在"}
        return entry, {
            "id": entry.get("id"),
            "规则编号": entry.get("规则编号"),
            "规则版本": max(_as_int(entry.get("规则版本"), 1), 1),
            "status": entry.get("status"),
            "ok": True,
            "message": "",
        }

    def batch_publish(self, values: dict[str, Any]) -> dict[str, Any]:
        """发布入口：多选归因规则一次提交；逐条给结论，整组契约完整才允许发布。"""
        raw_ids = (
            values.get("规则编号列表")
            or values.get("规则列表")
            or values.get("归因规则列表")
            or values.get("规则_id")
            or values.get("ids")
            or []
        )
        if isinstance(raw_ids, (str, int)):
            raw_ids = [raw_ids]
        if not raw_ids:
            return {"ok": False, "message": "请至少选择一条归因规则", "results": [], "contract": None}

        missing = [
            field for field in CONTRACT_REQUIRED
            if not str(values.get(field) or "").strip()
        ]
        conclusions: list[dict[str, Any]] = []
        selected: list[dict[str, Any]] = []
        for identifier in raw_ids:
            entry, conclusion = self._rule_conclusion(identifier)
            if entry is not None and entry.get("status") == "已下线":
                conclusion["ok"] = False
                conclusion["message"] = f"归因规则 {entry.get('规则编号')} 已下线，不能发布"
                entry = None
            if entry is not None:
                selected.append(entry)
            conclusions.append(conclusion)

        if missing:
            for conclusion in conclusions:
                if conclusion["ok"]:
                    conclusion["message"] = "整组契约缺少字段，未发布"
            return {
                "ok": False,
                "message": f"整组契约不完整，缺少：{'、'.join(missing)}",
                "results": conclusions,
                "contract": None,
            }
        failed = [item for item in conclusions if not item["ok"]]
        if failed:
            for conclusion in conclusions:
                if conclusion["ok"]:
                    conclusion["message"] = "同组存在无效规则，整组未发布"
            return {
                "ok": False,
                "message": f"同组有 {len(failed)} 条归因规则不满足发布条件，整组未发布",
                "results": conclusions,
                "contract": None,
            }
        # 规则发布后必须处于已发布 / 可用，否则契约发布成功也无法对外读取。
        blocked = [
            entry for entry in selected
            if entry.get("status") == "已下线" or entry.get("可用状态") == "停用"
        ]
        if blocked:
            for conclusion in conclusions:
                if conclusion["ok"]:
                    conclusion["message"] = "同组存在已下线或停用的规则，整组未发布"
            return {
                "ok": False,
                "message": "同组存在已下线或停用的归因规则，整组未发布",
                "results": conclusions,
                "contract": None,
            }

        # 并发版本在 delivery.publish_contract 内和建契约一起原子校验：
        # 冲突时不改规则版本，并把第二个版本记入冲突版本。
        from app.services.delivery import DeliveryService, UNSET

        delivery = DeliveryService()
        base_version = values.get("当前版本", values.get("base_version", UNSET))

        with state.lock:
            contract_values = dict(values)
            preview = delivery.check_publish(
                {
                    **contract_values,
                    "规则版本明细": [
                        {"规则编号": str(entry.get("规则编号")), "规则版本": self._prospective_version(entry)}
                        for entry in selected
                    ],
                },
                base_version=base_version,
                request_id=contract_values.get("请求编号") or contract_values.get("request_id"),
            )
            if preview.get("duplicate"):
                cached = preview["duplicate"]
                for conclusion in conclusions:
                    if conclusion["ok"]:
                        conclusion["message"] = "重复发布请求，返回首次发布结果，未生成第二个版本"
                return {
                    "ok": True,
                    "message": "重复发布请求，返回首次发布结果，未生成第二个版本",
                    "results": conclusions,
                    "contract": cached,
                    "duplicate": True,
                }
            if not preview.get("ok"):
                conflict = None
                if preview.get("code") in ("CONTRACT_CONFLICT", "REQUEST_MISMATCH"):
                    conflict = delivery.record_conflict(
                        contract_values,
                        preview,
                        request_id=contract_values.get("请求编号") or contract_values.get("request_id"),
                        source="批量发布",
                    )
                for conclusion in conclusions:
                    conclusion["ok"] = False
                    conclusion["message"] = f"{preview.get('message')}；整组未发布"
                return {
                    "ok": False,
                    "message": preview.get("message") or "整组契约未通过发布校验",
                    "results": conclusions,
                    "contract": None,
                    "conflict": conflict,
                }

            rule_versions: list[tuple[str, int]] = []
            for entry, conclusion in zip([item for item in selected if item is not None], [item for item in conclusions if item["ok"]]):
                published, message, _ = self._publish_rule(entry)
                conclusion["规则版本"] = published.get("规则版本")
                conclusion["message"] = message
                rule_versions.append(
                    (str(published.get("规则编号")), max(_as_int(published.get("规则版本"), 1), 1))
                )

            contract_values["规则版本明细"] = [
                {"规则编号": code, "规则版本": version} for code, version in rule_versions
            ]
            contract_values["规则版本"] = rule_versions_text(rule_versions)
            contract = delivery.publish_contract(
                contract_values,
                request_id=contract_values.get("请求编号") or contract_values.get("request_id"),
                base_version=base_version,
            )

        contract_ok = bool(contract.get("ok"))
        for conclusion in conclusions:
            if not conclusion["ok"]:
                continue
            if contract_ok:
                conclusion["message"] = (
                    f"规则版本 v{conclusion.get('规则版本')}；"
                    + ("契约复用，未生成新服务版本" if contract.get("reused") else f"契约已发布为服务版本 v{contract.get('version')}")
                )
            else:
                conclusion["ok"] = False
                conclusion["message"] = f"契约发布失败：{contract.get('message')}"

        return {
            "ok": contract_ok,
            "message": contract.get("message") if not contract_ok else "整组契约已发布",
            "results": conclusions,
            "contract": contract if contract_ok else None,
            "invalidated": contract.get("invalidated"),
            "conflict": contract.get("conflict"),
        }

    def initialize_published_signatures(self) -> None:
        """启动引导：给示例里已发布的规则补上发布签名。

        有了签名，后续调整规则内容再发布会正确升版；没有签名时首次发布会
        被当成「首次发布」而保持原版本。
        """
        with state.lock:
            for entry in store.rows(MODULE):
                if entry.get("status") == "已发布" and entry.get("发布签名") is None:
                    entry["发布签名"] = _signature(entry)
                    entry.setdefault("规则版本", 1)
                    entry.setdefault("版本状态", "已发布")
                    entry.setdefault("可用状态", "可用")
