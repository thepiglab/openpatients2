"""Manually authored pixel/source-grounded visual references for annotator1."""
import json
from pathlib import Path

def iaws():
 root=Path('benchmarks/dense50');aid='PMC13618765.1';s=json.loads((root/'sources'/f'{aid}.json').read_text());assets=json.loads((root/'figure-assets'/f'{aid}.json').read_text());seg={r['segment_id']:r['text'] for r in s['segments']}
 def ev(sid,quote):assert quote in seg[sid];return {'segment_id':sid,'quote':quote}
 def tag(value,basis,cue=None,evidence=None,as_doc=None,certainty='clear'):return {'value':value,'as_documented':as_doc,'basis':basis,'visual_cue':cue,'evidence':evidence or [],'certainty':certainty}
 photographs=[
 ('figure-1','b00010','The axillary web in the left axilla.',
 'Close clinical photograph of the axilla and raised proximal upper arm, with a hand touching the axillary region.',
 'A single portrait-oriented color photograph tightly frames exposed axillary skin and the proximal inner upper arm, raised diagonally toward the upper right. Several fingers cross the lower-left area and press or touch the skin below the axillary fold. Fine skin folds and short hair are visible. A narrow pale linear ridge/fold runs from the axillary region onto the inner arm. A small part of the face, hair and a necklace appears at the left edge; a blurred room and objects are in the background. There are no panel letters, arrows, scale bars, numerical measurements or printed labels.',
 ['Raised upper arm exposes the axillary fold.','Several fingers contact the skin below the axilla.','A narrow pale linear ridge/fold is visible near the axilla and inner upper arm.'],
 'The authors identify this photograph as the left axillary web at presentation, supporting their clinical IAWS description; the photograph alone does not establish the tissue composition or mechanism.',
 'diagnostic_evidence',
 'Physical examination revealed a cord-like and firm fibrous structure extending from the left axilla to the medial aspect of the arm (Figure 1).',
 ['No scale or depth information permits measurement of the visible linear feature.','Cord identity, firmness and left laterality come from the caption/examination, not from pixel-only identification.']),
 ('figure-2','b00011','Follow-up at two months.',
 'Follow-up clinical photograph of an exposed axilla with the arm raised.',
 'A single portrait-oriented color photograph shows the axillary fold and proximal inner upper arm, raised nearly vertically. The shoulder/torso is partly covered by a mustard-orange knit garment pulled away from the axilla, with a hand holding the garment at the bottom. Hair appears at the left edge and a plain pale wall occupies the right background. Fine skin creases and faint hair are visible, with no comparable prominent linear ridge evident in this view. There are no panel letters, arrows, scale bars, numerical measurements or printed labels. Framing, arm position and clothing differ from Figure 1.',
 ['Raised arm exposes the axillary skin.','A hand pulls the orange garment down and away from the axilla.','Fine axillary skin creases are visible; no comparable prominent ridge is evident in this view.'],
 'The caption dates this photograph to two-month follow-up, and the case narrative reports complete resolution of the fibrotic cord at this point. Differing framing prevents a calibrated image-only comparison.',
 'treatment_response',
 'At two months of follow-up, the fibrotic cord had completely resolved (Figure 2).',
 ['The two-month timing and complete cord resolution are author-reported clinical claims.','Different pose, framing and lighting prevent an objective dimension or degree-of-resolution estimate from these two photographs.'])]
 rows=[]
 for fid,sid,caption,general,detail,observations,meaning,role,claim,limits in photographs:
  ce=ev(sid,caption);ae=ev('b00004',claim)
  panel={'panel':None,'general_description':general,'detailed_description':detail,'image_kind':'clinical_photograph','has_chart':False,'charts':[],
   'imaging_modalities':[tag('clinical_photography','pixels','Color external-surface photograph of exposed axilla and arm.')],
   'imaging_submodalities':[],
   'medical_domains':[tag('other','article',evidence=[ev('b00004','A 15-session physical therapy program was planned')],as_doc='physical therapy')],
   'body_parts':[tag('axilla','pixels','Axillary fold exposed beneath the raised arm.'),tag('proximal upper arm','pixels','Inner proximal arm extends upward from the axillary fold.')],
   'mentioned_categories':[],'pixel_observations':observations,
   'caption_claims':[tag(caption,'caption',evidence=[ce],as_doc=caption)],
   'clinical_significance':[{'statement':meaning,'role':role,'clinical_fact_status':'unreviewed_annotation','basis':'article','visual_cue':None,'evidence':[ce,ae],'certainty':'clear'}],
   'diagram_relations':[],'limitations':limits}
  visual={'figure_id':fid,'general_description':general,'detailed_description':detail,'panels':[panel],'limitations':limits}
  attribution={'figure_id':fid,'assignments':[{'panel':None,'patient_ids':['p1'],'scope':'individual','subject':'patient','evidence':[ce,ae],'rationale':'The only index patient is explicitly linked to this photograph by the case narrative and its figure callout; identity and time rely on source text, not image resemblance.'}],'limitations':[]}
  declared=next(f for f in assets['figures'] if f['figure_id']==fid)
  rows.append({'figure_id':fid,'status':'inspected','assets':[{'url':a['url'],'sha256':a['sha256']} for a in declared['assets']],'analysis':{'visual':visual,'attribution':attribution},'limitations':limits})
 target={'format':'dense-figure-reference/1','article_id':aid,'source_text_sha256':s['text_sha256'],'figures':rows}
 (root/'visual-submissions').mkdir(exist_ok=True);(root/'visual-submissions'/f'{aid}.json').write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':iaws()

