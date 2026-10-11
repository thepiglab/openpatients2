import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
import yaml

from openpatients2.client import APIClient
from openpatients2.config import PipelineConfig
from openpatients2.data import read_jsonl
from synthetic_fixtures import transport


def test_grouping_metadata_changes_input_provenance_hash(row):
    from openpatients2.data import input_fingerprint
    changed={**row,"cluster_id":"reviewed-related-source"}
    assert input_fingerprint(row)!=input_fingerprint(changed)


