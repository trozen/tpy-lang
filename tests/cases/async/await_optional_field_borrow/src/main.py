# The await result of an `async def -> T | None` (pointer-repr Optional)
# binds in borrow form, matching the sync convention -- the coro return slot is
# now Poll<Box*>, not Poll<std::optional<Box>>. The bound local ALIASES the
# returned source (mutation visible), like the sync twin and CPython; pre-fix
# this was a hard g++ error (std::optional<Box> -> Box* at the await binding).
#
# Safe because a reference-type param lives in the frame by reference (H& h),
# so the returned borrow points into the caller's storage, which outlives the
# suspension. The existing dangling-return check gates what may be returned
# (a fresh `return Box(n)` is rejected, pointed at Own[Box] | None).
import asyncio
from tpy import Own


class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v


class H:
    opt: Box | None
    def __init__(self, b: Box | None) -> None:
        self.opt = b


async def get(h: H) -> Box | None:
    await asyncio.sleep(0)
    return h.opt


# Inverse: an owning return (Own[Box] | None, fresh value) must keep working --
# the value is owned, the binding consumes it.
async def make(present: bool) -> Own[Box] | None:
    await asyncio.sleep(0)
    if present:
        return Box(7)
    return None


async def main_coro() -> None:
    h = H(Box(1))
    t = await get(h)
    if t is not None:
        t.val = 99              # write through the aliased borrow
    if h.opt is not None:
        print(h.opt.val)        # 99 -- visible on the field (alias)

    empty = H(None)
    e = await get(empty)
    print("none" if e is None else "?")

    owned = await make(True)
    print(owned.val if owned is not None else -1)   # 7 (owning path intact)


def main() -> None:
    asyncio.run(main_coro())


main()
