import pytest
from openpatients2.output_parser import parse_output, OutputParseError

@pytest.mark.parametrize('text,method',[
 ('{"v":null,"items":[1,2]}','json'),
 ('Here is the result:\n```json\n{"v":null,"items":[1,2]}\n```','embedded_json'),
 ("{'v': None, 'items': [1, 2]}",'literal_object'),
 ('v: null\nitems:\n  - 1\n  - 2','yaml'),
 ('```yaml\nv: null\nitems: [1, 2]\n```','yaml'),
 ('{"text":"a } brace and a \\"quote\\""}','json'),
])
def test_common_response_formats(text,method):
    assert parse_output(text).method==method

@pytest.mark.parametrize('text',[
 '{"value":1,"value":2}',
 '{"value":1}\nAlternatively {"value":2}',
 '{"value":NaN}',
 '{"value":',
 "{'value': __import__('os').system('false')}",
 "{'value':1,'value':2}",
 'The patient seems well. No structure was provided.',
 'x: &a [1, 2]\ny: *a',
])
def test_no_unsafe_or_ambiguous_repairs(text):
    with pytest.raises(OutputParseError):parse_output(text)

def test_truncation_not_accepted_even_with_valid_prefix():
    with pytest.raises(OutputParseError):parse_output('{"ok":true}',finish_reason='length')
