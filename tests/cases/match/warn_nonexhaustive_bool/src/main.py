# warning: non-exhaustive match on bool (missing False)
def describe(b: bool) -> str:
    match b:  # tpyc: warning(/non-exhaustive match.*missing: False.*case _:/)
        case True:
            return "yes"
    return "unknown"

def main() -> None:
    print(describe(True))
    print(describe(False))

main()
