# Dense annotation review handoff

Updated 2026-10-08. This is an operational handoff, not the progress tracker.
`data/dense_clinical_cases_v1/article_checklist.json` is the canonical 500-ID
checklist. Export validates source/target/receipt hashes before deriving each
completion checkbox. Draft presence or validator success is never review.
These are AI reference annotations, not physician gold or measured recall.

## Active work — latest continuation

Canonical export: **78/500 completed**,112 drafts,84 parent-source/full-schema,
77 pixel reviews plus2 no-figure inventories,10817 reviewed representations.
JSON checklist is authoritative; never mark completed from drafts/validation.

- Annotator1: PMC12754322.1 mesenteric2cases final full target/visual audit; parent all27sourceunits/all4actualJPEGs read.85representations,28sections,33events25edges pending parentfulltarget. Next PMC12884857.1 periopPE: parent all25sourceunits/126initialfacts/14fields/112cites/11events10edges/4summaries/all6actualJPEGs read. Requested mergeHTNduplicate, provisionalPEpolarity, timeline links for f103–126, units/methods, noactualthrombectomy. Fig5A green caliper looked30.02mm, ask independent verification.
- Annotator2: PMC12999700.1 osteomalacia new draft. Parent all82sourceunits read; raw3tables/all5actualJPEGs personally read. Source conflicts in vitaminD ng/L vsng/mL, osteocalcinmg/dLvsng/mL, crosslaps4082vs4.082; PTH95vs94/BALP105vs106/osteocalcin116.6vs116.5; scintigraphy multi-site b16vsright4thribonly b61; 4mo then one-month-later(sixmo);9moVitD42vsTable8mo; bodylaterlabs/BMD vsmonth39; abstract−2.6at26mo vsfinalpost-bisphosphonate. No bonebiopsy, FGF23notmeasured, PINP1/TRAP5bunavailable. Fig2 actualpelvis contradictsshoulders/kneecaption; Fig5phosphatemg/mLvsTablemg/dL. Reduced sun/FGF23 mechanisms are postulates.
- Annotator3: PMC12831504.1 femoralherniaTAPP2cases. Parent all40units/all4actualJPEGs read, raw8rowtable personally read. Agent enriching76draft. Index73F, brieforiginalcontemporaneous secondcase sex/ageunknown. Body0.5cmperforation vscaptionimpendingserosaldefect andpixelPerforationorSerosalinjury source layers unresolved; no invented evolution. ICGoptional, followup/imagingplanned notdone. Actualfig1 A/B10/5/3cm labels;Fig2 A coronal+B/Caxial,Cwhirl arrow;Fig3 Aredcircle/Bpubicbonearrows;Fig4backgroundalgorithm allunitsunstated for labthresholds.

Newly completed — DO NOT REDO:
- PMC12774291.1 ASE2patients:207representations/28sections/52events55edges/3actualassets12panels. Allparent andagentcanonical signatures.
- PMC13096863.1 D-Jstent2patients:159reps/28sections/87cites/52events45edges/9summaries/6actualJPEGs. Removed duplicate discharge, preserved source route/stoneposition conflicts and untimed followup. Allreceipts.
- PMC12746974.1 bottlecap:61reps/14sections/14events14edges/3summaries/6JPEGs7scopes.18vs20cm unresolved, F2single sagittal, no intox/intentinference. Allreceipts.
- PMC12800717.1 twins:101reps/28sections/105cites/28events21edges/5summaries/4JPEGs. By-day7/by-month3 bounds retained;BMIpresentation;allfiguresindexpatient frombodylinks. Allreceipts.
- PMC12812266.1 functionalRYGBreversal:126reps/14sections/111cites/20events19edges/3summaries/32rawtablecells/2JPEGs3scopes8visualcites. Initialalbumin2.5vs2.8 reviewonly, malformedtotalproteing/d andscaledplatelets retained; presurgpanelunanchored to1monthimprovement;postsurgdrawnoexacttime. Allreceipts.
- PMC12802775.1 OSA/PFO4patients:283reps/56sections/224initialcites+finalrecommendationclauses/63events62edges/8summaries/raw2tables/2JPEGs6regions43visualcites. Finalcasekindmulti/careplans/maskchronology/p4handedness corrections personallyread. Allreceipts.
- PMC12781543.1 excludedstomachperforation:59reps/14sections/43cites/11events11edges/4summaries/2JPEGs6panels13visualcites. Seven explicit test/examination alternate representations checked. No omentalpatch, no assumedunitdenominator/IBPdrug, no falseperitonealnegative. Allreceipts.

