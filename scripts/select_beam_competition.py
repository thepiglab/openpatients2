#!/usr/bin/env python3
"""Freeze or verify the training-only 50-article Beam/FHIR competition subset."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openpatients2.competition_selection import main

if __name__ == '__main__':
    main()
