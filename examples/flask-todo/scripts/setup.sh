#!/bin/bash
cd "$(dirname "$0")/.."
pip install -r requirements.txt
echo "Setup complete. Run: python run.py"