Rehab PMC12310273.1 COMPLETE:288representations/14sections/19events28edges3summaries/all118citations/raw2tables/actual2JPEGs checked; all4finaldrug/exercise/stepgoal/KEF percentage refinements read and signed. Agentcanonical confirmed.
Goiter PMC12834363.1 COMPLETE:143representations(142accepted)/151citations/14sections/21events20edges3summaries/raw24rowlabs/actual7JPEGs7scopes34cites checked. EBL50mm and paramedian/pyramidal wording preserved, familyhistory untimed, no invented prior thyroidectomyextent/compressive symptoms. Allparent and agentcanonical done.
Fungal PMC7619249.1 COMPLETE:266representations(255accepted)/84sections/84events56edges6summaries/actual6panelJPEG8cites/raw6coltable checked.19namedassayexecutionalternates plusCase2debridement finaldelta read. p5biopsybody-site unspecified, p4late indication suspected, MPA raw preserved. Maintext/table/immunosuppression/timing/recurrence/pooledpathology conflicts retained. Allparent receipts signed, agentcanonicalrefreshrequested.

Newly completed and signed this continuation:
- PMC12728242.1 hepatic cyst: all85sourceunits/bothrawtables/216facts(211accepted5reviewonly)/14sections/37events38edges3summaries/2actualJPEGs3visualscopes7cites personally reviewed. f015 no false negative prior imaging; same-day intake/mobility separated from untimed LFTrecovery. Both agent canonical and all parent receipts done.
- PMC12747464.1 pump: all30sourceunits/rawtable/75facts(71accepted4reviewonly)/14sections/21events20edges3summaries/3actualAGPs12chartregions81printedreadings read. Added three pre-pump timelineedges; no doseerror date; F1 includes5%percentile; AGP has no diagnosticimagingmodality. Allparentreceipts; agentcanonicalrefresh requested.
- EIASPMC12605513.1 main241facts/28sections/51events43edges6summaries/main2viewphoto complete; supplements actual1pagePDF/Chineseantibodyimage,all69SM1+4ST1typedfacts/121transcriptions reviewed. Bothparent+agentreceipts signed, complete. ST1 unassigned,SM1missingcells notzero, day/clock and maintext discrepancies retained.
- ThyroidPMC12805194.1 all143facts/14sections/20events18edges3summaries/3actualJPEGs; biliaryPMC12256025.1 all200facts/70sections/39events36edges5summaries/4actualJPEGs19scopes. Allpairedreviews done.

Typed supplement extension code/docs/tests completed by agent3; parent read code.97 focusedtests pass. Review binds payload+schemahash; export supplements separately. No workpending there.

Newly finished: woundPMC12595977.1 (75facts14sections23events24edges4summaries4images15citations),HepBPMC12586072.1 (487facts42sections59events52edges3summaries6rawtables,nofigures),adrenalPMC12812052.1 (334facts42sections40events6summaries5rawtables2actualMRIassets12visualcitations). All personally source/field/evidence/media reviewed,exactfinaldraftspromotedandallparentreceipts signed. Allagentcanonicalschemarefreshesconfirmed; HepBemptyvisualinventory parent+agentsigned. DO NOT REDO.

Finished since the preceding checkpoint: RAS PMC12825434.1 (124facts/36events/3assets), hiatal repair PMC12169754.1 (155facts/fivepatients/96plotestimates), ethylene glycol PMC12802722.1 in dense50 (312facts/42sections/45events/30chartreadings), rhabdomyolysis PMC12450272.1 (71facts/14sections/11events/actualTable1JPEG), ventral hernia PMC12854244.1 (129facts/14sections/16events/3assets). Every source, runtime field, summary, chronology and actual image was personally read; parent receipts signed and exact drafts promoted; canonical agent receipts refreshed. Do not redo these. Deflux clinical/stills finished but continuous video/audio remain unreviewed.

