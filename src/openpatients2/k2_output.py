"""Recover explicit K2 answer boundaries misclassified by a serving parser."""
from dataclasses import replace
import json
import re

from .client import Completion

_END = re.compile(r'</ifm\|think(?:_fast|_faster)?>')


def recover_answer(response: Completion, model_id: str):
    """Never infer an answer from thoughts; require a single explicit closing tag.

    The endpoint response must be saved before calling this function. The returned
    object is a copy, and all clinical validation still applies to its answer.
    """
    if (not model_id.startswith('IFM/K2-Horizon-') or response.error
            or response.content.strip() or response.finish_reason not in {'stop', 'eos'}):
        return response, None
    text = response.reasoning_text or ''
    boundaries = list(_END.finditer(text))
    if len(boundaries) != 1:
        return response, None
    boundary = boundaries[0]
    answer = text[boundary.end():].strip()
    # Reject incomplete objects, prose, multiple objects and reasoning-only output.
    try:
        candidate = json.loads(answer)
    except (ValueError, TypeError):
        return response, None
    if not isinstance(candidate, dict):
        return response, None
    reasoning = text[:boundary.start()]
    recovered = replace(response, content=answer, reasoning_text=reasoning,
                        reasoning_characters=len(reasoning), reasoning_tokens=None)
    audit = {'method': 'explicit_k2_closing_tag', 'closing_tag': boundary.group(),
             'answer_characters': len(answer), 'clinical_validation_required': True,
             'reasoning_token_breakdown': 'unavailable_after_server_field_misclassification'}
    return recovered, audit
