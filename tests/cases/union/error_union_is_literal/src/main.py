# 'is' with non-None literal on union should error
from tpy import Int32

def check_int(v: Int32 | str | None) -> bool:
    return v is 0  # tpyc: error(/'is' \/ 'is not' can only compare/)
