"""洞群批量编排的异步队列。

- 批次提交后只把 batch_id 投递到 asyncio 队列，工作协程逐项处理；
- 每项任务以业务键幂等，重复投递/重试不会生成重复班组任务；
- 服务重启或批次复位后，从未完成项继续，已完成项自动跳过；
- 冲突任务不会覆盖任何已批准时段（业务规则在 service 里把关）。
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from app.services.tunnel_console import TunnelConsoleService

QUEUE_ITEM_INTERVAL = 0.12  # 模拟逐项编排，便于前端观察队列推进


class BatchQueue:
    def __init__(self, service: TunnelConsoleService) -> None:
        self._service = service
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._enqueued: set[int] = set()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(), name="tunnel-batch-worker"),
        ]
        # 复位恢复：进程重启后把未完成批次重新入队
        for batch_id in self._service.resume_candidates():
            self.enqueue(batch_id)

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers = []
        self._started = False

    def enqueue(self, batch_id: int) -> bool:
        """幂等入队：同一批次已在队列里就不重复投递。"""
        if batch_id in self._enqueued:
            return False
        self._enqueued.add(batch_id)
        self._queue.put_nowait(batch_id)
        return True

    @contextlib.asynccontextmanager
    async def lifespan(self, _app: object) -> AsyncIterator[None]:
        await self.start()
        try:
            yield
        finally:
            await self.stop()

    async def _worker(self) -> None:
        while True:
            batch_id = await self._queue.get()
            requeue = False
            try:
                while True:
                    await asyncio.sleep(QUEUE_ITEM_INTERVAL)
                    try:
                        batch = self._service.process_batch_item(batch_id)
                    except Exception:  # noqa: BLE001 - 单项异常不拖垮队列
                        # 未完成项仍标记为排队中，稍后重新入队从断点继续
                        requeue = True
                        break
                    if batch is None:
                        break
                    # 复位会把批次打回排队中，继续把剩余项做完
                    if batch.get("status") not in {"排队中", "生成中", "复位中"}:
                        break
                    if all(item["状态"] != "排队中" for item in batch.get("items", [])):
                        break
            finally:
                self._enqueued.discard(batch_id)
                self._queue.task_done()
                if requeue:
                    await asyncio.sleep(QUEUE_ITEM_INTERVAL)
                    self.enqueue(batch_id)


console_service = TunnelConsoleService()
batch_queue = BatchQueue(console_service)
