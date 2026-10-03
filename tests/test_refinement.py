import copy
import json
import gzip
from pathlib import Path

import pytest

from openpatients2.clinical_normalization import normalize_clinical
from openpatients2.evidence_recovery import resolve_timeline_spans
from openpatients2.longitudinal import relative_order, partial_timeline, canonical_timeline_ids
from openpatients2.pilot_extract import sources_for, timeline_messages
from openpatients2.targeted_repair import repair_snapshot, erased, ItemRepair, changed_accepted_atoms
from test_pilot_correctness import quote_only_timeline
from test_pilot_extract import article


def test_unbound_time_quarantine_preserves_measurement_and_audit():
    quote = 'Drainage fluid creatinine was 0.27 mg/dL.'
    original = {'items':[{'numeric_value':.27, 'unit':'mg/dL', 'specimen':'drainage fluid',
        'evidence':[{'quote':quote, 'source_section':'b1'}],
        'time':{'text':'at seven months', 'relation':'at', 'anchor':None, 'date_iso':None}}]}
    result, audit = normalize_clinical('observations', original, [{'segment_id':'b1','text':quote}])
    assert result['items'][0]['time']['text'] is None and original['items'][0]['time']['text']
    assert result['items'][0]['numeric_value'] == .27 and result['items'][0]['specimen'] == 'drainage fluid'
    assert audit[0]['before']['text'] == 'at seven months' and audit[0]['status'] == 'quarantined_association'
    original['items'][0]['evidence'][0]['quote'] = 'invented source'
    assert normalize_clinical('observations', original, [{'segment_id':'b1','text':quote}])[0] == original


def test_route_is_copied_only_from_explicit_bound_medication_phrase():
    quote = 'Treatment was IV flucloxacillin.'
    value = {'items':[{'name':'IV flucloxacillin','route':None,'evidence':[{'quote':quote,'source_section':'b1'}]}]}
    result, audit = normalize_clinical('medications',value,[{'segment_id':'b1','text':quote}])
    assert result['items'][0]['route'] == 'IV' and result['items'][0]['name'] == 'IV flucloxacillin'
    assert audit and value['items'][0]['route'] is None


def bound_graph():
    source = article(); raw = quote_only_timeline(source)
    kinds = ['history','presentation','test','treatment','adverse_event','treatment','follow_up','other']
    raw['events'] = [{**copy.deepcopy(raw['events'][0]), 'event_id':f'e{i+1}', 'kind':kind}
                     for i,kind in enumerate(kinds)]
    span = raw['events'][0]['evidence'][0]
    raw['edges'] = [{'edge_id':f'edge{i}', 'from_event_id':f'e{i}', 'to_event_id':f'e{i+1}',
        'relation':'before','offset':None,'evidence':[span]} for i in range(1,7)]
    graph, _ = resolve_timeline_spans(raw, 'PMC1.1:jats', source['segments'])
    return source, graph


def test_relative_clinical_sequence_needs_no_dates_and_keeps_disconnected_event_unknown():
    _, graph = bound_graph(); result = relative_order(graph)
    assert ['e1','e7'] in result['before_pairs'] and len(result['before_pairs']) == 21
    assert result['order_unknown_event_ids'] == ['e8']
    assert ['e1','e8'] in result['incomparable_pairs']
    assert result['topological_layers'][0] == [['e1'],['e8']]
    assert result['layers_are_simultaneous'] is False and not result['synthetic_dates_generated']
    assert not any(e['times'] for e in result['events'])
    graph['events'].reverse()
    assert set(map(tuple,relative_order(graph)['before_pairs'])) == set(map(tuple,result['before_pairs']))
    prompt = timeline_messages(article(), {}, 'PMC1.1:p1', {}, refined=True)[-1]['content']
    assert 'relative clinical sequence' in prompt and 'offset=null' in prompt


def test_only_explicit_same_time_edges_make_simultaneous_groups_and_cycles_are_rejected():
    _, graph = bound_graph(); graph['edges'] = [dict(graph['edges'][0], relation='same_time')]
    result = relative_order(graph)
    assert result['same_time_groups'] == [['e1','e2']] and not result['before_pairs']
    graph['edges'].append(dict(graph['edges'][0],edge_id='bad',relation='before'))
    with pytest.raises(ValueError,match='contradictory'): relative_order(graph)


