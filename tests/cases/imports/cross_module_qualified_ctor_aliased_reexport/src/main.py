# Qualified construction still resolves to the right module through an aliased
# import (`import pa as p`) and a re-exporting module (`pa_re` re-exports Box
# from pc_def), even with a same-named pb.Box bare-imported into scope.
import pa as p
import pa_re
from pb import Box

def main() -> None:
    print(p.Box(10).n)       # aliased qualified -> pa.Box (int field)
    print(pa_re.Box(5).n)    # re-exported -> pc_def.Box (int field), not pb.Box
    print(Box("hi").msg)     # bare -> pb.Box (str field)

main()
