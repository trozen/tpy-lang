# A whole-literal BigInt arithmetic expression that fits in 64 bits: it folds
# to one literal before lowering sees a binop, so the binop leg has nothing
# to render.
def f() -> None:
    print(bin(2 ** 62 + 1))  # tpyc: error(/stmt\.expr_stmt:binop\.shape\.\+/)


def main() -> None:
    f()


main()