def test_partial_graph_retains_events_and_quarantines_conflicting_edges_or_foreign_patient():
    source, graph = bound_graph(); _, sources = sources_for(source)
    graph['edges'].append({**graph['edges'][0],'edge_id':'reverse','from_event_id':'e7','to_event_id':'e1'})
    graph['events'][-1]['record_id'] = 'PMC1.1:p2'
    partial, quarantined = partial_timeline(graph,sources,{},'PMC1.1:p1')
    assert len(partial['events']) == 7 and partial['edges'] == []
    assert any(q['reason'] == 'combined_graph_conflict' for q in quarantined)
    assert any(q['kind'] == 'event' for q in quarantined)
    canonical, mapping = canonical_timeline_ids(partial)
    assert len(mapping) == 7 and canonical['events'][0]['event_id'] == 'e1'


def test_valid_graph_atoms_are_protected_without_freezing_invalid_edges_or_metadata():
    source, graph = bound_graph(); _, sources = sources_for(source)
    from openpatients2.longitudinal import PatientTimeline, audit_timeline
    def checker(candidate):
        parsed = PatientTimeline.model_validate(candidate)
        if not audit_timeline(parsed,sources,{})['structural_source_gates_passed']: raise ValueError('bad graph')
    graph['edges'][0]['to_event_id'] = 'nonexistent'
    graph['limitations'] = ['untrusted metadata']
    protected = repair_snapshot('timeline_v2',graph,checker)
    assert len(protected['events']) == 8 and len(protected['edges']) == 5
    fixed = copy.deepcopy(graph); fixed['edges'].pop(0); fixed['limitations'] = []
    assert erased(protected, fixed) == []
    fixed['events'][0]['description'] = 'A changed clinical claim'
    assert changed_accepted_atoms('timeline_v2',protected,fixed) == ['/events/0']
    fixed['events'].pop()
    assert erased(protected, fixed)


def test_targeted_batches_rotate_without_mutating_accepted_neighbors():
    plan = ItemRepair.__new__(ItemRepair)
    plan.pending = [{'index':i,'item':{'name':str(i)},'errors':['bad']} for i in range(12)]
    plan.policy = 'source_aware'; plan.visits = {}; plan._active = None
    plan.instruction(); first = [v['index'] for v in plan._active]
    plan.instruction(); second = [v['index'] for v in plan._active]
    assert first == list(range(8)) and second[:4] == [8,9,10,11]
    plan.policy = 'legacy'; plan.instruction()
    assert [v['index'] for v in plan._active] == first


def test_new_jats_markup_preserves_scientific_exponents_without_rewriting_frozen_fixture():
    from test_articles import XML
    from openpatients2.articles import parse_article
    xml = XML.replace('120/80 mmHg','40.6×10<sup>9</sup>/L; H<sub>2</sub>O')
    value = parse_article(xml, {'pmcid':'PMC1','version':1,'license_code':'CC BY-NC-SA',
        'is_pmc_openaccess':True,'is_retracted':False})
    assert '40.6×10^(9)/L; H_(2)O' in value['text'] and value['parser_version'] == '1.4'
    root = Path(__file__).resolve().parents[1]
    with gzip.open(root/'benchmarks/corpus-correctness/articles.jsonl.gz','rt') as stream:
        sources = [json.loads(line) for line in stream]
    assert any('40.6×109/L' in s['text'] for s in sources)


def test_mathml_powers_survive_and_stripped_superscript_citations_stay_empty():
    from openpatients2.articles import _clean
    from openpatients2.pmc_media import parsed_xml
    text = _clean(parsed_xml('<p xmlns:m="http://www.w3.org/1998/Math/MathML">'
        'WBC 40.6×<m:msup><m:mn>10</m:mn><m:mn>9</m:mn></m:msup>/L.'
        '<sup><xref ref-type="bibr">3</xref></sup> Done.</p>'))
    assert text == 'WBC 40.6×10^(9)/L. Done.'


