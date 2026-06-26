# socket timeout-mode introspection: gettimeout (None / 0.0 / positive),
# getblocking, the setblocking<->settimeout equivalence, and the negative-value
# ValueError. Prints comparisons/booleans (not raw floats) so the snapshot does
# not depend on float formatting.
import socket


def main() -> None:
    a, b = socket.socketpair()

    print("default:", a.gettimeout() is None, a.getblocking())
    a.settimeout(0.0)
    print("zero:", a.gettimeout() == 0.0, a.getblocking())
    a.settimeout(2.5)
    print("pos:", a.gettimeout() == 2.5, a.getblocking())
    a.settimeout(None)
    print("none:", a.gettimeout() is None, a.getblocking())

    # setblocking(False) == settimeout(0.0); setblocking(True) == settimeout(None).
    a.setblocking(False)
    print("nb:", a.gettimeout() == 0.0, a.getblocking())
    a.setblocking(True)
    print("blk:", a.gettimeout() is None, a.getblocking())

    try:
        a.settimeout(-1.0)
        print("NO ERROR")
    except ValueError as e:
        print("neg:", str(e))

    # Non-finite timeouts: NaN -> ValueError, inf -> OverflowError (CPython).
    try:
        a.settimeout(float("nan"))
        print("NO ERROR")
    except ValueError as e:
        print("nan:", str(e))
    try:
        a.settimeout(float("inf"))
        print("NO ERROR")
    except OverflowError as e:
        print("inf:", str(e))

    a.close()
    b.close()


main()
