# A nested def is its own function: a return inside it must not run the
# enclosing try's finally body, and its own return type (not the outer
# function's) drives return lowering (Optional case).
def main() -> None:
    try:
        def g() -> int:
            return 5

        def h(flag: bool) -> int | None:
            if flag:
                return 7
            return None

        print(g())
        print(g())
        r = h(True)
        if r is not None:
            print(r)
    finally:
        print("done")


main()
