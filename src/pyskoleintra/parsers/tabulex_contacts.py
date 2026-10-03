"""Read SFO contacts independently of SkoleIntra's class directory."""
from __future__ import annotations

import re
from ..exceptions import ParseError
from ..models import StaffContact, TabulexContact
from .common import make_soup
from .contacts import merge_info, photo, text, value_text


def _flag(cell) -> bool | None:
    if cell is None:
        return None
    if cell.select_one('.fa-check'):
        return True
    if cell.select_one('.fa-ban, .fa-times'):
        return False
    value = text(cell).casefold()
    return {'ja': True, 'nej': False}.get(value)


def parse_tabulex_contacts(html: str) -> list[TabulexContact]:
    soup = make_soup(html)
    table = next((t for t in soup.select('table')
                  if {'Navn', 'Relation', 'Må hente', 'Webadgang', 'Værge'}
                  <= {text(th) for th in t.select('th')}), None)
    if table is None:
        raise ParseError('SFO contact table missing')
    contacts = []
    for row in table.select('tbody tr'):
        cells = {e.get('data-label'): e for e in row.find_all('td', recursive=False)}
        if not text(cells.get('Navn')):
            # A real empty table may contain a single colspan status row.
            if len(row.select('td[colspan]')) == 1 and not cells.get('Navn'):
                continue
            raise ParseError('SFO contact name missing')
        ids = set()
        for e in row.select('[data-target]'):
            match = re.fullmatch(r'#(?:edit|delete)_contact(\d+)', e['data-target'])
            if match:
                ids.add(match[1])
        if len(ids) > 1:
            raise ParseError('Conflicting SFO contact IDs')
        contact = TabulexContact(
            name=text(cells['Navn']), id=next(iter(ids), None),
            relationship=text(cells.get('Relation')),
            can_pick_up=_flag(cells.get('Må hente')),
            has_web_access=_flag(cells.get('Webadgang')),
            is_guardian=_flag(cells.get('Værge')),
        )
        merge_info(contact.contact_info, {'adresse': value_text(cells.get('Adresse'))},
                   'tabulex_contacts')
        # Read only ordinary contact fields from an existing contact's edit
        # markup. Do not collect access credentials or identity-number inputs.
        modal = soup.find(id='edit_contact' + contact.id) if contact.id else None
        if modal:
            names = {'form_phone': 'tlf hjem', 'form_mobile': 'mobil prv.',
                     'form_mobilework': 'mobil arb.', 'form_work': 'tlf arbejde',
                     'form_webaccessemail': 'e-mail'}
            fields = {}
            for prefix, label in names.items():
                e = modal.find(id=prefix + contact.id)
                fields[label] = e.get('value', '') if e else ''
            merge_info(contact.contact_info, fields, 'tabulex_contact_form')
        phone = cells.get('Telefon')
        phone_text = value_text(phone)
        if phone_text and phone_text not in contact.contact_info.phones.values():
            merge_info(contact.contact_info, {'telefon': phone_text}, 'tabulex_contacts')
        contacts.append(contact)
    ids = [c.id for c in contacts if c.id]
    if len(ids) != len(set(ids)):
        raise ParseError('Duplicate SFO contact IDs')
    return contacts


def parse_tabulex_staff(html: str, base_url: str) -> list[StaffContact]:
    soup = make_soup(html)
    result = []
    for block in soup.select('.well'):
        body = block.select_one('.media-body')
        heading = body.select_one('.media-heading') if body else None
        if heading is None:
            continue
        item = StaffContact(name=text(heading))
        heading.extract()
        item.position = text(body)
        item.photo_url, item.photo_is_placeholder = photo(block.select_one('img'), base_url)
        result.append(item)
    if not result and not soup.find(string=re.compile(r"ingen (?:medarbejdere|personale)", re.I)):
        raise ParseError('SFO staff page missing')
    return result
