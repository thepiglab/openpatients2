"""Conservative parsing of unconstrained model responses, with an audit trail.

Never executes code, resolves duplicate keys silently, fills clinical fields or
repairs truncation. Output recovery is separate from schema/evidence validation.
"""
from __future__ import annotations
from dataclasses import dataclass
import ast
import json
import re
import yaml


class OutputParseError(ValueError):
    pass


@dataclass
class ParsedOutput:
    value: dict
    method: str
    transformations: list[str]


def _unique(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise OutputParseError('Duplicate object key: '+str(k))
        out[k]=v
    return out


def _strict_json(text):
    def bad(value): raise OutputParseError('Non-finite JSON constant: '+value)
    return json.loads(text,object_pairs_hook=_unique,parse_constant=bad)


def _balanced_objects(text):
    start=None; depth=0; quote=None; escaped=False
    for i,c in enumerate(text):
        if start is None:
            if c=='{': start=i; depth=1
            continue
        if quote:
            if escaped: escaped=False
            elif c=='\\': escaped=True
            elif c==quote: quote=None
        elif c in {'"',"'"}: quote=c
        elif c=='{': depth+=1
        elif c=='}':
            depth-=1
            if depth==0:
                yield text[start:i+1]; start=None


def _python_literal(text):
    # Whitelisted literals only; reject calls, attributes, comprehensions, sets,
    # tuples and non-string keys. ast.literal_eval never executes code.
    node=ast.parse(text,mode='eval')
    allowed=(ast.Expression,ast.Dict,ast.List,ast.Constant,ast.UnaryOp,ast.USub,ast.UAdd,ast.Load)
    for n in ast.walk(node):
        if not isinstance(n,allowed): raise OutputParseError('Not a simple object literal')
        if isinstance(n,ast.Dict):
            keys=[]
            for k in n.keys:
                if not isinstance(k,ast.Constant) or not isinstance(k.value,str):
                    raise OutputParseError('Object keys must be strings')
                keys.append(k.value)
            if len(keys)!=len(set(keys)): raise OutputParseError('Duplicate literal key')
    value=ast.literal_eval(node)
    # JSON roundtrip rejects unsupported Python values and nonfinite floats.
    return json.loads(json.dumps(value,allow_nan=False))


def _yaml_object(text):
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader,node):
        pairs=[]
        for k,v in node.value:
            key=loader.construct_object(k,deep=True)
            if not isinstance(key,str): raise OutputParseError('YAML object keys must be strings')
            pairs.append((key,loader.construct_object(v,deep=True)))
        return _unique(pairs)
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
    if any(isinstance(t,(yaml.tokens.AnchorToken,yaml.tokens.AliasToken,yaml.tokens.TagToken)) for t in yaml.scan(text)):
        raise OutputParseError('YAML aliases, tags and anchors are unsupported')
    value=yaml.load(text,Loader=UniqueLoader)
    return json.loads(json.dumps(value,allow_nan=False))


def parse_output(text: str, *, finish_reason: str | None = 'stop', max_chars: int = 2_000_000) -> ParsedOutput:
    if finish_reason not in {'stop','eos'}:
        raise OutputParseError('Incomplete/refused generation; truncation is not repairable data')
    if not isinstance(text,str) or len(text)>max_chars:
        raise OutputParseError('Output missing or exceeds parser bound')
    text=text.strip().lstrip('\ufeff')
    try:
        value=_strict_json(text)
        if isinstance(value,dict): return ParsedOutput(value,'json',[])
        raise OutputParseError('Expected an object, not a scalar or array')
    except json.JSONDecodeError:
        pass
    # All complete candidate objects are considered. Two different answers are
    # ambiguous even if only one happens to satisfy the downstream schema.
    candidates=list(dict.fromkeys(_balanced_objects(text)))
    parsed=[]
    for candidate in candidates:
        try:
            value=_strict_json(candidate); method='embedded_json'; changes=['removed prose or code fence']
        except json.JSONDecodeError:
            try:
                value=_python_literal(candidate); method='literal_object'; changes=['removed prose or code fence','normalized literal syntax']
            except (ValueError,SyntaxError,TypeError,RecursionError):
                continue
        if isinstance(value,dict): parsed.append(ParsedOutput(value,method,changes))
    unique={json.dumps(p.value,sort_keys=True,ensure_ascii=False):p for p in parsed}
    if len(unique)==1: return next(iter(unique.values()))
    if len(unique)>1: raise OutputParseError('Multiple conflicting output objects; request a single answer')
    # YAML is accepted only as a complete document or one explicit YAML fence,
    # never as a repair of broken JSON or a guess at a prose clinical report.
    fences=re.findall(r'```ya?ml\s*\n(.*?)\n```',text,re.S|re.I)
    yaml_text=fences[0] if len(fences)==1 else text
    if not candidates and len(fences)<=1 and not yaml_text.lstrip().startswith(('{','[')):
        try:
            value=_yaml_object(yaml_text)
            if isinstance(value,dict):return ParsedOutput(value,'yaml',['normalized YAML syntax'])
        except (ValueError,TypeError,yaml.YAMLError,RecursionError):
            pass
    raise OutputParseError('No complete parseable object; preserve raw response and request explicit structure')
