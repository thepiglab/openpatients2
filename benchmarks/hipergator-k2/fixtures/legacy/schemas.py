"""Versioned extraction schemas. Required nullable fields distinguish missing from false.

These are research-oriented source schemas, NOT FHIR resources or OMOP tables.
Terminology codes are deliberately not guessed by the generator.
"""
from __future__ import annotations

import copy
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Evidence(StrictModel):
    quote: str = Field(min_length=1, max_length=1200,
                       description="Exact contiguous source text; include qualifiers and negation.")
    source_section: str | None = Field(description="Heading actually present in the source, otherwise null.")


class TimeMention(StrictModel):
    text: str | None = Field(description="Verbatim temporal expression. Never invent dates or durations.")
    relation: Literal["at", "before", "after", "during", "since", "until", "unknown"]
    anchor: str | None = Field(description="Explicit anchor such as surgery or admission; not publication date.")
    date_iso: str | None = Field(description="YYYY-MM-DD only if explicitly present as an unshifted full date; otherwise null.")


Subject = Literal["index_patient", "family_member", "mother", "fetus", "newborn", "other", "unknown"]
Assertion = Literal["present", "absent", "possible", "conditional", "unknown"]
Temporality = Literal["current", "historical", "future", "unknown"]
EvidenceList = Annotated[list[Evidence], Field(min_length=1)]


class Fact(StrictModel):
    subject: Subject
    assertion: Assertion = Field(description="Whether this fact is explicitly affirmed, negated, or uncertain; not test positivity.")
    temporality: Temporality
    time: TimeMention
    evidence: EvidenceList


class Section(StrictModel):
    coverage: Literal["complete", "limited", "not_applicable"] = Field(
        description="complete means this task reviewed the supplied source, NOT that the patient's history is complete.")
    limitations: list[str]
    documentation_status: Literal["documented", "not_documented", "explicitly_unknown", "not_applicable"] = Field(
        description="documented includes positive, negative, possible and historical facts. Silence is not_documented, never a clinical negative.")
    documentation_evidence: list[Evidence] = Field(
        description="Exact source support for explicitly_unknown/not_applicable; empty for not_documented. Fact evidence stays on each item.")

    @model_validator(mode="after")
    def documentation_consistency(self):
        collections = [getattr(self, name) for name in ("items", "tumors", "biomarkers", "treatments") if hasattr(self, name)]
        has_facts = any(collections)
        if hasattr(self, "case_kind"):
            has_facts = bool(getattr(self, "evidence", []))
        if self.documentation_status == "documented" and not has_facts:
            raise ValueError("documented requires a source-supported fact, including explicit negatives; otherwise use not_documented")
        if self.documentation_status != "documented" and has_facts:
            raise ValueError("Nonempty clinical collections require documentation_status=documented")
        if self.documentation_status in {"explicitly_unknown", "not_applicable"} and not self.documentation_evidence:
            raise ValueError("Explicit unknown/not-applicable requires exact documentation_evidence")
        if self.documentation_status == "not_documented" and self.documentation_evidence:
            raise ValueError("Source silence must not have invented documentation_evidence")
        if self.coverage == "limited" and not self.limitations:
            raise ValueError("Limited extraction requires a stated limitation")
        if self.coverage == "not_applicable" and self.documentation_status != "not_applicable":
            raise ValueError("coverage=not_applicable must have supported documentation_status=not_applicable")
        return self


class CaseContext(Section):
    case_kind: Literal["clinical_case", "clinical_record", "exam_vignette", "synthetic_vignette",
                       "cadaveric_anatomic", "animal", "in_vitro", "review_without_case",
                       "multi_patient", "unknown"]
    index_subject_description: str | None
    species: Literal["human", "nonhuman", "unknown"]
    multiple_index_patients: bool | None
    subject_state: Literal["living_at_presentation", "deceased_during_course", "cadaver", "unknown"]
    care_setting: str | None
    chief_complaint: str | None
    evidence: list[Evidence]


