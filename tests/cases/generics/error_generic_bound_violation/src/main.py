# Test that bound violations are caught
from typing import Sized

class LengthHolder[T: Sized]:
    item: T

    def __init__(self, item: T):
        self.item = item

def main() -> None:
    # int32 doesn't satisfy Sized (no __len__ method)
    holder = LengthHolder[int](42)  # tpyc: error(/does not satisfy bound/)
