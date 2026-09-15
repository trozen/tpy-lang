# Assert with expression messages (variables, method calls, field access,
# a @property read). The message is evaluated only on failure, so the
# property's getter call sits inside the negated-if.
import asyncio
from typing import Iterator
from tpy import int32, StrView

class Error:
    message: str
    def __init__(self, message: str) -> None:
        self.message = message

    @property
    def detail(self) -> StrView:
        return self.message

    def described(self) -> str:
        return "described:" + self.message

def check_positive(n: int32, msg: str) -> int32:
    assert n > 0, msg
    return n

def check_error(n: int32, e: Error) -> int32:
    assert n > 0, e.message
    return n

def check_property(n: int32, e: Error) -> int32:
    # a @property message: the getter CALL, not a member read
    assert n > 0, e.detail  # tpyc: ok
    return n

def check_method(n: int32, e: Error) -> int32:
    assert n > 0, e.described()  # tpyc: ok
    return n

class Checker:
    limit: int32
    def __init__(self, limit: int32) -> None:
        self.limit = limit

    # method position
    def check(self, n: int32, e: Error) -> int32:
        assert n > 0, e.detail  # tpyc: ok
        return n + self.limit

# generator position
def gen(n: int32, e: Error) -> Iterator[int32]:
    assert n > 0, e.detail  # tpyc: ok
    yield n

# async position
async def coro(n: int32, e: Error) -> int32:
    assert n > 0, e.detail  # tpyc: ok
    await asyncio.sleep(0)
    return n

def main() -> None:
    e = Error("bad value")
    print(check_positive(5, "must be positive"))
    print(check_error(3, e))
    print("property", check_property(7, e))
    print("method_msg", check_method(8, e))
    print("method", Checker(100).check(9, e))
    for v in gen(11, e):
        print("gen", v)
    print("async", asyncio.run(coro(12, e)))

main()
