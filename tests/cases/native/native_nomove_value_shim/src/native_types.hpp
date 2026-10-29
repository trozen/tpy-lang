#pragma once

// Copyable but not movable: a shim must copy its by-value parameter.
struct PinnedValue {
    PinnedValue() = default;
    PinnedValue(const PinnedValue&) = default;
    PinnedValue(PinnedValue&&) = delete;
};
