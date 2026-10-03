#!/usr/bin/env python3
"""Audit an opt-in timeline request offline; never calls a model or changes facts."""
import argparse
import json
from pathlib import Path

from openpatients2.longitudinal import PatientTimeline, audit_timeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.input.read_text())
    timeline = PatientTimeline.model_validate(request['timeline'])
    report = audit_timeline(timeline, request['sources'], request['fact_records'])
    with args.output.open('x') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'output': str(args.output), 'structural_source_gates_passed': report['structural_source_gates_passed'],
                      'clinical_timeline_verified': False}))
    return 0 if report['structural_source_gates_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
