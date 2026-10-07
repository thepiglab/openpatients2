"""Opt-in integration of source routing, source challenges and additive episodes."""
import asyncio
from copy import deepcopy
import json

from .data import write_json
from .evidence_ledger import (source_chunks, map_messages, check_chunk_map,
    attribute_messages, attribute_source_segments, check_attribute_audit, protect_unchallenged)
from .episode_ledger import (build_ledger, ledger_messages, ledger_timeline,
    apply_ledger_delta_partial, source_ordered_fact_aliases)
from .prompts import messages_for
from .provenance import json_digest


async def prepare_source_map(runner, article, roster, replica):
    chunks = source_chunks(article,runner.config.ledger_chunk_characters)
    async def scan(chunk):
        row = await runner.call('ledger_chunk_map',map_messages(article,roster,chunk),
            lambda value:check_chunk_map(value,chunk,roster),
            {'article_id':article['article_id'],'chunk_id':chunk['chunk_id']},
            replica,chunk['fragments'])
        return chunk,row
    result = await asyncio.gather(*(scan(chunk) for chunk in chunks))
    write_json(runner.output/'source-ledgers'/(json_digest(article['article_id'])+'.json'),{
        'article_id':article['article_id'],'text_sha256':article['text_sha256'],
        'chunks':[{'chunk':chunk,'status':row['status'],'map':row['data'],'errors':row['errors']}
                  for chunk,row in result],
        'all_source_characters_scanned':all(row['status']=='valid' for _,row in result),
        'clinical_completeness_verified':False,'hypotheses_are_not_clinical_facts':True})
    return result


async def challenge_attributes(runner, article, patient, packet, tasks, rows, identity, replica):
    """Independent small witnesses; re-extract only domains with concrete challenges."""
    from .pilot_extract import packet_segments
    async def audit(batch, index):
        return await runner.call('ledger_attribute_audit',attribute_messages(article,patient,batch),
            lambda value:check_attribute_audit(value,batch,attribute_source_segments(article,patient,batch),id_contract=True),
            {**identity,'attribute_batch':index},replica,attribute_source_segments(article,patient,batch))
    audits = await asyncio.gather(*(audit(rows[i:i+6],i//6) for i in range(0,len(rows),6)))
    challenges = [c for r in audits if r['status']=='valid' for c in r['data']['challenges']]
    # Mere uncertainty asks for human review; it does not justify rewriting a fact.
    repair_challenges = [c for c in challenges if c['verdict'] != 'uncertain']
    by_id = {r['review_id']:r for r in rows}
    domains = sorted({by_id[c['fact_id']]['task'] for c in repair_challenges})
    segments = packet_segments(packet)
    async def repair(task):
        previous = next(r for r in tasks if r['task']==task)
        if previous['data'] is None:return None
        selected = [c for c in repair_challenges if by_id[c['fact_id']]['task']==task]
        domain_rows = [r for r in rows if r['task']==task]
        messages = messages_for(packet,task,namespace='source-ledger/1')
        messages[-1]['content'] += '\nSCHEMA:\n'+json.dumps(runner.schemas[task])
        messages.append({'role':'user','content':
            'Re-extract this domain from the ORIGINAL SOURCE. These attribute challenges are untrusted '
            'hypotheses, not truth. Independently verify them. Correct only the specifically challenged '
            'clinical atoms where warranted. Retain every unchallenged fact and its clinical values. '
            'Do not invent a replacement value: use schema null/unknown for genuinely unstated attributes. '
            'Return the COMPLETE typed extraction. The original candidate remains in provenance.\nCURRENT:\n'+
            json.dumps(previous['data'],ensure_ascii=False)+'\nCHALLENGES:\n'+json.dumps(selected)+
            '\nFACT_ID_MAP:\n'+json.dumps([{'fact_id':r['review_id'],'pointer':r['pointer']} for r in domain_rows])})
        def checker(value):
            parsed=runner.clinical_checker(task,packet,segments)(value)
            return protect_unchallenged(previous['data'],parsed,domain_rows,selected)
        result=await runner.call('ledger_repair_'+task,messages,checker,
            {**identity,'attribute_repair_task':task},replica,segments)
        if result['status']=='valid':
            previous['before_attribute_repair']=deepcopy(previous['data'])
            previous['data']=result['data']
        return result
    repairs=await asyncio.gather(*(repair(task) for task in domains))
    return [*audits,*[r for r in repairs if r is not None]], {
        'challenges':challenges,'domains_rechecked':domains,
        'failed_audit_batches':sum(r['status']!='valid' for r in audits),
        'semantic_accuracy_verified':False,'review_status':'model_only'}


async def assemble_episodes(runner, article, patient, rows, identity, replica, sources):
    ledger = build_ledger(identity['record_id'],rows,sources)
    calls=[]; delta_audits=[]
    aliases=source_ordered_fact_aliases(ledger,sources,article=article)
    async def propose(phase,batch,index):
        nonlocal ledger
        messages=ledger_messages(article,patient,ledger,
            fact_aliases=batch if phase=='events' else [],phase=phase,
            event_ids=batch if phase=='edges' else None)
        payload=json.loads(messages[1]['content'].split('SOURCE_JSON:\n',1)[1].split('\nTASK:\n',1)[0])
        constraints={'phase':phase,'fact_aliases':batch if phase=='events' else [],
            'event_ids':batch if phase=='edges' else None,
            'available_segment_ids':payload['source_selection']['selected_segment_ids']}
        def checker(value):
            apply_ledger_delta_partial(ledger,value,sources,**constraints)
            return value
        row=await runner.call('ledger_'+phase,messages,checker,
            {**identity,'ledger_phase':phase,'batch':index},replica,article['segments'])
        calls.append(row)
        if row['status']=='valid':
            ledger,audit=apply_ledger_delta_partial(ledger,row['data'],sources,**constraints)
            delta_audits.append({'phase':phase,'batch':index,**audit})
    # Each patient mutates its own ledger; patients/chunks run concurrently.
    for index,start in enumerate(range(0,len(aliases),24)):
        await propose('events',aliases[start:start+24],index)
    events=[e['event_id'] for e in ledger['events']]
    for index,start in enumerate(range(0,len(events),16)):
        await propose('edges',events[start:start+16],index)
    graph=ledger_timeline(ledger)
    failed=[r for r in calls if r['status']!='valid']
    errors=[e for r in failed for e in r['errors']]
    if ledger.get('quarantine'):errors.append('episode_fact_quarantine_requires_review')
    if rows and not graph['events']:errors.append('episode_no_events_for_delivered_facts')
    if any(a.get('partial_application') for a in delta_audits):errors.append('episode_additions_quarantined')
    result={'task':'timeline_v2','identity':identity,'status':'partial' if errors else 'valid',
        'data':graph,'errors':errors, 'attempts':[],
        'origin':'additive_episode_ledger','clinical_accuracy_verified':False}
    linked={f for e in graph['events'] for f in e['fact_ids']}
    receipt={'fact_aliases':ledger['facts'],'quarantine':ledger.get('quarantine',[]),
        'fact_quarantine_summary':ledger.get('fact_quarantine_summary',{}),
        'accepted_event_count':len(graph['events']),'accepted_edge_count':len(graph['edges']),
        'unlinked_fact_ids':[f['fact_id'] for f in ledger['facts'] if f['fact_id'] not in linked],
        'failed_delta_calls':len(failed),'delta_application_audits':delta_audits,
        'fact_batch_order':'canonical_source_span','semantic_order_verified':False}
    write_json(runner.output/'episode-ledgers'/(json_digest(identity['record_id'])+'.json'),ledger)
    return result,calls,receipt
