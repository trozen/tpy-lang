# The async sibling of generators/frame_local_last_use_move: a movable local at
# its last use inside a resumable frame. Both spellings share the frame_slot
# machinery, so both render `(*buf)` -- which must not be read as a
# materialized temporary, or the last-use move is silently dropped.
import asyncio


async def collect(n: int) -> int:
    out: list[bytes] = []
    buf = bytearray(b"abcd")
    await asyncio.sleep(0)
    out.append(buf)
    return len(out) + len(out[0]) + n


async def amain() -> None:
    print(await collect(1))


asyncio.run(amain())
