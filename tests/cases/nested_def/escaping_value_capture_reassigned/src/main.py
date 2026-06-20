# Escaping by-value closures warn when a captured local is reassigned after
# capture (the silent CPython-divergence case). Closures run before the
# reassignment so TPy and CPython agree on output -- the warning is what's pinned.
from typing import Callable

def hold(f: Callable[[], int]) -> Callable[[], int]:
    return f

def lambda_reassigned() -> None:
    k = 10
    f = hold(lambda: k)  # tpyc: warning(/captures local 'k' by value.*reassigned after/)
    print(f())
    k = 20
    print(k)

def nested_def_via_local() -> None:
    k = 100
    def inner() -> int:  # tpyc: warning(/captures local 'k' by value.*reassigned after/)
        return k
    g: Callable[[], int] = inner
    print(g())
    k = 200
    print(k)

def nested_def_direct_return() -> Callable[[], int]:
    k = 5
    def inner() -> int:  # tpyc: warning(/captures local 'k' by value.*reassigned after/)
        return k
    k = 6
    print(k)
    return inner  # returned closure is dropped by the caller -- never invoked across the divergence

def aug_assign_counter() -> None:
    c = 0
    f = hold(lambda: c)  # tpyc: warning(/captures local 'c' by value.*reassigned after/)
    print(f())
    c += 1
    print(c)

def walrus_reassigned() -> None:
    k = 10
    f = hold(lambda: k)  # tpyc: warning(/captures local 'k' by value.*reassigned after/)
    print(f())
    n = (k := 20)
    print(n)
    print(k)

def tuple_unpack_reassigned() -> None:
    k = 1
    f = hold(lambda: k)  # tpyc: warning(/captures local 'k' by value.*reassigned after/)
    print(f())
    k, m = (2, 3)
    print(k)
    print(m)

class Box:
    def make(self) -> None:
        k = 1
        f = hold(lambda: k)  # tpyc: warning(/captures local 'k' by value.*reassigned after/)
        print(f())
        k = 2
        print(k)

def clean_no_reassign() -> None:
    k = 7
    f = hold(lambda: k)  # tpyc: ok
    print(f())

def reassign_before_capture() -> None:
    k = 1
    k = 2
    f = hold(lambda: k)  # tpyc: ok
    print(f())

def loop_capture_no_warn() -> None:
    # Loop-var capture is the deferred case (rebinding is the loop edge, not a
    # later statement) -- no warning today; each closure runs before the next
    # iteration so TPy and CPython still agree here.
    for k in range(3):
        f = hold(lambda: k)  # tpyc: ok
        print(f())

def main() -> None:
    lambda_reassigned()
    nested_def_via_local()
    nested_def_direct_return()
    aug_assign_counter()
    walrus_reassigned()
    tuple_unpack_reassigned()
    Box().make()
    clean_no_reassign()
    reassign_before_capture()
    loop_capture_no_warn()

main()
