# tpy: cpp_namespace("tpystd::tpy")
from tpy import Ptr
from .._bootstrap._decorators import readonly, pure, Own
from .._bootstrap._extern import native, cpp_template, value_ptr_coercion, builtin_function
from ._containers import Span
from ._types import ReadOnlySpanLike, Deref, Default


@pure
@readonly
@native("tpy::as_span")
def span[T](x: ReadOnlySpanLike[T]) -> Span[readonly[T]]: ...

@pure
@readonly
@native("tpy::deref_check")
def deref[T](x: Deref[T]) -> T: ...

@value_ptr_coercion
@cpp_template("{0}")
def take_ptr[T](p: Ptr[T]) -> Ptr[T]: ...

@cpp_template("{T}{{}}")
def make_default[T: Default]() -> Own[T]: ...


# -- Special-handling functions (custom sema/codegen, signatures are illustrative) --

@builtin_function("tpy.copy")
def copy[T](x: T) -> Own[T]: ...

@builtin_function("tpy.copy_iter")
def copy_iter[T](x: T) -> T: ...

@builtin_function("tpy.own_iter")
def own_iter[T](x: T) -> T: ...

@builtin_function("tpy.try_parse")
def try_parse(enum_type, name: str): ...
