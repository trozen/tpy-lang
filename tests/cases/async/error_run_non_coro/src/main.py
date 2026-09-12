# CPython parity: asyncio.run accepts coroutine objects only -- a Future
# (or any other merely-conforming awaitable) raises ValueError in CPython;
# TPy rejects at compile time.
from tpy import int32
from asyncio import Future, run


def main() -> None:
    f = Future[int32]()
    f.set_result(int32(0))
    run(f)  # tpyc: error(/expects a coroutine/)


main()
