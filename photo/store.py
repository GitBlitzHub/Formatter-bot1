"""In-memory state for photo mode queue and LookSettings."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

from .phone_look import LookSettings

MAX_QUEUE = 30
MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_TTL_SECONDS = 3 * 3600

LIMITS: dict[str, tuple[float, float]] = {
    "soft": (0.0, 80.0),
    "paper": (0.0, 9.0),
    "noise": (0.0, 5.0),
}
STEPS: dict[str, float] = {
    "soft": 10.0,
    "paper": 1.0,
    "noise": 0.5,
}


@dataclass
class QueuedPhoto:
    filename: str
    data: bytes
    compressed: bool
    added: float = field(default_factory=time.time)


@dataclass
class PhotoUserState:
    in_photo_mode: bool = False
    queue: list[QueuedPhoto] = field(default_factory=list)
    settings: LookSettings = field(default_factory=LookSettings)
    queue_msg_id: int | None = None
    queue_task: asyncio.Task | None = None


class PhotoStore:
    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        time_fn: Callable[[], float] | None = None,
    ):
        self._ttl_seconds = ttl_seconds
        self._time_fn: Callable[[], float] = time_fn or time.time
        self._states: dict[int, PhotoUserState] = {}

    def _get_or_create(self, user_id: int) -> PhotoUserState:
        state = self._states.get(user_id)
        if state is None:
            state = PhotoUserState()
            self._states[user_id] = state
        return state

    def _prune_queue(self, state: PhotoUserState) -> None:
        now = self._time_fn()
        state.queue[:] = [x for x in state.queue if now - x.added < self._ttl_seconds]

    def get_state(self, user_id: int) -> PhotoUserState:
        state = self._get_or_create(user_id)
        self._prune_queue(state)
        return state

    def is_photo_mode(self, user_id: int) -> bool:
        state = self._states.get(user_id)
        return bool(state and state.in_photo_mode)

    def enter_photo_mode(self, user_id: int) -> PhotoUserState:
        state = self._get_or_create(user_id)
        state.in_photo_mode = True
        self._prune_queue(state)
        return state

    def exit_photo_mode(self, user_id: int) -> None:
        state = self._states.get(user_id)
        if state is not None:
            state.in_photo_mode = False

    def add_photo(self, user_id: int, item: QueuedPhoto) -> bool:
        state = self.get_state(user_id)
        if len(state.queue) >= MAX_QUEUE:
            return False
        if item.added <= 0:
            item.added = self._time_fn()
        state.queue.append(item)
        return True

    def get_queue(self, user_id: int) -> list[QueuedPhoto]:
        return self.get_state(user_id).queue

    def pop_queue(self, user_id: int) -> list[QueuedPhoto]:
        state = self.get_state(user_id)
        items = list(state.queue)
        state.queue.clear()
        return items

    def clear_queue(self, user_id: int) -> int:
        state = self.get_state(user_id)
        n = len(state.queue)
        state.queue.clear()
        if state.queue_task and not state.queue_task.done():
            state.queue_task.cancel()
        state.queue_task = None
        state.queue_msg_id = None
        return n

    def get_settings(self, user_id: int) -> LookSettings:
        return self._get_or_create(user_id).settings

    def change_setting(self, user_id: int, key: str, direction: str) -> LookSettings:
        if key not in LIMITS:
            raise KeyError(f"Unknown setting: {key}")
        s = self._get_or_create(user_id).settings
        lo, hi = LIMITS[key]
        delta = STEPS[key] if direction == "+" else -STEPS[key]
        val = round(min(hi, max(lo, getattr(s, key) + delta)), 2)
        setattr(s, key, val)
        return s

    def reset_settings(self, user_id: int) -> LookSettings:
        state = self._get_or_create(user_id)
        state.settings = LookSettings()
        return state.settings
