# Two same-name Exception subclasses from different modules coexist: the
# qualified `ea.Err` must not resolve to the bare-imported `eb.Err`.
import ea
from eb import Err

def main() -> None:
    a = ea.Err(7)           # qualified, int code field
    print(a.code)           # 7
    try:
        raise ea.Err(9)
    except ea.Err as e:
        print(e.code)       # 9
    b = Err("oops")         # bare = eb.Err, str msg field
    print(b.msg)            # oops

main()
