# random -- pseudo-random number generation (Mersenne Twister)
# tpy: cpp_namespace("tpystd::random")
# tpy: include("<tpy/random.hpp>")
from tpy.extern import native, cpp_template
from tpy import Int32

@native("tpy::random_random")
def random() -> float: ...

@native("tpy::random_seed")
def seed(n: Int32) -> None: ...

@native("tpy::random_randint")
def randint(a: Int32, b: Int32) -> Int32: ...

@native("tpy::random_uniform")
def uniform(a: float, b: float) -> float: ...

@native("tpy::random_gauss")
def gauss(mu: float, sigma: float) -> float: ...

@native("tpy::random_expovariate")
def expovariate(lambd: float) -> float: ...

@cpp_template("tpy::random_shuffle({0})")
def shuffle[T](lst: list[T]) -> None: ...

def choice[T](seq: list[T]) -> T:
    n = Int32(len(seq))
    if n == 0:
        raise ValueError("Cannot choose from an empty sequence")
    return seq[randint(0, n - 1)]
