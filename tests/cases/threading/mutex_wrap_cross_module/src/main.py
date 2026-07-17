# Instantiate an Arc[Mutex[..]] channel-shaped type from a separate module and
# call a method that locks it. Regression guard for the builtin-index ordering
# bug (see achan.py) -- fails to compile ("Cannot access field 'storage'")
# before the post-finalize builtin re-index fix.
from tpy import Int32
from achan import make_producer


def main() -> None:
    p = make_producer[Int32]()
    print(p.capacity())


main()
