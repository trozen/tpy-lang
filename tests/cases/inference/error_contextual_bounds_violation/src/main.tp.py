# Contextual inference respects type parameter bounds.
from tpy import Int32, Sized

class C[T: Sized]:
    val: T

def main():
    x: C[Int32] = C()  # tpyc: error(/does not satisfy bound/)
