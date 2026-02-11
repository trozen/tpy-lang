from tpy import Int32, Bool


def is_missing(x: Int32 | None) -> Bool:
    return x == None  # tpyc: error(/Use 'is None' \/ 'is not None'/)
