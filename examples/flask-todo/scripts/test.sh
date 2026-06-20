#!/bin/bash
cd "$(dirname "$0")/.."
python -m pytest tests/ -v
