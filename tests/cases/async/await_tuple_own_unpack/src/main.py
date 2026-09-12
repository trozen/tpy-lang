# An await-result tuple with a @nocopy/Own (or reference) element is moved out
# at unpack, not copied -- mutate-after-boundary proves the move (a copy of a
# @nocopy element would be a compile error).
import asyncio
from tpy import int32, Own, nocopy
from tpy.coro import Poll, Waker, poll_ready


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


@nocopy
class _OwnPair:
    _polls: int32

    def __init__(self) -> None:
        self._polls = 0

    def cancel(self) -> None:
        pass

    def __poll__(self, w: Waker) -> Own[Poll[tuple[Own[Counter], int32]]]:
        self._polls += 1
        return poll_ready((Counter(10), self._polls))


@nocopy
class _ValPair:
    """Inverse: a pure value tuple must keep working (no spurious move)."""

    def cancel(self) -> None:
        pass

    def __poll__(self, w: Waker) -> Own[Poll[tuple[int32, int32]]]:
        # Explicit int32: poll_ready[T]'s arg isn't target-typed by the return,
        # so bare literals stay IntLiteral and mismatch tuple[int32, int32].
        return poll_ready((int32(1), int32(2)))


@nocopy
class _RefPair:
    """Reference-element (non-Own) tuple: the await result owns the list, so
    it must get owning storage (not a borrow-form `std::tuple<vec*, int>`
    field) and move the list out -- mutating it after proves it's the moved
    list, not a copy."""

    def cancel(self) -> None:
        pass

    def __poll__(self, w: Waker) -> Own[Poll[tuple[list[int32], int32]]]:
        xs: list[int32] = [10, 20]
        return poll_ready((xs, int32(2)))


async def main_coro() -> None:
    # Owned-element unpack: move the @nocopy Counter out, then mutate it.
    c, tag = await _OwnPair()
    c.bump()
    c.bump()
    print(c.n, tag)

    # Re-await in a loop: the frame_slot must re-emplace each iteration.
    i: int32 = 0
    while i < 3:
        d, k = await _OwnPair()
        d.bump()
        print(d.n, k)
        i += 1

    # Inverse: value tuple still unpacks.
    a, b = await _ValPair()
    print(a, b)

    # Reference-element tuple: the list is moved out, then mutated.
    lst, m = await _RefPair()
    lst.append(30)
    print(len(lst), lst[2], m)


def main() -> None:
    asyncio.run(main_coro())


main()
