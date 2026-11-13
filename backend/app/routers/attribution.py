"""归因模型接口：维护归因规则，覆盖提交规则评审、发布规则、下线规则等动作。

发布入口同时承担「归因规则 -> 数据服务契约」的整组发布：
多条归因规则一次提交，逐条返回结论，整组契约完整后才允许发布。

注意：具体路径（/export、/batch/actions、/publish/batch、/audit/logs）必须注册在
/{entry_id} 这类动态路径之前，否则会被后者抢先匹配成 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.audit import audit
from app.schemas import ActionResult, BatchActionPayload, BatchResult, EntryPayload, PageResult
from app.services.attribution import AttributionService

router = APIRouter(prefix="/api/attribution", tags=["归因模型"])

service = AttributionService()

LIST_FIELDS = ["规则编号", "归因类型", "回溯窗口天", "触点权重", "首次触点权重", "末次触点权重", "中间触点衰减", "关联渠道"]
STATUSES = ["草稿", "规则评审中", "已发布", "已下线"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、规则评审中、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤归因模型列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出归因模型清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "attribution", "total": total, "items": items}


@router.get("/audit/logs")
def audit_logs(
    action: str | None = Query(default=None, description="动作名，如 契约发布 / 契约复用 / 规则换版"),
    service_code: str | None = Query(default=None, alias="服务编号"),
    rule_code: str | None = Query(default=None, alias="规则编号"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """审计入口：按动作、服务编号、规则编号过滤，记录里带规则版本。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = audit.list_entries(
        action=action,
        service_code=service_code,
        rule_code=rule_code,
        page=page,
        size=size,
    )
    return {"module": "attribution", "items": items, "total": total, "page": page, "size": size}


@router.post("/batch/actions", response_model=BatchResult)
def batch_actions(payload: BatchActionPayload) -> BatchResult:
    """多选批量动作：多条归因规则一次提交，动作逐条给结论。"""
    if not payload.ids:
        return BatchResult(ok=False, message="请至少选择一条归因规则")
    result = service.batch_actions(payload.ids, payload.action)
    return BatchResult(**result)


@router.post("/publish/batch", response_model=BatchResult)
def batch_publish(payload: EntryPayload) -> BatchResult:
    """批量发布入口：归因规则多选 + 契约字段一次提交。

    - 逐条返回规则结论；
    - 契约必须整组完整（服务编号、返回字段、请求参数、速率限制、条数上限、消费方）；
    - 相同契约复用服务版本，变化时生成新版本并失效旧页段；
    - 并发 / 重复请求只接受一个版本，另一个记入冲突版本。
    """
    result = service.batch_publish(payload.values)
    return BatchResult(**result)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条归因规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="归因规则已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条归因规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"归因规则 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条归因规则执行提交规则评审、发布规则、下线规则；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
