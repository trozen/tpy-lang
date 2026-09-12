# Cycle members participate in concrete inheritance: A inherits from
# B which is in the same SCC. Concrete inheritance requires the
# parent's complete type, which the cycle's forward-decl headers
# cannot provide.
from a import A
from tpy import int32

def main() -> int32:
    return 0

main()
