from tpy import Int32


def is_missing(x: Int32 | None) -> bool:
    return x == None  # tpyc: error(/Use 'is None' \/ 'is not None'/)
