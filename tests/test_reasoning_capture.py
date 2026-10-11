import copy
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from openpatients2.client import APIClient, Completion
from openpatients2.data import read_jsonl
from synthetic_fixtures import transport


def test_reasoning_aliases_count_once_and_preserve_raw():
    c=Completion()
    c.capture_reasoning({"reasoning":"alpha ","reasoning_content":"alpha "})
    c.capture_reasoning({"reasoning":"beta"})
    assert c.reasoning_text=="alpha beta"
    assert c.reasoning_characters==10
    assert c.reasoning_fields["reasoning_content"]=="alpha "
    assert c.response()["reasoning_status"]=="returned_text"
    assert "reasoning_text" not in c.metrics()
    assert "reasoning_fields" not in c.metrics()


def test_missing_empty_opaque_and_disabled_reasoning_are_distinct():
    c=Completion()
    assert c.response()["reasoning_status"]=="not_returned"
    c.capture_reasoning({"reasoning":None})
    assert c.response()["reasoning_status"]=="empty_field"
    c.capture_reasoning({"reasoning_details":[{"type":"encrypted","data":"opaque"}]})
    assert c.response()["reasoning_status"]=="returned_nontext"
    assert c.reasoning_text is None
    disabled=Completion(reasoning_saved=False)
    disabled.capture_reasoning({"reasoning_content":"returned but opted out"})
    assert disabled.reasoning_text is None
    assert disabled.reasoning_fields=={}
    assert disabled.reasoning_characters>0
    assert disabled.response()["reasoning_status"]=="disabled_by_client"


