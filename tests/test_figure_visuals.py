from copy import deepcopy

import pytest

from openpatients2.figure_visuals import FigureVisuals, ChartReading, ClinicalSignificance, panel_columns, validate_visuals


def tag(value):
    return {'value':value,'as_documented':value,'basis':'pixels','visual_cue':'Legible chart legend',
            'evidence':[],'certainty':'clear'}


def chart_figure():
    panel={'panel':'a','general_description':'Pie chart of imaging-modality proportions.',
        'detailed_description':'Colored sectors labeled by modality, with printed percentages and a legend.',
        'image_kind':'graph','has_chart':True,'charts':[{'chart_type':'pie','title':'Imaging modalities',
            'scope':'aggregate','is_clinical_chart':False,'x_axis':None,'y_axis':None,
            'measured_quantities':[tag('percentage')],'readings':[{
                'metric_name':'Light microscopy proportion','metric_kind':'percentage','series_label':'Light Microscopy',
                'time_or_x_label':None,'value_text':'21.7%','numeric_value':21.7,'unit':'%',
                'precision':'printed_exact','basis':'pixels','visual_cue':'Printed sector label 21.7%',
                'evidence':[],'certainty':'clear'}],'limitations':[]}],
        'imaging_modalities':[],'imaging_submodalities':[],'medical_domains':[],'body_parts':[],
        'mentioned_categories':[tag('light_microscopy'),tag('ct'),tag('mri')],
        'pixel_observations':['A sector is labeled Light Microscopy 21.7%.'],
        'caption_claims':[],'clinical_significance':[],'limitations':[]}
    return {'figure_id':'F1','general_description':'Dataset distribution charts.',
        'detailed_description':'Pie chart summarizing imaging types, not an individual scan.', 'panels':[panel],'limitations':[]}


def test_chart_about_modalities_does_not_become_ct_or_mri_acquisition():
    value=chart_figure()
    result=FigureVisuals.model_validate(value).model_dump()
    columns=list(panel_columns('PMC1.1',result))[0]
    assert columns['has_chart'] and columns['chart_types']==['pie'] and columns['imaging_modalities']==[]
    assert columns['mentioned_categories']==['light_microscopy','ct','mri'] and columns['is_clinical_chart'] is False
    value['panels'][0]['imaging_modalities']=[tag('ct')]
    with pytest.raises(ValueError,match='not itself'):FigureVisuals.model_validate(value)


def test_unreadable_chart_values_and_inferred_clinical_significance_rejected():
    reading=chart_figure()['panels'][0]['charts'][0]['readings'][0]
    with pytest.raises(ValueError,match='Unreadable'):ChartReading.model_validate({**reading,'precision':'unreadable'})
    with pytest.raises(ValueError,match='Clinical significance'):ClinicalSignificance.model_validate({
        'statement':'The image establishes cancer','role':'diagnostic_evidence','basis':'pixels',
        'visual_cue':'A dark spot','evidence':[],'certainty':'clear'})


def test_bp_chart_reading_preserves_series_time_units_and_estimation():
    reading={**chart_figure()['panels'][0]['charts'][0]['readings'][0],
        'metric_name':'Systolic blood pressure','metric_kind':'blood_pressure_systolic',
        'series_label':'Case 2','time_or_x_label':'Day 3','unit':'mmHg','value_text':'approximately 120',
        'numeric_value':120,'precision':'estimated_from_plot','certainty':'uncertain',
        'visual_cue':'Curve near the 120 tick on the y-axis at day 3'}
    result=ChartReading.model_validate(reading).model_dump()
    assert result['series_label']=='Case 2' and result['precision']=='estimated_from_plot'
    assert result['unit']=='mmHg' and result['time_or_x_label']=='Day 3'


def test_figure_identity_and_literal_evidence_checked_without_patient_fields():
    value=chart_figure()
    assert validate_visuals(value,{'segments':[]},'F1')['figure_id']=='F1'
    with pytest.raises(ValueError,match='another figure'):validate_visuals(value,{'segments':[]},'F2')
    value['panels'][0]['medical_domains']=[{**tag('radiology'),'basis':'article','visual_cue':None,
        'evidence':[{'segment_id':'s1','quote':'not in source'}]}]
    with pytest.raises(ValueError,match='nonliteral'):validate_visuals(value,{'segments':[{'segment_id':'s1','text':'actual source'}]},'F1')
    value=chart_figure();value['panels'][0]['patient_ids']=['p1']
    with pytest.raises(ValueError):FigureVisuals.model_validate(value)  # Ownership cannot leak into independent description.
