# Implicit-T inference across a generic protocol param: a value of a
# user-record type arriving via a generic outer-function parameter (so its
# expression type carries a Ref[] wrapper) must still resolve through the
# record's structural conformance. Covers both the same-name (T/T) and
# distinct-name (T/U) shapes -- the fix isn't about TypeParamRef identity,
# it's about the structural-inference path stripping Ref on the arg side.
from tpy import int32
from tpy import Own
from tpy.coro import Awaitable, Poll, Waker, poll_once, poll_pending


class MyTask[T]:
    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        return poll_pending()


def drive_implicit[T](aw: Awaitable[T]) -> Own[Poll[T]]:
    return poll_once(aw)


def use_shadowed[T](t: MyTask[T]) -> Own[Poll[T]]:
    return drive_implicit(t)


def use_renamed[U](t: MyTask[U]) -> Own[Poll[U]]:
    return drive_implicit(t)


def main() -> None:
    a = MyTask[int32]()
    print(use_shadowed(a).is_pending())
    b = MyTask[int32]()
    print(use_renamed(b).is_pending())


main()
