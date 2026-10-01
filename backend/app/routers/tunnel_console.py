"""洞群任务编排台接口。

计划签发、作业执行、现场收口的单向流转都走 service；路由层不做业务判断，
只负责参数透传与结果包装。批量计划提交后进异步队列，前端轮询批次进度。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.queue_app import batch_queue, console_service as service
from app.schemas import ActionResult, EntryPayload
from app.services.tunnel_console import FlowError

router = APIRouter(prefix="/api/tunnel-console", tags=["洞群任务编排台"])


@router.get("/meta")
def console_meta() -> dict[str, Any]:
    """班组、检查项、可执行状态等前端渲染所需的字典。"""
    return service.meta()


@router.get("/tunnels")
def tunnel_options() -> list[dict[str, Any]]:
    """按洞群分组返回可多选隧道。"""
    return service.tunnel_options()


@router.get("/map")
def map_view() -> dict[str, Any]:
    """洞群示意图数据：隧道坐标、作业窗口与互相影响连线。"""
    return service.map_view()


# ------------------------------------------------------------ 批量计划与队列
@router.post("/batches", response_model=ActionResult)
def submit_batch(payload: EntryPayload) -> ActionResult:
    """多选隧道 + 检查类别生成班组任务批次，提交后进入异步队列。"""
    batch, message = service.submit_batch(payload.values)
    if batch is None:
        return ActionResult(ok=False, message=message)
    batch_queue.enqueue(int(batch["id"]))
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/batches")
def list_batches() -> list[dict[str, Any]]:
    return service.list_batches()


@router.post("/batches/{batch_id}/reset", response_model=ActionResult)
def reset_batch(batch_id: int) -> ActionResult:
    """批次复位：从未完成项继续，已完成项靠业务键不重复生成。"""
    batch, message = service.reset_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    batch_queue.enqueue(batch_id)
    return ActionResult(ok=True, message=message, entry=batch)


# ------------------------------------------------------------ 任务流转
@router.get("/tasks")
def list_tasks(
    status: str | None = Query(default=None),
    tunnel_id: int | None = Query(default=None),
    检查类别: str | None = Query(default=None, alias="category"),
) -> list[dict[str, Any]]:
    return service.list_tasks(status=status, tunnel_id=tunnel_id, category=检查类别)


@router.post("/tasks/{task_id}/advance", response_model=ActionResult)
def advance_task(task_id: int, payload: EntryPayload) -> ActionResult:
    """签发计划 / 开始作业 / 完成作业 / 现场收口 / 取消任务。

    未完成前序确认不得跳级；收口时必须逐项给出检查结论与现场负责人。
    """
    values = dict(payload.values)
    action = str(values.pop("action", "") or "").strip()
    try:
        task, message = service.advance(task_id, action, values)
    except FlowError as exc:
        return ActionResult(ok=False, message=str(exc))
    if task is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=task)


@router.post("/tasks/{task_id}/adjust", response_model=ActionResult)
def adjust_plan(task_id: int, payload: EntryPayload) -> ActionResult:
    """待签发任务可改作业窗口；签发后窗口锁定，冲突任务不得覆盖已批准时段。"""
    try:
        task, message = service.adjust_plan(task_id, payload.values)
    except FlowError as exc:
        return ActionResult(ok=False, message=str(exc))
    if task is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=task)


@router.post("/tasks/{task_id}/approve", response_model=ActionResult)
def approve_plan(task_id: int, payload: EntryPayload) -> ActionResult:
    """冲突待裁或待签发计划由现场负责人批准；批准不改变任何既有已批准时段。"""
    try:
        task, message = service.approve_plan(task_id, payload.values)
    except FlowError as exc:
        return ActionResult(ok=False, message=str(exc))
    if task is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=task)


# ------------------------------------------------------------ 回写后的汇总页
@router.get("/checklist")
def checklist(
    tunnel_id: int | None = Query(default=None),
    crew: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """设施检查清单：历史检查按原班组快照留痕。"""
    return service.list_checklist(tunnel_id=tunnel_id, crew=crew, status=status)


@router.get("/dispatch")
def dispatch(
    status: str | None = Query(default=None),
    tunnel_id: int | None = Query(default=None),
) -> list[dict[str, Any]]:
    return service.list_dispatch(status=status, tunnel_id=tunnel_id)


@router.get("/dispatch/summary")
def dispatch_summary() -> dict[str, Any]:
    """车辆调度汇总页数据。"""
    return service.dispatch_summary()
