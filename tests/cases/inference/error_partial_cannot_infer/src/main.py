# Partial explicit type args: remaining params cannot be inferred.
from tpy import Int32, Own

class Wrapper[A, B]:
    inner: A
    tag: B

def make_wrapper[A, B]() -> Own[Wrapper[A, B]]:
    return Wrapper[A, B]()

def main() -> None:
    # A=Int32 explicit, but B cannot be inferred (no args, no context)
    w = make_wrapper[Int32]()  # tpyc: error(/Cannot infer remaining type arguments/)

main()
