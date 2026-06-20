CXX := g++
CXXFLAGS := -std=c++17 -Wall -Wextra
BUILD_DIR := build
SRC_DIR := src
TEST_DIR := tests

.PHONY: all clean test

all: $(BUILD_DIR)/calc

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/calc: $(BUILD_DIR) $(SRC_DIR)/main.cpp $(SRC_DIR)/calculator.cpp $(SRC_DIR)/calculator.hpp
	$(CXX) $(CXXFLAGS) -o $@ $(SRC_DIR)/main.cpp $(SRC_DIR)/calculator.cpp

$(BUILD_DIR)/test_main: $(BUILD_DIR) $(TEST_DIR)/test_main.cpp $(SRC_DIR)/calculator.cpp $(SRC_DIR)/calculator.hpp
	$(CXX) $(CXXFLAGS) -o $@ $(TEST_DIR)/test_main.cpp $(SRC_DIR)/calculator.cpp

$(BUILD_DIR)/test_edge: $(BUILD_DIR) $(TEST_DIR)/test_edge.cpp $(SRC_DIR)/calculator.cpp $(SRC_DIR)/calculator.hpp
	$(CXX) $(CXXFLAGS) -o $@ $(TEST_DIR)/test_edge.cpp $(SRC_DIR)/calculator.cpp

test: $(BUILD_DIR)/calc $(BUILD_DIR)/test_main $(BUILD_DIR)/test_edge
	bash scripts/run_tests.sh

clean:
	rm -rf $(BUILD_DIR)
