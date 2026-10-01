import pytest

from openpatients2.client import Completion
from openpatients2.k2_output import recover_answer


@pytest.mark.parametrize('tag', ['</ifm|think>', '</ifm|think_fast>', '</ifm|think_faster>'])
def test_explicit_answer_recovery_preserves_original_and_excludes_thoughts(tag):
    original = Completion(finish_reason='stop', reasoning_text='Consider {"incorrect": true}\n' + tag + '\n{"items": []}',
                          reasoning_tokens=123, completion_tokens=200)
    recovered, audit = recover_answer(original, 'IFM/K2-Horizon-375B-A23B-NVFP4')
    assert recovered.content == '{"items": []}'
    assert recovered.reasoning_text == 'Consider {"incorrect": true}\n'
    assert recovered.reasoning_tokens is None and recovered.completion_tokens == 200
    assert original.content == '' and original.reasoning_tokens == 123
    assert audit['closing_tag'] == tag


@pytest.mark.parametrize('text,finish,content,model', [
    ('{"items": []}', 'stop', '', 'IFM/K2-Horizon-7B-FP8'),
    ('</ifm|think>{"items": []}', 'length', '', 'IFM/K2-Horizon-7B-FP8'),
    ('</ifm|think>{"items":', 'stop', '', 'IFM/K2-Horizon-7B-FP8'),
    ('</ifm|think>{}</ifm|think>{}', 'stop', '', 'IFM/K2-Horizon-7B-FP8'),
    ('</ifm|think>{}', 'stop', '{"correct": true}', 'IFM/K2-Horizon-7B-FP8'),
    ('</ifm|think>{}', 'stop', '', 'other/model'),
])
def test_no_recovery_for_missing_ambiguous_truncated_or_unrelated_boundaries(text, finish, content, model):
    original = Completion(finish_reason=finish, content=content, reasoning_text=text)
    result, audit = recover_answer(original, model)
    assert result is original and audit is None
