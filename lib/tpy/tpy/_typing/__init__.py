# tpy: cpp_namespace("tpystd::typing")
from tpy import int32
from .._bootstrap._decorators import readonly
from .._bootstrap._extern import builtin_type, builtin_decorator, builtin_function


@builtin_type("typing.Protocol")
class Protocol: ...

@builtin_type("typing.Self")
class Self: ...

@builtin_type("typing.Optional")
class Optional: ...

@builtin_type("typing.Any")
class Any:
    """Type-erased value cell. Holds any concrete copyable value.
    Use `typing.cast(T, x)` or `isinstance(x, T)` to extract."""

@builtin_type("typing.Final")
class Final: ...

@builtin_type("typing.ClassVar")
class ClassVar: ...

@builtin_type("typing.Callable")
class Callable: ...

@builtin_type("typing.Literal")
class Literal: ...

@builtin_type("typing.TypedDict")
class TypedDict: ...

@builtin_type("typing.Unpack")
class Unpack: ...

@builtin_decorator("typing.overload")
def overload(): ...

@builtin_decorator("typing.override")
def override(): ...

# typing.cast(T, x): static-only no-op in CPython; in TPy compiled
# binaries this gets runtime panic-on-mismatch semantics when the source
# is Any. Signature is illustrative; sema/codegen handle this specially.
@builtin_function("typing.cast")
def cast(target_type, value): ...


class Sized(Protocol):
    @readonly
    def __len__(self) -> int32: ...


class Sequence[T](Protocol):
    @readonly
    def __len__(self) -> int32: ...
    @readonly
    def __getitem__(self, index: int32) -> T: ...


class MutableSequence[T](Protocol):
    @readonly
    def __len__(self) -> int32: ...
    def __getitem__(self, index: int32) -> T: ...
    def __setitem__(self, index: int32, value: T) -> None: ...


class Iterator[T](Protocol):
    def __next__(self) -> T: ...
    def __iter__(self) -> Self: ...


class Iterable[T](Protocol):
    def __iter__(self) -> Iterator[T]: ...
