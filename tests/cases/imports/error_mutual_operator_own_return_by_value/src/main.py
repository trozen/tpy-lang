# A cycle member's operator may not return `Own[B | None]` of a peer's class:
# the owning result holds the class by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
