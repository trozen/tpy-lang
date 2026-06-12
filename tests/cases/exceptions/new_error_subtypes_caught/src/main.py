# PermissionError / FloatingPointError / RecursionError / EOFError exist
# and slot into CPython's hierarchy: each is caught via its base class.


def main() -> None:
    try:
        raise PermissionError("denied")
    except OSError as e:
        print("via OSError:", str(e))

    try:
        raise FloatingPointError("bad fp")
    except ArithmeticError as e:
        print("via ArithmeticError:", str(e))

    try:
        raise RecursionError("too deep")
    except RuntimeError as e:
        print("via RuntimeError:", str(e))

    try:
        raise EOFError("no more input")
    except EOFError as e:
        print("exact EOFError:", str(e))

    # Each new leaf is also reachable through the common Exception base.
    try:
        raise EOFError("eof again")
    except Exception as e:
        print("eof via Exception:", str(e))

    try:
        raise PermissionError("perm again")
    except Exception as e:
        print("perm via Exception:", str(e))


main()
