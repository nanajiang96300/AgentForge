#include <iostream>
#include <string>
#include "calculator.hpp"

// BUG #1: Argument parsing out-of-bounds access
// When called without arguments, argv[1] is accessed without checking argc
int main(int argc, char* argv[]) {
    if (argc != 4) {
        std::cerr << "Usage: calc <op> <a> <b>" << std::endl;
        return 1;
    }

    std::string op = argv[1];
    double a = std::stod(argv[2]);
    double b = std::stod(argv[3]);

    double result = 0;

    if (op == "add") {
        result = Calculator::add(a, b);
    } else if (op == "sub") {
        result = Calculator::subtract(a, b);
    } else if (op == "mul") {
        result = Calculator::multiply(a, b);
    } else if (op == "div") {
        result = Calculator::divide(a, b);
    } else {
        std::cerr << "Error: Unknown operation: " << op << std::endl;
        return 1;
    }

    std::cout << "Result: " << result << std::endl;
    return 0;
}