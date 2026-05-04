# Sema warns on mixed-sign fixed-int comparisons; the runtime answer still
# matches user intent (the warning is informational about the type mix).
from tpy import Int32, UInt32, Int64, UInt64


def same_rank(a: Int32, b: UInt32) -> bool:
    return a < b  # tpyc: warning(/comparison between signed and unsigned.*Int32.*UInt32.*cast one operand/)


def diff_rank_signed_smaller(a: Int32, b: UInt64) -> bool:
    # Negative Int32 reinterpreted as UInt64 would be a huge positive value,
    # so the codegen handles it; the warning surfaces the type mismatch.
    return a < b  # tpyc: warning(/comparison between signed and unsigned.*Int32.*UInt64.*cast one operand/)


def diff_rank_unsigned_smaller(a: Int64, b: UInt32) -> bool:
    # UInt32 widens losslessly to Int64 in C++ (so the warning is informational
    # here, not a correctness signal) -- TPy warns regardless for consistency.
    return a < b  # tpyc: warning(/comparison between signed and unsigned.*Int64.*UInt32.*cast one operand/)


def equality_too(a: Int32, b: UInt32) -> bool:
    return a == b  # tpyc: warning(/comparison between signed and unsigned.*Int32.*UInt32.*cast one operand/)


# Negative case: same-sign comparisons must NOT warn.
def same_sign_signed(a: Int32, b: Int32) -> bool:
    return a < b  # tpyc: ok


def same_sign_unsigned(a: UInt32, b: UInt32) -> bool:
    return a < b  # tpyc: ok


# Negative case: literal-seeded local that retro-widens to match the
# unsigned target must NOT warn at the comparison site. The arg slot
# `take_u64(offset)` locks the seed to UInt64 so by the end of body
# analysis offset is UInt64 and `offset < limit` is same-sign.
def take_u64(x: UInt64) -> UInt64:
    return x


def literal_seed(limit: UInt64) -> UInt64:
    offset = 0
    while offset < limit:  # tpyc: ok
        offset = take_u64(offset) + 1
    return offset


def main() -> None:
    print(same_rank(-1, UInt32(1)))
    print(diff_rank_signed_smaller(Int32(0), UInt64(1)))
    print(diff_rank_unsigned_smaller(Int64(0), UInt32(1)))
    print(equality_too(Int32(0), UInt32(0)))
    print(same_sign_signed(1, 2))
    print(same_sign_unsigned(UInt32(1), UInt32(2)))
    print(literal_seed(UInt64(3)))


main()