def test_caption_measurements_use_same_preserved_math_notation_as_body():
    from test_articles import XML
    from openpatients2.articles import parse_article
    value = parse_article(XML.replace('Case 1 radiograph.','WBC 40.6×10<sup>9</sup>/L.'),
        {'pmcid':'PMC1','version':1,'license_code':'CC BY-NC-SA','is_pmc_openaccess':True,'is_retracted':False})
    assert value['figures'][0]['caption'] == 'WBC 40.6×10^(9)/L.'
    assert 'WBC 40.6×10^(9)/L.' in value['text']


def test_refined_visuals_hold_ambiguous_labels_and_keep_independently_valid_panels():
    from test_figure_visuals import chart_figure
    from openpatients2.figure_visuals import validate_visuals, partial_visuals
    value = chart_figure(); bad = copy.deepcopy(value['panels'][0]); bad['panel'] = 'b'
    bad['detailed_description'] = 'B? or D? label is visible.'
    value['panels'].append(bad)
    assert validate_visuals(value,{'segments':[]},'F1')
    with pytest.raises(ValueError,match='Ambiguous'): validate_visuals(value,{'segments':[]},'F1',refined=True)
    partial, audit = partial_visuals(value,{'segments':[]},'F1')
    assert [p['panel'] for p in partial['panels']] == ['a']
    assert any(q['kind'] == 'visual_panel' and q['candidate']['panel'] == 'b' for q in audit)
    value['panels'][1]['panel'] = 'a'
    assert partial_visuals(value,{'segments':[]},'F1')[0] is None


def test_cited_cases_are_source_bound_link_candidates_and_excluded_from_primary_roster():
    from test_articles import article as multi_article, roster
    from openpatients2.patient_context import check_patient_roster, discovery_messages
    value = multi_article(); predicted = roster(value)
    predicted['cited_cases'] = [{'label':'Prior published patient', 'species':'human',
        'identity_evidence':[{'segment_id':'b00002','quote':'A woman received 5 mg.'}],
        'source_segment_ids':['b00002'],'origin_reference_ids':[], 'attribution_limitations':['Prior case candidate']}]
    data = check_patient_roster(predicted,value)
    assert len(data['patients']) == 2 and len(data['cited_cases']) == 1
    predicted['cited_cases'][0]['origin_reference_ids'] = ['invented']
    with pytest.raises(ValueError,match='reference'): check_patient_roster(predicted,value)
    assert 'PRIMARY VERSUS CITED CASES' in discovery_messages(value,refined=True)[-1]['content']


def test_representation_alternatives_do_not_change_strict_scoring_or_use_quoted_evidence():
    from test_corpus_fidelity import fixture
    from openpatients2.corpus_fidelity import evaluate
    reference, patient = fixture()
    reference['checks'][0]['semantic_alternatives'] = [{'task':'conditions','collection':'items',
        'pattern':{'name':'sodium result 115'}}]
    patient['sections']['observations'] = {'items':[]}
    patient['sections']['conditions'] = {'items':[{'name':'sodium result 115'}]}
    score = evaluate(reference,[patient])
    assert score['summary']['delivered']['matched'] == 0
    assert score['representation_aware']['matched'] == 1
    patient['sections']['conditions']['items'] = [{'name':'different','evidence':[{'name':'sodium result 115'}]}]
    assert evaluate(reference,[patient])['representation_aware']['matched'] == 0


async def test_pixel_priority_reaches_late_figure_without_bypassing_rights(tmp_path):
    import httpx
    from test_pilot_media import article as media_article, source, PNG
    from openpatients2.pilot_media import prepare_media
    values = [media_article(1,5),media_article(2,1)]
    values[0]['figures'][4]['rights_statements'] = ['Third party copyright']
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,content=PNG))) as client:
        result = await prepare_media(source(tmp_path,values),tmp_path/'media',max_figures=2,http=client,
            priority_figures=[{'article_id':'PMC1.1','figure_id':'F4'}])
    assert result['figures'][0]['figure_id'] == 'F4' and result['figures'][0]['status'] == 'asset_rights_review'
    assert result['figures'][1]['figure_id'] == 'F0' and result['figures'][1]['status'] == 'ready'


