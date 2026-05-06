# Cycle binding for an enum imported across the SCC. b defines
# Color, a imports it and uses it in a function signature; b imports
# from a in turn. Skeleton pre-registration mints a `NominalType`
# for the enum at pre-populate time and attaches the matching qname
# to TypeDef so peer type resolution sees the same identity.
from a import lookup
from b import describe, describe_default

def main() -> None:
    print(describe(lookup()))
    print(describe_default())

main()
