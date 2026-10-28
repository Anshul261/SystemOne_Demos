"""Lab panels: the tests Synthea reports, and OpenEMR configured to take their results.

Synthea reports a basic metabolic panel, lipids, a blood count and HbA1c by LOINC code, some tests under two
codes (glucose in blood, 2339-0, and in serum or plasma, 2345-7). PANELS keys each component by one code and
lists the others it may come under.

setup(page, base) configures OpenEMR once, through its own admin screens (its compendium loader takes only
three named labs and keeps no units or ranges): an address-book lab service, a procedure provider for it, and
a group of orders whose discrete results carry LOINC codes, units and ranges. The ids go to data/.
"""

import json
import re
from pathlib import Path

FILE = Path(__file__).parent / "data" / "openemr_labs.json"
# code: (name, LOINC panel code, [(component code, name, unit, range, other codes)])
PANELS = {
    "bmp": ("Basic metabolic panel", "51990-0", [
        ("2345-7", "Glucose", "mg/dL", "70-99", ["2339-0"]),
        ("3094-0", "Urea nitrogen (BUN)", "mg/dL", "7-20", ["6299-2"]),
        ("2160-0", "Creatinine", "mg/dL", "0.6-1.3", ["38483-4"]),
        ("17861-6", "Calcium", "mg/dL", "8.6-10.3", ["49765-1"]),
        ("2951-2", "Sodium", "mmol/L", "135-145", ["2947-0"]),
        ("2823-3", "Potassium", "mmol/L", "3.5-5.1", ["6298-4"]),
        ("2075-0", "Chloride", "mmol/L", "98-107", ["2069-3"]),
        ("2028-9", "Carbon dioxide, total", "mmol/L", "22-29", ["20565-8"]),
        ("33914-3", "eGFR", "mL/min/1.73m2", ">60", [])]),
    "lipid": ("Lipid panel", "57698-3", [
        ("2093-3", "Cholesterol, total", "mg/dL", "<200", []),
        ("2571-8", "Triglycerides", "mg/dL", "<150", []),
        ("18262-6", "LDL cholesterol", "mg/dL", "<100", []),
        ("2085-9", "HDL cholesterol", "mg/dL", ">40", [])]),
    "cbc": ("Complete blood count", "58410-2", [
        ("6690-2", "White blood cells", "10*3/uL", "4.5-11.0", []),
        ("789-8", "Red blood cells", "10*6/uL", "4.2-5.9", []),
        ("718-7", "Hemoglobin", "g/dL", "12.0-17.5", []),
        ("4544-3", "Hematocrit", "%", "36-52", []),
        ("787-2", "MCV", "fL", "80-100", []),
        ("785-6", "MCH", "pg", "27-33", []),
        ("786-4", "MCHC", "g/dL", "32-36", []),
        ("777-3", "Platelets", "10*3/uL", "150-400", [])]),
    "a1c": ("Hemoglobin A1c", "4548-4", [("4548-4", "Hemoglobin A1c", "%", "4.0-5.6", [])]),
}
LAB = "Bench Lab"
UNITS = {"mg/dL": "mg_dl", "g/dL": "g_dl", "%": "percent", "fL": "fl", "pg": "pg", "10*3/uL": "thous_cu_mm",
         "10*6/uL": "mil_cu_mm"}  # OpenEMR's unit list; others (mmol/L) are "oth"
CODES = {other: code for _, _, parts in PANELS.values() for code, *_, others in parts for other in [code, *others]}


def save(page) -> None:
    """Submit an OpenEMR admin form with its Save button, as the server checks which button sent it."""
    with page.expect_navigation():
        page.evaluate("f => f.requestSubmit(f.querySelector('[name=form_save]'))", page.locator("form").first.element_handle())


def children(page, base: str, parent: int) -> dict[str, int]:
    """The procedure types under a node of OpenEMR's order tree, by name: read from the HTML that the tree's own
    loader script carries, one row per node."""
    script = page.request.get(f"{base}/interface/orders/types_ajax.php?id={parent}&order=0&labid=0").text()
    html = json.loads(re.search(r'\.html\(("(?:[^"\\]|\\.)*")\)', script)[1])
    found = {}
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        node = re.search(r"handleNode\((\d+),", row)
        name = re.sub(r"<[^>]+>|^\s*[|+-]\s", "", re.search(r"<td[^>]*>(.*?)</td>", row, re.S)[1]).strip(" |+-")
        if node:
            found[name] = int(node[1])
    return found