class Demographic(Fact):
    attribute: Literal["age_at_presentation", "age_at_diagnosis", "age_other",
                       "documented_sex", "sex_assigned_at_birth", "gender_identity",
                       "race_as_documented", "ethnicity_as_documented", "language"]
    text_value: str | None
    numeric_value: float | None
    unit: str | None

    @model_validator(mode="after")
    def age_uses_time_units(self):
        if self.attribute.startswith("age_") and self.numeric_value is not None:
            if self.numeric_value < 0:
                raise ValueError("Age cannot be negative")
            units = {"year", "years", "yr", "yrs", "y", "month", "months", "mo",
                     "week", "weeks", "wk", "day", "days", "d", "hour", "hours", "hr", "minute", "minutes", "min"}
            if self.unit is not None and self.unit.strip().lower() not in units:
                raise ValueError("Age requires a time unit; body weight and other measurements belong in observations")
        return self


class Demographics(Section):
    items: list[Demographic]


class Condition(Fact):
    verification_status: Literal["confirmed", "provisional", "differential", "refuted", "unconfirmed", "unknown"] = Field(
        description="Documented diagnostic certainty; never infer confirmation from treatment, a billing label, or a symptom alone.")
    name: str
    clinical_status: Literal["active", "resolved", "remission", "recurrence", "inactive", "unknown"]
    role: Literal["primary_diagnosis", "comorbidity", "complication", "history", "differential", "unknown"]
    body_site: str | None
    laterality: Literal["left", "right", "bilateral", "midline", "unknown"]
    severity: str | None
    diagnostic_basis: str | None


    @model_validator(mode="after")
    def refutation_consistency(self):
        if self.verification_status == "refuted" and self.assertion != "absent":
            raise ValueError("A refuted diagnosis must be a negated fact; preserve earlier suspicion separately")
        return self


class Conditions(Section):
    items: list[Condition]


class SymptomFunction(Fact):
    name: str
    kind: Literal["symptom", "sign", "functional_status", "performance_score", "mental_status", "quality_of_life"]
    text_value: str | None
    numeric_value: float | None
    unit: str | None
    body_site: str | None
    severity: str | None
    activity_context: str | None


class SymptomsFunction(Section):
    items: list[SymptomFunction]


class Medication(Fact):
    name: str = Field(description="Drug or regimen name exactly as stated; do not invent its ingredients.")
    action: Literal["current", "started", "administered", "ordered", "planned", "held", "stopped", "declined", "historical", "unknown"]
    ingredient_as_documented: str | None
    dose_text: str | None
    dose_value: float | None
    dose_unit: str | None
    route: str | None
    frequency: str | None
    duration_text: str | None
    indication: str | None
    regimen: str | None


class Medications(Section):
    items: list[Medication]


class Allergy(Fact):
    scope: Literal["specific_substance", "drug_allergies", "all_allergies", "unknown"] = Field(
        description="NKDA negates known drug allergies only; it does not negate food/environmental allergies.")
    substance: str
    category: Literal["drug", "food", "environmental", "other", "unknown"]
    reaction: str | None
    reaction_type: Literal["allergy", "intolerance", "adverse_effect", "unknown"]
    severity: str | None


    @model_validator(mode="after")
    def scoped_allergy_consistency(self):
        if self.scope == "drug_allergies" and self.category != "drug":
            raise ValueError("Drug-only allergy scope must retain category=drug")
        return self


class Allergies(Section):
    items: list[Allergy]


class ProcedureDevice(Fact):
    name: str
    kind: Literal["procedure", "device", "nonpharmacologic_therapy"]
    action: Literal["completed", "ordered", "planned", "declined", "cancelled", "device_present", "removed", "historical", "unknown"]
    body_site: str | None
    laterality: Literal["left", "right", "bilateral", "midline", "unknown"]
    indication: str | None
    finding: str | None
    complication: str | None


class ProceduresDevices(Section):
    items: list[ProcedureDevice]


