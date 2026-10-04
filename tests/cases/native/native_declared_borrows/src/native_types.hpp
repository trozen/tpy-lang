#pragma once
#include <cstdint>
#include <vector>

struct Node {
    int32_t v;
};

// Both return the FIRST argument by reference.
Node& pick_ref(Node& a, Node& b);
Node& pick_copy(Node& a, Node& b);

// The first element, by reference.
Node& first(std::vector<Node>& items);
