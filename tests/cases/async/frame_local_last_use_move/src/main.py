# The async sibling of generators/frame_local_last_use_move: a movable local at
# its last use inside a resumable frame. Both spellings share the frame_slot
# machinery, so both render `(*buf)` -- which must not be read as a
# materialized temporary, or the last-use move is silently dropped. The second
# append is the contrast: `bytearray` and `bytes` are distinct types, so an
# owning `bytes` sink refuses a bytearray outright and the copy is WRITTEN --
# `bytes(ba)`, which is never a move. The moved local is a `list[int32]` rather
# than the bytearray it used to be for the same reason: that pair no longer
# reaches the move arm at all. What the written copy does to the two objects is
# pinned in tests/cases/bytes/bytearray_copy_into_bytes_sink.
import asyncio

from tpy import int32


async def collect(n: int) -> int:
    out: list[list[int32]] = []
    seen: list[bytes] = []
    buf = [1, 2, 3, 4]
    ba = bytearray(b"abcd")
    await asyncio.sleep(0)
    out.append(buf)  # the frame slot, moved at its last use
    seen.append(bytes(ba))  # the written copy into a `bytes` element slot
    return len(out) + len(out[0]) + len(seen[0]) + n


async def amain() -> None:
    print(await collect(1))


asyncio.run(amain())
