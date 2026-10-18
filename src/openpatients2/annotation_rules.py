"""Render the shared annotation rules (rules/annotation_rules.yaml) into every prompt and document.

    PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --check   # exit 1 on drift
    PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write   # regenerate blocks

Runtime prompt files (prompts/<task>.md) end with a generated SHARED RULES block. Code-built
prompts (roster/summary/timeline in article_tasks, figure visual/EHR/privacy instructions) call
`prompt_block()` at import, so they cannot drift. Reference documents (corpus protocol and
Claude briefs) contain a generated block between HTML comment markers. Edit the YAML, never a block.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

import yaml

from . import semantic_rules

RULES_FILE = 'rules/annotation_rules.yaml'
PROMPT_FILES = ('system', 'case_context', 'demographics', 'conditions', 'symptoms_function', 'medications',
                'allergies', 'procedures_devices', 'observations', 'oncology', 'family_genetics',
                'reproductive_perinatal', 'social_exposures', 'outcomes', 'care_plans')
CODE_PROMPTS = ('roster', 'summary', 'timeline', 'figure_visual', 'figure_ehr_label', 'figure_privacy_flags')
PROMPT_START = 'SHARED RULES (generated from openpatients2/rules/annotation_rules.yaml; rule IDs in brackets):'
PROMPT_END = 'END SHARED RULES'
DOC_START = ('<!-- GENERATED rules:start — edit src/openpatients2/rules/annotation_rules.yaml, then run '
             '`PYTHONPATH=src .venv/bin/python -m openpatients2.annotation_rules --write` -->')
DOC_END = '<!-- GENERATED rules:end -->'
REPO = Path(__file__).resolve().parents[2]
# Reference documents: path relative to the repo root -> (style, audience). 'full' renders the rule
# text of rules whose `docs` list the audience; 'checklist' lists every rule ID and title.
DOCS = {
    'benchmarks/dense/ANNOTATION_PROTOCOL.md': ('full', 'protocol'),
    'benchmarks/dense/claude_prompts/ANNOTATOR_BRIEF.md': ('checklist', None),
    'benchmarks/dense/claude_prompts/PARENT_REVIEWER_BRIEF.md': ('checklist', None),
    'benchmarks/dense/claude_prompts/RESIGN_BRIEF.md': ('checklist', None),
    'benchmarks/dense/claude_prompts/BACKFILL_BRIEF.md': ('full', 'backfill'),
}


@lru_cache(maxsize=None)
def load_rules():
    data = yaml.safe_load(files('openpatients2').joinpath(RULES_FILE).read_text(encoding='utf-8'))
    if data.get('format') != 'openpatients2-annotation-rules/1':
        raise ValueError('Unknown annotation rules format')
    rules = data['rules']
    ids = [r['id'] for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate rule id')
    values = {'EVENT_OCCURRENCES': ', '.join(semantic_rules.EVENT_OCCURRENCES),
              'TEMPORAL_RELATIONS': ', '.join(semantic_rules.TEMPORAL_RELATIONS)}
    out = []
    for r in rules:
        unknown = set(r.get('prompts', [])) - set(PROMPT_FILES) - set(CODE_PROMPTS)
        if unknown:
            raise ValueError(f'{r["id"]}: unknown prompt targets {sorted(unknown)}')
        out.append({**r, 'text': ' '.join(r['text'].format(**values).split())})
    return tuple(out)


def rules_for(prompt):
    return [r for r in load_rules() if prompt in r.get('prompts', [])]


def prompt_block(prompt):
    """Text block appended to one runtime prompt ('' when no rule targets it)."""
    rules = rules_for(prompt)
    if not rules:
        return ''
    return '\n'.join([PROMPT_START] + [f'- [{r["id"]}] {r["text"]}' for r in rules] + [PROMPT_END])


def doc_block(style, audience):
    rules = load_rules()
    if style == 'checklist':
        lines = ['Shared annotation rules (full text: `src/openpatients2/rules/annotation_rules.yaml`, rendered in '
                 '`benchmarks/dense/ANNOTATION_PROTOCOL.md` "Shared annotation rules"). Check every item before '
                 'signing; `validator` rules are also enforced by the runtime gate command.', '',
                 '| Rule | What | Checked by validator |', '| --- | --- | --- |']
        lines += [f'| `{r["id"]}` | {r["title"]} | {"yes" if r.get("validator") else "review"} |' for r in rules]
    else:
        lines = ['Rules shared with the runtime prompts (`src/openpatients2/rules/annotation_rules.yaml`). Rule IDs '
                 'appear in validator messages as `[ID]`.', '']
        lines += [f'- **{r["id"]}** ({r["title"]}{"; validator" if r.get("validator") else ""}): {r["text"]}'
                  for r in rules if audience in r.get('docs', [])]
    return '\n'.join([DOC_START] + lines + [DOC_END])


def _replace(text, start, end, block, append_sep):
    if start in text:
        if end not in text.split(start, 1)[1]:
            raise ValueError('Generated block start without end marker')
        before, rest = text.split(start, 1)
        after = rest.split(end, 1)[1]
        return before + block + after
    if not block:
        return text
    return text.rstrip('\n') + append_sep + block + '\n'


def prompt_path(name):
    return Path(str(files('openpatients2').joinpath('prompts', f'{name}.md')))


def render_prompt_file(name, text):
    return _replace(text, PROMPT_START, PROMPT_END, prompt_block(name), '\n\n')


def render_doc(text, style, audience):
    return _replace(text, DOC_START, DOC_END, doc_block(style, audience), '\n\n')


def targets(repo=REPO):
    """[(path, current_text, rendered_text)] for every generated target that exists."""
    out = []
    for name in PROMPT_FILES:
        path = prompt_path(name)
        text = path.read_text(encoding='utf-8')
        out.append((path, text, render_prompt_file(name, text)))
    for relative, (style, audience) in DOCS.items():
        path = Path(repo) / relative
        if path.exists():
            text = path.read_text(encoding='utf-8')
            out.append((path, text, render_doc(text, style, audience)))
    return out


def drift(repo=REPO):
    """Paths whose generated block differs from the rules file (or lacks it)."""
    return [str(path) for path, text, rendered in targets(repo) if text != rendered]


def write(repo=REPO):
    changed = []
    for path, text, rendered in targets(repo):
        if text != rendered:
            path.write_text(rendered, encoding='utf-8'); changed.append(str(path))
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true'); group.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    if args.write:
        for path in write():
            print('updated', path)
        return 0
    stale = drift()
    for path in stale:
        print('drift', path)
    return 1 if stale else 0


if __name__ == '__main__':
    raise SystemExit(main())
