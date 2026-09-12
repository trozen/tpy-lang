# Regression: package re-exporting records / enums / generic records /
# @dynamic protocols / functions / variables from two sibling
# submodules, where one sibling imports the other. Pre-fix this
# crashed at C++ compile with `'tpyapp::pkg::a' has not been declared`
# from pkg.hpp's using-decls.
#
# Also exercises:
#   - V used in default-arg position (drives default_to_cpp's
#     defining-module routing for re-exported Finals).
#   - Generic record (Container[T]) re-exported through a package,
#     exercising the template-header branch of the fwd-decl helper.
#   - @dynamic protocol (Greeter) re-export, ensuring the protocol-
#     using suppression for descendant submodules doesn't break
#     downstream references.
from tpy import int32
from pkg import A, B, Container, Greeter, K, V, f


def cap(n: int32 = V) -> int32:
    return n


def takes_greeter(g: Greeter) -> int32:
    return g.greet()


def main() -> None:
    a = A()
    b = B()
    box = Container(int32(42))
    print(a.value() + b.value() + V + f(int32(5)))
    print(K.ONE)
    print(cap())
    print(cap(int32(10)))
    print(box.get())
    print(takes_greeter(b))


main()
