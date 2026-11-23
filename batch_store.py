import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Literal

from schemas import Person


@dataclass
class MatchRecord:
    index: int
    person: Person
    gender: Literal["M", "F", "U"]
    dob: str
    age: int | None
    display_name: str
    full_block: str
    clean_block: str


@dataclass
class PhotoItem:
    index: int
    file_id: str
    file_unique_id: str
    gender: Literal["M", "F", "U"]
    age: int | None
    vision_error: str = ""
    is_document: bool = False
    photo_bytes: bytes | None = None
    raw_bytes: bytes | None = None
    cropped_file_id: str | None = None


@dataclass
class MatchAssignment:
    record_index: int
    photo_index: int
    mode: Literal["auto", "manual", "duplicate"]
    age_diff: int | None = None
    warning: str = ""


@dataclass
class BatchSession:
    batch_id: str
    user_id: int
    date_str: str
    records: list[MatchRecord] = field(default_factory=list)
    photos: list[PhotoItem] = field(default_factory=list)
    assignments: dict[int, MatchAssignment] = field(default_factory=dict)
    is_closed: bool = False
    crop_35x45: bool = True


def calc_age(dob_str: str, ref_date_str: str) -> int | None:
    """Вычисляет полных лет по dob (DD.MM.YYYY) на дату ref_date_str (YYYY-MM-DD)."""
    if not dob_str:
        return None
    try:
        dob_dt = datetime.strptime(dob_str.strip(), "%d.%m.%Y").date()
        ref_dt = date.fromisoformat(ref_date_str)
    except ValueError:
        return None
    age = ref_dt.year - dob_dt.year - ((ref_dt.month, ref_dt.day) < (dob_dt.month, dob_dt.day))
    return age if 0 <= age <= 120 else None


class BatchStore:
    def __init__(self, today_fn: Callable[[], str] | None = None):
        self._today_fn: Callable[[], str] = today_fn or (lambda: date.today().isoformat())
        self._sessions: dict[int, BatchSession] = {}
        self._stale_cleared: set[int] = set()
        self._crop_prefs: dict[int, bool] = {}

    def set_crop_35x45(self, user_id: int, enabled: bool) -> None:
        self._crop_prefs[user_id] = enabled
        session = self.get_active(user_id)
        if session is not None:
            session.crop_35x45 = enabled

    def today(self) -> str:
        return self._today_fn()

    def get_active(self, user_id: int) -> BatchSession | None:
        session = self._sessions.get(user_id)
        if session is None:
            return None
        if session.is_closed or session.date_str != self._today_fn():
            if not session.is_closed and session.date_str != self._today_fn():
                self._stale_cleared.add(user_id)
            session.is_closed = True
            self._sessions.pop(user_id, None)
            return None
        return session

    def get_or_create(self, user_id: int) -> tuple[BatchSession, bool, bool]:
        """Возвращает (session, created_new, cleared_stale)."""
        existing = self._sessions.get(user_id)
        cleared_stale = user_id in self._stale_cleared
        self._stale_cleared.discard(user_id)
        today = self._today_fn()

        if existing is not None:
            if not existing.is_closed and existing.date_str == today:
                return existing, False, False
            if not existing.is_closed and existing.date_str != today:
                cleared_stale = True
            existing.is_closed = True
            self._sessions.pop(user_id, None)

        session = BatchSession(
            batch_id=secrets.token_hex(3),
            user_id=user_id,
            date_str=today,
            crop_35x45=self._crop_prefs.get(user_id, True),
        )
        self._sessions[user_id] = session
        return session, True, cleared_stale

    def is_valid_batch(self, user_id: int, batch_id: str) -> bool:
        session = self.get_active(user_id)
        return session is not None and session.batch_id == batch_id

    def reset(self, user_id: int) -> BatchSession | None:
        session = self._sessions.pop(user_id, None)
        if session is not None:
            session.is_closed = True
        return session


@dataclass
class _GroupEntry:
    batch_id: str
    messages: list[Any] = field(default_factory=list)
    task: asyncio.Task[Any] | None = None


class MediaGroupBuffer:
    def __init__(self, delay: float = 0.7):
        self.delay = delay
        self._groups: dict[tuple[int, str], _GroupEntry] = {}

    def add(
        self,
        user_id: int,
        media_group_id: str,
        batch_id: str,
        message: Any,
        flush_cb: Callable[[int, str, str, list[Any]], Awaitable[None]],
    ) -> bool:
        key = (user_id, media_group_id)
        entry = self._groups.get(key)
        is_first = False
        if entry is None or entry.batch_id != batch_id:
            if entry is not None and entry.task and not entry.task.done():
                entry.task.cancel()
            entry = _GroupEntry(batch_id=batch_id)
            self._groups[key] = entry
            is_first = True

        entry.messages.append(message)

        if entry.task is None or entry.task.done():
            async def _runner() -> None:
                try:
                    await asyncio.sleep(self.delay)
                except asyncio.CancelledError:
                    return
                current = self._groups.pop(key, None)
                if current is None or current.batch_id != batch_id:
                    return
                await flush_cb(user_id, media_group_id, batch_id, current.messages)

            entry.task = asyncio.create_task(_runner())

        return is_first

    def cancel_batch(self, user_id: int, batch_id: str) -> None:
        to_delete = [
            key
            for key, entry in self._groups.items()
            if key[0] == user_id and entry.batch_id == batch_id
        ]
        for key in to_delete:
            entry = self._groups.pop(key, None)
            if entry and entry.task and not entry.task.done():
                entry.task.cancel()
