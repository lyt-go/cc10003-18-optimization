"""启动引导：把示例数据里的已发布服务登记为可复用契约。

只登记契约，不额外推进版本：示例里的 DELI-0003 已经是已发布状态，
引导后读取入口和「相同契约再次发布复用」都能立刻验收。
"""
from __future__ import annotations

from app.services.attribution import AttributionService
from app.services.delivery import DeliveryService


def bootstrap_contracts() -> None:
    AttributionService().initialize_published_signatures()
    delivery = DeliveryService()
    for row in list(delivery.list_entries(page=1, size=10000)[0]):
        if row.get("status") != "已发布":
            continue
        code = str(row.get("服务编号") or "")
        if not code or delivery.resolve_contract(code):
            continue
        delivery.publish_contract(
            {**row, "规则版本明细": row.get("规则版本明细") or row.get("规则版本")},
            base_version=None,
        )
