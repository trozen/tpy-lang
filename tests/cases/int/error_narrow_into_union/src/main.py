# A wider integer does not narrow into a union's fixed-width member (an
# Optional payload does): docs/LANGUAGE_FEATURES.md "Implicit narrowing".
from tpy import int8, int32


def f(v: int32) -> None:
    u: int8 | str = v  # tpyc: error(/expected int8 \| str, got int32/)
    print(u)


f(5)
