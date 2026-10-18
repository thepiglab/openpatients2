"""Themed categories for every reference article: theme group -> sublane.

The scheme is the round-2 selection's own `group`/`lane` labels plus one catch-all group. Articles
that carry a selection lane keep it as their primary theme. The others (round 1, which was selected by
specialty stratum) are classified here by fixed rules over their own data: title, journal, keywords and affiliations from the
JATS XML, the selection stratum, and the reviewed patients' count, ages and species. No model and no
randomness: the same inputs always give the same themes.

Every matching lane becomes a tag. The primary theme is the lane the stratum or title puts first, else the
first matching lane in `PRIORITY`; an article that matches nothing is "rare / acute, other". The terms and
order were tuned on half of round 2 (lanes hidden); tests/test_article_themes.py scores the other half.
"""
from __future__ import annotations

import html
import re

GROUPS = {
    'data_rich_x': ('Data-rich modalities', 'A test or record the case is built around: waveforms, imaging, serial data.'),
    'data_rich': ('Data-rich specialties', 'Specialties whose cases come with dense findings.'),
    'common_chronic': ('Common & chronic disease', 'Conditions that dominate real clinical records.'),
    'populations': ('Patient populations', 'Age groups and specialties with their own record shape.'),
    'series_settings': ('Series & care settings', 'Multi-patient reports, dental and primary care.'),
    'regions': ('World regions', 'Reports from under-covered regions (by author affiliation).'),
    'other': ('Rare, acute & other', 'Original-batch cases that fit no lane above.'),
}

# lane -> (group, label). Order is the display order inside each group.
LANES = {
    'ecg_waveform': ('data_rich_x', 'ECG waveforms'),
    'serial_labs': ('data_rich_x', 'Serial labs'),
    'longitudinal_multiyear': ('data_rich_x', 'Multi-year follow-up'),
    'echo_cardiac': ('data_rich_x', 'Echocardiography'),
    'ophthalmic_imaging': ('data_rich_x', 'Eye imaging'),
    'dermoscopy_skin': ('data_rich_x', 'Skin & dermoscopy'),
    'neurophysiology': ('data_rich_x', 'EEG, EMG & sleep'),
    'pulmonary_function': ('data_rich_x', 'Lung function'),
    'audiology': ('data_rich_x', 'Hearing tests'),
    'endoscopy': ('data_rich_x', 'Endoscopy'),
    'icu_monitoring': ('data_rich_x', 'ICU monitoring'),
    'ecg_arrhythmia': ('data_rich', 'Arrhythmia'),
    'longitudinal': ('data_rich', 'Long follow-up'),
    'neuro_eeg': ('data_rich', 'Seizures & epilepsy'),
    'dermatology': ('data_rich', 'Dermatology'),
    'ophthalmology': ('data_rich', 'Ophthalmology'),
    'ent_audiology': ('data_rich', 'ENT'),
    'diabetes_endocrine': ('common_chronic', 'Diabetes & endocrine'),
    'heart_failure_af_htn': ('common_chronic', 'Heart failure, AF & hypertension'),
    'acs_stroke': ('common_chronic', 'Heart attack & stroke'),
    'ckd_renal': ('common_chronic', 'Kidney disease'),
    'copd_asthma_pulm': ('common_chronic', 'COPD, asthma & lung'),
    'liver_gi': ('common_chronic', 'Liver & digestive'),
    'infection': ('common_chronic', 'Common infections'),
    'common_cancer': ('common_chronic', 'Common cancers'),
    'geriatric': ('populations', 'Older adults'),
    'pediatric_common': ('populations', 'Children'),
    'obstetric': ('populations', 'Pregnancy'),
    'psychiatry': ('populations', 'Psychiatry'),
    'toxicology_substance': ('populations', 'Poisoning & substances'),
    'case_series': ('series_settings', 'Case series (3+ patients)'),
    'dental_oral': ('series_settings', 'Dental & oral'),
    'primary_care': ('series_settings', 'Primary care'),
    'africa': ('regions', 'Africa'),
    'latin_america': ('regions', 'Latin America & Caribbean'),
    'oceania_europe_east': ('regions', 'Oceania & Eastern Europe'),
    'veterinary': ('other', 'Veterinary'),
    'rare_acute': ('other', 'Rare / acute, other'),
}
FALLBACK = 'rare_acute'


def _any(*terms):
    return re.compile(r'\b(?:' + '|'.join(terms) + r')', re.I)


