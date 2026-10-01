"""洞群任务编排业务规则。

任务按「计划签发 → 作业执行 → 现场收口」单向推进，未完成前序确认不得跳级；
任务结论一次性回写隧道台账、设施检查清单与车辆调度待办，不只活在弹窗里。

- 通风 / 消防 / 照明 三类检查项对应不同班组与作业车辆；
- 同一洞群（走廊）内作业窗口互相影响，地图接口给出冲突标记；
- 同一隧道已有封闭作业时，新任务不得直接签发，需现场负责人按批准计划放行；
- 批量任务走异步队列，以业务键（洞群编排:{批次号}:{隧道}-{类别}）幂等，
  复位后从未完成项继续，冲突任务不得覆盖已批准时段；
- 历史检查按原班组留痕：收口回写只动结果，不改生成时写入的班组。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "tunnel_task"

# 计划签发 → 作业执行 → 现场收口，单向状态推进，不可跳级
STAGE_ISSUED = "计划签发"
STAGE_EXECUTING = "作业执行"
STAGE_CLOSED = "现场收口"
STAGE_PENDING_APPROVAL = "待现场批准"
STAGE_REJECTED = "批准驳回"

# 允许的动作与其唯一前置阶段：计划签发 → 作业执行 → 现场收口，不可跳级
ACTION_TRANSITIONS = {
    "开始作业": STAGE_ISSUED,
    "现场收口": STAGE_EXECUTING,
}

# 检查类别 → 班组、车辆、检查项
CATEGORY_SPECS: dict[str, dict[str, Any]] = {
    "通风检查": {
        "班组": "通风机电班",
        "车辆": "VEHI-FN-01 通风检测车",
        "items": ["风机运转", "CO/VI 浓度检测", "排烟风道"],
    },
    "消防检查": {
        "班组": "消防设施班",
        "车辆": "VEHI-FF-02 消防巡检车",
        "items": ["消火栓水压", "灭火器有效期", "火灾报警联动"],
    },
    "照明检查": {
        "班组": "照明电气班",
        "车辆": "VEHI-LT-03 高空作业车",
        "items": ["灯具亮灯率", "应急照明", "照明回路绝缘"],
    },
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _parse_window(start: str, end: str) -> tuple[datetime, datetime] | None:
    try:
        s = datetime.strptime(start.strip(), "%Y-%m-%d %H:%M")
        e = datetime.strptime(end.strip(), "%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return None
    if e <= s:
        return None
    return s, e


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    a = _parse_window(a_start, a_end)
    b = _parse_window(b_start, b_end)
    if a is None or b is None:
        return False
    return a[0] < b[1] and b[0] < a[1]


class BatchQueue:
    """异步批量队列：后台线程逐行处理，业务键幂等，复位后从未完成项继续。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tunnel-batch-queue", daemon=True)
            self._thread.start()

    def enqueue(self) -> None:
        # 工作线程会扫批次里所有待处理项；入队只需唤醒
        with self._condition:
            self._condition.notify_all()

    def _run(self) -> None:
        service = orchestration_service
        while True:
            # 注意：编排锁与队列条件锁不能嵌套，否则会与入队请求形成死锁
            pending = service._pending_items()
            if not pending:
                with self._condition:
                    self._condition.wait(timeout=1.0)
                continue
            for item in pending:
                # 处理慢一点，前端能看到排队/处理中的真实过程
                time.sleep(0.15)
                service.process_item(int(item["id"]))


