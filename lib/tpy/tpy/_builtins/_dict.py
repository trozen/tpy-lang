# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import overload, Self, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, Own, auto_readonly
from .._core._types import Int32, NativeIterable
from .._bootstrap._extern import native, cpp_template, native_preserves_refs, builtin_type, copy_returns_warn


@builtin_type("builtins.dict_keys")
@native("tpy::dict_keys_view")
class dict_keys[K, V](Iterable[K], NativeIterable[K]):
    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, key: K) -> bool: ...


@builtin_type("builtins.dict_values")
@native("tpy::dict_values_view")
class dict_values[K, V](Iterable[V], NativeIterable[V]):
    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[V]: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, value: V) -> bool: ...


@builtin_type("builtins.dict_items")
@native("tpy::dict_items_view")
class dict_items[K, V](Iterable[tuple[K, V]], NativeIterable[tuple[K, V]]):
    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[tuple[K, V]]: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, item: tuple[K, V]) -> bool: ...


@builtin_type("builtins.dict")
@native("tpy::ordered_map", indirecting=True)
class dict[K, V](Iterable[K], NativeIterable[K]):
    @pure
    @readonly
    @cpp_template("::tpy::dict_construct<{K}, {V}>({0})")
    def __init__(self, x: Iterable[Own[tuple[K, V]]]) -> None: ...

    @overload
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...

    @overload
    @native("tpy::own_iter_dict", function=True)
    def __iter__(self: Own[Self]) -> Iterator[Own[K]]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    # Pure-READ methods take readonly[K]: the key is only hashed/compared, never
    # stored, so a readonly key is safe. __setitem__/setdefault store K (mutable).
    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, key: readonly[K]) -> V: ...

    @native("tpy::__setitem__", function=True)
    @native_preserves_refs
    def __setitem__(self, key: K, value: Own[V]) -> None: ...

    @native("tpy::__delitem__", function=True)
    def __delitem__(self, key: readonly[K]) -> None: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, key: readonly[K]) -> bool: ...

    @overload
    @native("tpy::dict_get", function=True)
    @pure
    @readonly
    def get(self, key: readonly[K]) -> V | None: ...

    # Copies where CPython aliases, so `d.get(k, []).append(x)` silently
    # no-ops -- @copy_returns_warn flags the call site.
    @overload
    @native("tpy::dict_get_default", function=True)
    @pure
    @readonly
    @copy_returns_warn
    def get(self, key: readonly[K], default: V) -> Own[V]: ...

    @overload
    @native("tpy::dict_pop", function=True)
    def pop(self, key: readonly[K]) -> Own[V]: ...

    @overload
    @native("tpy::dict_pop_default", function=True)
    def pop(self, key: readonly[K], default: V) -> Own[V]: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::dict_update", function=True)
    def update(self, other: dict[K, Own[V]]) -> None: ...

    @native("tpy::dict_update", function=True)
    def __ior__(self, other: dict[K, Own[V]]) -> dict[K, V]: ...

    # Returns a borrow of the stored value (CPython returns the stored
    # object), so `d.setdefault(k, []).append(x)` mutates the dict. The
    # bare `-> V` (not `Own[V]`) is what makes the result alias.
    @native("tpy::dict_setdefault", function=True)
    def setdefault(self, key: K, default: Own[V]) -> V: ...

    @native("tpy::dict_keys", function=True)
    @pure
    @readonly
    def keys(self) -> dict_keys[K, V]: ...

    @native("tpy::dict_values", function=True)
    @pure
    @auto_readonly
    def values(self) -> dict_values[K, auto_readonly[V]]: ...

    @native("tpy::dict_items", function=True)
    @pure
    @auto_readonly
    def items(self) -> dict_items[K, auto_readonly[V]]: ...