# Matched against the title, keywords and journal title (not the abstract or body: too many incidental mentions).
TERMS = {
    'ecg_waveform': _any(r'electrocardiogra', r'ecgs?\b', r'ekgs?\b', r'brugada', r'(?:long|short) qt', r'qtc?\b', r'qt interval',
                         r'wellens', r'osborn wave', r'tachycardi', r'(?:heart|atrioventricular|av) block', r'wolff', r'wpw\b', r'pre[- ]?excitation', r'torsade', r'ventricular fibrillation', r'j waves?\b', r't[- ]wave', r'epsilon wave', r'holter'),
    'serial_labs': _any(r'serial (?:lab|measure|biomarker|monitor)', r'laboratory (?:trend|course|monitoring)', r'biochemical (?:course|trend)',
                        r'hyponatr', r'hypernatr', r'hyperkal', r'hypokal', r'hypercalc', r'hypocalc', r'hypophosph', r'hypomagnes',
                        r'electrolyte', r'h(?:a)?emoly(?:sis|tic)', r'an(?:a)?emia', r'thrombocytop', r'pancytop', r'cytopeni', r'neutropeni',
                        r'hyperammon', r'lactic acidosis', r'metabolic acidosis', r'tumou?r lysis', r'rhabdomyolysis'),
    'longitudinal_multiyear': _any(r'(?:\d+|two|three|four|five|six|seven|eight|nine|ten|twenty)[- ](?:year|decade)s?[- ](?:long[- ])?follow',
                                   r'decades?[- ]long', r'over (?:\d+|two|three|four|five|ten|twenty) (?:years|decades)', r'longitudinal',
                                   r'lifelong', r'natural history', r'(?<!non-)recurren(?:t|ce)(?! laryngeal)', r'relaps'),
    'echo_cardiac': _any(r'echocardiogra', r'echo\b', r'transthoracic', r'transo?esophageal', r'saline contrast', r'cardiac ultrasound', r'strain imaging', r'speckle tracking'),
    'ophthalmic_imaging': _any(r'optical coherence', r'oct\b', r'octa\b', r'fundus', r'fundoscop', r'fluorescein', r'indocyanine',
                               r'angiograph(?:y|ic) (?:of the )?(?:retina|choroid)', r'retinal imaging', r'retinal detach', r'retinopath', r'macular', r'retinograph', r'slit[- ]lamp',
                               r'ultrasound biomicroscop', r'visual field', r'swept[- ]source', r'autofluorescence', r'electroretinogra'),
    'dermoscopy_skin': _any(r'dermoscop', r'dermatoscop', r'trichoscop', r'videodermoscop', r'reflectance confocal', r'melanoma', r'n(?:a)?evus', r'n(?:a)?evi\b', r'basal cell carcinoma',
                            r'skin cancer', r'keratos[ie]s', r'spitz', r'mohs'),
    'neurophysiology': _any(r'electroencephalogra', r'eegs?\b', r'electromyogra', r'emg\b', r'nerve conduction', r'electroneuro',
                            r'polysomnogra', r'evoked potential', r'sleep apn(?:o)?ea', r'obstructive sleep', r'narcolep', r'insomnia',
                            r'parasomnia', r'sleep disorder'),
    'pulmonary_function': _any(r'spirometr', r'pulmonary function', r'lung function', r'fev1', r'dlco', r'diffusing capacity',
                               r'plethysmograph', r'cardiopulmonary exercise', r'interstitial lung', r'pulmonary fibrosis', r'restrictive (?:lung|ventilatory)', r'six[- ]minute walk', r'6[- ]minute walk'),
    'audiology': _any(r'hearing', r'audiometr', r'audiogra', r'audiolog', r'cochlear implant', r'tinnitus', r'deafness', r'\bdeaf\b',
                      r'auditory', r'otoacoustic', r'vestibular', r'vertigo', r'tympanometr'),
    'endoscopy': _any(r'endoscop', r'colonoscop', r'gastroscop', r'esophagogastroduodenoscop', r'bronchoscop', r'cystoscop',
                      r'ercp\b', r'eus\b', r'enteroscop', r'laryngoscop', r'hysteroscop', r'cholangioscop', r'arthroscop'),
    'icu_monitoring': _any(r'intensive care', r'icu\b', r'picu\b', r'nicu\b', r'critical care', r'critically ill', r'mechanical ventilation',
                           r'ecmo\b', r'extracorporeal membrane', r'septic shock', r'cardiogenic shock', r'vasopressor', r'cardiac arrest',
                           r'multi[- ]?organ (?:failure|dysfunction)', r'ards\b', r'acute respiratory distress'),
    'ecg_arrhythmia': _any(r'arrhythmi', r'tachycardi', r'bradycardi', r'(?:heart|atrioventricular|av|bundle branch) block', r'wolff',
                           r'wpw\b', r'pre[- ]?excitation', r'ventricular fibrillation', r'pacemaker', r'sick sinus', r'sinus node',
                           r'torsade', r'asystole', r'syncope', r'premature ventricular', r'ablation'),
    'longitudinal': _any(r'long[- ]term (?:follow|outcome|course|survival|management)', r'follow[- ]up', r'years later',
                         r'(?:recurren|relaps)\w* after (?:\d+|two|three|four|five|ten) years', r'over (?:a|one|two|three) years'),
    'neuro_eeg': _any(r'epilep', r'encephalitis', r'encephalopath', r'seizure', r'status epilepticus', r'convulsi', r'west syndrome', r'infantile spasm'),
    'dermatology': _any(r'dermat', r'cutaneous', r'skin', r'psoria', r'eczema', r'pemphig', r'urticari', r'vitiligo', r'alopecia',
                        r'acne', r'melanoma', r'naev', r'nevus', r'rash', r'erythema', r'keratos'),
    'ophthalmology': _any(r'ophthalm', r'ocular', r'\beyes?\b', r'retin', r'uveitis', r'macul', r'cornea', r'glaucoma', r'cataract',
                          r'orbit', r'optic neur', r'choroid', r'scler(?:a|itis)', r'keratitis', r'conjunctiv', r'visual loss',
                          r'vision loss', r'blindness', r'strabism'),
    'ent_audiology': _any(r'otolaryng', r'otorhinolaryng', r'sinus(?:itis)?\b', r'nasal', r'laryn', r'pharyn', r'tonsil', r'otitis',
                          r'\bear\b', r'parotid', r'salivary', r'thyroglossal', r'head and neck'),
    'diabetes_endocrine': _any(r'diabet', r'dka\b', r'ketoacidosis', r'hyperglyc', r'hypoglyc', r'insulin', r'thyroid', r'thyrotox',
                               r'graves', r'hashimoto', r'obesity', r'metabolic syndrome', r'osteoporo', r'hyperparathyroid',
                               r'adrenal insufficiency', r'cushing'),
    'heart_failure_af_htn': _any(r'heart failure', r'cardiac failure', r'hfref', r'hfpef', r'atrial fibrillation', r'atrial flutter',
                                 r'hypertensi', r'cardiomyopath', r'ejection fraction', r'cor pulmonale'),
    'acs_stroke': _any(r'myocardial infarction', r'stemi\b', r'nstemi\b', r'acute coronary', r'coronary artery disease',
                       r'coronary (?:occlusion|thrombosis|dissection)', r'angina', r'stroke', r'cerebral infarct', r'cerebrovascular',
                       r'intracerebral h(?:a)?emorrhage', r'transient ischemic', r'cerebral venous'),
    'ckd_renal': _any(r'chronic kidney', r'ckd\b', r'kidney (?:failure|injury|disease)', r'renal (?:failure|insufficiency|injury|disease)',
                      r'aki\b', r'dialysis', r'h(?:a)?emodialysis', r'nephrotic', r'nephritic', r'glomerul', r'kidney transplant',
                      r'renal transplant', r'end[- ]stage renal', r'esrd\b', r'nephropath'),
    'copd_asthma_pulm': _any(r'copd\b', r'chronic obstructive', r'asthma', r'emphysema', r'bronchiectasis', r'interstitial lung',
                             r'pulmonary fibrosis', r'bronchiolitis', r'pneumothorax', r'pleural effusion', r'respiratory failure', r'pulmonary embol', r'pneumonitis', r'pneumonias?\b', r'lung injury'),
    'liver_gi': _any(r'cirrho', r'hepatitis', r'fatty liver', r'nafld\b', r'masld\b', r'steatohepatitis', r'liver (?:failure|disease|injury)',
                     r'hepatic (?:failure|encephalopathy)', r'gerd\b', r'gastro[- ]?(?:o)?esophageal reflux', r'peptic', r'gastric ulcer',
                     r'duodenal ulcer', r'inflammatory bowel', r'crohn', r'ulcerative colitis', r'pancreatitis', r'gallstone',
                     r'cholecystitis', r'cholelithiasis', r'diverticul', r'gastrointestinal (?:bleed|hemorrhage|haemorrhage)',
                     r'celiac', r'coeliac', r'irritable bowel'),
    'infection': _any(r'pneumonia', r'tubercul', r'\btb\b', r'hiv\b', r'aids\b', r'sepsis', r'septic', r'urinary tract infection', r'uti\b',
                      r'pyelonephritis', r'cellulitis', r'covid', r'sars[- ]cov', r'malaria', r'dengue', r'osteomyelitis', r'endocarditis',
                      r'meningitis', r'influenza', r'abscess', r'bacter[ae]mia', r'syphilis', r'herpes zoster', r'leprosy', r'typhoid'),
    'common_cancer': _any(r'breast (?:cancer|carcinoma|tumou?r)', r'ductal carcinoma', r'lung (?:cancer|carcinoma|adenocarcinoma)', r'non[- ]small[- ]cell',
                          r'nsclc\b', r'small[- ]cell lung', r'colorectal (?:cancer|carcinoma)', r'colon (?:cancer|carcinoma|adenocarcinoma)',
                          r'rectal (?:cancer|carcinoma|adenocarcinoma)', r'prostate (?:cancer|carcinoma|adenocarcinoma)',
                          r'cervical (?:cancer|carcinoma)', r'gastric (?:cancer|carcinoma|adenocarcinoma)', r'hepatocellular carcinoma',
                          r'pancreatic (?:cancer|adenocarcinoma|ductal)', r'bladder (?:cancer|carcinoma)', r'ovarian (?:cancer|carcinoma)',
                          r'endometrial (?:cancer|carcinoma)', r'thyroid (?:cancer|carcinoma)', r'papillary thyroid',
                          r'renal cell carcinoma', r'(?:o)?esophageal (?:cancer|carcinoma)'),
    'geriatric': _any(r'elderly', r'older (?:adult|patient|woman|women|man|men|person|people)', r'octogenarian', r'nonagenarian',
                      r'centenarian', r'geriatric', r'aged (?:patient|woman|man)', r'frailty', r'dementia', r'nursing home'),
    'pediatric_common': _any(r'p(?:a)?ediatric', r'child(?:ren|hood)?\b', r'infan(?:t|cy)', r'neonat', r'newborn', r'toddler',
                             r'adolescen', r'\bboy\b', r'\bgirl\b', r'preterm', r'premature (?:infant|neonate|baby)', r'kawasaki'),
    'obstetric': _any(r'pregnan', r'postpartum', r'post[- ]partum', r'gestation', r'preeclampsia', r'pre[- ]eclampsia', r'eclampsia',
                      r'c(?:a)?esarean', r'puerper', r'obstetric', r'antenatal', r'prenatal', r'peripartum', r'placent', r'hellp\b',
                      r'ectopic', r'miscarriage', r'labou?r induction', r'trimester', r'f(?:o)?etal'),
    'psychiatry': _any(r'psychiatr', r'schizophren', r'depressi(?:on|ve) (?:disorder|episode)', r'major depress', r'bipolar', r'psychos',
                       r'psychotic', r'anorexia nervosa', r'bulimi', r'eating disorder', r'catatoni', r'autis', r'adhd\b', r'attention[- ]deficit',
                       r'obsessive', r'delirium', r'suicid', r'self[- ]harm', r'anxiety', r'ptsd\b', r'post[- ]traumatic stress',
                       r'neuroleptic malignant', r'serotonin syndrome', r'conversion disorder', r'functional neurological'),
    'toxicology_substance': _any(r'poison', r'overdose', r'intoxicat', r'toxicity', r'toxic\b', r'ingestion', r'envenom', r'snake ?bite',
                                 r'scorpion', r'alcohol', r'substance use', r'cannabi', r'opioid', r'cocaine', r'methamphetamine',
                                 r'heroin', r'paraquat', r'organophosph', r'carbon monoxide', r'withdrawal'),
    'case_series': _any(r'case series', r'series of (?:\d+|two|three|four|five|six|seven|eight|nine|ten) ', r'(?:three|four|five|six|seven|eight|nine|ten|\d{2,}|[3-9]) (?:cases|patients)',
                        r'cohort', r'retrospective (?:study|analysis|review)', r'single[- ]cent(?:er|re) (?:experience|study)'),
    'dental_oral': _any(r'dental', r'dentist', r'tooth', r'teeth', r'oral (?:cavity|mucosa|lesion|lichen|ulcer|surgery|health|squamous)',
                        r'periodont', r'gingiv', r'maxillofacial', r'mandib', r'maxill', r'odontogen', r'endodont', r'orthodont',
                        r'implantolog', r'stomatolog', r'tongue', r'temporomandibular', r'denture', r'pulp(?:itis|al)'),
    'primary_care': _any(r'primary care', r'general practi', r'family (?:medicine|physician|practice)', r'community (?:setting|clinic|health|care)',
                         r'outpatient', r'emergency department', r'rural (?:clinic|setting|hospital|health)', r'gp\b'),
}

