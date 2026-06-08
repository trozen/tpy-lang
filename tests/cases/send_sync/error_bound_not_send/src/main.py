# Explicit type argument violating a T: Send bound (Rc is non-Send).
from tpy import Int32, Send
from tplib.rc import Rc

def use[T: Send](x: T) -> None:
    print("ok")

def main() -> None:
    rc = Rc.new(7)
    use[Rc[Int32]](rc)  # tpyc: error(/does not satisfy bound 'Send'/)

main()
