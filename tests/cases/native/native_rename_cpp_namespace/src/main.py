# @native rename interaction with cpp_namespace:
# - bare rename ("name", no ::) is relative to cpp_namespace
# - rename containing :: is absolute (module namespace ignored)
# - leading :: is absolute to global scope
# tpy: cpp_namespace("mylib")
# tpy: include("native_types.hpp")
from tpy import int32
from tpy.extern import native


# Bare rename -> ::mylib::ns_add_impl (relative to cpp_namespace)
@native("ns_add_impl")
def ns_add(a: int32, b: int32) -> int32: ...


# Rename with :: -> ::other_ns::other_add (absolute; contains :: = skips prefix)
@native("other_ns::other_add")
def other_add(a: int32, b: int32) -> int32: ...


# Leading :: -> ::global_mul (absolute to global scope)
@native("::global_mul")
def global_mul(a: int32, b: int32) -> int32: ...


def main() -> None:
    print(ns_add(int32(10), int32(32)))      # -> ::mylib::ns_add_impl
    print(other_add(int32(20), int32(22)))   # -> ::other_ns::other_add
    print(global_mul(int32(6), int32(7)))    # -> ::global_mul


main()
