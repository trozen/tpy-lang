# CPython parity: asyncio.run accepts coroutine objects only -- a Future
# (or any other merely-conforming awaitable) raises ValueError in CPython;
# TPy rejects at compile time.
from tpy import Int32
from asyncio import Future, run


def main() -> None:
    f = Future[Int32]()
    f.set_result(Int32(0))
    run(f)  # tpyc: error(/expects a coroutine/)


main()
