# A cycle member's `@error_return` operator may not return a peer's class under
# `| None`: `std::expected` holds it by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
