"""Contact snapshots and report enrichment. No network access in parsers."""
from __future__ import annotations

from collections import Counter
from datetime import date
import re
from urllib.parse import urljoin, urlsplit

from ..exceptions import ParseError
from ..models import (
    ClassParentContact, ContactInfo, ParentContact, SchoolContactPerson,
    SchoolContacts, StaffContact, StudentContact,
)
from .common import MONTH_LONG, MONTH_SHORT, make_soup


PHONE_LABELS = {
    "fastnettelefon": "home", "hjemmenr.": "home", "tlf hjem": "home",
    "mobiltelefon": "mobile", "elevens mobil": "mobile", "mobil prv.": "mobile",
    "arbejdstlf.": "work", "arbejdstelefon": "work", "tlf arbejde": "work",
    "mobil arb.": "work_mobile", "kontakttelefon": "contact",
    "kontakttelefon 2": "contact2", "telefon": "contact",
}


def text(element) -> str:
    return element.get_text(" ", strip=True) if element else ""


def value_text(element) -> str:
    return "\n".join(element.stripped_strings) if element else ""


def photo(element, base_url: str) -> tuple[str, bool | None]:
    if element is None or not element.get("src"):
        return "", None
    url = urljoin(base_url, element["src"])
    if urlsplit(url).scheme not in ("http", "https"):
        raise ParseError("Unsupported contact photo URL")
    path = urlsplit(url).path.lower()
    placeholder = True if "/skins/images/" in path or "/cssimages/" in path else (
        False if path.startswith("/file/") or "/files/" in path else None
    )
    return url, placeholder


def labeled_fields(block) -> dict[str, str]:
    result = {}
    for row in block.select(".sk-labeledtext"):
        label = row.select_one(".sk-labeledtext-caption")
        value = row.select_one(".sk-labeledtext-value")
        if label and value is not None:
            result[text(label).rstrip(":").strip().casefold()] = value_text(value)
    return result


def merge_info(info: ContactInfo, fields: dict[str, str], source: str) -> None:
    """Keep earlier nonempty values; never replace them with blank report cells."""
    for label, value in fields.items():
        if not value:
            continue
        if label in ("adresse", "e-mail", "email"):
            key = "address" if label == "adresse" else "email"
            if not getattr(info, key):
                setattr(info, key, value)
                info.sources[key] = source
        elif label in PHONE_LABELS:
            key = PHONE_LABELS[label]
            if not info.phones.get(key):
                info.phones[key] = value
                info.sources["phones." + key] = source


def birth_date(value: str) -> date | None:
    """A full Danish birth date is a calendar date, independent of timezone."""
    match = re.fullmatch(r"(\d{1,2})\.?\s+([\wæøå]+)\.?\s+(\d{4})", value.casefold())
    if not match:
        return None
    month = {**MONTH_SHORT, **MONTH_LONG}.get(match[2])
    try:
        return date(int(match[3]), month, int(match[1])) if month else None
    except ValueError:
        return None


def contact_options(html: str, kind: str, base_url: str = "") -> list[tuple[str, str, int | None]]:
    soup = make_soup(html)
    dropdown = soup.select_one("select#sk-toolbar-contact-dropdown")
    if dropdown is None:
        raise ParseError("Contact selector missing; expected an authenticated contact page")
    result = []
    for option in dropdown.select("option[value]"):
        url = urljoin(base_url, option["value"])
        if not option["value"]:
            continue
        match = re.search(r"/contacts/" + re.escape(kind) + r"/(\d+)$", urlsplit(url).path)
        result.append((text(option), url, int(match[1]) if match else None))
    return result


def parse_student_contacts(html: str, base_url: str = "") -> list[StudentContact]:
    """Lightweight index: keep URLs and IDs; do not fetch individual cards."""
    soup = make_soup(html)
    heading = soup.select_one("h2.h-ta-c")
    result = [StudentContact(name, text(heading), id=id, source_url=url)
              for name, url, id in contact_options(html, "students", base_url) if id is not None]
    ids = [s.id for s in result]
    if len(ids) != len(set(ids)):
        raise ParseError("Duplicate student IDs in contact selector")
    return result


