# asyncio reactor: sock_connect to a refused port raises
# ConnectionRefusedError (an OSError), matching CPython asyncio.
# Grab a port by binding+listening, then close it: connecting to the freed
# port draws an RST -> ECONNREFUSED on both Linux and macOS. (A bound-but-not-
# listen()ed socket only refuses on Linux; BSD/macOS silently drops the SYN
# and the connect times out instead -- host-divergent, so not usable here.)
import asyncio
from socket import socket, AF_INET, SOCK_STREAM


async def main_coro() -> None:
    loop = asyncio.get_running_loop()
    probe = socket(AF_INET, SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    host, port = probe.getsockname()
    probe.close()

    s = socket(AF_INET, SOCK_STREAM)
    s.setblocking(False)
    try:
        await loop.sock_connect(s, ("127.0.0.1", port))
        print("connected unexpectedly")
    except ConnectionRefusedError:
        # The specific subclass pins the CPython-parity claim; a regression
        # to generic SocketError would fall through to the arm below.
        print("connect refused")
    except OSError:
        print("caught generic OSError")
    s.close()


def main() -> None:
    asyncio.run(main_coro())


main()