class OrchestrationService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.queue = BatchQueue()

    # ---------- 隧道选择 ----------

    def list_tunnels(self, *, keyword: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("tunnel")
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("隧道编号", "")) or keyword in str(row.get("隧道名称", ""))
            ]
        return rows

    # ---------- 批量签发 ----------

    def create_batch(
        self,
        *,
        tunnel_ids: list[int],
        categories: list[str],
        plan_start: str,
        plan_end: str,
        leader: str,
        closure_required: bool,
    ) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            tunnels = [store.find("tunnel", tid) for tid in dict.fromkeys(tunnel_ids)]
            tunnels = [row for row in tunnels if row is not None]
            cats = [cat for cat in dict.fromkeys(categories) if cat in CATEGORY_SPECS]
            window = _parse_window(plan_start, plan_end)
            if not tunnels:
                return None, "请至少选择一座隧道"
            if not cats:
                return None, "请至少选择通风、消防、照明中的一项检查类别"
            if window is None:
                return None, "计划作业窗口格式应为 YYYY-MM-DD HH:MM，且开始早于结束"

            rows = store.rows("tunnel_batch")
            batch_no = f"BATCH-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
            batch = {
                "id": _next_id("tunnel_batch"),
                "批次号": batch_no,
                "创建时间": _now_text(),
                "计划开始": plan_start,
                "计划结束": plan_end,
                "现场负责人": leader or "未指定",
                "需封闭交通": "是" if closure_required else "否",
                "status": "排队中",
            }
            rows.append(batch)

            item_rows = store.rows("tunnel_batch_item")
            for tunnel in tunnels:
                for cat in cats:
                    biz_key = f"洞群编排:{batch_no}:{tunnel.get('隧道编号')}-{cat}"
                    item_rows.append({
                        "id": _next_id("tunnel_batch_item"),
                        "批次号": batch_no,
                        "业务键": biz_key,
                        "隧道编号": tunnel.get("隧道编号"),
                        "隧道名称": tunnel.get("隧道名称"),
                        "检查类别": cat,
                        "处理状态": "待处理",
                        "结果说明": "",
                        "任务编号": "",
                    })
            self.queue.start()
            self.queue.enqueue()
            return batch, f"批次 {batch_no} 已进入异步队列，共 {len(tunnels) * len(cats)} 项"

    def _pending_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                item for item in store.rows("tunnel_batch_item")
                if item.get("处理状态") in {"待处理", "处理中"}
            ]

    def process_item(self, item_id: int) -> None:
        """队列消费：业务键幂等，复位后只继续未完成项。"""
        with self._lock:
            item = store.find("tunnel_batch_item", item_id)
            if item is None or item.get("处理状态") not in {"待处理", "处理中"}:
                return
            # 业务键幂等：已有任务直接关联，不重复签发
            existed = self._find_task_by_biz_key(str(item["业务键"]))
            if existed is not None:
                item["处理状态"] = "已完成"
                item["结果说明"] = "业务键已存在，复用既有任务"
                item["任务编号"] = existed.get("任务编号", "")
                return

            item["处理状态"] = "处理中"
            batch = self._find_batch(str(item["批次号"]))
            tunnel = self._find_tunnel_by_code(str(item["隧道编号"]))
            if batch is None or tunnel is None:
                item["处理状态"] = "失败"
                item["结果说明"] = "批次或隧道已不存在"
                return

            task, message = self._create_task(
                tunnel=tunnel,
                category=str(item["检查类别"]),
                plan_start=str(batch["计划开始"]),
                plan_end=str(batch["计划结束"]),
                leader=str(batch["现场负责人"]),
                closure_required=batch.get("需封闭交通") == "是",
                source_batch=str(item["批次号"]),
                biz_key=str(item["业务键"]),
            )
            if task is None:
                item["处理状态"] = "失败"
                item["结果说明"] = message
                return
            item["处理状态"] = "已完成"
            item["任务编号"] = task.get("任务编号", "")
            stage = task.get("当前阶段")
            item["结果说明"] = "已进入现场负责人批准队列" if stage == STAGE_PENDING_APPROVAL else message

    def reset_batch(self, batch_no: str) -> tuple[dict[str, Any] | None, str]:
        """复位：把未完成项重新置为待处理并继续，已完成项按业务键跳过，不重复生成。"""
        with self._lock:
            batch = self._find_batch(batch_no)
            if batch is None:
                return None, f"批次 {batch_no} 不存在"
            reset_count = 0
            for item in store.rows("tunnel_batch_item"):
                if item.get("批次号") != batch_no:
                    continue
                if item.get("处理状态") in {"待处理", "失败", "处理中"}:
                    item["处理状态"] = "待处理"
                    item["结果说明"] = "复位后重新入队"
                    reset_count += 1
            batch["status"] = "排队中"
            self.queue.start()
            self.queue.enqueue()
            return batch, f"批次已复位，{reset_count} 个未完成项重新入队，已完成项不再重复"

    def get_batch(self, batch_no: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self._find_batch(batch_no)
            if batch is None:
                return None
            items = [item for item in store.rows("tunnel_batch_item") if item.get("批次号") == batch_no]
            total = len(items)
            done = sum(1 for item in items if item.get("处理状态") == "已完成")
            failed = sum(1 for item in items if item.get("处理状态") == "失败")
            processing = sum(1 for item in items if item.get("处理状态") == "处理中")
            if failed and not processing:
                batch["status"] = "部分失败"
            elif done == total and total:
                batch["status"] = "已完成"
            elif processing:
                batch["status"] = "处理中"
            return {
                "batch": batch,
                "items": items,
                "progress": {
                    "total": total,
                    "done": done,
                    "failed": failed,
                    "pending": total - done - failed,
                },
            }

    # ---------- 任务台账 ----------

    def list_tasks(
        self,
        *,
        stage: str | None = None,
        category: str | None = None,
        tunnel_keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if stage:
            rows = [row for row in rows if row.get("当前阶段") == stage]
        if category:
            rows = [row for row in rows if row.get("检查类别") == category]
        if tunnel_keyword:
            rows = [
                row for row in rows
                if tunnel_keyword in str(row.get("隧道编号", ""))
                or tunnel_keyword in str(row.get("隧道名称", ""))
            ]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            task = store.find(MODULE, task_id)
            if task is None:
                return None
            task_no = str(task.get("任务编号", ""))
            return {
                "task": task,
                "checklist": [
                    row for row in store.rows("facility_checklist")
                    if row.get("任务编号") == task_no
                ],
                "dispatch": [
                    row for row in store.rows("vehicle_dispatch")
                    if row.get("任务编号") == task_no
                ],
            }

    # ---------- 签发 ----------

    def _create_task(
        self,
        *,
        tunnel: dict[str, Any],
        category: str,
        plan_start: str,
        plan_end: str,
        leader: str,
        closure_required: bool,
        source_batch: str = "",
        biz_key: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        spec = CATEGORY_SPECS.get(category)
        if spec is None:
            return None, f"检查类别「{category}」不在通风、消防、照明范围内"
        if _parse_window(plan_start, plan_end) is None:
            return None, "计划作业窗口无效，开始时间需早于结束时间"

        # 冲突判定：同洞群走廊内，与已批准/在执行/已收口的窗口互相影响。
        # 同一批次里同时段生成的任务属于同一份编排计划，互不阻断（地图仍互相标记），
        # 只与批次外已经占窗的任务冲突。
        conflicts = self.find_conflicts(
            corridor=str(tunnel.get("所属走廊", "")),
            start=plan_start,
            end=plan_end,
            exclude_batch=source_batch,
        )
        coordinated = self.find_conflicts(
            corridor=str(tunnel.get("所属走廊", "")),
            start=plan_start,
            end=plan_end,
            same_batch_only=source_batch,
        ) if source_batch else []
        # 同一隧道已有封闭作业：新任务只能以现场负责人批准的计划为准
        closure = self._active_closure(int(tunnel.get("id", 0)))
        needs_approval = bool(conflicts) or (closure is not None) or closure_required

        rows = store.rows(MODULE)
        prefix = {"通风检查": "FN", "消防检查": "FF", "照明检查": "LT"}[category]
        task = {
            "id": _next_id(MODULE),
            "任务编号": f"TSK-{prefix}-{datetime.now().strftime('%m%d')}-{uuid.uuid4().hex[:4].upper()}",
            "隧道编号": tunnel.get("隧道编号"),
            "隧道名称": tunnel.get("隧道名称"),
            "所属走廊": tunnel.get("所属走廊", ""),
            "检查类别": category,
            "班组": spec["班组"],
            "现场负责人": leader,
            "计划开始": plan_start,
            "计划结束": plan_end,
            "当前阶段": STAGE_PENDING_APPROVAL if needs_approval else STAGE_ISSUED,
            "签发时间": _now_text(),
            "开工时间": "",
            "收口时间": "",
            "任务结论": "",
            "异常项": "",
            "封闭交通": "是" if closure_required else "否",
            "已批准时段": "",
            "批准人": "",
            "批准说明": "",
            "来源批次": source_batch,
            "业务键": biz_key,
            "冲突任务": "、".join(sorted({str(c["任务编号"]) for c in conflicts})),
            "协同任务": "、".join(sorted({str(c["任务编号"]) for c in coordinated})),
            "既有封闭任务": str(closure.get("任务编号", "")) if closure else "",
            "pending": True,
            "abnormal": False,
        }
        rows.append(task)
        self._spawn_checklist_and_dispatch(task, spec)
        if needs_approval:
            reasons = []
            if conflicts:
                reasons.append("与同洞群已批准作业窗口冲突")
            if closure:
                reasons.append(f"同一隧道已有封闭作业 {closure.get('任务编号')}")
            if closure_required:
                reasons.append("该任务需封闭交通")
            return task, f"任务已生成，等待现场负责人按批准计划放行（{'；'.join(reasons)}）"
        return task, "任务计划已签发"

    def approve_task(
        self,
        task_id: int,
        *,
        decision: str,
        approver: str,
        approved_start: str = "",
        approved_end: str = "",
        note: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        """现场负责人批准：冲突任务只能改到不覆盖已批准时段的窗口，禁止强行覆盖。"""
        with self._lock:
            task = store.find(MODULE, task_id)
            if task is None:
                return None, f"任务 {task_id} 不存在"
            if task.get("当前阶段") != STAGE_PENDING_APPROVAL:
                return None, "仅待现场批准的任务可以审批"
            if not approver.strip():
                return None, "请填写现场负责人姓名以留痕"

            if decision == "驳回":
                task["当前阶段"] = STAGE_REJECTED
                task["批准人"] = approver
                task["批准说明"] = f"驳回：{note}" or "驳回"
                task["pending"] = False
                self._sync_dispatch(task)
                return task, "任务已驳回，未占用作业窗口"

            if decision != "批准":
                return None, "审批决定只支持「批准」或「驳回」"

            new_start = approved_start.strip() or str(task["计划开始"])
            new_end = approved_end.strip() or str(task["计划结束"])
            if _parse_window(new_start, new_end) is None:
                return None, "批准时段无效，开始时间需早于结束时间"

            blocked = self.find_conflicts(
                corridor=str(task.get("所属走廊", "")),
                start=new_start,
                end=new_end,
                exclude_task_id=task_id,
            )
            if blocked:
                return None, (
                    "批准时段仍与已批准作业冲突："
                    + "、".join(str(c["任务编号"]) for c in blocked)
                    + "，冲突任务不得覆盖已批准时段，请改期"
                )
            closure = self._active_closure(
                self._tunnel_id_by_code(str(task.get("隧道编号"))),
                exclude_task_id=task_id,
            )
            if closure is not None and not note.strip():
                return None, f"同一隧道封闭作业 {closure.get('任务编号')} 仍在执行，请填写与负责人确认的批准说明"

            task["计划开始"] = new_start
            task["计划结束"] = new_end
            task["已批准时段"] = f"{new_start} ~ {new_end}"
            task["批准人"] = approver
            task["批准说明"] = note or "按批准计划放行"
            task["当前阶段"] = STAGE_ISSUED
            task["冲突任务"] = ""
            self._sync_dispatch(task)
            return task, "任务已按现场负责人批准的计划放行"

    # ---------- 单向状态推进 ----------

    def run_action(
        self,
        task_id: int,
        action: str,
        *,
        conclusion: str = "",
        abnormal_items: str = "",
    ) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            task = store.find(MODULE, task_id)
            if task is None:
                return None, f"任务 {task_id} 不存在"
            if action not in ACTION_TRANSITIONS:
                return None, f"动作「{action}」不属于编排任务可执行范围"
            if task.get("当前阶段") == STAGE_PENDING_APPROVAL:
                return None, "任务尚待现场负责人批准，不能开始作业"
            if task.get("当前阶段") == STAGE_REJECTED:
                return None, "任务已被驳回，需重新签发"
            required_stage = ACTION_TRANSITIONS[action]
            if task.get("当前阶段") != required_stage:
                return None, f"未完成前序确认，不能执行「{action}」；当前阶段为「{task.get('当前阶段')}」，需先处于「{required_stage}」"

            if action == "开始作业":
                task["当前阶段"] = STAGE_EXECUTING
                task["开工时间"] = _now_text()
                self._sync_dispatch(task)
                return task, "作业已开工，车辆调度同步为作业中"

            # 现场收口：结论回写设施检查清单、车辆调度待办、隧道台账
            if not conclusion.strip():
                return None, "现场收口必须填写任务结论"
            task["当前阶段"] = STAGE_CLOSED
            task["收口时间"] = _now_text()
            task["任务结论"] = conclusion
            task["异常项"] = abnormal_items
            task["pending"] = False
            task["abnormal"] = bool(abnormal_items.strip())
            self._writeback_checklist(task, abnormal_items)
            self._writeback_tunnel(task)
            self._sync_dispatch(task)
            return task, "现场收口完成，结论已回写隧道台账、设施检查清单与车辆调度"

    # ---------- 回写：设施检查清单 / 车辆调度 / 隧道台账 ----------

    def _spawn_checklist_and_dispatch(self, task: dict[str, Any], spec: dict[str, Any]) -> None:
        task_no = str(task["任务编号"])
        checklist_rows = store.rows("facility_checklist")
        for item in spec["items"]:
            checklist_rows.append({
                "id": _next_id("facility_checklist"),
                "清单编号": f"CHK-{uuid.uuid4().hex[:8].upper()}",
                "任务编号": task_no,
                "隧道编号": task.get("隧道编号"),
                "隧道名称": task.get("隧道名称"),
                "检查类别": task.get("检查类别"),
                "检查项": item,
                # 历史检查按原班组留痕：班组在计划签发时写入，收口只回写结果
                "执行班组": task.get("班组"),
                "计划日期": str(task["计划开始"])[:10],
                "检查结果": "待检查",
                "异常描述": "",
                "回写时间": "",
                "status": "待检查",
                "pending": True,
                "abnormal": False,
            })
        dispatch_rows = store.rows("vehicle_dispatch")
        waiting_approval = task.get("当前阶段") == STAGE_PENDING_APPROVAL
        dispatch_rows.append({
            "id": _next_id("vehicle_dispatch"),
            "调度编号": f"DSP-{uuid.uuid4().hex[:8].upper()}",
            "任务编号": task_no,
            "隧道编号": task.get("隧道编号"),
            "隧道名称": task.get("隧道名称"),
            "检查类别": task.get("检查类别"),
            "作业班组": task.get("班组"),
            "安排车辆": spec["车辆"],
            "计划开始": task.get("计划开始"),
            "计划结束": task.get("计划结束"),
            "调度状态": "待批准" if waiting_approval else "待派车",
            "回写结论": "任务等待现场负责人批准，暂缓派车" if waiting_approval else "计划已签发，等待出车",
            "status": "待批准" if waiting_approval else "待派车",
            "pending": True,
            "abnormal": False,
        })

    def _writeback_checklist(self, task: dict[str, Any], abnormal_items: str) -> None:
        """收口结论落到清单每一项；执行班组保持签发时的原值，历史留痕不被改写。"""
        task_no = str(task["任务编号"])
        abnormal_list = {part.strip() for part in abnormal_items.replace("，", "、").split("、") if part.strip()}
        for row in store.rows("facility_checklist"):
            if row.get("任务编号") != task_no:
                continue
            is_abnormal = str(row.get("检查项")) in abnormal_list or bool(abnormal_items.strip() and "全部异常" in abnormal_items)
            row["检查结果"] = "异常待整改" if is_abnormal else "合格"
            row["异常描述"] = task.get("任务结论", "") if is_abnormal else ""
            row["回写时间"] = _now_text()
            row["status"] = "待复查" if is_abnormal else "合格"
            row["pending"] = is_abnormal
            row["abnormal"] = is_abnormal

    def _writeback_tunnel(self, task: dict[str, Any]) -> None:
        """收口结果进入隧道台账，而不只是弹窗里展示。"""
        tunnel = self._find_tunnel_by_code(str(task.get("隧道编号")))
        if tunnel is None:
            return
        tunnel["最近任务编号"] = task.get("任务编号", "")
        tunnel["最近收口时间"] = task.get("收口时间", "")
        tunnel["最近任务结论"] = task.get("任务结论", "")
        if task.get("封闭交通") == "是":
            tunnel["status"] = "封闭"
            tunnel["隧道状态"] = "封闭"
            tunnel["pending"] = bool(task.get("abnormal"))
        elif task.get("abnormal"):
            tunnel["status"] = "维修"
            tunnel["隧道状态"] = "维修"
            tunnel["abnormal"] = True
            tunnel["pending"] = True

    def _sync_dispatch(self, task: dict[str, Any]) -> None:
        """任务变更同步车辆调度待办。"""
        task_no = str(task["任务编号"])
        stage_status = {
            STAGE_PENDING_APPROVAL: ("待批准", "任务等待现场负责人批准，暂缓派车"),
            STAGE_ISSUED: ("待派车", "计划已签发，等待出车"),
            STAGE_EXECUTING: ("作业中", "车辆已出车，现场作业进行中"),
            STAGE_CLOSED: ("已归队", str(task.get("任务结论", ""))),
            STAGE_REJECTED: ("已取消", str(task.get("批准说明", "任务驳回"))),
        }
        dispatch_status, conclusion = stage_status.get(str(task.get("当前阶段")), ("待派车", ""))
        for row in store.rows("vehicle_dispatch"):
            if row.get("任务编号") != task_no:
                continue
            row["计划开始"] = task.get("计划开始")
            row["计划结束"] = task.get("计划结束")
            row["调度状态"] = dispatch_status
            row["status"] = dispatch_status
            row["回写结论"] = conclusion
            row["pending"] = dispatch_status in {"待批准", "待派车", "作业中"}
            row["abnormal"] = bool(task.get("异常项"))

    # ---------- 冲突 / 地图 ----------

    def find_conflicts(
        self,
        *,
        corridor: str,
        start: str,
        end: str,
        exclude_task_id: int | None = None,
        exclude_batch: str = "",
        same_batch_only: str = "",
    ) -> list[dict[str, Any]]:
        """同洞群走廊内、与已批准窗口（含已签发/执行/收口）时间重叠的任务。

        待批准与已驳回任务不占用窗口，避免互相误判；
        exclude_batch / same_batch_only 用于区分批次外冲突与同批次协同作业。
        """
        if not corridor:
            return []
        blocking_stages = {STAGE_ISSUED, STAGE_EXECUTING, STAGE_CLOSED}
        result = []
        for row in store.rows(MODULE):
            if exclude_task_id is not None and int(row.get("id", 0)) == exclude_task_id:
                continue
            batch = str(row.get("来源批次", ""))
            if same_batch_only:
                if batch != same_batch_only:
                    continue
            elif exclude_batch and batch == exclude_batch:
                continue
            if row.get("所属走廊") != corridor or row.get("当前阶段") not in blocking_stages:
                continue
            if _overlaps(start, end, str(row.get("计划开始")), str(row.get("计划结束"))):
                result.append(row)
        return result

    def _active_closure(
        self,
        tunnel_id: int,
        *,
        exclude_task_id: int | None = None,
    ) -> dict[str, Any] | None:
        """同一隧道上尚未结束的封闭作业（待批准也算占窗，新任务须走批准）。"""
        tunnel = store.find("tunnel", tunnel_id)
        if tunnel is None:
            return None
        code = str(tunnel.get("隧道编号", ""))
        active_stages = {STAGE_ISSUED, STAGE_EXECUTING, STAGE_PENDING_APPROVAL}
        for row in store.rows(MODULE):
            if exclude_task_id is not None and int(row.get("id", 0)) == exclude_task_id:
                continue
            if row.get("隧道编号") == code and row.get("封闭交通") == "是":
                if row.get("当前阶段") in active_stages:
                    return row
        return None

    def map_windows(self, date: str | None = None) -> dict[str, Any]:
        """洞群地图：走廊 → 隧道点位与作业窗口，标出互相影响的窗口。"""
        tunnels = sorted(
            store.rows("tunnel"),
            key=lambda row: (str(row.get("所属走廊", "")), float(row.get("桩号km", 0) or 0)),
        )
        tasks = [
            row for row in store.rows(MODULE)
            if row.get("当前阶段") != STAGE_REJECTED
            and (not date or str(row.get("计划开始", ""))[:10] == date)
        ]
        corridors: dict[str, list[dict[str, Any]]] = {}
        for tunnel in tunnels:
            corridors.setdefault(str(tunnel.get("所属走廊", "未分组")), []).append({
                "隧道编号": tunnel.get("隧道编号"),
                "隧道名称": tunnel.get("隧道名称"),
                "x": tunnel.get("x"),
                "y": tunnel.get("y"),
                "桩号km": tunnel.get("桩号km"),
                "隧道状态": tunnel.get("隧道状态") or tunnel.get("status"),
            })

        windows = []
        blocking_stages = {STAGE_ISSUED, STAGE_EXECUTING, STAGE_CLOSED}
        for task in tasks:
            blocking = task.get("当前阶段") in blocking_stages
            conflicts = []
            for other in tasks:
                if other is task:
                    continue
                if other.get("所属走廊") != task.get("所属走廊"):
                    continue
                if not _overlaps(
                    str(task.get("计划开始")), str(task.get("计划结束")),
                    str(other.get("计划开始")), str(other.get("计划结束")),
                ):
                    continue
                other_blocking = other.get("当前阶段") in blocking_stages
                # 已批准窗口互相重叠都在图上互相影响；待批准任务不占用窗口
                if blocking and other_blocking:
                    conflicts.append(str(other.get("任务编号")))
            windows.append({
                "任务编号": task.get("任务编号"),
                "隧道编号": task.get("隧道编号"),
                "隧道名称": task.get("隧道名称"),
                "所属走廊": task.get("所属走廊"),
                "检查类别": task.get("检查类别"),
                "班组": task.get("班组"),
                "开始": task.get("计划开始"),
                "结束": task.get("计划结束"),
                "阶段": task.get("当前阶段"),
                "封闭交通": task.get("封闭交通"),
                "冲突任务": conflicts,
                "协同任务": task.get("协同任务", ""),
            })
        return {"corridors": corridors, "windows": windows}

    # ---------- 设施检查清单 / 车辆调度汇总 ----------

    def list_checklist(
        self,
        *,
        category: str | None = None,
        result: str | None = None,
        crew: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows("facility_checklist")
        if category:
            rows = [row for row in rows if row.get("检查类别") == category]
        if result:
            rows = [row for row in rows if row.get("检查结果") == result]
        if crew:
            rows = [row for row in rows if crew in str(row.get("执行班组", ""))]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_dispatch(
        self,
        *,
        status: str | None = None,
        crew: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows("vehicle_dispatch")
        if status:
            rows = [row for row in rows if row.get("调度状态") == status]
        if crew:
            rows = [row for row in rows if crew in str(row.get("作业班组", ""))]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def dispatch_summary(self) -> dict[str, Any]:
        rows = store.rows("vehicle_dispatch")
        by_status: dict[str, int] = {}
        by_crew: dict[str, int] = {}
        for row in rows:
            status = str(row.get("调度状态", "未知"))
            crew = str(row.get("作业班组", "未知"))
            by_status[status] = by_status.get(status, 0) + 1
            by_crew[crew] = by_crew.get(crew, 0) + 1
        return {
            "total": len(rows),
            "pending_vehicle": sum(1 for row in rows if row.get("调度状态") in {"待批准", "待派车"}),
            "working": sum(1 for row in rows if row.get("调度状态") == "作业中"),
            "closed": sum(1 for row in rows if row.get("调度状态") == "已归队"),
            "abnormal": sum(1 for row in rows if row.get("abnormal")),
            "by_status": by_status,
            "by_crew": by_crew,
        }

    # ---------- 内部工具 ----------

    def _find_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows("tunnel_batch"):
            if row.get("批次号") == batch_no:
                return row
        return None

    def _find_task_by_biz_key(self, biz_key: str) -> dict[str, Any] | None:
        if not biz_key:
            return None
        for row in store.rows(MODULE):
            if row.get("业务键") == biz_key:
                return row
        return None

    def _find_tunnel_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows("tunnel"):
            if row.get("隧道编号") == code:
                return row
        return None

    def _tunnel_id_by_code(self, code: str) -> int:
        tunnel = self._find_tunnel_by_code(code)
        return int(tunnel.get("id", 0)) if tunnel else 0


orchestration_service = OrchestrationService()
