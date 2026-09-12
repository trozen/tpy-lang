# A deduced str view local in an async coro frame is promoted to owned
# storage: the unpack temp dies before the await, so the post-suspension
# read needs the frame to own the copies (async sibling of
# generators/gen_view_local_owned_frame).
import asyncio
from tpy import int32


def pair(n: int32) -> tuple[str, str]:
    return ("host-" + str(n), "port-" + str(n))


async def read_addr() -> None:
    # The promoted owned fields must survive the suspension.
    host, port = pair(9)
    await asyncio.sleep(0)
    print(host, port)


def main() -> None:
    asyncio.run(read_addr())


main()