class Observation(Fact):
    result_absent_reason: Literal["pending", "not_performed", "not_reported", "not_applicable", "unknown"] | None = Field(
        description="null when a result is provided. A pending/unreported result is not a negative or normal result.")
    name: str
    kind: Literal["laboratory", "vital", "imaging", "pathology", "microbiology", "physiologic", "other"]
    status: Literal["resulted", "ordered", "planned", "pending", "not_performed", "historical", "unknown"]
    text_value: str | None
    numeric_value: float | None
    comparator: Literal["=", "<", "<=", ">", ">=", "unknown"]
    unit: str | None
    flag: Literal["normal", "high", "low", "positive", "negative", "indeterminate", "unknown"]
    reference_range_text: str | None
    specimen: str | None
    method: str | None
    body_site: str | None


    @model_validator(mode="after")
    def result_state_consistency(self):
        has_value = self.numeric_value is not None or self.text_value is not None or self.flag != "unknown"
        if has_value and self.result_absent_reason is not None:
            raise ValueError("A reported result cannot have result_absent_reason")
        if self.status in {"ordered", "planned", "pending", "not_performed"} and has_value:
            raise ValueError("An unresulted observation cannot carry a result; split distinct encounters/tests")
        if not has_value and self.result_absent_reason is None:
            raise ValueError("An observation without a result must document its absence reason")
        return self


class Observations(Section):
    items: list[Observation]


class Tumor(Fact):
    tumor_ref: str = Field(pattern=r"^t[1-9][0-9]*$", description="Local tumor ID within this section; never identifies a person.")
    name: str
    primary_site: str | None
    histology: str | None
    grade: str | None
    laterality: Literal["left", "right", "bilateral", "midline", "unknown"]
    disease_extent: Literal["in_situ", "localized", "regional", "distant_metastatic", "hematologic", "unknown"]
    metastatic_sites: list[str]
    stage_group: str | None
    stage_system: str | None
    stage_edition: str | None
    stage_context: Literal["clinical", "pathologic", "post_neoadjuvant", "recurrent", "unknown"]
    t_category: str | None
    n_category: str | None
    m_category: str | None
    disease_status: Literal["active", "remission", "recurrence", "progression", "resolved", "unknown"]


class Biomarker(Fact):
    tumor_ref: str | None
    name: str
    gene: str | None
    alteration: str | None
    origin: Literal["somatic", "germline", "unknown"]
    interpretation: Literal["positive", "negative", "wild_type", "pathogenic", "likely_pathogenic", "vus", "indeterminate", "unknown"]
    text_value: str | None
    numeric_value: float | None
    unit: str | None
    assay: str | None
    specimen: str | None


class CancerTreatment(Fact):
    tumor_ref: str | None
    name: str
    modality: Literal["surgery", "radiotherapy", "systemic", "supportive", "other", "unknown"]
    action: Literal["given", "ongoing", "planned", "stopped", "declined", "unknown"]
    line_of_therapy: int | None
    intent: Literal["curative", "palliative", "unknown"]
    setting: Literal["neoadjuvant", "adjuvant", "definitive", "maintenance", "salvage", "unknown"]
    response: str | None


class Oncology(Section):
    tumors: list[Tumor]
    biomarkers: list[Biomarker]
    treatments: list[CancerTreatment]

    @model_validator(mode="after")
    def check_references(self):
        refs = [x.tumor_ref for x in self.tumors]
        if len(set(refs)) != len(refs):
            raise ValueError("Duplicate tumor_ref; references must uniquely identify tumors.")
        for fact in [*self.biomarkers, *self.treatments]:
            if fact.tumor_ref is not None and fact.tumor_ref not in refs:
                raise ValueError("Unresolved tumor_ref. Use null when the source does not link a tumor.")
        return self


class FamilyGenetic(Fact):
    name: str
    kind: Literal["family_condition", "genetic_test", "inheritance", "consanguinity"]
    relative: str | None
    age_at_diagnosis_text: str | None
    gene: str | None
    variant: str | None
    origin: Literal["germline", "somatic", "unknown"]
    interpretation: str | None
    inheritance: str | None


class FamilyGenetics(Section):
    items: list[FamilyGenetic]


class ReproductivePerinatal(Fact):
    event: Literal["pregnancy", "gravidity_parity", "delivery", "gestational_age", "newborn", "congenital", "fertility", "lactation", "other"]
    text_value: str
    numeric_value: float | None
    unit: str | None
    related_subject: str | None
    gestational_age_weeks: float | None
    birth_weight_text: str | None
    outcome: str | None


class ReproductivePerinatalSection(Section):
    items: list[ReproductivePerinatal]


class SocialExposure(Fact):
    domain: Literal["tobacco", "alcohol", "substance", "occupation", "environment", "housing", "food_security",
                    "social_support", "exercise", "diet", "travel", "sexual_history", "access_to_care", "other"]
    name: str
    status: Literal["current", "former", "never", "passive", "unknown"]
    amount_text: str | None
    frequency: str | None
    duration_text: str | None
    pack_years: float | None = Field(description="Only when explicitly reported, never calculated by the model.")


