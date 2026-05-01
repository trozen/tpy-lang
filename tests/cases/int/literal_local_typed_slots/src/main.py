# Retro-widen of literal-seeded locals fires anywhere a TpyName flows into
# a concretely-typed fixed-int slot via check_type_compatible: ARG, RETURN,
# INIT (annotated new local), ASSIGN (reassign to existing typed), SETITEM
# (typed container element), FIELD assign, dict-key in subscript-assign.
# Method calls work too -- Own/Readonly/Ref wrappers around the param type
# (e.g. list[T].append takes Own[T]) are stripped before the fit check.
from tpy import UInt32, UInt64


def ret_unsigned() -> UInt64:
    x = 0
    return x


def init_typed_local() -> UInt64:
    x = 5
    y: UInt64 = x
    return y


def reassign_existing_typed() -> UInt64:
    y: UInt64 = 100
    x = 7
    y = x
    return y


def setitem_typed_list() -> UInt64:
    xs: list[UInt64] = [0, 0, 0]
    n = 9
    xs[1] = n
    return xs[1]


class Holder:
    val: UInt64

    def __init__(self) -> None:
        self.val = UInt64(0)


def field_assign_typed() -> UInt64:
    h = Holder()
    n = 11
    h.val = n
    return h.val


def dict_key_typed() -> str:
    d: dict[UInt64, str] = {}
    k = 13
    d[k] = "ok"
    return d[k]


def method_arg_with_own_param() -> UInt64:
    xs: list[UInt64] = []
    n = 19
    xs.append(n)
    return xs[0]


def main() -> None:
    print(ret_unsigned())
    print(init_typed_local())
    print(reassign_existing_typed())
    print(setitem_typed_list())
    print(field_assign_typed())
    print(dict_key_typed())
    print(method_arg_with_own_param())


main()