# Author-affiliation and journal countries for the region lanes.
REGIONS = {
    'africa': ('Algeria', 'Angola', 'Benin', 'Botswana', 'Burkina Faso', 'Burundi', 'Cameroon', 'Cape Verde', 'Chad', 'Comoros', 'Congo',
               "Côte d'Ivoire", 'Ivory Coast', 'Djibouti', 'Egypt', 'Egyptian', 'Equatorial Guinea', 'Eritrea', 'Eswatini', 'Swaziland',
               'Ethiopia', 'Gabon', 'Gambia', 'Ghana', 'Guinea', 'Guinea-Bissau', 'Kenya', 'Lesotho', 'Liberia', 'Libya', 'Madagascar',
               'Malawi', 'Mali', 'Mauritania', 'Mauritius', 'Morocco', 'Mozambique', 'Namibia', 'Niger', 'Nigeria', 'Rwanda', 'Senegal',
               'Sierra Leone', 'Somalia', 'South Africa', 'South Sudan', 'Sudan', 'Tanzania', 'Togo', 'Tunisia', 'Uganda', 'Zambia',
               'Zimbabwe', 'African', 'Africa'),
    'latin_america': ('Argentina', 'Bolivia', 'Brazil', 'Brasil', 'Chile', 'Colombia', 'Costa Rica', 'Cuba', 'Dominican Republic',
                      'Ecuador', 'El Salvador', 'Guatemala', 'Haiti', 'Honduras', 'Jamaica', 'Mexico', 'México', 'Nicaragua', 'Panama',
                      'Panamá', 'Paraguay', 'Peru', 'Perú', 'Puerto Rico', 'Trinidad', 'Uruguay', 'Venezuela', 'Barbados', 'Bahamas',
                      'Grenada', 'Saint Lucia', 'Antigua', 'Guyana', 'Suriname', 'Belize', 'Sint Maarten', 'Curaçao', 'Latin America',
                      'Caribbean'),
    'oceania_europe_east': ('Australia', 'New Zealand', 'Fiji', 'Papua New Guinea', 'Samoa', 'Tonga', 'Vanuatu', 'Solomon Islands',
                            'Poland', 'Czech Republic', 'Czechia', 'Slovakia', 'Hungary', 'Romania', 'Bulgaria', 'Serbia', 'Croatia',
                            'Slovenia', 'Bosnia', 'Montenegro', 'North Macedonia', 'Macedonia', 'Albania', 'Kosovo', 'Ukraine', 'Belarus',
                            'Moldova', 'Russia', 'Russian Federation', 'Lithuania', 'Latvia', 'Estonia'),
}
# "New " keeps New Mexico (USA) and Papua New Guinea out of Latin America and Africa.
REGION_PATTERNS = {lane: re.compile(r'(?<![\w-])(?<!New )(?:' + '|'.join(re.escape(n) for n in names) + r')(?![\w-])', re.I)
                   for lane, names in REGIONS.items()}

