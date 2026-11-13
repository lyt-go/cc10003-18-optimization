"""数据服务接口：维护服务版本与返回契约，并提供分页读取入口。

具体路径必须注册在 /{entry_id} 之前：/contracts、/stats、/segments、
/conflicts、/read 这些都是固定路径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.audit import audit
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.delivery import DeliveryService

router = APIRouter(prefix="/api/delivery", tags=["数据服务"])

service = DeliveryService()

LIST_FIELDS = ["服务编号", "数据集名称", "查询参数定义", "返回字段", "速率限制", "分页最大条数", "版本号", "消费方应用"]
STATUSES = ["草稿", "服务评审中", "已发布", "已下线"]

# 读取失败时给出明确 HTTP 状态：服务端错误用 503，其余是调用方请求问题。
READ_STATUS = {
    "CONTRACT_NOT_PUBLISHED": 409,
    "CONTRACT_SUPERSEDED": 409,
    "CONTRACT_UNAVAILABLE": 503,
    "RULE_UNAVAILABLE": 503,
    "PAGE_SIZE_EXCEEDED": 400,
    "INVALID_REQUEST": 400,
    "QUOTA_EXCEEDED": 429,
    "SESSION_REQUIRED": 400,
    "SESSION_NOT_FOUND": 409,
    "SESSION_INVALID": 409,
    "CONTRACT_CHANGED": 409,
    "RULE_VERSION_CHANGED": 409,
    "PAGE_ORDER": 409,
    "REQUEST_MISMATCH": 409,
}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按服务编号检索"),
    status: str | None = Query(default=None, description="草稿、服务评审中、已发布、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按服务编号与状态过滤数据服务列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据服务清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "delivery", "total": total, "items": items}


@router.get("/contracts")
def list_contracts(
    service_code: str | None = Query(default=None, alias="服务编号"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """契约清单：同一契约只有一份，复用次数直接可见。"""
    items, total = service.list_contracts(service_code=service_code, page=page, size=size)
    return {"module": "delivery", "items": items, "total": total, "page": page, "size": size}


@router.get("/stats")
def contract_stats() -> dict[str, Any]:
    """验收口径：契约数量、契约复用数量、失效页段、冲突版本。"""
    return service.contract_stats()


@router.get("/segments")
def list_segments(
    service_code: str | None = Query(default=None, alias="服务编号"),
    contract_key: str | None = Query(default=None, alias="契约键"),
    status: str | None = Query(default=None, alias="状态"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """页段结果：可以按服务编号 / 契约键 / 状态查看失效范围。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_segments(
        service_code=service_code,
        contract_key=contract_key,
        status=status,
        page=page,
        size=size,
    )
    return {"module": "delivery", "items": items, "total": total, "page": page, "size": size}


@router.get("/conflicts")
def list_conflicts(
    service_code: str | None = Query(default=None, alias="服务编号"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """冲突版本：并发发布或重复请求被拒绝时留下的记录。"""
    items, total = service.list_conflicts(service_code=service_code, page=page, size=size)
    return {"module": "delivery", "items": items, "total": total, "page": page, "size": size}


@router.post("/read")
def read_page(payload: EntryPayload) -> dict[str, Any]:
    """分页读取归因数据服务。

    首段从 page=1 开始；后续页段带会话编号和同一契约键按固定次序读取。
    单次条数超过契约上限在入口直接拒绝；规则换版 / 不可用时明确返回不可用。
    """
    result = service.read_page(payload.values)
    if result.get("ok"):
        return result
    status = READ_STATUS.get(result.get("code"), 400)
    return JSONResponse(status_code=status, content=result)


@router.get("/audit/logs")
def audit_logs(
    action: str | None = Query(default=None, description="动作名，如 契约发布 / 契约复用 / 读取不可用"),
    service_code: str | None = Query(default=None, alias="服务编号"),
    rule_code: str | None = Query(default=None, alias="规则编号"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """审计入口：数据服务侧的契约发布、复用、换版、冲突与读取不可用记录。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = audit.list_entries(
        action=action,
        service_code=service_code,
        rule_code=rule_code,
        page=page,
        size=size,
    )
    return {"module": "delivery", "items": items, "total": total, "page": page, "size": size}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条服务版本，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="服务版本已登记", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条服务版本明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"服务版本 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条服务版本执行提交服务评审、发布服务、下线服务；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