def add_type(page, base: str, parent: int, kind: str, name: str, lab_id: int, code: str = "", standard: str = "",
             unit: str = "", rng: str = "", seq: int = 0) -> None:
    """One node of the order tree. OpenEMR 8.4.1 creates a node with its values one column off: its INSERT puts
    `parent = ?` first but binds the parent last, so the name field's value becomes the parent. So the node is
    created with the parent id typed as its name, then saved again through its edit form, whose UPDATE binds
    every field right. The form hides fields that do not apply to a kind, so values are set directly: this is
    configuration, not the entry under test."""
    before = set(children(page, base, parent).values())
    page.goto(f"{base}/interface/orders/types_edit.php?typeid=0&parent={parent}")
    page.evaluate("p => { document.querySelector('[name=form_name]').value = p; }", str(parent))
    save(page)
    (node,) = set(children(page, base, parent).values()) - before
    page.goto(f"{base}/interface/orders/types_edit.php?typeid={node}&parent={parent}")
    values = {"form_procedure_type": kind, "form_procedure_type_name": "laboratory_test", "form_name": name,
              "form_seq": str(seq), "form_lab_id": str(lab_id), "form_procedure_code": code,
              "form_standard_code": standard, "form_units": UNITS.get(unit, "oth") if unit else "", "form_range": rng}
    page.evaluate("""values => { for (const [name, value] of Object.entries(values)) {
        const field = document.querySelector(`[name=${name}]`);
        if (field.tagName === 'SELECT' && ![...field.options].some(o => o.value === value)) continue;
        field.value = value; } }""", values)
    save(page)


def setup(page, base: str) -> dict:
    """Configure OpenEMR for the panels once; later calls return the remembered ids."""
    if FILE.exists():
        return json.loads(FILE.read_text())
    page.goto(f"{base}/interface/usergroup/addrbook_list.php?form_organization={LAB}")
    if LAB not in page.locator("body").inner_text():
        page.goto(f"{base}/interface/usergroup/addrbook_edit.php")
        page.select_option("[name=form_abook_type]", "ord_lab")
        page.fill("[name=form_organization]", LAB)
        save(page)
    page.goto(f"{base}/interface/orders/procedure_provider_list.php")
    if LAB not in page.locator("body").inner_text():
        token = re.search(r'csrf_token_form: "(\w+)"', page.content())[1]
        page.goto(f"{base}/interface/orders/procedure_provider_edit.php?ppid=0&csrf_token_form={token}")
        page.select_option("[name=form_name]", label=LAB)
        page.select_option("[name=form_protocol]", "DL")
        page.select_option("[name=form_direction]", "B")
        save(page)
        page.goto(f"{base}/interface/orders/procedure_provider_list.php")
    lab_id = int(re.search(rf"<td>{LAB}</td>.*?doedclick_edit\(\D*(\d+)", page.content(), re.S)[1])
    if LAB not in children(page, base, 0):
        add_type(page, base, 0, "grp", LAB, lab_id)
    group = children(page, base, 0)[LAB]
    for key, (name, code, parts) in PANELS.items():
        if name not in children(page, base, group):
            add_type(page, base, group, "ord", name, lab_id, code=code, standard=f"LOINC:{code}")
        order = children(page, base, group)[name]
        existing = children(page, base, order)
        for seq, (part, part_name, unit, rng, _) in enumerate(parts):
            if part_name not in existing:
                add_type(page, base, order, "res", part_name, lab_id, code=part, standard=f"LOINC:{part}",
                         unit=unit, rng=rng, seq=seq)
    ids = {"lab_id": lab_id, "group": group,
           "orders": {key: children(page, base, group)[name] for key, (name, _, _) in PANELS.items()}}
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(ids, indent=1))
    return ids


def place_order(page, base: str, panel: str) -> None:
    """Order one panel at the current patient's current visit, so its results can be entered. Setup, so the
    order form's fields are set by name."""
    ids = json.loads(FILE.read_text())
    name, code, _ = PANELS[panel]
    page.goto(f"{base}/interface/forms/procedure_order/new.php")
    page.evaluate("""v => { for (const [name, value] of Object.entries(v)) document.querySelector(`[name="${name}"]`).value = value; }""",
                  {"form_lab_id": str(ids["lab_id"]), "form_order_status": "pending", "form_proc_type[0]": str(ids["orders"][panel]),
                   "form_proc_type_desc[0]": name, "form_proc_code[0]": code, "form_procedure_type[0]": "laboratory_test"})
    with page.expect_navigation():
        page.evaluate("f => f.requestSubmit(f.querySelector('#bn_save_exit'))", page.locator("form").first.element_handle())


def clear_orders(page, base: str) -> None:
    """Delete the current visit's lab orders, so the results screen shows only those placed for this note."""
    page.goto(f"{base}/interface/patient_file/encounter/forms.php")
    for link in re.findall(r"delete_form\.php\?formname=procedure_order(?:&amp;|&)[^'\"]+", page.content()):
        page.goto(f"{base}/interface/patient_file/encounter/{link.replace('&amp;', '&')}")
        with page.expect_navigation():
            page.evaluate("document.forms.my_form.submit()")


def panels_in(record: dict) -> list[str]:
    """The panels with at least one result in a record."""
    return [key for key, (_, _, parts) in PANELS.items() if any(code in record for code, *_ in parts)]
