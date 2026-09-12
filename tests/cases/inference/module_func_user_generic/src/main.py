# Explicit type args on user-module generic function via module.func[T](args).
from tpy import int32
import helpers

def main() -> None:
    # module.func[T](args) syntax on user-defined generic
    y = helpers.identity[int32](int32(7))
    print(y)
    print("done")

main()
