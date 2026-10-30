# A None-typed f-string operand with a side effect is refused, never folded
# into the constant text "None" (BUGS.md#fstring-none-operand-rejects).
from tpy import Own


class M:
    n: None

    def __init__(self) -> None:
        self.n = None


def mk() -> Own[M]:
    print("mk")
    return M()


def main() -> None:
    print(f"[{mk().n}]")  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt.expr_stmt:expr.fstring:field.result_type\)/)


main()