async def test_refinement_trial_rotation_policy_and_live_conditioning(tmp_path,monkeypatch):
    from openpatients2 import corpus_pilot as pilot
    root = Path(__file__).resolve().parents[1]
    campaign = pilot.prepare(root,tmp_path/'run',root/'configs/pilot/refinement.yaml')
    work = Path(campaign['work']); (work/'profile').mkdir(); (work/'profile/rosters.json').write_text('{}')
    calls = []
    async def discover(config,*args,**kwargs):
        assert config['refinement_policy'] == 'source_aware'
        return {}
    async def warm(*args): return {'controlled_prefix_reset':True}
    async def run(config,sample,out,*args,**kwargs):
        assert not out.exists()  # Warmup files must not violate fresh-output gate.
        calls.append((kwargs['seed'],out.name,config['refinement_policy'],kwargs['frozen_rosters']))
        return {'tokens':{},'valid_tasks':1,'task_count':1}
    monkeypatch.setattr('openpatients2.pilot_extract.prepare_rosters',discover)
    monkeypatch.setattr('openpatients2.pilot_extract.run_pilot',run)
    monkeypatch.setattr(pilot,'trial_warmup',warm)
    rows, found = await pilot.correctness_runs(campaign,work/'extraction',['mock'],65536)
    assert len(rows) == len(calls) == 16 and len(found) == 4
    for variant in campaign['config']['variants']:
        trials = [r for r in rows if r['arm'] == variant['name']]
        assert {r['order'] for r in trials} == {0,1,2,3}
    assert all(frozen is None for _,name,_,frozen in calls if name == 'compact-live')
    assert all(frozen is not None for _,name,_,frozen in calls if name != 'compact-live')
    assert '--gres=none' in pilot.job_command(campaign,'cpu')
    assert '--gres=gpu:b200:8' in pilot.job_command(campaign,'gpu')


@pytest.mark.parametrize('bad',['owner','lock',None])
def test_holdout_cleanup_checks_both_owned_source_trees_before_deletion(tmp_path,bad):
    import fcntl
    from test_pilot_cleanup import campaign as cleanup_campaign, TARGETS
    from openpatients2.data import write_json
    from openpatients2.provenance import json_digest
    from openpatients2.pilot_cleanup import cleanup_sources, SCOPE
    campaign,work,_ = cleanup_campaign(tmp_path)
    campaign['config']['holdout_source_config'] = 'bounded-holdout.yaml'
    campaign['config_sha256'] = json_digest(campaign['config'])
    write_json(work/'source-owner.json',{'work':str(work),'config_sha256':campaign['config_sha256'],'scope':SCOPE})
    child = work/'holdout'; child.mkdir(); (child/'profile').mkdir(); (child/'acquisition').mkdir()
    for name in ('articles.jsonl.gz','profile/sample.jsonl.gz','acquisition/owner.lock'):
        (child/name).write_text('owned source')
    write_json(child/'source-owner.json',{'work':str(child),'config_sha256':campaign['config_sha256'],
        'scope':'forged' if bad == 'owner' else 'corpus-pilot-holdout/1'})
    (child/'profile/counts.jsonl.gz').write_text('review scalar counts')
    if bad == 'lock':
        with (child/'acquisition/owner.lock').open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with pytest.raises(ValueError,match='Active acquisition'): cleanup_sources(campaign)
    elif bad == 'owner':
        with pytest.raises(ValueError,match='Holdout source ownership'): cleanup_sources(campaign)
    else:
        result = cleanup_sources(campaign)
        assert 'holdout/articles.jsonl.gz' in result['deleted_paths']
        assert not (child/'profile/sample.jsonl.gz').exists()
        assert (child/'profile/counts.jsonl.gz').read_text() == 'review scalar counts'
    if bad:
        assert all((work/name).exists() for name in TARGETS)
        assert (child/'articles.jsonl.gz').exists()


