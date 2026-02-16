# @readonly: calling a cross-module function that doesn't receive any param is allowed.
from tpy import readonly
import helpers as h


@readonly
def ok() -> None:
    h.mutate()  # tpyc: ok


ok()
print(0)
