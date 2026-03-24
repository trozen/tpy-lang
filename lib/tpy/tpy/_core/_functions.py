# tpy: cpp_namespace("tpystd::tpy")
from tpy import Span, Ptr, Own, readonly, pure
from tpy.extern import native, cpp_template, value_ptr_coercion
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
