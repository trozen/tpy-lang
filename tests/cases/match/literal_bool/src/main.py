# match/case on bool subject with True/False singleton patterns
def describe(b: bool) -> str:
    match b:
        case True:
            return "yes"
        case False:
            return "no"

def main() -> None:
    print(describe(True))
    print(describe(False))

main()
