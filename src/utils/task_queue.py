"""TaskQueue — a priority queue with FIFO ordering for equal priorities."""

from __future__ import annotations

import heapq


class TaskQueue:
    """Priority queue that returns highest-priority items first, with FIFO
    ordering for items of equal priority.  pop() and peek() return None when
    the queue is empty.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, object]] = []
        self._counter = 0  # monotonically-increasing insertion order

    def push(self, item: object, priority: int = 0) -> None:
        """Push *item* into the queue with the given *priority* (default 0).

        Higher numeric priority values are dequeued before lower ones.
        """
        heapq.heappush(self._heap, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> object | None:
        """Remove and return the highest-priority item, or ``None`` if empty."""
        if not self._heap:
            return None
        _, _, item = heapq.heappop(self._heap)
        return item

    def peek(self) -> object | None:
        """Return the highest-priority item without removing it.
        Returns ``None`` if the queue is empty.
        """
        if not self._heap:
            return None
        return self._heap[0][2]

    def size(self) -> int:
        """Return the number of items currently in the queue."""
        return len(self._heap)
