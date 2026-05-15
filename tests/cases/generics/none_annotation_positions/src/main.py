# Bare `None` is accepted at every value-bearing annotation slot:
# function param, method param, variable annotation, record field, and
# `__exit__` exc params. Lowers to NoneType -> std::monostate. The
# return slot is the only carve-out -- `-> None` stays VoidType ->
# `void` (function-returns-nothing semantic).
#
# Free function and method params go through different finalize paths
# (`_finalize_function_refs` vs `resolve_refs.resolve_refs`'s record-
# method loop), so both are covered here.
#
# The Guard / __exit__ case is a parse-only guard: parser.py strips the
# three exc params before `_parse_type_ref` is called, so codegen never
# emits them today. The case verifies the annotations parse + resolve
# cleanly so a future stop-stripping upgrade doesn't have to re-type
# them. The other shapes exercise the full parse + resolve + codegen
# path.
class Field:
    slot: None

    def __init__(self):
        self.slot = None

    def take(self, x: None) -> None:
        self.slot = x


class Guard:
    def __enter__(self) -> 'Guard':
        print("enter")
        return self

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("exit")


def takes_none(x: None) -> None:
    local: None = x
    print("ran")


def main() -> None:
    f = Field()
    f.take(None)
    takes_none(None)
    with Guard():
        print("body")


main()
