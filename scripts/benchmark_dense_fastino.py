#!/usr/bin/env python3
"""Bootstrap the campaign CLI without installing dependencies on a login node."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openpatients2.dense_fastino import main

if __name__ == '__main__':
    main()
