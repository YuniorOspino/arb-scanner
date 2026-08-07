"""
Buffer de agrupación por ventana de tiempo.

Junta alertas VALIDAS que lleguen dentro de una ventana (default 4s).
Al vencer la ventana sin alertas nuevas, entrega la de mayor ROI/beneficio
y descarta (loguea) el resto del buffer.

Soporta callbacks sync (threading.Timer) y async (asyncio) para enganchar
tanto el scanner sync como bots asyncio.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Awaitable, Callable, List

logger = logging.getLogger(__name__)


class BufferAgrupacion:
    def __init__(
        self,
        on_ganadora: Callable,
        on_descartadas: Callable | None = None,
        ventana_seg: float = 4.0,
        criterio: str = "roi",
    ):
        self.on_ganadora = on_ganadora
        self.on_descartadas = on_descartadas
        self.ventana_seg = ventana_seg
        self.criterio = criterio
        self.buffer: List[dict] = []
        self._task: asyncio.Task | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    async def agregar(self, alerta: dict):
        self.buffer.append(alerta)
        if self._task:
            self._task.cancel()
        self._task = asyncio.create_task(self._esperar_y_vencer())

    def agregar_sync(self, alerta: dict) -> None:
        """Entrada sync para el scanner (main.py)."""
        with self._lock:
            self.buffer.append(alerta)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.ventana_seg, self._vencer_sync)
            self._timer.daemon = True
            self._timer.start()

    async def _esperar_y_vencer(self):
        try:
            await asyncio.sleep(self.ventana_seg)
        except asyncio.CancelledError:
            return

        if not self.buffer:
            return

        ordenadas = sorted(
            self.buffer, key=lambda a: float(a.get(self.criterio, 0) or 0), reverse=True
        )
        ganadora, *descartadas = ordenadas
        self.buffer = []

        await self._invoke(self.on_ganadora, ganadora)
        if descartadas and self.on_descartadas:
            await self._invoke(self.on_descartadas, descartadas)

    def _vencer_sync(self) -> None:
        with self._lock:
            if not self.buffer:
                return
            ordenadas = sorted(
                self.buffer,
                key=lambda a: float(a.get(self.criterio, 0) or 0),
                reverse=True,
            )
            ganadora, *descartadas = ordenadas
            self.buffer = []

        try:
            result = self.on_ganadora(ganadora)
            if asyncio.iscoroutine(result):
                asyncio.run(result)
        except Exception:
            logger.exception("Buffer on_ganadora failed")

        if descartadas and self.on_descartadas:
            try:
                result = self.on_descartadas(descartadas)
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
            except Exception:
                logger.exception("Buffer on_descartadas failed")

    @staticmethod
    async def _invoke(fn: Callable, arg) -> None:
        result = fn(arg)
        if asyncio.iscoroutine(result):
            await result
