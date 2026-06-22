# Regression guard for sock_sendall's offset advance across parks: the
# sender streams a payload far larger than the socket buffer, so sendall
# makes many partial sends with a growing offset. The payload is built in
# 4096-byte bands of distinct values, so it is NON-periodic over its full
# length -- the receiver checks each byte against its absolute position, so
# a sender that re-sent the head each park instead of advancing the offset
# would land low-band bytes where high-band bytes are expected and mismatch
# (a periodic payload would let page-aligned parks hide that). The payload
# is read-only here, so no aliasing/mutation semantics are at stake.
import asyncio
from socket import socketpair, socket

_N = 1 << 20  # 1 MiB, well past any socketpair buffer


def _make_payload() -> bytes:
    out = b""
    band = 0
    while band < 256:
        out += bytes([band]) * 4096  # 256 * 4096 == _N
        band += 1
    return out


_PAYLOAD = _make_payload()


async def sender(sock: socket) -> None:
    loop = asyncio.get_running_loop()
    await loop.sock_sendall(sock, _PAYLOAD)
    sock.shutdown(1)  # SHUT_WR -> receiver sees EOF


async def receiver(sock: socket) -> None:
    loop = asyncio.get_running_loop()
    pos = 0
    mismatches = 0
    while True:
        chunk = await loop.sock_recv(sock, 4096)
        n = len(chunk)
        if n == 0:
            break
        for i in range(n):
            if chunk[i] != _PAYLOAD[pos + i]:
                mismatches += 1
        pos += n
    print(pos)
    print(mismatches)


async def main_coro() -> None:
    a, b = socketpair()
    a.setblocking(False)
    b.setblocking(False)
    rx = asyncio.create_task(receiver(b))
    await sender(a)
    await rx


def main() -> None:
    asyncio.run(main_coro())


main()
