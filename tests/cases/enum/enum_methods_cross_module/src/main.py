# Enum methods called from another module under every import spelling, and
# two modules' same-named enums each reaching their own methods.
import palette
import shades
import tones
from shades import Shade
from shades import Shade as S, pick


def main() -> None:
    a = Shade.parse("Light")  # tpyc: ok
    b = S.darkest()  # tpyc: ok
    c = shades.Shade.parse("Dark")  # tpyc: ok
    d = pick()
    print("plain:", a.flip(), a.weight())
    print("alias:", b.flip(), b.weight())
    print("qualified:", c.flip(), c.weight())
    print("member:", d.flip(), shades.pick().flip())  # tpyc: ok
    # a same-named enum from another module reaches its own methods
    print("collision:", tones.pick().flip(), d.flip())  # tpyc: ok
    # a static on the same-named enum, directly and through a re-export
    print("qualified static:", tones.Shade.loudest(), palette.Shade.loudest())  # tpyc: ok
    # a property on an imported enum
    print("property:", a.heavy, S.darkest().heavy)  # tpyc: ok
    # generic methods: instance, static through the import and the module
    print("generic:", a.tag("x"), Shade.ident(3), shades.Shade.ident(4))  # tpyc: ok


main()
