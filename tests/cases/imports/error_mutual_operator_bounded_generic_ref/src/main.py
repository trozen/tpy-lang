# A cycle member's operator may not name a generic class over a peer's class,
# even by reference: a bounded type parameter checks the argument (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
