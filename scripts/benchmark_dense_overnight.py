#!/usr/bin/env python3
"""Lightweight entry point for the paired overnight campaign."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openpatients2.dense_overnight import main

if __name__ == '__main__':
    main()
