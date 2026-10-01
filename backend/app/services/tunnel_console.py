"""洞群任务编排台业务规则。

核心口径：
- 任务按「计划签发 → 作业执行 → 现场收口」单向推进，未完成前序确认不得跳级；
- 多选隧道 + 通风/消防/照明检查类别，按类别拆分给不同班组；
- 同一隧道已有封闭作业时，新任务进入「冲突待裁」，以现场负责人批准的计划为准，
  冲突任务不得覆盖已批准时段，已签发计划锁定不可改窗；
- 批量任务走异步队列，业务键幂等，复位/重启后从未完成项继续；
- 收口结论一次性回写隧道台账、设施检查清单、车辆调度待办；
- 历史检查按任务创建时的班组快照留痕，事后改任务不回改清单班组。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

TUNNEL_MODULE = "tunnel"
TASK_MODULE = "tunnel_task"
BATCH_MODULE = "tunnel_batch"
CHECK_MODULE = "facility_check"
DISPATCH_MODULE = "vehicle_dispatch"
VEHICLE_MODULE = "vehicle"

TIME_FMT = "%Y-%m-%d %H:%M"

# 检查类别 → 班组、检查项、首选车辆
CATEGORY_META: dict[str, dict[str, Any]] = {
    "通风": {
        "crew": "通风班",
        "items": ["射流风机运转与异响检查", "CO/VI 检测器校准", "排烟道与风阀启闭检查"],
        "vehicle_type": "通风检测车",
    },
    "消防": {
        "crew": "消防班",
        "items": ["消火栓出水压力测试", "灭火器压力与有效期核查", "火灾报警按钮联动试验"],
        "vehicle_type": "消防巡检车",
    },
    "照明": {
        "crew": "照明班",
        "items": ["灯具点亮率巡查", "洞内外亮度检测", "应急照明与诱导灯检查"],
        "vehicle_type": "照度检测车",
    },
}
CATEGORIES = list(CATEGORY_META.keys())
CLOSURE_VEHICLE_TYPE = "交通锥封闭车"

# 签发后进入「已批准时段」，任何冲突任务都不得覆盖
OCCUPYING_STATUSES = {"已签发", "作业中", "待收口", "已收口"}
ACTIVE_WINDOW_STATUSES = {"待签发", "已签发", "作业中", "待收口"}

# 单向状态机：动作 → (前置状态, 目标状态)
ACTION_FLOW: dict[str, tuple[str, str]] = {
    "签发计划": ("待签发", "已签发"),
    "开始作业": ("已签发", "作业中"),
    "完成作业": ("作业中", "待收口"),
    "现场收口": ("待收口", "已收口"),
    "取消任务": ("待签发", "已取消"),
}

RESULT_OPTIONS = ["合格", "隐患整改"]


def now_text() -> str:
    return datetime.now().strftime(TIME_FMT)


def parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), TIME_FMT)


def windows_overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    return parse_time(start_a) < parse_time(end_b) and parse_time(start_b) < parse_time(end_a)


def next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class TunnelConsoleService:
    # ---------------------------------------------------------------- 基础数据
    def tunnel_options(self) -> list[dict[str, Any]]:
        tunnels = store.rows(TUNNEL_MODULE)
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in tunnels:
            groups.setdefault(str(row.get("洞群") or "未分组"), []).append(
                {
                    "id": row["id"],
                    "隧道编号": row.get("隧道编号"),
                    "隧道名称": row.get("隧道名称"),
                    "洞群": row.get("洞群"),
                    "隧道长度": row.get("隧道长度"),
                    "现场负责人": row.get("现场负责人"),
                    "地图X": row.get("地图X"),
                    "地图Y": row.get("地图Y"),
                    "status": row.get("status"),
                }
            )
        return [{"洞群": name, "tunnels": items} for name, items in sorted(groups.items())]

    def meta(self) -> dict[str, Any]:
        return {
            "categories": CATEGORIES,
            "crews": {key: value["crew"] for key, value in CATEGORY_META.items()},
            "check_items": {key: value["items"] for key, value in CATEGORY_META.items()},
            "vehicle_types": {key: value["vehicle_type"] for key, value in CATEGORY_META.items()},
            "closure_vehicle_type": CLOSURE_VEHICLE_TYPE,
            "statuses": ["待签发", "已签发", "作业中", "待收口", "已收口", "已取消"],
            "results": RESULT_OPTIONS,
        }

    # ---------------------------------------------------------------- 冲突检测
    def find_blocking_closures(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        """同一隧道、时段重叠且涉及封闭作业的已批准计划。"""
        blockers: list[dict[str, Any]] = []
        for other in store.rows(TASK_MODULE):
            if int(other.get("id", 0)) == int(task.get("id", 0)):
                continue
            if other.get("status") not in OCCUPYING_STATUSES:
                continue
            if int(other.get("隧道id", 0)) != int(task.get("隧道id", 0)):
                continue
            if not (task.get("封闭作业") or other.get("封闭作业")):
                continue
            try:
                if windows_overlap(
                    str(task["作业开始"]), str(task["作业结束"]),
                    str(other["作业开始"]), str(other["作业结束"]),
                ):
                    blockers.append(other)
            except (KeyError, ValueError):
                continue
        return blockers

    def refresh_conflict(self, task: dict[str, Any]) -> bool:
        blockers = self.find_blocking_closures(task)
        task["冲突任务ids"] = [int(item["id"]) for item in blockers]
        task["冲突说明"] = (
            "与已批准封闭作业时段冲突："
            + "、".join(f"#{item['id']} {item['作业开始']}~{item['作业结束']}" for item in blockers)
            if blockers
            else ""
        )
        task["冲突待裁"] = bool(blockers) and not task.get("批准记录")
        return bool(blockers)

    # ---------------------------------------------------------------- 批量计划
    def make_batch_key(self, tunnel_ids: list[int], categories: list[str], start: str,
                       end: str, closed: bool) -> str:
        ids = ",".join(str(i) for i in sorted(tunnel_ids))
        cats = ",".join(sorted(categories))
        return f"B:{ids}|{cats}|{start}|{end}|{1 if closed else 0}"

    def submit_batch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        tunnel_ids = [int(i) for i in values.get("隧道ids", [])]
        categories = [str(c) for c in values.get("检查类别", []) if str(c) in CATEGORY_META]
        start = str(values.get("作业开始") or "").strip()
        end = str(values.get("作业结束") or "").strip()
        closed = bool(values.get("封闭作业"))
        supervisor = str(values.get("现场负责人") or "").strip()
        note = str(values.get("备注") or "").strip()
        if not tunnel_ids:
            return None, "请至少选择一座隧道"
        if not categories:
            return None, "请至少选择通风、消防、照明中的一个检查类别"
        try:
            if parse_time(end) <= parse_time(start):
                return None, "作业结束时间必须晚于开始时间"
        except ValueError:
            return None, "作业窗口时间格式应为 YYYY-MM-DD HH:MM"
        tunnels = {int(row["id"]): row for row in store.rows(TUNNEL_MODULE)}
        missing = [i for i in tunnel_ids if i not in tunnels]
        if missing:
            return None, f"隧道 {','.join(map(str, missing))} 不存在或已归档"

        business_key = self.make_batch_key(tunnel_ids, categories, start, end, closed)
        for row in store.rows(BATCH_MODULE):
            if row.get("business_key") == business_key:
                return row, "相同编排计划已提交，按原批次幂等返回"

        items: list[dict[str, Any]] = []
        for tunnel_id in sorted(tunnel_ids):
            for category in categories:
                tunnel = tunnels[tunnel_id]
                items.append({
                    "隧道id": tunnel_id,
                    "隧道编号": tunnel.get("隧道编号"),
                    "隧道名称": tunnel.get("隧道名称"),
                    "洞群": tunnel.get("洞群"),
                    "检查类别": category,
                    "检查班组": CATEGORY_META[category]["crew"],
                    "状态": "排队中",
                    "task_id": None,
                    "message": "",
                    "business_key": f"T:{tunnel_id}:{category}:{start}:{end}",
                })
        batch = {
            "id": next_id(store.rows(BATCH_MODULE)),
            "batch_no": f"PB-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(store.rows(BATCH_MODULE)) + 1}",
            "business_key": business_key,
            "隧道ids": tunnel_ids,
            "检查类别": categories,
            "作业开始": start,
            "作业结束": end,
            "封闭作业": closed,
            "现场负责人": supervisor,
            "备注": note,
            "status": "排队中",
            "total": len(items),
            "processed": 0,
            "冲突数": 0,
            "items": items,
            "创建时间": now_text(),
            "message": "已进入异步队列",
        }
        store.rows(BATCH_MODULE).append(batch)
        return batch, "编排计划已入队"

    def list_batches(self) -> list[dict[str, Any]]:
        return sorted(store.rows(BATCH_MODULE), key=lambda row: int(row["id"]), reverse=True)

    def process_batch_item(self, batch_id: int) -> dict[str, Any] | None:
        """队列工作单元：处理一个「隧道 × 检查类别」。业务键幂等，可安全重试。"""
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None
        if batch["status"] not in {"排队中", "生成中", "复位中"}:
            return batch
        batch["status"] = "生成中"
        pending = [item for item in batch["items"] if item["状态"] == "排队中"]
        if not pending:
            self._finish_batch(batch)
            return batch
        item = pending[0]
        key = str(item["business_key"])
        # 业务键幂等：任务已存在则直接挂接，绝不重复生成
        existed = next(
            (row for row in store.rows(TASK_MODULE) if row.get("business_key") == key),
            None,
        )
        if existed is not None:
            item.update({"状态": "已生成", "task_id": int(existed["id"]),
                         "message": "业务键命中既有任务，幂等挂接"})
        else:
            task = self._create_task(batch, item)
            blocked = self.refresh_conflict(task)
            item.update({
                "状态": "冲突" if blocked else "已生成",
                "task_id": int(task["id"]),
                "message": "命中已批准封闭作业，转冲突待裁" if blocked else "班组任务已生成",
            })
            batch["冲突数"] = sum(1 for row in batch["items"] if row["状态"] == "冲突")
        batch["processed"] = sum(1 for row in batch["items"] if row["状态"] != "排队中")
        if not [row for row in batch["items"] if row["状态"] == "排队中"]:
            self._finish_batch(batch)
        return batch

    def _finish_batch(self, batch: dict[str, Any]) -> None:
        batch["status"] = "部分冲突" if batch["冲突数"] else "已完成"
        batch["message"] = (
            f"已生成 {batch['processed']}/{batch['total']} 项，其中 {batch['冲突数']} 项冲突待裁"
            if batch["冲突数"]
            else f"全部 {batch['total']} 项班组任务已生成"
        )

    def reset_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None, f"批次 {batch_id} 不存在"
        unfinished = [item for item in batch["items"] if item["状态"] == "排队中"]
        if not unfinished:
            return batch, "没有未完成项，无需复位"
        batch["status"] = "排队中"
        batch["message"] = f"复位后从 {len(unfinished)} 个未完成项继续"
        return batch, "批次已复位，从未完成项继续"

    def resume_candidates(self) -> list[int]:
        """启动恢复：排队中/生成中断的批次重新入队，已完成项靠业务键跳过。"""
        return [
            int(row["id"])
            for row in store.rows(BATCH_MODULE)
            if row.get("status") in {"排队中", "生成中", "复位中"}
            and any(item["状态"] == "排队中" for item in row.get("items", []))
        ]

    def _create_task(self, batch: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        category = str(item["检查类别"])
        meta = CATEGORY_META[category]
        task = {
            "id": next_id(store.rows(TASK_MODULE)),
            "batch_id": int(batch["id"]),
            "business_key": item["business_key"],
            "隧道id": int(item["隧道id"]),
            "隧道编号": item["隧道编号"],
            "隧道名称": item["隧道名称"],
            "洞群": item["洞群"],
            "检查类别": category,
            "检查班组": meta["crew"],
            "作业开始": batch["作业开始"],
            "作业结束": batch["作业结束"],
            "封闭作业": bool(batch["封闭作业"]),
            "现场负责人": batch["现场负责人"],
            "status": "待签发",
            "已批准": False,
            "批准记录": None,
            "冲突待裁": False,
            "冲突任务ids": [],
            "冲突说明": "",
            "收口结论": "",
            "收口负责人": "",
            "收口时间": "",
            "收口说明": "",
            "创建时间": now_text(),
            "history": [{"时间": now_text(), "动作": "生成任务", "操作人": batch["现场负责人"] or "编排台",
                         "说明": f"批次 {batch['batch_no']} 生成{category}班组任务"}],
        }
        store.rows(TASK_MODULE).append(task)
        # 设施检查清单随任务生成即落账，班组按此时快照留痕，事后不回改
        for seq, check_item in enumerate(meta["items"], start=1):
            store.rows(CHECK_MODULE).append({
                "id": next_id(store.rows(CHECK_MODULE)),
                "检查编号": f"CHK-{int(task['id']):04d}-{seq}",
                "task_id": int(task["id"]),
                "batch_id": int(batch["id"]),
                "隧道id": int(item["隧道id"]),
                "隧道编号": item["隧道编号"],
                "隧道名称": item["隧道名称"],
                "洞群": item["洞群"],
                "检查类别": category,
                "检查班组": meta["crew"],
                "班组快照": meta["crew"],
                "检查项目": check_item,
                "检查结果": "待检",
                "情况说明": "",
                "作业开始": batch["作业开始"],
                "作业结束": batch["作业结束"],
                "status": "待检",
                "收口时间": "",
                "收口结论": "",
                "pending": True,
                "abnormal": False,
            })
        return task

    # ---------------------------------------------------------------- 任务流转
    def list_tasks(self, *, status: str | None = None, tunnel_id: int | None = None,
                   category: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(TASK_MODULE)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if tunnel_id:
            rows = [row for row in rows if int(row.get("隧道id", 0)) == tunnel_id]
        if category:
            rows = [row for row in rows if row.get("检查类别") == category]
        return sorted(rows, key=lambda row: int(row["id"]), reverse=True)

    def _append_history(self, task: dict[str, Any], action: str, operator: str, note: str) -> None:
        task.setdefault("history", []).append(
            {"时间": now_text(), "动作": action, "操作人": operator, "说明": note}
        )

    def adjust_plan(self, task_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        task = store.find(TASK_MODULE, task_id)
        if task is None:
            return None, f"任务 #{task_id} 不存在"
        if task["status"] != "待签发":
            return None, "计划签发后时段即锁定，不能再调整作业窗口"
        start = str(values.get("作业开始") or task["作业开始"]).strip()
        end = str(values.get("作业结束") or task["作业结束"]).strip()
        try:
            if parse_time(end) <= parse_time(start):
                return None, "作业结束时间必须晚于开始时间"
        except ValueError:
            return None, "作业窗口时间格式应为 YYYY-MM-DD HH:MM"
        operator = str(values.get("操作人") or "").strip() or task.get("现场负责人") or "值班员"
        note = str(values.get("说明") or "").strip()
        task["作业开始"] = start
        task["作业结束"] = end
        if "封闭作业" in values:
            task["封闭作业"] = bool(values.get("封闭作业"))
        task["批准记录"] = None
        task["已批准"] = False
        self.refresh_conflict(task)
        self._append_history(task, "调整计划", operator, note or "按现场负责人意见调整作业窗口")
        return task, "计划窗口已调整，等待现场负责人批准"

    def approve_plan(self, task_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        task = store.find(TASK_MODULE, task_id)
        if task is None:
            return None, f"任务 #{task_id} 不存在"
        if task["status"] != "待签发":
            return None, "只有待签发任务可以走批准流程"
        operator = str(values.get("批准人") or "").strip()
        if not operator:
            return None, "请填写现场负责人姓名"
        blockers = self.find_blocking_closures(task)
        record = {
            "批准人": operator,
            "批准时间": now_text(),
            "批准说明": str(values.get("批准说明") or "").strip(),
            "仍有封闭冲突": bool(blockers),
            "冲突任务ids": [int(item["id"]) for item in blockers],
        }
        task["批准记录"] = record
        task["已批准"] = True
        task["现场负责人"] = operator
        # 批准代表新任务服从现场负责人计划；系统不动既有任务，已批准时段永不被覆盖
        task["冲突待裁"] = False
        self._append_history(
            task, "现场负责人批准", operator,
            record["批准说明"] or ("批准计划；与既有封闭作业重叠，以已批准封闭时段为准" if blockers else "批准作业计划"),
        )
        return task, ("计划已获现场负责人批准，仍与封闭作业重叠，签发时须改窗避让"
                      if blockers else "计划已获现场负责人批准，可以签发")

    def advance(self, task_id: int, action: str,
                values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        task = store.find(TASK_MODULE, task_id)
        if task is None:
            return None, f"任务 #{task_id} 不存在"
        if action not in ACTION_FLOW:
            return None, f"动作「{action}」不在编排台可执行范围"
        expected, target = ACTION_FLOW[action]
        if task["status"] != expected:
            return None, f"当前状态为「{task['status']}」，不能{action}；请先完成前序确认"

        if action == "签发计划":
            message = self._issue_task(task, values)
        elif action == "开始作业":
            message = self._start_task(task, values)
        elif action == "完成作业":
            message = self._finish_execution(task, values)
        elif action == "现场收口":
            message = self._close_task(task, values)
        else:  # 取消任务
            for row in store.rows(CHECK_MODULE):
                if int(row.get("task_id", 0)) == task_id and row["status"] == "待检":
                    row["status"] = "已取消"
                    row["pending"] = False
            task["status"] = target
            message = "任务已取消，未生成调度待办"
        self._append_history(task, action, str(values.get("操作人") or "") or task.get("现场负责人") or "值班员",
                             str(values.get("说明") or ""))
        return task, message

    def _issue_task(self, task: dict[str, Any], values: dict[str, Any]) -> str:
        if not task.get("已批准"):
            raise FlowError("计划未经现场负责人批准，不能签发")
        blockers = self.find_blocking_closures(task)
        if blockers:
            task["冲突任务ids"] = [int(item["id"]) for item in blockers]
            raise FlowError(
                "作业窗口仍覆盖已批准封闭时段（"
                + "、".join(f"#{item['id']}" for item in blockers)
                + "），请先调整计划避让，冲突任务不得覆盖已批准时段"
            )
        operator = str(values.get("操作人") or task.get("现场负责人") or "值班员")
        task["status"] = "已签发"
        task["冲突待裁"] = False
        task["冲突说明"] = ""
        # 计划签发即生成车辆调度待办（业务键幂等）
        types = [CATEGORY_META[str(task["检查类别"])]["vehicle_type"]]
        if task["封闭作业"]:
            types.append(CLOSURE_VEHICLE_TYPE)
        chosen: list[str] = []
        for vehicle_type in types:
            vehicle = self._pick_vehicle(vehicle_type)
            if vehicle is None:
                continue
            key = f"D:{task['id']}:{vehicle['id']}"
            if not any(row.get("business_key") == key for row in store.rows(DISPATCH_MODULE)):
                store.rows(DISPATCH_MODULE).append({
                    "id": next_id(store.rows(DISPATCH_MODULE)),
                    "business_key": key,
                    "调度编号": f"DSP-{len(store.rows(DISPATCH_MODULE)) + 1:04d}",
                    "task_id": int(task["id"]),
                    "batch_id": int(task.get("batch_id", 0)),
                    "隧道id": int(task["隧道id"]),
                    "隧道编号": task["隧道编号"],
                    "隧道名称": task["隧道名称"],
                    "洞群": task["洞群"],
                    "检查类别": task["检查类别"],
                    "检查班组": task["检查班组"],
                    "车辆id": int(vehicle["id"]),
                    "车辆编号": vehicle.get("车辆编号"),
                    "车牌号": vehicle.get("车牌号"),
                    "车辆类型": vehicle.get("车辆类型"),
                    "作业开始": task["作业开始"],
                    "作业结束": task["作业结束"],
                    "封闭作业": bool(task["封闭作业"]),
                    "现场负责人": operator,
                    "status": "待派车",
                    "派出时间": "",
                    "归库时间": "",
                    "pending": True,
                    "abnormal": False,
                })
            chosen.append(str(vehicle.get("车牌号")))
        return f"计划已签发，车辆调度待办已生成（{ '、'.join(chosen) if chosen else '暂无可派车辆'}）"

    def _pick_vehicle(self, vehicle_type: str) -> dict[str, Any] | None:
        rows = store.rows(VEHICLE_MODULE)
        return next((row for row in rows if row.get("车辆类型") == vehicle_type and row.get("status") == "在库"),
                    None) or next((row for row in rows if row.get("车辆类型") == vehicle_type), None)

    def _start_task(self, task: dict[str, Any], values: dict[str, Any]) -> str:
        task["status"] = "作业中"
        for row in store.rows(DISPATCH_MODULE):
            if int(row.get("task_id", 0)) == int(task["id"]) and row["status"] == "待派车":
                row["status"] = "已派出"
                row["派出时间"] = now_text()
                vehicle = store.find(VEHICLE_MODULE, int(row["车辆id"]))
                if vehicle is not None:
                    vehicle["status"] = "出车作业"
        if task["封闭作业"]:
            tunnel = store.find(TUNNEL_MODULE, int(task["隧道id"]))
            if tunnel is not None:
                tunnel["status"] = "封闭"
                tunnel["隧道状态"] = "封闭"
        return "班组已开始作业，调度车辆已派出"

    def _finish_execution(self, task: dict[str, Any], values: dict[str, Any]) -> str:
        task["status"] = "待收口"
        return "作业执行完成，等待现场收口确认"

    def _close_task(self, task: dict[str, Any], values: dict[str, Any]) -> str:
        task.setdefault("检查班组", CATEGORY_META.get(str(task.get("检查类别")), {}).get("crew", ""))
        results = values.get("检查结果") or []
        result_map: dict[str, str] = {}
        notes_map: dict[str, str] = {}
        for item in results:
            name = str(item.get("检查项目") or "").strip()
            result = str(item.get("结果") or "").strip()
            if name and result in RESULT_OPTIONS:
                result_map[name] = result
                notes_map[name] = str(item.get("说明") or "").strip()
        check_rows = [
            row for row in store.rows(CHECK_MODULE)
            if int(row.get("task_id", 0)) == int(task["id"]) and row["status"] != "已取消"
        ]
        missing = [row["检查项目"] for row in check_rows if row["检查项目"] not in result_map]
        if missing:
            raise FlowError(f"以下检查项尚未给出结论：{'、'.join(missing)}")
        conclusion = str(values.get("收口结论") or "").strip()
        if conclusion not in RESULT_OPTIONS:
            raise FlowError("请选择收口结论：合格 / 隐患整改")
        closer = str(values.get("收口负责人") or "").strip()
        if not closer:
            raise FlowError("请填写现场收口负责人")
        closed_at = now_text()

        # 1) 回写设施检查清单：只更结果与留痕，班组仍取任务创建时快照，历史检查按原班组留痕
        for row in check_rows:
            result = result_map[str(row["检查项目"])]
            row["检查结果"] = result
            row["情况说明"] = notes_map.get(str(row["检查项目"]), "")
            row["检查班组"] = row["班组快照"]
            row["status"] = "已收口"
            row["pending"] = False
            row["abnormal"] = result == "隐患整改"
            row["收口时间"] = closed_at
            row["收口结论"] = conclusion
            row["收口负责人"] = closer

        # 2) 回写车辆调度：车辆归库
        for row in store.rows(DISPATCH_MODULE):
            if int(row.get("task_id", 0)) == int(task["id"]) and row["status"] == "已派出":
                row["status"] = "已归库"
                row["归库时间"] = closed_at
                row["pending"] = False
                vehicle = store.find(VEHICLE_MODULE, int(row["车辆id"]))
                if vehicle is not None:
                    vehicle["status"] = "在库"

        # 3) 回写隧道台账：状态 + 留痕（按原班组）
        tunnel = store.find(TUNNEL_MODULE, int(task["隧道id"]))
        if tunnel is not None:
            tunnel["最近任务"] = f"{task['检查类别']}检查（{task['检查班组']}）"
            tunnel["最近结论"] = conclusion
            tunnel["最近收口时间"] = closed_at
            if conclusion == "隐患整改":
                tunnel["status"] = "维修"
                tunnel["隧道状态"] = "维修"
                tunnel["abnormal"] = True
            elif task["封闭作业"] and tunnel.get("status") == "封闭":
                tunnel["status"] = "正常"
                tunnel["隧道状态"] = "正常"
            tunnel.setdefault("任务留痕", []).append({
                "任务编号": f"#{task['id']}",
                "检查类别": task["检查类别"],
                "检查班组": task["检查班组"],
                "作业开始": task["作业开始"],
                "作业结束": task["作业结束"],
                "收口结论": conclusion,
                "收口负责人": closer,
                "收口时间": closed_at,
                "收口说明": str(values.get("说明") or "").strip(),
            })

        task["status"] = "已收口"
        task["收口结论"] = conclusion
        task["收口负责人"] = closer
        task["收口时间"] = closed_at
        task["收口说明"] = str(values.get("说明") or "").strip()
        task["冲突待裁"] = False
        self._append_history(task, "现场收口", closer, f"结论：{conclusion}")
        return "现场收口完成，结论已回写隧道台账、设施检查清单与车辆调度汇总"

    # ---------------------------------------------------------------- 清单/调度/地图
    def list_checklist(self, *, tunnel_id: int | None = None, crew: str | None = None,
                       status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(CHECK_MODULE)
        if tunnel_id:
            rows = [row for row in rows if int(row.get("隧道id", 0)) == tunnel_id]
        if crew:
            rows = [row for row in rows if row.get("检查班组") == crew]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return sorted(rows, key=lambda row: int(row["id"]), reverse=True)

    def list_dispatch(self, *, status: str | None = None,
                      tunnel_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(DISPATCH_MODULE)
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if tunnel_id:
            rows = [row for row in rows if int(row.get("隧道id", 0)) == tunnel_id]
        return sorted(rows, key=lambda row: int(row["id"]), reverse=True)

    def dispatch_summary(self) -> dict[str, Any]:
        rows = store.rows(DISPATCH_MODULE)
        by_status = {name: 0 for name in ["待派车", "已派出", "已归库"]}
        for row in rows:
            by_status[str(row.get("status"))] = by_status.get(str(row.get("status")), 0) + 1
        vehicles = store.rows(VEHICLE_MODULE)
        return {
            "待派车": by_status.get("待派车", 0),
            "已派出": by_status.get("已派出", 0),
            "已归库": by_status.get("已归库", 0),
            "在库车辆": sum(1 for row in vehicles if row.get("status") == "在库"),
            "出车车辆": sum(1 for row in vehicles if row.get("status") == "出车作业"),
            "items": self.list_dispatch(),
        }

    def map_view(self) -> dict[str, Any]:
        tunnels = [
            {
                "id": row["id"],
                "隧道编号": row.get("隧道编号"),
                "隧道名称": row.get("隧道名称"),
                "洞群": row.get("洞群"),
                "x": float(row.get("地图X", 50)),
                "y": float(row.get("地图Y", 50)),
                "status": row.get("status"),
            }
            for row in store.rows(TUNNEL_MODULE)
        ]
        windows = [
            {
                "task_id": row["id"],
                "隧道id": row.get("隧道id"),
                "隧道名称": row.get("隧道名称"),
                "洞群": row.get("洞群"),
                "检查类别": row.get("检查类别"),
                "检查班组": row.get("检查班组"),
                "作业开始": row.get("作业开始"),
                "作业结束": row.get("作业结束"),
                "封闭作业": bool(row.get("封闭作业")),
                "status": row.get("status"),
                "冲突待裁": bool(row.get("冲突待裁")),
            }
            for row in store.rows(TASK_MODULE)
            if row.get("status") in ACTIVE_WINDOW_STATUSES
        ]
        # 互相影响：同隧道时段重叠，或同洞群时段重叠
        links: list[dict[str, Any]] = []
        for i, left in enumerate(windows):
            for right in windows[i + 1:]:
                try:
                    overlap = windows_overlap(
                        str(left["作业开始"]), str(left["作业结束"]),
                        str(right["作业开始"]), str(right["作业结束"]),
                    )
                except ValueError:
                    continue
                if not overlap:
                    continue
                same_tunnel = int(left["隧道id"]) == int(right["隧道id"])
                same_group = (not same_tunnel) and left["洞群"] == right["洞群"]
                if not (same_tunnel or same_group):
                    continue
                links.append({
                    "from": left["task_id"],
                    "to": right["task_id"],
                    "类型": "同洞群通风/视线影响" if same_group else "同隧道时段冲突",
                    "封闭相关": bool(left["封闭作业"] or right["封闭作业"]),
                })
        return {"tunnels": tunnels, "windows": windows, "links": links}


class FlowError(Exception):
    """业务流转不满足前置条件时抛出，路由层转成 ok=False 的动作结果。"""
