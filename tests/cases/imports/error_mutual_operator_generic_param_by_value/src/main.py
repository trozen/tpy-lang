# A cycle member's operator may not take a generic class that stores a peer's
# value type by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