def test_result_dedup_retains_every_attempt_and_nonidentical_patient_bundle(tmp_path):
    import tarfile
    from openpatients2 import corpus_pilot as pilot
    from openpatients2.data import write_json
    root = Path(__file__).resolve().parents[1]
    campaign = pilot.prepare(root,tmp_path/'run',root/'configs/pilot/refinement.yaml'); work = Path(campaign['work'])
    write_json(work/'gpu.json',{'status':'completed'})
    trial = work/'extraction/run'; (trial/'tasks').mkdir(parents=True); (trial/'patients').mkdir()
    attempt = {'ledger_sequence':1,'response':{'content':'all raw model output'}}
    write_json(trial/'tasks/task.json',{'attempts':[attempt]})
    (trial/'attempts.jsonl').write_text(json.dumps(attempt)+'\n')
    write_json(trial/'patients/one.json',{'source':'one'})
    write_json(trial/'patients/two.json',{'source':'different'})
    (trial/'patients.jsonl').write_text(json.dumps({'source':'one'})+'\n')
    output = tmp_path/'results.tar.gz'; pilot.archive_results(campaign,output)
    with tarfile.open(output) as tar:
        names = set(tar.getnames())
        assert 'extraction/run/attempts.jsonl' not in names
        assert 'extraction/run/patients/one.json' not in names
        assert 'extraction/run/patients/two.json' in names
        assert json.load(tar.extractfile('extraction/run/tasks/task.json'))['attempts'] == [attempt]
    (trial/'attempts.jsonl').write_text(json.dumps({**attempt,'ledger_sequence':2})+'\n')
    output = tmp_path/'nonredundant.tar.gz'; pilot.archive_results(campaign,output)
    with tarfile.open(output) as tar:
        assert 'extraction/run/attempts.jsonl' in tar.getnames()


@pytest.mark.parametrize('only_overlap',[False,True])
async def test_cpu_holdout_disjoint_sample_pin_and_failed_parent_readiness(tmp_path,monkeypatch,only_overlap):
    from openpatients2 import corpus_pilot as pilot
    from openpatients2.data import write_json, read_jsonl
    from openpatients2.corpus_stats import sha256
    root = Path(__file__).resolve().parents[1]
    campaign = pilot.prepare(root,tmp_path/'run',root/'configs/pilot/refinement.yaml'); work = Path(campaign['work'])
    calls = []
    async def prepare(work_dir,config):
        calls.append(config)
        (work_dir/'profile').mkdir()
        a = article(); a.update(pmcid='PMC1',article_id='PMC1.1')
        b = copy.deepcopy(a); b.update(pmcid='PMC2',article_id='PMC2.1')
        values = [a] if work_dir == work or only_overlap else [a,b]
        path = work_dir/'profile/sample.jsonl.gz'
        with gzip.open(path,'wt') as stream:
            for value in values: stream.write(json.dumps(value)+'\n')
        write_json(work_dir/'vision-assets/manifest.json',{'figures':[{'article_id':v['article_id']} for v in values]})
        ready = {'status':'ready','hashes':{'profile/sample.jsonl.gz':sha256(path),
            'vision-assets/manifest.json':sha256(work_dir/'vision-assets/manifest.json')}}
        write_json(work_dir/'cpu.json',ready)
        return ready
    monkeypatch.setattr('openpatients2.pilot_cpu.run_cpu',prepare)
    if only_overlap:
        with pytest.raises(ValueError,match='overlaps'): await pilot.prepare_cpu_campaign(campaign)
        assert json.loads((work/'cpu.json').read_text())['status'] == 'failed'
        with pytest.raises(ValueError,match='not ready'): pilot.verify_cpu(campaign)
    else:
        result = await pilot.prepare_cpu_campaign(campaign)
        assert result['holdout']['selected_articles'] == 1
        assert [a['pmcid'] for a in read_jsonl(work/'holdout/profile/sample.jsonl.gz')] == ['PMC2']
        assert json.loads((work/'holdout/vision-assets/manifest.json').read_text())['figures'] == [{'article_id':'PMC2.1'}]
        assert result['hashes']['review-source-sample.json'] == sha256(work/'review-source-sample.json')
        assert json.loads((work/'review-source-sample.json').read_text())['articles'][0]['pmcid'] == 'PMC2'
        pilot.verify_cpu(campaign)
    assert len(calls) == 2 and calls[1]['sample_size'] == 24
    assert calls[1]['source_mode'] == 'acquisition' and not calls[1]['fixed_rosters']