# A region or an older patient named in the title is what the report is about, so it leads.
TITLE_FIRST = {
    **REGION_PATTERNS,
    'geriatric': _any(r'elderly', r'older (?:adult|patient|woman|women|man|men|person|people)', r'octogenarian', r'nonagenarian', r'centenarian', r'geriatric'),
}

# A selection stratum (the specialty an article was drawn for) that defines a lane by itself.
STRATUM_LANES = {'obstetric': 'obstetric', 'psychiatry': 'psychiatry', 'toxicology': 'toxicology_substance',
                 'pediatric': 'pediatric_common', 'neonatal': 'pediatric_common', 'dermatology': 'dermatology',
                 'ophthalmology': 'ophthalmology', 'ent': 'ent_audiology', 'veterinary': 'veterinary'}
# Strata whose specialty shapes the whole record, so the stratum's lane leads.
STRATUM_FIRST = {'obstetric', 'psychiatry', 'toxicology_substance'}

# Primary theme = first tagged lane in this order, after any lane a stratum in `STRATUM_FIRST` or the title puts first (see
# `classify`). The explicit data modalities come first; then the populations; then condition lanes; then the
# broad specialty, age, series and region lanes, which say less about what the record contains.
PRIORITY = ('veterinary',
            'ecg_waveform', 'echo_cardiac', 'ophthalmic_imaging', 'dermoscopy_skin', 'neurophysiology', 'pulmonary_function',
            'audiology', 'endoscopy', 'obstetric', 'psychiatry', 'toxicology_substance', 'icu_monitoring', 'serial_labs', 'longitudinal_multiyear',
            'ecg_arrhythmia', 'neuro_eeg',
            'acs_stroke', 'heart_failure_af_htn', 'diabetes_endocrine', 'ckd_renal', 'copd_asthma_pulm', 'liver_gi',
            'common_cancer', 'infection',
            'dental_oral', 'primary_care', 'dermatology', 'ophthalmology', 'ent_audiology', 'longitudinal',
            'geriatric', 'pediatric_common', 'case_series', 'africa', 'latin_america', 'oceania_europe_east')
