# Cross-module use of a recursive union alias defined in another module.
# pkg_v.V (= None | bool | int | str | list[V] | dict[str, V]) is referenced
# here both through `import pkg_v` (no alias name imported) and via explicit
# `from pkg_v import V` (name in scope, used in our own annotations).
# Codegen must emit `::tpyapp::pkg_v::V` everywhere the wrapper struct is
# referenced, regardless of how the alias entered scope.
import pkg_v
from pkg_v import V
from tpy import Own


def is_null(v: V) -> bool:
    # Cross-module is-None narrowing.
    if v is None:
        return True
    return False


def wrap(v: V) -> V:
    # Wrapper param returned by reference: const-inference keeps v mutable to
    # match the `V&` return, mirroring the record passthrough convention.
    return v


class Holder:
    # Recursive alias used as a record field type.
    value: V

    def __init__(self, value: Own[V]) -> None:
        self.value = value


def main() -> None:
    a = pkg_v.make_int()
    b = pkg_v.make_dict()
    print(a)
    print(b)

    print(pkg_v.kind(a))
    print(pkg_v.kind(b))

    print(is_null(a))
    nl: V = None
    print(is_null(nl))

    h = Holder(7)
    print(pkg_v.kind(h.value))

    tmp = pkg_v.make_int()
    w = wrap(tmp)
    print(pkg_v.kind(w))


main()
