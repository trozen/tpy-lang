# os._environ -- the os.environ mapping and its process-environment snapshot.
# A leaf module (imports only os._native) so both os/__init__ and os/path can
# read `environ` without an os <-> os.path cycle. Internal: import via `os`.
from typing import Iterator
from tpy import int32, Own
from ._native import (
    environ_keys, env_get, setenv as _setenv, unsetenv as _unsetenv,
)


# A snapshot of the process environment captured at program start. Writes
# through this mapping (__setitem__/__delitem__) keep it in sync with libc;
# os.putenv/os.unsetenv go straight to libc and intentionally leave it stale,
# matching CPython, where os.environ is the only mapping kept in sync.
class _Environ:
    _data: dict[str, str]

    def __init__(self) -> None:
        self._data = {}
        for k in environ_keys():
            self._data[k] = env_get(k)

    def __getitem__(self, key: str) -> str:
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    # CPython raises ValueError on an embedded NUL in key/value; we don't check
    # (setenv would truncate at the NUL) -- env names/values never contain NUL.
    def __setitem__(self, key: str, value: str) -> None:
        self._data[key] = value
        _setenv(key, value)

    def __delitem__(self, key: str) -> None:
        if key not in self._data:
            raise KeyError(key)
        del self._data[key]
        _unsetenv(key)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int32:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    # keys/values/items return owned snapshot lists, not CPython's live set-like
    # views: a dict view borrows self._data and carries an auto_readonly[V] wrap
    # that the wrapper return type can't name. Iteration is identical; what is
    # lost is live reflection and set ops (uncommon for os.environ, itself a
    # snapshot). See STDLIB_ROADMAP.md.
    def keys(self) -> Own[list[str]]:
        out: list[str] = []
        for k in self._data:
            out.append(k)
        return out

    def values(self) -> Own[list[str]]:
        out: list[str] = []
        for k in self._data:
            out.append(self._data[k])
        return out

    def items(self) -> Own[list[tuple[str, str]]]:
        out: list[tuple[str, str]] = []
        for k in self._data:
            out.append((k, self._data[k]))
        return out

    # Single method, not the typeshed str / str|None overload pair: an
    # overloaded method on a cross-module-imported type does not resolve (see
    # BUGS.md), and os.environ.get is always called from a downstream module.
    def get(self, key: str, default: str | None = None) -> str | None:
        if key in self._data:
            return self._data[key]
        return default

    # pop takes a required default (returns it when absent) rather than the
    # typeshed pop(key)-raises / pop(key, default) pair -- same cross-module
    # overload limitation as get (BUGS.md); bare pop(key) is deferred.
    def pop(self, key: str, default: str) -> str:
        if key in self._data:
            value = self._data[key]
            del self._data[key]
            _unsetenv(key)
            return value
        return default

    def setdefault(self, key: str, default: str) -> str:
        if key in self._data:
            return self._data[key]
        self._data[key] = default
        _setenv(key, default)
        return default

    def update(self, other: dict[str, str]) -> None:
        for k in other:
            value = other[k]
            self._data[k] = value
            _setenv(k, value)

    def clear(self) -> None:
        # Collect first: unsetenv-while-iterating the same dict is unsafe.
        keys: list[str] = []
        for k in self._data:
            keys.append(k)
        for k in keys:
            _unsetenv(k)
        self._data.clear()

    def copy(self) -> Own[dict[str, str]]:
        out: dict[str, str] = {}
        for k in self._data:
            out[k] = self._data[k]
        return out


environ: _Environ = _Environ()
