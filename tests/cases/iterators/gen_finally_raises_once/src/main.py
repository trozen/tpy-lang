# Regression: in a resumable generator, cleanup that RAISES must run exactly
# once and the FIRST exception must propagate -- across all exit edges of a
# try/finally or with that suspends. Each exit-edge cleanup copy sets its
# region's guard so the region's own catch does not re-run a copy that raised
# (an enclosing region's guard stays unset, so its cleanup still runs).

from typing import Iterator

_code = 0


def bump() -> int:
    global _code
    _code += 1
    print(f"side-effect {_code}")
    return _code


class Err(Exception):
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class Thrower:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        raise Err(bump())


def normal_exit() -> Iterator[int]:
    # finally raises on the try's fall-through after a suspension.
    try:
        yield 1
    finally:
        raise Err(bump())


def handler_exit() -> Iterator[int]:
    # finally raises after the except handler completes normally.
    try:
        yield 1
        raise ValueError("v")
    except ValueError:
        print("caught")
    finally:
        raise Err(bump())


def return_exit() -> Iterator[int]:
    # return inside the try after a suspension; the finally raises.
    try:
        yield 1
        return
    finally:
        raise Err(bump())


def with_exit() -> Iterator[int]:
    # __exit__ raises on the with's fall-through after a suspension.
    with Thrower():
        yield 1


def nested_exit() -> Iterator[int]:
    # inner finally raises on the return path; outer finally must still run.
    try:
        try:
            yield 1
            return
        finally:
            print("inner fin")
            raise Err(bump())
    finally:
        print("outer fin")


def break_exit() -> Iterator[int]:
    # break out of a try in a loop after a suspension; the finally raises.
    for i in range(3):
        try:
            yield i
            if i == 1:
                break
        finally:
            if i == 1:
                raise Err(bump())


def continue_exit() -> Iterator[int]:
    # continue out of a try in a loop after a suspension; the finally raises.
    for i in range(2):
        try:
            yield i
            continue
        finally:
            raise Err(bump())


def run(tag: str, g: Iterator[int]) -> None:
    global _code
    _code = 0
    print(f"-- {tag} --")
    try:
        for v in g:
            print(v)
    except Err as e:
        print(f"caught code={e.code}")


def main() -> None:
    run("normal_exit", normal_exit())
    run("handler_exit", handler_exit())
    run("return_exit", return_exit())
    run("with_exit", with_exit())
    run("nested_exit", nested_exit())
    run("break_exit", break_exit())
    run("continue_exit", continue_exit())


main()