def parse_student_card(html: str, *, student_id: int, source_url: str) -> StudentContact:
    soup = make_soup(html)
    section = soup.select_one(".text-block > .section")
    if section is None:
        raise ParseError("Student contact card missing")
    fields = labeled_fields(section)
    if not fields.get("navn"):
        raise ParseError("Student contact card has no name")
    result = StudentContact(fields["navn"], id=student_id, source_url=source_url,
                            details_loaded=True)
    result.birth_date_text = fields.get("fødselsdag", fields.get("fødselsdato", ""))
    result.birth_date = birth_date(result.birth_date_text)
    result.photo_url, result.photo_is_placeholder = photo(soup.select_one(".photo-block img"), source_url)
    merge_info(result.contact_info, fields, "student_card")
    for block in soup.select(".text-block .section-block"):
        fields = labeled_fields(block)
        if not fields.get("navn"):
            raise ParseError("Parent contact card has no name")
        parent = ParentContact(fields["navn"], text(block.select_one("h2.title")))
        merge_info(parent.contact_info, fields, "student_card")
        result.parents.append(parent)
    return result


def report_form(html: str) -> tuple[str, dict[str, str], dict[str, str], str]:
    """Discover the report action, default class, and supported report choices."""
    soup = make_soup(html)
    selector = soup.select_one('select[name="SelectedClassOrGroup"]')
    form = selector.find_parent("form") if selector else None
    if form is None or form.get("method", "").lower() != "post":
        raise ParseError("Contact report form missing")
    fields = {e["name"]: e.get("value", "") for e in form.select('input[type="hidden"][name]')}
    class_name = ""
    for select in form.select("select[name]"):
        option = select.select_one("option[selected]") or select.select_one("option")
        if option is None:
            raise ParseError("Empty contact report selector")
        fields[select["name"]] = option.get("value", "")
        if select["name"] == "SelectedClassOrGroup":
            class_name = text(option)
    if not fields.get("SelectedClassOrGroup"):
        raise ParseError("Contact report has no default class/group")
    fields["IncludeExternalStudents"] = "false"
    choices = {text(e): e.get("value", "") for e in form.select('select[name="ListTemplateType"] option')}
    return form.get("action", ""), fields, choices, class_name


def _grid_fields(row) -> dict[str, str]:
    fields = {}
    for label in row.find_all("li", recursive=False):
        if "sk-grid-inline-header" in label.get("class", []):
            fields[text(label).rstrip(":").strip().casefold()] = value_text(label.find_next_sibling("li"))
    return fields


def _unique_match(items, name: str, what: str):
    matches = [item for item in items if item.name == name]
    if len(matches) > 1:
        raise ParseError(f"Ambiguous {what} name; refusing to attach contact data")
    return matches[0] if matches else None


def enrich_students(students: list[StudentContact], html: str, kind: str, base_url: str) -> None:
    """Join only exact, unique full names within the chosen class and student.

    Reports have no person IDs. Ambiguity fails instead of guessing by order,
    truncated names, or a person's name in a different family.
    """
    soup = make_soup(html)
    if kind == "parent_photos":
        blocks = soup.select(".sk-contacts-parentphotos-container")
        if not blocks and not soup.select_one(".sk-no-data-feedback"):
            raise ParseError("Parent photo report missing")
        names = [text(block.select_one("h2")) for block in blocks]
        matched = set()
        for block, name in zip(blocks, names):
            student = _unique_match(students, name, "student")
            if student is None:
                continue
            if names.count(name) != 1:
                raise ParseError("Ambiguous student in parent photo report")
            matched.add(name)
            entries = block.select(".ccl-imagewithtext")
            parent_names = [text(e.select_one(".ccl-imagewithtext-text")) for e in entries]
            for entry, parent_name in zip(entries, parent_names):
                parent = _unique_match(student.parents, parent_name, "parent")
                if not parent_name or parent_names.count(parent_name) != 1:
                    raise ParseError("Ambiguous parent in photo report")
                if parent is None:
                    parent = ParentContact(parent_name)
                    student.parents.append(parent)
                parent.photo_url, parent.photo_is_placeholder = photo(entry.select_one("img"), base_url)
        if any(s.parents and s.name not in matched for s in students):
            raise ParseError("Parent photo report does not cover the selected students")
        return
    expected = {"addresses": "adresse", "parent_contacts": "kontaktperson",
                "parent_emails": "e-mail", "student_emails": "e-mail"}[kind]
    header = next((h for h in soup.select(".sk-grid-top-header") if expected in text(h).casefold()), None)
    if header is None:
        if soup.select_one(".sk-no-data-feedback"):
            return
        raise ParseError("Expected contact report columns missing")
    rows = header.parent.find_all("ul", recursive=False)[1:]
    names = [text(row.select_one(".sk-printlist-person-name-cell")) for row in rows]
    frequencies = Counter(names)
    matched = set()
    for row, name in zip(rows, names):
        if not name:
            raise ParseError("Contact report row has no student name")
        student = _unique_match(students, name, "student")
        if student is None:
            continue
        if frequencies[name] != 1:
            raise ParseError("Ambiguous student in contact report")
        matched.add(name)
        if kind.startswith("parent_"):
            entries = [_grid_fields(r) for r in row.select("ul.ccl-rwgm-row")]
            parent_names = [e.get("kontaktperson", "") for e in entries]
            for fields, parent_name in zip(entries, parent_names):
                if not parent_name or parent_names.count(parent_name) != 1:
                    raise ParseError("Missing or ambiguous parent name in contact report")
                parent = _unique_match(student.parents, parent_name, "parent")
                if parent is None:
                    parent = ParentContact(parent_name)
                    student.parents.append(parent)
                merge_info(parent.contact_info, fields, kind)
        else:
            merge_info(student.contact_info, _grid_fields(row), kind)
    if any(s.name not in matched for s in students):
        raise ParseError("Contact report does not cover the selected students")


