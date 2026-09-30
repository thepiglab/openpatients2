from openpatients2.fidelity import matches,without_evidence,evaluate,summarize,last_parseable


def test_units_cannot_match_by_substring_or_drop_threshold():
    assert not matches({'unit':{'regex':'g/L'}},{'unit':'mg/L'})
    assert matches({'unit':{'regex':'mmol/L'}},{'unit':'mmol/L'})
    assert not matches({'numeric_value':6.8,'comparator':'<'},{'numeric_value':6.8,'comparator':'='})


def test_unit_prefix_case_matters_but_litre_spelling_does_not():
    assert not matches({'unit':{'regex':'G/L'}},{'unit':'g/L'})
    assert not matches({'unit':{'regex':'mg/dL'}},{'unit':'Mg/dL'})
    assert matches({'unit':{'regex':'g/dl'}},{'unit':'g/dL'})
    assert matches({'unit':{'regex':r'K/[uµμ]l|10\^9/L'}},{'unit':'K/µL'})


def test_quote_content_cannot_satisfy_claim_and_zero_is_numeric():
    claim=without_evidence({'name':'potassium','evidence':[{'quote':'sodium 149 mmol/L'}]})
    assert not matches({'name':{'regex':'sodium'}},claim)
    assert matches({'numeric_value':0},{'numeric_value':0.0})
    assert not matches({'numeric_value':0},{'numeric_value':False})


def test_failed_delivery_and_unavailable_negative_are_explicit():
    ref={'checks':[{'id':'1','record_id':'case','task':'observations','collection':'items','kind':'required','category':'numeric','pattern':{'numeric_value':1}},
        {'id':'2','record_id':'case','task':'conditions','collection':'items','kind':'forbidden','category':'wrong','pattern':{'name':'wrong'}}]}
    pred={'case':{'raw':{'observations':{'items':[{'numeric_value':1}]}},'delivered':{}}}
    rows=evaluate(ref,pred)
    assert summarize(rows,'raw')['matched']==1 and summarize(rows,'delivered')['matched']==0
    assert summarize(rows,'raw')['forbidden_checks_unavailable']==1


def test_raw_audit_never_selects_earlier_better_attempt():
    assert last_parseable([{'content':'{"items":[]}','finish_reason':'stop'},{'content':'{"items":','finish_reason':'length'}]) is None