assert set(PRIORITY) | {FALLBACK} == set(LANES)


def _text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def xml_features(raw_xml):
    """Journal title, keywords and affiliation text from a JATS article (empty when there is no XML)."""
    raw_xml = raw_xml or ''
    front = raw_xml.split('<body', 1)[0]
    journal = re.search(r'<journal-title>(.*?)</journal-title>', front, re.S)
    return {'journal': _text(journal.group(1)) if journal else None,
            'keywords': [_text(k) for k in re.findall(r'<kwd>(.*?)</kwd>', front, re.S) if _text(k)],
            'affiliations': [_text(a) for a in re.findall(r'<aff\b[^>]*>(.*?)</aff>', front, re.S)]}


def classify(title, *, journal=None, keywords=(), affiliations=(), stratum=None, ages=(), species=(), patients=None):
    """Rule-based themes for one article: {'lane', 'group', 'tags'} (tags in PRIORITY order, primary first).

    `ages` are the reviewed index patients' ages in years (None where unknown), `species` their species,
    and `patients` the reported number of individual patients.
    """
    text = ' | '.join(filter(None, [title, *keywords, journal]))
    tags = {lane for lane, pattern in TERMS.items() if pattern.search(text)}
    first = []
    if stratum in STRATUM_LANES:
        tags.add(STRATUM_LANES[stratum])
        if STRATUM_LANES[stratum] in STRATUM_FIRST:
            first.append(STRATUM_LANES[stratum])
    if any(s and s != 'human' for s in species):
        tags.add('veterinary')
        tags.discard('pediatric_common')
        tags.discard('geriatric')
    known = [a for a in ages if a is not None]
    if known and 'veterinary' not in tags:
        if min(known) >= 65:
            tags.add('geriatric')
        if max(known) < 18:
            tags.add('pediatric_common')
    if (patients or 0) >= 3:
        tags.add('case_series')
    places = ' | '.join(filter(None, [*affiliations, journal]))
    tags.update(lane for lane, pattern in REGION_PATTERNS.items() if pattern.search(places))
    first += [lane for lane, pattern in TITLE_FIRST.items() if lane in tags and pattern.search(title or '')]
    ordered = [lane for lane in PRIORITY if lane in tags]
    lead = next((lane for lane in ['veterinary', *first] if lane in tags), None)
    if lead:
        ordered = [lead] + [lane for lane in ordered if lane != lead]
    lane = ordered[0] if ordered else FALLBACK
    return {'lane': lane, 'group': LANES[lane][0], 'tags': ordered or [FALLBACK]}


def assign(selected_lane, rules):
    """Final themes: a selection lane wins as primary; the rule tags stay as secondary tags."""
    if selected_lane in LANES:
        tags = [selected_lane] + [t for t in rules['tags'] if t not in (selected_lane, FALLBACK)]
        return {'lane': selected_lane, 'group': LANES[selected_lane][0], 'tags': tags, 'basis': 'selection'}
    return {**rules, 'basis': 'rules'}


def scheme():
    """The category scheme for the browser: groups in display order, each with its lanes."""
    return [{'group': key, 'name': name, 'description': about,
             'lanes': [{'lane': lane, 'name': label} for lane, (group, label) in LANES.items() if group == key]}
            for key, (name, about) in GROUPS.items()]