def parse_staff_contacts(html: str, base_url: str, *, ids_by_name=None) -> list[StaffContact]:
    soup = make_soup(html)
    blocks = soup.select(".photo-block")
    if not blocks and not soup.select_one(".sk-no-data-feedback"):
        raise ParseError("Staff contact cards missing")
    result = []
    for image_block in blocks:
        block = image_block.parent
        fields = labeled_fields(block)
        if not fields.get("navn"):
            raise ParseError("Staff card has no name")
        item = StaffContact(fields["navn"], fields.get("stilling", ""))
        ids = (ids_by_name or {}).get(item.name, [])
        item.id = ids[0] if len(ids) == 1 else None
        item.photo_url, item.photo_is_placeholder = photo(image_block.select_one("img"), base_url)
        merge_info(item.contact_info, fields, "staff_card")
        result.append(item)
    return result


def parse_school_contacts(html: str, base_url: str) -> SchoolContacts:
    soup = make_soup(html)
    block = soup.select_one(".sk-contact-school-container")
    if block is None:
        raise ParseError("School contact details missing")
    result = SchoolContacts(text(block.find_previous_sibling("h3")))
    info = block.select_one(".sk-contact-school-info")
    if info is None or not result.name:
        raise ParseError("School contact heading missing")
    fields = {"adresse": value_text(info.select_one(".sk-contact-school-adress"))}
    for css, key in [("email", "e-mail"), ("phone", "telefon")]:
        icon = info.select_one(".sk-contact-school-" + css)
        fields[key] = text(icon.parent) if icon else ""
    merge_info(result.contact_info, fields, "school")
    website = info.select_one(".sk-contact-school-website")
    result.website = urljoin(base_url, website.parent.get("href", "")) if website else ""
    result.photo_url, _ = photo(info.select_one("img"), base_url)
    for group in block.select("ul.sk-list"):
        group_name = text(group.select_one("h4"))
        for entry in group.find_all("li", recursive=False):
            name = text(entry.select_one("p b"))
            if not name:
                continue
            person = SchoolContactPerson(name, group_name)
            fields = {text(row.select_one(".sk-contact-details-table-label")).casefold():
                      value_text(row.select_one(".sk-contact-details-table-content"))
                      for row in entry.select("table tr")}
            merge_info(person.contact_info, fields, "school")
            result.people.append(person)
    return result


def parse_class_parents(html: str) -> list[ClassParentContact]:
    soup = make_soup(html)
    header = soup.select_one(".sk-signup-container .sk-grid-top-header")
    if header is None:
        if soup.select_one(".sk-no-data-feedback"):
            return []
        raise ParseError("Class representative list missing")
    result = []
    for row in header.parent.find_all("ul", recursive=False)[1:]:
        fields = _grid_fields(row)
        if not fields.get("navn") or not fields.get("klasse"):
            raise ParseError("Class representative fields missing")
        result.append(ClassParentContact(fields["navn"], fields["klasse"]))
    return result
