# tpy: cpp_namespace("tpystd::builtins")
from typing import overload
from ._typing import Iterator, Iterable
from tpy import Int32, Own, readonly, pure
from ._types import NativeIterable
from tpy.extern import native, cpp_template, native_preserves_refs, builtin_type


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
@native("tpy::ordered_map")
class dict[K, V](Iterable[K], NativeIterable[K]):
    @pure
    @readonly
    @cpp_template("::tpy::dict_construct<{K}, {V}>({0})")
    def __init__(self, x: Iterable[Own[tuple[K, V]]]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, key: K) -> V: ...

    @native("tpy::__setitem__", function=True)
    @native_preserves_refs
    def __setitem__(self, key: K, value: Own[V]) -> None: ...

    @native("tpy::__delitem__", function=True)
    def __delitem__(self, key: K) -> None: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, key: K) -> bool: ...

    @overload
    @native("tpy::dict_get", function=True)
    @pure
    @readonly
    def get(self, key: K) -> V | None: ...

    @overload
    @native("tpy::dict_get_default", function=True)
    @pure
    @readonly
    def get(self, key: K, default: V) -> Own[V]: ...

    @overload
    @native("tpy::dict_pop", function=True)
    def pop(self, key: K) -> Own[V]: ...

    @overload
    @native("tpy::dict_pop_default", function=True)
    def pop(self, key: K, default: V) -> Own[V]: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::dict_update", function=True)
    def update(self, other: dict[K, Own[V]]) -> None: ...

    @native("tpy::dict_update", function=True)
    def __ior__(self, other: dict[K, Own[V]]) -> dict[K, V]: ...

    @native("tpy::dict_setdefault", function=True)
    def setdefault(self, key: K, default: Own[V]) -> Own[V]: ...

    @native("tpy::dict_keys", function=True)
    @pure
    @readonly
    def keys(self) -> dict_keys[K, V]: ...

    @native("tpy::dict_values", function=True)
    @pure
    @readonly
    def values(self) -> dict_values[K, V]: ...

    @native("tpy::dict_items", function=True)
    @pure
    @readonly
    def items(self) -> dict_items[K, V]: ...
