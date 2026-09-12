# Contextual inference respects type parameter bounds.
from tpy import int32
from typing import Sized

class C[T: Sized]:
    val: T

def main():
    x: C[int32] = C()  # tpyc: error(/does not satisfy bound/)
