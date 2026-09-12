# Sema warns on mixed-sign fixed-int comparisons; the runtime answer still
# matches user intent (the warning is informational about the type mix).
from tpy import int32, uint32, int64, uint64


def same_rank(a: int32, b: uint32) -> bool:
    return a < b  # tpyc: warning(/comparison between signed and unsigned.*int32.*uint32.*cast one operand/)


def diff_rank_signed_smaller(a: int32, b: uint64) -> bool:
    # Negative int32 reinterpreted as uint64 would be a huge positive value,
    # so the codegen handles it; the warning surfaces the type mismatch.
    return a < b  # tpyc: warning(/comparison between signed and unsigned.*int32.*uint64.*cast one operand/)


def diff_rank_unsigned_smaller(a: int64, b: uint32) -> bool:
    # uint32 widens losslessly to int64 in C++ (so the warning is informational
    # here, not a correctness signal) -- TPy warns regardless for consistency.
    return a < b  # tpyc: warning(/comparison between signed and unsigned.*int64.*uint32.*cast one operand/)


def equality_too(a: int32, b: uint32) -> bool:
    return a == b  # tpyc: warning(/comparison between signed and unsigned.*int32.*uint32.*cast one operand/)


# Negative case: same-sign comparisons must NOT warn.
def same_sign_signed(a: int32, b: int32) -> bool:
    return a < b  # tpyc: ok


def same_sign_unsigned(a: uint32, b: uint32) -> bool:
    return a < b  # tpyc: ok


# Negative case: literal-seeded local that retro-widens to match the
# unsigned target must NOT warn at the comparison site. The arg slot
# `take_u64(offset)` locks the seed to uint64 so by the end of body
# analysis offset is uint64 and `offset < limit` is same-sign.
def take_u64(x: uint64) -> uint64:
    return x


def literal_seed(limit: uint64) -> uint64:
    offset = 0
    while offset < limit:  # tpyc: ok
        offset = take_u64(offset) + 1
    return offset


def main() -> None:
    print(same_rank(-1, uint32(1)))
    print(diff_rank_signed_smaller(int32(0), uint64(1)))
    print(diff_rank_unsigned_smaller(int64(0), uint32(1)))
    print(equality_too(int32(0), uint32(0)))
    print(same_sign_signed(1, 2))
    print(same_sign_unsigned(uint32(1), uint32(2)))
    print(literal_seed(uint64(3)))


main()
