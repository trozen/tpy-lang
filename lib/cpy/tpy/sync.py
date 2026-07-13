"""CPython stub for tpy.sync: Mutex/RwLock over threading locks.

The guard aliases the payload (never copies it), so mutation through the guard
is visible after release -- matching the TPy reference semantics. `g.method(...)`
forwards to the payload via __getattr__; `g.get()` returns it and `g.set(v)`
replaces it. RwLock uses one lock for both read and write: it over-serializes
readers, which is invisible to the byte-compared output but keeps the single
committed snapshot deterministic across hosts.
"""
import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class _Guard(Generic[T]):
    def __init__(self, mutex, acquire, release) -> None:
        self._mutex = mutex
        self._acquire = acquire
        self._release = release
        self._locked = False

    def __enter__(self):
        self._acquire()
        self._locked = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._locked = False
        self._release()

    def _require_locked(self) -> None:
        # Mirror the TPy runtime backstop: the payload is only accessible while
        # the lock is held (inside the `with` block).
        if not self._locked:
            raise RuntimeError(
                "lock guard used without holding the lock -- access the payload "
                "only inside the guard's `with` block")

    def get(self) -> T:
        self._require_locked()
        return self._mutex._value

    def set(self, value: T) -> None:
        self._require_locked()
        self._mutex._value = value

    def _raw_mutex(self):
        return self._mutex._lock

    def __getattr__(self, name):
        # Private/dunder lookups fall through to normal attribute machinery;
        # payload names (append, keys, ...) forward to the aliased payload but
        # only while the lock is held.
        if name.startswith("_"):
            raise AttributeError(name)
        self._require_locked()
        return getattr(self._mutex._value, name)


class Mutex(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value
        self._lock = threading.Lock()

    @staticmethod
    def new(value: T) -> "Mutex[T]":
        return Mutex(value)

    def lock(self) -> "_Guard[T]":
        return _Guard(self, self._lock.acquire, self._lock.release)


class RwLock(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value
        self._lock = threading.Lock()

    @staticmethod
    def new(value: T) -> "RwLock[T]":
        return RwLock(value)

    def read(self) -> "_Guard[T]":
        return _Guard(self, self._lock.acquire, self._lock.release)

    def write(self) -> "_Guard[T]":
        return _Guard(self, self._lock.acquire, self._lock.release)


class Condvar:
    """threading.Condition over the guard's mutex lock, mirroring the C++
    Condvar waiting on the caller-held lock. The Condition is (re)bound whenever
    a different lock is passed, so one Condvar works with successive mutexes; a
    notify with no prior waiter is a no-op. The (re)bind and every _cond access
    happen while the caller holds the lock, so there is no race."""

    def __init__(self) -> None:
        self._lock = None
        self._cond = None

    def wait(self, guard) -> None:
        # Pass the live guard (matches TPy's `_CondvarLock`); pull its raw lock.
        # The caller holds it, so Condition.wait over it releases + reacquires.
        lock = guard._raw_mutex()
        if self._lock is not lock:
            self._lock = lock
            self._cond = threading.Condition(lock)
        self._cond.wait()

    def notify_one(self) -> None:
        if self._cond is not None:
            self._cond.notify()

    def notify_all(self) -> None:
        if self._cond is not None:
            self._cond.notify_all()
