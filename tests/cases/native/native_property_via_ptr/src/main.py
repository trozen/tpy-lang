# @property + @native(rename) on a native class accessed through Ptr[T].
# The Ptr[T] method-call codegen path must honor the native rename the same way
# the plain-value path does; otherwise generated code references a member the
# C++ class does not have.
# tpy: include("native_types.hpp")
from tpy import int32, Ptr, readonly, pure
from tpy.extern import native, cpp_template

@native("x::A")
class A:
    v: int32

@native("x::S")
class S:
    @property
    @native("config")
    @pure
    @readonly
    def Config(self) -> A: ...

@cpp_template("get_s()")
def get_s() -> Ptr[S]: ...

def main() -> None:
    s: Ptr[S] = get_s()
    # Property access through Ptr[T] -- must emit `.config()`, not `.Config()`.
    print(s.Config.v)

main()
