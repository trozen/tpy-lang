# @nosend / @nosync force the trait False unconditionally -- they take no
# arguments. The conditional if_params_* form belongs only to @unsafe_send /
# @unsafe_sync.
from tpy import int32, nosend

@nosend(if_params_send=True)  # tpyc: error(/@nosend does not take arguments/)
class A:
    data: int32

    def __init__(self) -> None:
        self.data = 0
