# A qualified open `T | None` is the same per-instantiation trait as a bare one:
# the None test and the print, at a value, BigInt, ValueType, str and class `T`.
from typing import Optional

from tpy import Send, Sync, int32, readonly

from fixtures import Named, P, V


def plain[T](x: T | None) -> None:
    print("plain", x is not None, x)  # tpyc: ok


def send[T](x: Send[T] | None) -> None:
    # The Send marker on `T` does not hide the open parameter.
    print("send", x is not None, x)  # tpyc: ok


def sync[T](x: Sync[T] | None) -> None:
    # Nor does the Sync marker.
    print("sync", x is not None, x)  # tpyc: ok


def ro[T](x: readonly[T] | None) -> None:
    print("ro", x is not None, x)  # tpyc: ok


def opt_ro[T](x: Optional[readonly[T]]) -> None:
    print("opt_ro", x is not None, x)  # tpyc: ok


def send_ro[T](x: Send[readonly[T]] | None) -> None:
    print("send_ro", x is not None, x)  # tpyc: ok


def send_closure[T](x: Send[T] | None) -> bool:
    # A closure capturing the qualified parameter reads it in its form.
    def inner() -> bool:
        return x is not None  # tpyc: ok

    return inner()


def bound[T: Named](x: T | None) -> None:
    print("bound", x is not None, x)  # tpyc: ok


def main() -> None:
    p = P(3)
    # A bare `None` argument would instantiate `T = None`
    # (BUGS.md#open-t-union-param-unusable); a typed None keeps `T = int32`.
    no: int32 | None = None
    plain(1)
    plain(V(2))
    plain("s")
    plain(p)
    plain(no)
    send(1)
    send(V(2))
    send("s")
    send(p)
    send(no)
    sync(1)
    sync(V(2))
    sync("s")
    sync(p)
    sync(no)
    ro(1)
    ro(V(2))
    ro("s")
    ro(p)
    ro(no)
    opt_ro(1)
    opt_ro(V(2))
    opt_ro("s")
    opt_ro(p)
    send_ro(1)
    send_ro(V(2))
    send_ro("s")
    send_ro(p)
    # `int` is BigInt: the value form at a non-trivial payload.
    big: int = 123456789012345678901234567890
    plain(big)
    send(big)
    sync(big)
    ro(big)
    opt_ro(big)
    send_ro(big)
    print("send_closure", send_closure(1), send_closure("s"), send_closure(p),
          send_closure(no))
    bound(V(2))
    bound(p)
