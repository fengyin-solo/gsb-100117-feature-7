"""洞群任务编排台接口。

- 多选隧道按通风/消防/照明批量生成班组任务，异步队列执行；
- 任务计划签发 → 作业执行 → 现场收口单向推进；
- 任务结论回写隧道台账、设施检查清单与车辆调度汇总；
- 同洞群地图展示互相影响的作业窗口与冲突。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchTaskPayload,
    PageResult,
    TaskActionPayload,
    TaskApprovalPayload,
)
from app.services.orchestration import CATEGORY_SPECS, orchestration_service

router = APIRouter(prefix="/api/orchestration", tags=["洞群任务编排台"])

service = orchestration_service
CATEGORIES = list(CATEGORY_SPECS.keys())
STAGES = ["计划签发", "作业执行", "现场收口", "待现场批准", "批准驳回"]


@router.get("/categories")
def categories() -> dict[str, Any]:
    """检查类别字典：通风/消防/照明各自对应的班组、车辆与检查项。"""
    return {"categories": [{"name": name, **spec} for name, spec in CATEGORY_SPECS.items()]}


@router.get("/tunnels")
def selectable_tunnels(keyword: str | None = None) -> dict[str, Any]:
    """编排台多选隧道列表，带走廊坐标，供地图与多选表格共用。"""
    return {"items": service.list_tunnels(keyword=keyword)}


@router.post("/batches", response_model=ActionResult)
def create_batch(payload: BatchTaskPayload) -> ActionResult:
    """批量签发：为「隧道 × 检查类别」逐行入异步队列，业务键幂等。"""
    batch, message = service.create_batch(
        tunnel_ids=payload.tunnel_ids,
        categories=payload.categories,
        plan_start=payload.plan_start,
        plan_end=payload.plan_end,
        leader=payload.leader,
        closure_required=payload.closure_required,
    )
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/batches/{batch_no}")
def get_batch(batch_no: str) -> dict[str, Any]:
    """查询批量进度：前端轮询，复位后只看未完成项是否继续。"""
    result = service.get_batch(batch_no)
    if result is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")
    return result


@router.post("/batches/{batch_no}/reset", response_model=ActionResult)
def reset_batch(batch_no: str) -> ActionResult:
    """复位批次：未完成项重新入队继续，已完成项按业务键幂等跳过。"""
    batch, message = service.reset_batch(batch_no)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/tasks", response_model=PageResult[dict])
def list_tasks(
    stage: str | None = Query(default=None, description="计划签发、作业执行、现场收口、待现场批准"),
    category: str | None = None,
    tunnel: str | None = Query(default=None, description="按隧道编号或名称检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """编排任务台账，支持按阶段、检查类别与隧道过滤。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_tasks(stage=stage, category=category, tunnel_keyword=tunnel, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    """任务详情：含回写生成的设施检查清单项与车辆调度待办。"""
    result = service.get_task(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return result


@router.post("/tasks/{task_id}/actions", response_model=ActionResult)
def run_task_action(task_id: int, payload: TaskActionPayload) -> ActionResult:
    """开始作业 / 现场收口；未完成前序确认不得跳级，收口结论三处回写。"""
    task, message = service.run_action(
        task_id,
        payload.action,
        conclusion=payload.conclusion,
        abnormal_items=payload.abnormal_items,
    )
    if task is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=task)


@router.post("/tasks/{task_id}/approval", response_model=ActionResult)
def approve_task(task_id: int, payload: TaskApprovalPayload) -> ActionResult:
    """现场负责人批准：同一隧道封闭或窗口冲突时以批准计划为准，禁止覆盖已批准时段。"""
    task, message = service.approve_task(
        task_id,
        decision=payload.decision,
        approver=payload.approver,
        approved_start=payload.approved_start,
        approved_end=payload.approved_end,
        note=payload.note,
    )
    if task is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=task)


@router.get("/map/windows")
def map_windows(date: str | None = Query(default=None, description="YYYY-MM-DD，留空返回全部")) -> dict[str, Any]:
    """洞群地图：走廊、隧道点位、作业窗口及互相影响的冲突标记。"""
    return service.map_windows(date=date)


@router.get("/checklist", response_model=PageResult[dict])
def list_checklist(
    category: str | None = None,
    result: str | None = Query(default=None, description="合格、异常待整改、待检查"),
    crew: str | None = Query(default=None, description="按执行班组检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """设施检查清单：历史检查按原班组留痕，收口后只回写结果不改班组。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_checklist(category=category, result=result, crew=crew, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/dispatch", response_model=PageResult[dict])
def list_dispatch(
    status: str | None = None,
    crew: str | None = None,
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """车辆调度待办：任务变更同步进入此页，不只是弹窗更新。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_dispatch(status=status, crew=crew, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/dispatch/summary")
def dispatch_summary() -> dict[str, Any]:
    """车辆调度汇总：按状态、班组统计待派车/作业中/已归队。"""
    return service.dispatch_summary()
