from tpy import native_c, Int32

@native_c
def abs(x: Int32) -> Int32: ...

@native_c("tpy_srand")
def srand(seed: Int32) -> None: ...
