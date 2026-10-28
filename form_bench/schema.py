"""The patient record every screen is mapped to, the same for any EMR.

Sections follow FHIR resources (Patient, Observation, AllergyIntolerance, Condition, MedicationStatement).
Every section is a set of items that share attributes: a vital sign has a value and an abnormal flag, a lab
test has a value, unit, range, flag and date, a patient detail has only a value. A screen field holds one
attribute of one item, in one unit when the value has units, or nothing the record fills. The mapper
(mapping.py) chooses among these; nothing here knows any EMR.
"""

from labs import PANELS

SECTIONS = {
    "patient": "Registering a patient: name, date of birth, sex, address and contact details",
    "vitals": "Vital signs measured at a visit: weight, height, blood pressure, pulse, temperature",
    "lab_results": "Entering laboratory test results: each test's value, unit, reference range and flag",
    "allergy": "Recording an allergy or intolerance",
    "condition": "Recording a medical problem or diagnosis",
    "medication": "Recording a medication the patient takes",
    "other": "Something else, such as a search, a configuration or a report screen",
}

ITEMS = {
    "patient": {
        "given": "the patient's current first (given) name, not a previous or birth name",
        "middle": "the patient's current middle name, not a previous or birth name",
        "family": "the patient's current last (family) name, not a previous or birth name",
        "birth_date": "the patient's date of birth", "sex": "the patient's sex",
        "street": "the patient's street address now: house number and street",
        "unit": "the apartment, suite or unit of the patient's address now, or its second line",
        "city": "the city or town the patient lives in now", "state": "the state the patient lives in now",
        "postal_code": "the ZIP or postal code of the patient's address now"},
    "vitals": {
        "weight": "body weight", "height": "height or length", "bp_systolic": "systolic blood pressure",
        "bp_diastolic": "diastolic blood pressure", "pulse": "pulse or heart rate",
        "respiration": "respiratory rate", "temperature": "body temperature",
        "temp_site": "where on the body the temperature was taken", "oxygen_saturation": "oxygen saturation"},
    "lab_results": {code: name for _, _, parts in PANELS.values() for code, name, *_ in parts} | {
        "report_date": "the lab report as a whole: when it was issued",
        "collection_date": "the lab report as a whole: when the sample was collected",
        "report_status": "the lab report as a whole: its status (final, preliminary, corrected)"},
    "allergy": {"substance": "what the patient is allergic to", "reaction": "the reaction it causes",
                "severity": "how severe the reaction is", "onset": "when the allergy began"},
    "condition": {"name": "the problem or diagnosis", "onset": "when it began", "end": "when it ended"},
    "medication": {"name": "the medication", "dose": "the dose and how to take it", "start": "when it was started",
                   "end": "when it was stopped"},
    "other": {},
}

ATTRIBUTES = {
    "vitals": {"value": "the value itself: the reading, or what was chosen, such as where it was taken",
               "abnormal": "whether the reading is abnormal"},
    "lab_results": {"value": "the result value", "unit": "the unit the result is reported in",
                    "range": "the reference range", "abnormal": "whether the result is abnormal (high, low)",
                    "date": "when this one result was observed", "end_date": "when this one result's period ended",
                    "code": "the test's code", "name": "the test's name"},
}
VALUE = "value"  # a section without attributes holds only values
# Items about a whole entry rather than one of its parts (the report, not one test): they hold only a value.
WHOLE = {"lab_results": {"report_date", "collection_date", "report_status"}}

# Units a field may expect a value in, when the record converts between them (pounds and kilograms).
UNITS = {"lb": "pounds", "kg": "kilograms", "in": "inches", "cm": "centimetres", "F": "degrees Fahrenheit",
         "C": "degrees Celsius"}
RECORD_UNITS = {"weight": "lb", "height": "in", "temperature": "F"}  # the units record values are kept in
