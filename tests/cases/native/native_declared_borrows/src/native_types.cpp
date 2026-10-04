#include "native_types.hpp"

Node& pick_ref(Node& a, Node&) {
    return a;
}

Node& pick_copy(Node& a, Node&) {
    return a;
}

Node& first(std::vector<Node>& items) {
    return items[0];
}
