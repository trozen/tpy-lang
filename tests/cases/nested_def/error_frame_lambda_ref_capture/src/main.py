# A frame member is named in an init-capture, so it needs one C++ spelling the
# entry and the body can both use. A frame LOCAL of reference type lives in a
# `tpy::frame_slot<T>` and reads `(*ys)`, which the body inside the closure
# would have to spell as the bare capture name -- no single entry gives both,
# so the position rejects. The borrowed-PARAM sibling is a bare member and
# compiles (see nested_def/lambda_in_gen_method).
from typing import Iterator
from tpy import Fn, int32


def apply(f: Fn[[int32], int32], v: int32) -> int32:
    return f(v)


def gen() -> Iterator[int32]:
    ys = [1, 2]
    yield apply(lambda i: ys[i], 0)  # tpyc: error(/lambda\.capture_frame_slot/)
    yield apply(lambda i: ys[i], 1)


def main() -> None:
    for v in gen():
        print(v)


main()