Parser fix completed: table-wrap raster alternatives now link to table rows/footnotes/crossrefs; excluded references/supplements aren't swept into figures. Parser1.6. Only rhabdo source refreshed, text/segments/hash/IDs unchanged. 54 focused tests passed. Journal acquisition/source-repairs.jsonl.

## Newly finished in current continuation

The items below record earlier completed reviews; use the JSON checklist for live counts.

PMC13587162.1 PHPT: all16source units/105finalrepresentations/evidence/14sections/23events25edges/2summaries/3actualassets6scopes12citations personally reviewed. Condensed paragraph-sized statements/evidence without dropping facts, distinguished administered CaSR agonist class from cinacalcet example, no inferred hypercalciuria, unknown-screening-specimen retained, scintigraphy phases overlap. All parent and agent canonical receipts signed.

PMC12811543.1 Watchman: all36source units/133rows (120included13review-only)/14sections22events19edges/summary/5actualassets13scopes18citations personally reviewed. Conflicting AF subtype, implant date, first DRT timing, INR target, recurrence date quarantined; generic AF2020 and Nov6,2023 resolution retained. All parent and agent canonical receipts signed.

PMC12790481.1 Deflux: all27source units/65representations/evidence/14sections17events17edges4summaries/twoactualJPEGs3scopes5citations personally reviewed; all parent clinical/schema/still receipts signed, exact draft promoted; agent canonical refresh requested. Video84.683sec5.9MB personally sampled85one-secondframes and4nativeframes; parent partial sidecar signed. Continuous motion/audio unreviewed; must not count as all-media complete. Six yellow arrows in EACH Figure1 panel confirmed.

PMC12531118.1 rehab and PMC12849829.1 Sjogren/RTA, PMC12702475.1 thoracobiliary and PMC12883935.1 buried balloon previously finished. Consult canonical checklist and receipts.

## Newly finished parent reviews

PMC12477045.1 PGNMID: parent source/schema/pixel signed,310remaining representations/42sections/41events30edges/6summaries/2assets4scopes. Deduplicated28 test/procedure rows, quarantined both p1 table/narrative numerical candidates, corrected stable vitals and temporal/polarity fields. Canonical agent receipt refresh requested.

PMC12875552.1 giant aneurysm and PMC13587219.1 cyclosporine: exact drafts promoted and all parent/agent canonical receipts signed. Giant108representations/14schemas/23events21edges/3assets; CsA120representations/14schemas/23events21edges/3assets. Giant source/pixel aneurysm measurements and CsA renal-status source inconsistencies retained. Kommerell PMC12229790.1 also fully completed after requested corrections.

PMC12747116.1 serial transplant: all83source units/raw serial table/350fact
representations/42clinical sections,41events37edges/3summaries,3actual images
and11panels checked. Corrected retrospective transplant/stable-function
anchors and Fig2B arrow direction; retained p3 fibrosis/IFTA caveat. Exact draft
promoted and all parent receipts signed. Agent canonical receipt refresh asked.

PMC12611315.1 tezepelumab/BLVR: all22units/raw6epoch table/144representations,
14schemas,13events12edges/summary and2actual assets/4panels checked. Source
unit/percentage/threshold/timing conflicts remain review-only. Exact draft
promoted and all parent receipts signed. Agent canonical refresh asked.

PMC12859224.1 FGN and PMC12283752.1 Mucor: all source, full schema, timeline,
summary and actual pixel reviews finished and signed; exact drafts promoted.
FGN has156representations, Mucor256 after6numeric BP components. Mucor chart
corrections inspected; source-specific immunostain/timing conflicts retained.
Agent canonical receipt refreshes requested (Mucor confirmed).

PMC12756086.1 HMPV: all150representations/full clinical target/source/timeline,
2actual CT assets and1page CARE PDF supplement personally checked; all parent
receipts signed and canonical agent schema confirmed. COQ8B PMC12863730.1
CARE PDF SD1 also personally inspected and parent-signed; prior source/schema/
pixel reviews remain valid. Both CARE documents are administrative with no
new clinical facts, and printed BY-NC-ND template badges remain separate from
article licenses. See SUPPLEMENT_DOCUMENT_REVIEW.md for strict PDF contract.