class SocialExposures(Section):
    items: list[SocialExposure]


class Outcome(Fact):
    event: Literal["death", "discharge", "recovery", "improvement", "deterioration", "recurrence", "readmission",
                   "complication", "follow_up", "lost_to_follow_up", "censored", "functional_outcome",
                   "live_birth", "stillbirth", "termination", "other"]
    text_value: str
    related_condition: str | None
    disposition: str | None
    follow_up_duration_text: str | None
    cause_as_documented: str | None


class Outcomes(Section):
    items: list[Outcome]


class CarePlan(Fact):
    action_text: str
    plan_type: Literal["follow_up", "referral", "monitoring", "counseling", "goals_of_care", "other"]
    target: str | None
    responsible_specialty: str | None
    urgency: str | None
    action_status: Literal["planned", "recommended", "scheduled", "declined", "completed", "unknown"]


class CarePlans(Section):
    items: list[CarePlan]


TASK_MODELS: dict[str, type[StrictModel]] = {
    "case_context": CaseContext,
    "demographics": Demographics,
    "conditions": Conditions,
    "symptoms_function": SymptomsFunction,
    "medications": Medications,
    "allergies": Allergies,
    "procedures_devices": ProceduresDevices,
    "observations": Observations,
    "oncology": Oncology,
    "family_genetics": FamilyGenetics,
    "reproductive_perinatal": ReproductivePerinatalSection,
    "social_exposures": SocialExposures,
    "outcomes": Outcomes,
    "care_plans": CarePlans,
}


def wire_schema(task: str) -> dict:
    """Remove annotations, not validation constraints; schemas never use uniqueItems.

    Descriptions are delivered as a compact field guide in the task suffix instead.
    The full Pydantic model remains the application-side validation authority.
    """
    schema = copy.deepcopy(TASK_MODELS[task].model_json_schema())
    def clean(value):
        if isinstance(value, dict):
            for key in ["title", "description", "examples", "default"]:
                value.pop(key, None)
            for child in value.values():
                clean(child)
        elif isinstance(value, list):
            for child in value:
                clean(child)
    clean(schema)
    return schema


def field_guide(task: str) -> str:
    """Name/types/enums per object once: not the giant combined schema in every prompt."""
    schema = TASK_MODELS[task].model_json_schema()
    def describe(value):
        if "$ref" in value:
            return value["$ref"].rsplit("/", 1)[-1]
        if "anyOf" in value:
            return "|".join(describe(x) for x in value["anyOf"])
        if "enum" in value:
            return "/".join(str(x) for x in value["enum"])
        if "const" in value:
            return str(value["const"])
        if value.get("type") == "array":
            return "list[" + describe(value["items"]) + "]"
        return value.get("type", "object")
    lines = []
    for name, obj in [("ROOT", schema), *schema.get("$defs", {}).items()]:
        if "properties" in obj:
            lines.append(name + ": " + "; ".join(f"{k}={describe(v)}" for k, v in obj["properties"].items()))
    return "\n".join(lines)


def provider_schema(schema: dict, profile: str = 'standard') -> dict:
    """Explicit wire-only projection; application constraints remain authoritative.

    Cohere documents no numeric/array/string bounds or anchored patterns.
    Prompts still contain the full contract, and returned data must satisfy it.
    Never remove properties, required keys, types, enums or reference structure.
    """
    result=copy.deepcopy(schema)
    if profile=='standard':return result
    if profile!='cohere':raise ValueError('Unknown provider schema profile')
    omitted={'minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf',
             'minItems','maxItems','uniqueItems','minLength','maxLength','pattern'}
    def visit(node):
        if not isinstance(node,dict):return
        for key in omitted:node.pop(key,None)
        for key in ('properties','$defs','definitions','patternProperties'):
            for child in node.get(key,{}).values():visit(child)
        for key in ('items','additionalProperties','not'):
            visit(node.get(key))
        for key in ('anyOf','allOf','oneOf','prefixItems'):
            for child in node.get(key,[]):visit(child)
    visit(result)
    return result
