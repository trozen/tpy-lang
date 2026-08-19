# Async coroutines share the resumable frame with generators, so a walrus target
# in a coroutine body is a frame field too: no C++ local may be declared for it.
# Each shape below writes the walrus target, suspends, then reads it back.
import asyncio
from tpy import Int32


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


async def step(i: Int32) -> Int32:
    await asyncio.sleep(0)
    return i * 10


# -- value scalar bound from an await result, read after a later suspension.
async def val_scalar() -> None:
    i = 0
    while i < 2:
        print("scalar val", (n := await step(i)))
        await asyncio.sleep(0)
        print("scalar resume", n)
        i += 1


# -- owning non-value (a `frame_slot<T>` field): the write must emplace.
async def owning() -> None:
    i = 0
    while i < 2:
        print("owning len", len(xs := [i, i + 1]))
        await asyncio.sleep(0)
        print("owning resume", len(xs), xs[0])
        i += 1


# -- statement-borrow alias: mutation after the suspension must reach the caller.
async def borrow_alias(rows: list[list[Int32]]) -> None:
    i = 0
    while i < len(rows):
        print("borrow len", len(row := rows[i]))
        await asyncio.sleep(0)
        row.append(99)
        print("borrow resume", len(row), row[0])
        i += 1


def pick(nodes: list[Node], i: Int32) -> Node | None:
    if i < len(nodes):
        return nodes[i]
    return None


# -- pointer-repr Optional: nullptr doubles as None, so a shadowed write would
# silently skip the `is not None` branch.
async def opt_ptr(nodes: list[Node]) -> None:
    i = 0
    while i < 3:
        m = pick(nodes, i)
        print("optptr bound", (p := m) is not None)
        await asyncio.sleep(0)
        if p is not None:
            p.v += 100
            print("optptr resume", p.v)
        else:
            print("optptr resume none")
        i += 1


async def drive() -> None:
    await val_scalar()
    await owning()
    rows = [[1, 2], [3, 4, 5]]
    await borrow_alias(rows)
    print("rows after", rows[0], rows[1])
    nodes = [Node(7), Node(8)]
    await opt_ptr(nodes)
    print("nodes after", nodes[0].v, nodes[1].v)


def main() -> None:
    asyncio.run(drive())


main()
