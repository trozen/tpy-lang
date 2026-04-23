# @native rename interaction with cpp_namespace:
# - bare rename ("name", no ::) is relative to cpp_namespace
# - rename containing :: is absolute (module namespace ignored)
# - leading :: is absolute to global scope
# tpy: cpp_namespace("mylib")
# tpy: include("native_types.hpp")
from tpy import Int32
from tpy.extern import native


# Bare rename -> ::mylib::ns_add_impl (relative to cpp_namespace)
@native("ns_add_impl")
def ns_add(a: Int32, b: Int32) -> Int32: ...


# Rename with :: -> ::other_ns::other_add (absolute; contains :: = skips prefix)
@native("other_ns::other_add")
def other_add(a: Int32, b: Int32) -> Int32: ...


# Leading :: -> ::global_mul (absolute to global scope)
@native("::global_mul")
def global_mul(a: Int32, b: Int32) -> Int32: ...


def main() -> None:
    print(ns_add(Int32(10), Int32(32)))      # -> ::mylib::ns_add_impl
    print(other_add(Int32(20), Int32(22)))   # -> ::other_ns::other_add
    print(global_mul(Int32(6), Int32(7)))    # -> ::global_mul


main()