def pad():
 root=Path('benchmarks/dense50');aid='PMC13624890.1';s=json.loads((root/'sources'/f'{aid}.json').read_text());assets=json.loads((root/'figure-assets'/f'{aid}.json').read_text());seg={r['segment_id']:r['text'] for r in s['segments']}
 def ev(sid,quote):assert quote in seg[sid];return {'segment_id':sid,'quote':quote}
 def tag(value,basis,cue=None,evidence=None,as_doc=None,certainty='clear'):return {'value':value,'as_documented':as_doc,'basis':basis,'visual_cue':cue,'evidence':evidence or [],'certainty':certainty}
 def panel(letter,general,detail,kind,modality,cue,body,observations,caption,sid,significance,role,article_quote=None,limits=None):
  ce=ev(sid,caption);sig_ev=[ce]+([ev('b00004',article_quote)] if article_quote else [])
  return {'panel':letter,'general_description':general,'detailed_description':detail,'image_kind':kind,'has_chart':False,'charts':[],
   'imaging_modalities':[tag(modality,'pixels',cue)],'imaging_submodalities':[],
   'medical_domains':[tag('dermatology','article',evidence=[ev('b00004','The dermatological examination revealed an erosive and slightly hyperkeratotic plaque')],as_doc='dermatological examination')]+([tag('pathology','caption',evidence=[ce],as_doc='Histopathological and DIF aspects')] if kind=='microscopy' else []),
   'body_parts':[tag(body,'caption' if body in ['glans','epidermis'] else 'article',evidence=[ce] if body in ['glans','epidermis'] else [ev('b00004','located just next to the urethral meatus in the context of a whitish and sclerotic appearance of the penis')],as_doc=body)],
   'mentioned_categories':[],'pixel_observations':observations,'caption_claims':[tag(caption,'caption',evidence=[ce],as_doc=caption)],
   'clinical_significance':[{'statement':significance,'role':role,'clinical_fact_status':'unreviewed_annotation','basis':'article' if article_quote else 'caption','visual_cue':None,'evidence':sig_ev,'certainty':'clear'}],
   'diagram_relations':[],'limitations':limits or ['No scale bar or printed magnification is visible.']}
 panels1=[
 panel('A','Clinical photograph of pale, irregularly discolored penile skin.',
  'Left panel of a two-image horizontal composite, labelled A at the lower-left edge. The genital skin occupies nearly the whole photograph and is held at the side by fingers. Broad irregular whitish surface areas alternate with pink/red spots and shallow folds; sparse hair is visible near the upper edge. There are no arrows, scale bars or numeric labels.',
  'clinical_photograph','clinical_photography','Color external-surface photograph with skin folds, hair and touching fingers.','penis',
  ['Broad pale/whitish surface areas are visible.','Smaller pink/red patches interrupt the pale areas.','Fingers hold the skin at the left edge.'],
  'A) Whitish discoloration and sclerotic appearance of the penis','b00012',
  'The authors identify penile whitish discoloration and sclerosis in the known LSA setting; a photograph alone does not establish histology.','diagnostic_evidence'),
 panel('B','Close clinical photograph of a red surface lesion on the glans.',
  'Right panel labelled B at its lower-left edge. A rounded pale pink/white glans fills the upper and central frame, with a prominent bright red, shiny, irregular patch near its center. Smaller dark red spots are visible below the main patch. A slit-like opening is visible to the left of the lesion, and gloved fingers support the shaft on the lower sides. Dark hair forms the upper background. There are no arrows, scale bars or numeric labels.',
  'clinical_photograph','clinical_photography','Color close photograph of glans with gloved fingers.','glans',
  ['An irregular bright red patch contrasts with surrounding pale skin.','Smaller red spots lie below the main patch.','White gloves hold the genital tissue at the lower sides.'],
  '(B) an erosive and slightly hyperkeratotic lesion of the glans.','b00012',
  'The caption identifies an erosive, slightly hyperkeratotic glans lesion. The case reports15×8mm beside the urethral meatus and an initial SCC suspicion; diagnosis of PAD in LSA was proposed after the workup, not established from appearance.','diagnostic_evidence',
  'A diagnosis of PAD arising in the context of LSA was proposed for this case.',
  ['No scale allows a pixel-derived size estimate.','Erosion/hyperkeratosis and the proposed PAD interpretation are author-reported; the photograph does not diagnose SCC or PAD.'])]
 panels2=[
 panel('A','Low-power pink/purple histology view of a biopsy tissue fragment.',
  'Upper-left panel labelled A shows an isolated tapered biopsy fragment on a white background. A dark purple undulating surface rim overlies a broad pale pink interior with scattered darker profiles. A conspicuous pale/white space lies near the superficial tissue beneath the surface rim. Tissue narrows toward the bottom. No scale bar, numerical label or stain name is printed.',
  'microscopy','light_microscopy','Pink/purple tissue section on a bright background.','penis',
  ['Dark purple surface layer overlies lighter pink tissue.','A white/pale space separates portions of superficial tissue.','The tissue fragment is tapered at the bottom.'],
  'A) Intense papillary edema, producing a subepidermal bulla, and homogeneous sclerotic collagen with patchy lymphocytic infiltration.','b00013',
  'The authors attribute this biopsy from the whitish sclerotic area to classical LSA findings, including edema, a subepidermal bulla and sclerotic collagen; these are source interpretations of the histology.','diagnostic_evidence',
  'the one from the whitish sclerotic area surrounding the erosion confirmed the classical histopathological features of LSA'),
 panel('B','Enlarged pink/purple tissue section with irregular clefts and a small overview inset.',
  'Large field occupying the right side of the composite, associated with label B below its small black-bordered inset at the upper center. The main tissue has a dark purple superficial band, long irregular purple cellular projections and numerous pale/white spaces separating purple structures. Scattered rounded dark-purple profiles lie within some of the spaces. Lighter pink tissue is visible along the lower/right region. The inset shows a smaller broader tissue view in similar pink/purple colors, explicitly identified by the caption as the same biopsy. No scale, magnification or stain label is printed.',
  'microscopy','light_microscopy','Large pink/purple microscopic tissue field with white clefts and a smaller tissue inset.','epidermis',
  ['Irregular white spaces interrupt dense purple tissue structures.','Long irregular purple projections extend toward lighter pink tissue.','A black-bordered low-power tissue inset sits at the upper center.'],
  'B) Epidermal hyperplasia with suprabasal clefting and “dilapidated brick wall” acantholysis. In the inset, the panoramic view of the same biopsy specimen.','b00013',
  'The caption describes epidermal hyperplasia and suprabasal acantholysis. The case says the erosion-biopsy appearance was indistinguishable from HHD and contributed to the proposed PAD diagnosis; this panel does not independently prove a genetic HHD diagnosis.','diagnostic_evidence',
  'The specimen from the erosion showed epidermal hyperplasia with suprabasal clefting and “dilapidated brick wall” acantholysis, which was indistinguishable from HHD (Figure 2B).'),
 panel('C','Green fluorescence microscopy field with a reticular cellular-outline pattern.',
  'Lower-left rectangular panel labelled C within its bottom-left edge. Bright green thin interconnected lines form irregular polygonal outlines against a dark green/black background, with a less regular mottled green region toward the upper/left edge. The lower and central regions show a clearer meshwork. No scale bar, intensity legend, numerical measurement or channel label is printed in the panel.',
  'microscopy','fluorescence_microscopy','Bright green reticular signal on a dark background.','epidermis',
  ['Interconnected bright green lines form polygonal cell-like outlines.','Signal is more mottled toward the upper/left portion.','No quantitative intensity scale is visible.'],
  'C) Intraepidermal intercellular IgG deposition with a “net-like” pattern.','b00013',
  'The caption identifies intercellular epidermal IgG staining on DIF. The case considered pemphigus after positive DIF but excluded it using morphology and negative autoantibody/IIF workup, so fluorescence positivity is not a confirmed pemphigus diagnosis.','diagnostic_evidence',
  'Further examination of specific anti-skin autoantibodies, particularly anti-Dsg1 and anti-Dsg3 antibodies, was negative, as was indirect immunofluorescence.',
  ['Antibody identity and direct-immunofluorescence technique come from the source caption, not green color alone.','The caption identifies IgG; the image does not identify an independent C3 channel or establish the pemphigus diagnosis.'])]
 panels1[1]['clinical_significance'][0]['evidence'].append(ev('b00004','In the suspicion of a squamous cell carcinoma arising on LSA, two punch biopsies were performed.'))
 panels2[2]['clinical_significance'][0]['evidence'].append(ev('b00007',seg['b00007']))
 panels1[1]['clinical_significance'][0]['evidence'].append(ev('b00004','The dermatological examination revealed an erosive and slightly hyperkeratotic plaque measuring 15×8 mm, located just next to the urethral meatus in the context of a whitish and sclerotic appearance of the penis (Figure 1).'))
 panels2[1]['body_parts'][0]['as_documented']='Epidermal'
 panels2[2]['body_parts'][0]['as_documented']='Intraepidermal'
 for p in panels2:
  p['medical_domains'][1]['evidence'].append(ev('b00013','Histopathological and DIF aspects.'))
 # Use caption-grounded epidermal identity only where caption explicitly names it.
 for p in panels2:
  p['imaging_submodalities']=[tag('direct_immunofluorescence','caption',evidence=[ev('b00013','Histopathological and DIF aspects.')],as_doc='DIF')] if p['panel']=='C' else []
 rows=[]
 for fid,panels,general,detail in [
  ('fig001',panels1,'Two-panel clinical photograph composite of genital discoloration and a focal glans lesion.','A and B lie side by side, separated by a narrow white line. PanelA emphasizes broad whitish discoloration; panelB provides a frontal close view of the focal red lesion. Visible panel letters are A/B; there are no chart axes, arrows or measurement scales.'),
  ('fig002',panels2,'Composite of two pink/purple histology views and one green fluorescence view.','PanelA is the upper-left biopsy overview; panelB is the large right-hand histology field with its small top-center overview inset; panelC is the lower-left green fluorescence field. The images are unscaled and have visible A/B/C labels; the inset belongs to B and is not assigned an invented additional letter.')]:
  ce=ev('b00012' if fid=='fig001' else 'b00013',seg['b00012' if fid=='fig001' else 'b00013']);ae=ev('b00004','A 68-year-old man, followed up for a previous diagnosis of genital lichen sclerosus et atrophicus (LSA), was referred to our institution for a recent-onset genital erosion refractory to topical treatment.')
  attribution={'figure_id':fid,'assignments':[{'panel':p['panel'],'patient_ids':['p1'],'scope':'individual','subject':'patient_specimen' if fid=='fig002' else 'patient','evidence':[ce,ae],'rationale':('The sole index patient is explicitly linked to these biopsy/DIF specimens by the source callouts. PanelB inset is the same specimen according to its caption; identity is not inferred from image similarity.' if fid=='fig002' else 'The sole index patient is explicitly linked to these clinical photographs by the source callouts; identity is not inferred from image similarity.')} for p in panels],'limitations':[]}
  declared=next(f for f in assets['figures'] if f['figure_id']==fid);limits=['Source does not print stain identity, magnification or measurement scale in the histology views.'] if fid=='fig002' else ['No scale permits pixel-derived lesion dimensions.']
  rows.append({'figure_id':fid,'status':'inspected','assets':[{'url':a['url'],'sha256':a['sha256']} for a in declared['assets']],'analysis':{'visual':{'figure_id':fid,'general_description':general,'detailed_description':detail,'panels':panels,'limitations':limits},'attribution':attribution},'limitations':limits})
 target={'format':'dense-figure-reference/1','article_id':aid,'source_text_sha256':s['text_sha256'],'figures':rows};(root/'visual-submissions').mkdir(exist_ok=True);(root/'visual-submissions'/f'{aid}.json').write_text(json.dumps(target,ensure_ascii=False,indent=2)+'\n')