Earlier checkpoint outstanding items for Q-fever, small-cell tumor, OTSC,
IDH, DSP, cannulation, biloma, hepatopleural, MNFIL, stump, DVT, HAV and COVID
were resolved; consult current hash-bound receipts, not older handoff text.
PMC12998254.1 TTC still has unavailable echo video and must not count as
all-media complete despite completed source/schema/still-image reviews.

## Next parent-review queue

- EIASPMC12605513.1 supplements COMPLETED. All typed facts/transcriptions/pixels personally reviewed, both receipt roles signed; do not redo.
- Pump and hepatic COMPLETE; see above. Do not redo.
- RehabPMC12310273.1 parent70sourceunits/2rawtables/2actualJPEGsdone; agent3queued.80sF,T2D+periphneuropathyosteoporosisL1compression10mobeforeOR,10kgweightloss4mo/1moadmission/readmissionCOVIDbedrest.2yrOR2/wk80min+homeexercise;frailbaseline→prefrail3/6mo→robust9moonward. Fullserialtable values source. eGFRNOunit. HbA1c9.1baseline→8.7/7.6/7.8/8.5/8/7.5/7.2/7.8, reportmonth12highest onlypostisCGM. Insulindose/brandchanges3/6/12/14mo,mitiglinidefreq11mo,stopglimepiride8mo. Libre9mo→Libre2 18mo. Figure1 actualKEFsensor/belt/dynamometerprocedurephoto;Figure2protocoltimelineKEF0/3/6/9/12/24 HbA1cevery3mo. No supplemental assets.
- PMC12784445.1 and PMC12998254.1 Takotsubo already source/schema/stills signed but unavailable echo video remains incomplete. Deflux VID1 partial85frames +4nativeframes viewed, continuousmotion/audio unreviewed. Other outstanding ESD/mirror videos remain explicit gaps.

Read-only helpers: schema_view.py (use --patient and bounded ranges),
visual_view.py (one figure line plus deduplicated citations), review_view.py.
Use stdlib xml.etree.ElementTree for raw XML; lxml is not installed. PDF actual
page inspection uses bounded cached renders; neither downloading nor rendering
is itself inspection. Keep reviewing independently while agents correct deltas.

Refresh the public checklist and JSONL exports with:

```bash
PYTHONPATH=src .venv/bin/python benchmarks/dense500/export.py
```

## Next source already read ahead

PMC12774291.1 ASEtwo cases: parent all30sourceunits read,targets/media pending. p1 61Mright-handed, priorstroke57righttemporal/59lefttemporooccipital→quadrantanopiaamnesia→2molaterhallucinations/LEV500. FirstASE50minexam,2hMRI/DWIneg+oldFLAIR,IVdiazepam10+PHT250,additionalPHT6h doseunknown→10hpartialrecover/EEG1HzleftLPD,LEV1000,Day1SPECT↑lefttemporal/inferiorparietal,Day5EEGneg/langbaseline,followupSPECTDay6bodyvs7caption,DC7. Recurrence2y IVdiazepam20at1h,4hpartial,Day2complete+leftLPD,addgabapentin1200,DC14,7yfree/independent. p2 80Fright-handed,epilepsyLEV1000,lungadenocarcinoma4moosimertinib,brainmetsbodyrightcerebellar+leftoccipital vs captionvermis+bilatoccipital. SecondsGTCS,80minaphasiaalert,CTneg,IVLEV500 then literal didexperience convulsive seizure (do not addnot),Day1preservedADL,22hMRIneg/resolvedmets,EEGgeneralized,IVdiazepam5immediatepartial,LEV1500. Day2partialrecallDay1not0,Day4EEGneg+complete,Day5bilatparietalSPECT↑,rehab6→DC1mo. Recurrence2.5y noGTCS,90minIVdiazepam5beforeimagingimmediate,6hEEGbilatFPO,Day1SPECTbilatparietal+leftfrontal,addlacosamide100,Day2partial/Day3EEGneg/Day4complete/DC8,3yfreeinclcancer/independent. MostAEDfrequencies unstated. Rawb6xrefREF9 explicitlysamep1 previouscase→PMID33264155 DOI10.1097/WNN.0000000000000248. Preserveauthorlink,noexternalfactsorpriorcasenumberassumed.
