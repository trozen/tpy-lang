# is / is not with bool literals: lowered to == / != at sema time.
def check_is(x: bool) -> None:
    if x is True:
        print("is True")
    if x is False:
        print("is False")
    if x is not True:
        print("is not True")
    if x is not False:
        print("is not False")

def check_reversed(x: bool) -> None:
    if True is x:
        print("True is x")
    if False is not x:
        print("False is not x")

def main() -> None:
    check_is(True)
    print("---")
    check_is(False)
    print("---")
    check_reversed(True)

main()
