"""Synthetic fixtures only: no captured student or parent data."""
from dataclasses import asdict
from datetime import date
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from requests import Response

from pyskoleintra import (
    Child, ClassParentContact, ContactInfo, ParentContact, SchoolContacts,
    StaffContact, StudentContact, Tabulex, TabulexContact,
)
from pyskoleintra.models import ChildInfo
from pyskoleintra.exceptions import NetworkError, NotAuthorizedError, ParseError
from pyskoleintra.parsers.contacts import (
    birth_date, enrich_students, parse_class_parents, parse_school_contacts,
    parse_staff_contacts, parse_student_card, parse_student_contacts, photo,
)
from pyskoleintra.parsers.tabulex_contacts import parse_tabulex_contacts, parse_tabulex_staff
from pyskoleintra.parsers.common import make_soup

BASE = 'https://school.example'
PREFIX = '/parent/1/Child/contacts/'
TYPES = {'Adresse- og telefonliste': '41', 'Kontaktoplysninger': '48',
         'Elevernes e-mailadresser': '44', 'Forældres e-mailadresser': '49',
         'Forældres profilbilleder': '50'}


def index_html():
    return f'''<select id="sk-toolbar-contact-dropdown">
    <option value="{PREFIX}students/11">Ada Examp...</option>
    <option value="{PREFIX}students/12">Bo Example</option></select>'''


def field(label, value):
    return (f'<div class="sk-labeledtext"><span class="sk-labeledtext-caption">{label}:</span>'
            f'<span class="sk-labeledtext-value">{value}</span></div>')


def card(name='Ada Example'):
    return '<div class="section"><div class="photo-block"><img src="/file/student.jpg"></div>' + (
        '<div class="text-block"><div class="section">' + field('Navn', name)
        + field('Fødselsdag', '29. feb. 2020') + field('Kontakttelefon 2', '+45 1111 2222')
        + '</div><div class="section"><div class="section-block"><h2 class="title">MOR</h2>'
        + field('Navn', 'Sam Example') + field('Mobiltelefon', '1234 5678')
        + field('E-mail', '') + '</div></div></div></div>'
    )


def grid_fields(fields):
    return ''.join(f'<li class="sk-grid-inline-header">{k}:</li><li>{v}</li>'
                   for k, v in fields.items())


def report(kind, names=('Ada Example', 'Bo Example')):
    if kind == '50':
        return ''.join('<div class="sk-contacts-parentphotos-container"><h2>' + n + '</h2>'
                       '<div class="ccl-imagewithtext"><img src="/skins/images/default.png">'
                       '<div class="ccl-imagewithtext-text">Sam Example</div></div></div>' for n in names)
    header = {'41': 'Elever Adresse Kontakttelefon', '48': 'Elever Kontaktperson Adresse',
              '44': 'Elever E-mail', '49': 'Elever Kontaktperson E-mail'}[kind]
    result = '<div><ul class="sk-grid-top-header"><li>' + header + '</li></ul>'
    for name in names:
        result += '<ul><li class="sk-printlist-person-name-cell">' + name + '</li>'
        if kind == '41':
            result += grid_fields({'Adresse': 'Eksempelvej 1<br>1000 Eksempelby',
                                   'Kontakttelefon': '1111 2222', 'Elevens mobil': ''})
        elif kind == '44':
            result += grid_fields({'E-mail': ''})
        else:
            fields = {'Kontaktperson': 'Sam Example'}
            fields.update({'Adresse': 'Eksempelvej 1', 'Mobiltelefon': '9999 9999'}
                          if kind == '48' else {'E-mail': 'sam@example.invalid'})
            result += '<li><ul class="ccl-rwgm-row">' + grid_fields(fields) + '</ul></li>'
        result += '</ul>'
    return result + '</div>'


def form():
    return f'''<form method="post" action="{PREFIX}studentlist">
      <input type="hidden" name="csrf" value="synthetic-token">
      <select name="SelectedClassOrGroup"><option value="G1">1A</option></select>
      <select name="ListTemplateType">{''.join(f'<option value="{v}">{k}</option>' for k,v in TYPES.items())}</select>
      <select name="GenderFilter"><option value="0">Alle</option></select>
      <select name="SortBy"><option value="0">Fornavn</option></select>
      <input name="IncludeExternalStudents" type="checkbox" value="true">
      <input name="IncludeExternalStudents" type="hidden" value="false"></form>'''


def response(body='', status=200, url=BASE):
    r = Response(); r.status_code = status; r.url = url
    r._content = body.encode(); r.encoding = 'utf-8'
    return r


class FakeHttp:
    def __init__(self):
        self.gets = []; self.posts = []; self.status = 200
        self.form = form(); self.redirect = None; self.index = index_html()

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        path = urlsplit(url).path
        if self.status != 200:
            return response(status=self.status)
        if path.endswith('/students/cards'):
            body = self.index
        elif path.endswith('/students/11'):
            body = card()
        elif path.endswith('/students/12'):
            body = card('Bo Example')
        elif path.endswith('/studentlist'):
            body = self.form
        elif path.endswith('/studentlist/list'):
            body = report(parse_qs(urlsplit(url).query)['type'][0])
        else:
            raise AssertionError('Unexpected GET: ' + path)
        return response(body, url=url)

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data))
        assert url == BASE + PREFIX + 'studentlist'
        assert data['csrf'] == 'synthetic-token'
        assert data['IncludeExternalStudents'] == 'false'
        if data['ListTemplateType'] == '50':
            return response(report('50'))
        r = response(status=302)
        r.headers['Location'] = self.redirect or PREFIX + 'studentlist/list?type=' + data['ListTemplateType']
        return r


def child(http):
    return Child(ChildInfo('Child', 1, '/parent/1/Child'), BASE, http)


def test_lightweight_index_and_legacy_constructor():
    http = FakeHttp(); students = child(http).contacts()
    assert len(http.gets) == 1 and not http.posts
    assert students[0].id == 11 and students[0].name == 'Ada Examp...'
    assert not students[0].details_loaded and not students[0].parents
    legacy = StudentContact('Ada', '1A', '/photo')
    assert (legacy.name, legacy.class_name, legacy.photo_url) == ('Ada', '1A', '/photo')


def test_rich_snapshots_merge_reports_without_mixing_families_or_overwriting_card():
    http = FakeHttp(); students = child(http).contacts(detailed=True, refresh=True)
    assert [s.id for s in students] == [11, 12]
    assert [s.name for s in students] == ['Ada Example', 'Bo Example']
    assert all(s.details_loaded and s.class_name == '1A' for s in students)
    assert all(s.birth_date == date(2020, 2, 29) for s in students)
    assert students[0].contact_info.address == 'Eksempelvej 1\n1000 Eksempelby'
    assert students[0].photo_url == BASE + '/file/student.jpg'
    assert students[0].photo_is_placeholder is False
    parent = students[0].parent('sam example')
    assert parent.relationship == 'MOR'
    assert parent.contact_info.email == 'sam@example.invalid'
    assert parent.contact_info.phones['mobile'] == '1234 5678'
    assert parent.contact_info.sources['phones.mobile'] == 'student_card'
    assert parent.contact_info.sources['email'] == 'parent_emails'
    assert parent.photo_is_placeholder is True
    assert parent is not students[1].parents[0]
    parent.contact_info.email = 'new@example.invalid'
    assert students[1].parents[0].contact_info.email == 'sam@example.invalid'
    assert len(http.posts) == 5 and all(not kw['use_cache'] for _, kw in http.gets)
    assert all(url == BASE + PREFIX + 'studentlist' for url, _ in http.posts)
    count = len(http.gets)
    assert students[0].parents_by_name['Sam Example'] == [parent]
    assert students[0].parent('Sam Example') is parent and len(http.gets) == count


def test_single_contact_fetches_only_selected_card_and_rejects_unknown_id():
    http = FakeHttp(); student = child(http).contact(12)
    assert student.name == 'Bo Example'
    assert not any(url.endswith('/students/11') for url, _ in http.gets)
    with pytest.raises(KeyError):
        child(FakeHttp()).contact(1000)
    for value in [True, 0, -1, '11']:
        with pytest.raises(ValueError):
            child(FakeHttp()).contact(value)


def test_parent_lookup_keeps_ambiguity_and_independent_defaults():
    s = StudentContact('Ada', parents=[ParentContact('Sam'), ParentContact('Sam')])
    assert len(s.parents_by_name['Sam']) == 2
    with pytest.raises(ValueError): s.parent('Sam')
    with pytest.raises(KeyError): s.parent('Unknown')
    a, b = ContactInfo(), ContactInfo(); a.phones['mobile'] = '123'
    assert not b.phones


@pytest.mark.parametrize('report_names,students', [
    (('Ada Example', 'Ada Example'), [StudentContact('Ada Example')]),
    (('Ada Example',), [StudentContact('Ada Example'), StudentContact('Ada Example')]),
    (('Other Class',), [StudentContact('Ada Example')]),
])
def test_reports_reject_ambiguous_or_wrong_class_join(report_names, students):
    with pytest.raises(ParseError): enrich_students(students, report('48', report_names), 'parent_contacts', BASE)


def test_parent_report_namesakes_not_silently_combined():
    s = StudentContact('Ada Example', parents=[ParentContact('Sam Example'), ParentContact('Sam Example')])
    with pytest.raises(ParseError): enrich_students([s], report('49'), 'parent_emails', BASE)
    with pytest.raises(ParseError): enrich_students([s], report('50'), 'parent_photos', BASE)


@pytest.mark.parametrize('html', ['<html>Login</html>', '<html>Error</html>'])
def test_wrong_pages_are_not_empty_contact_results(html):
    with pytest.raises(ParseError): parse_student_contacts(html)
    with pytest.raises(ParseError): parse_student_card(html, student_id=11, source_url=BASE)
    with pytest.raises(ParseError): parse_school_contacts(html, BASE)
    with pytest.raises(ParseError): parse_staff_contacts(html, BASE)
    with pytest.raises(ParseError): parse_class_parents(html)
    with pytest.raises(ParseError): parse_tabulex_contacts(html)
    with pytest.raises(ParseError): parse_tabulex_staff(html, BASE)


@pytest.mark.parametrize('status,error', [(302, NotAuthorizedError), (403, NotAuthorizedError), (500, NetworkError)])
def test_http_failures(status, error):
    http = FakeHttp(); http.status = status
    with pytest.raises(error): child(http).contacts()


@pytest.mark.parametrize('url', ['https://other.example/contacts/students/11',
                                '/parent/2/Other/contacts/students/11'])
def test_card_scope_checked_before_fetch(url):
    http = FakeHttp(); http.index = index_html().replace(PREFIX + 'students/11', url)
    with pytest.raises(ParseError): child(http).contacts(detailed=True)
    assert len(http.gets) == 1 and not http.posts


def test_report_action_and_redirect_scopes_checked():
    http = FakeHttp(); http.form = form().replace('action="' + PREFIX, 'action="https://other.example/')
    with pytest.raises(ParseError): child(http).contact(11)
    assert not http.posts
    for target in ['https://other.example/data', PREFIX + 'students/cards', '/Account/login']:
        http = FakeHttp(); http.redirect = target
        with pytest.raises(ParseError): child(http).contact(11)
        assert not any(url == target for url, _ in http.gets)


@pytest.mark.parametrize('value,expected', [('29. feb. 2020', date(2020,2,29)),
    ('1. oktober 2018', date(2018,10,1)), ('29. feb. 2021', None), ('1. maj', None), ('', None)])
def test_birth_date_has_no_inferred_year_or_timezone(value, expected):
    assert birth_date(value) == expected


def test_staff_school_and_representatives_are_separate_models():
    staff_html = '<div class="section"><div class="photo-block"><img src="/file/staff.jpg"></div>' + field('Navn','Kim Example') + field('Stilling','Lærer') + field('E-mail','kim@example.invalid') + '</div>'
    staff = parse_staff_contacts(staff_html, BASE, ids_by_name={'Kim Example':[17]})[0]
    assert isinstance(staff, StaffContact) and staff.id == 17 and staff.position == 'Lærer'
    assert staff.contact_info.email == 'kim@example.invalid'
    assert parse_staff_contacts(staff_html, BASE, ids_by_name={'Kim Example':[17,18]})[0].id is None
    html = '''<h3>Eksempelskolen</h3><div class="sk-contact-school-container">
    <div class="sk-contact-school-info"><div class="sk-contact-school-adress">Skolevej 1</div>
    <a><i class="sk-contact-school-email"></i>skole@example.invalid</a>
    <a href="https://school.example"><i class="sk-contact-school-website"></i>Website</a></div>
    <ul class="sk-list"><li><h4>Ledelse</h4><p><b>Kim Example</b></p></li></ul></div>'''
    school = parse_school_contacts(html, BASE)
    assert isinstance(school, SchoolContacts) and school.contact_info.address == 'Skolevej 1'
    assert school.people[0].group == 'Ledelse'
    reps = parse_class_parents('<div class="sk-signup-container"><ul class="sk-grid-top-header"></ul><ul>' + grid_fields({'Klasse':'1A','Navn':'Sam Example'}) + '</ul></div>')
    assert reps == [ClassParentContact('Sam Example','1A')]


SFO_HTML = '''<table><thead><th>Navn</th><th>Relation</th><th>Må hente</th><th>Webadgang</th><th>Værge</th></thead>
<tbody><tr><td data-label="Navn">Sam Example</td><td data-label="Relation">Bedsteforælder</td>
<td data-label="Adresse">Eksempelvej 2</td><td data-label="Telefon">1234 5678</td>
<td data-label="Må hente"><i class="fa-check"></i></td><td data-label="Webadgang"><i class="fa-ban"></i></td>
<td data-label="Værge"></td><td><button data-target="#edit_contact31"></button></td></tr></tbody></table>
<div id="edit_contact31"><input id="form_mobile31" value="1234 5678">
<input id="form_webaccessemail31" value="sam@example.invalid">
<input name="contactwebaccessssn" value="DO-NOT-COLLECT"></div>'''


def test_sfo_contacts_flags_and_details_without_identity_number():
    c = parse_tabulex_contacts(SFO_HTML)[0]
    assert isinstance(c, TabulexContact) and c.id == '31'
    assert c.relationship == 'Bedsteforælder'
    assert c.can_pick_up is True and c.has_web_access is False and c.is_guardian is None
    assert c.contact_info.email == 'sam@example.invalid'
    assert c.contact_info.phones == {'mobile':'1234 5678'}
    assert 'DO-NOT-COLLECT' not in repr(asdict(c))


def test_sfo_methods_fetch_only_their_separate_pages():
    http = Mock(); tabulex = Tabulex(http, Mock())
    tabulex._page = Mock(return_value=response(SFO_HTML))
    assert tabulex.contacts()[0].id == '31'
    tabulex._page.assert_called_once_with('/guardian/contacts')
    staff = '<div class="well"><div><img src="/CssImages/person.png"></div><div class="media-body"><h4 class="media-heading">Kim Example</h4>Pædagog</div></div>'
    tabulex._page = Mock(return_value=response(staff))
    s = tabulex.staff_contacts()[0]
    assert s.name == 'Kim Example' and s.position == 'Pædagog' and s.photo_is_placeholder is True
    tabulex._page.assert_called_once_with('/guardian/staff')
    http.post.assert_not_called()


def test_photo_placeholder_detection_preserves_unknown():
    assert photo(make_soup('<img src="/custom.jpg">').img, BASE) == (BASE+'/custom.jpg', None)
    with pytest.raises(ParseError): photo(make_soup('<img src="javascript:bad">').img, BASE)


def test_empty_index_and_explicit_empty_directories():
    http = FakeHttp(); http.index = '<select id="sk-toolbar-contact-dropdown"></select>'
    assert child(http).contacts(detailed=True) == [] and not http.posts
    assert parse_class_parents('<div class="sk-no-data-feedback">Ingen kontakter</div>') == []
    assert parse_staff_contacts('<div class="sk-no-data-feedback">Ingen personale</div>', BASE) == []


def test_missing_report_type_fails_without_returning_partial_details():
    http = FakeHttp(); http.form = form().replace('<option value="50">Forældres profilbilleder</option>', '')
    with pytest.raises(ParseError, match='unavailable'):
        child(http).contact(11)


def test_staff_and_school_public_methods_use_their_own_routes():
    http = Mock()
    staff_html = '<div class="section"><div class="photo-block"></div>' + field('Navn', 'Kim Example') + '</div>'
    staff_index = f'<select id="sk-toolbar-contact-dropdown"><option value="{PREFIX}staff/contactcards">Alle</option><option value="{PREFIX}staff/71">Kim Example</option></select>'
    http.get.side_effect = [response(staff_index), response(staff_html)]
    assert child(http).staff_contacts(refresh=True)[0].id == 71
    assert http.get.call_args_list[1].args == (BASE + PREFIX + 'staff/contactcards',)
    assert all(c.kwargs == {'use_cache': False} for c in http.get.call_args_list)
    http.get.side_effect = None
    http.get.return_value = response('<h3>Skolen</h3><div class="sk-contact-school-container"><div class="sk-contact-school-info"></div></div>')
    assert child(http).school_contacts().name == 'Skolen'
    http.get.assert_called_with(BASE + PREFIX + 'school', use_cache=True)
    http.get.return_value = response('<div class="sk-no-data-feedback">Ingen kontakter</div>')
    assert child(http).contact_parents() == []
    http.get.assert_called_with(BASE + PREFIX + 'contactparents', use_cache=True)
    http.post.assert_not_called()


def test_staff_fallback_uses_individual_card_id_without_name_join():
    http = Mock()
    index = f'<select id="sk-toolbar-contact-dropdown"><option value="{PREFIX}staff/71">Kim Examp...</option></select>'
    html = '<div class="section"><div class="photo-block"></div>' + field('Navn','Kim Example') + '</div>'
    http.get.side_effect = [response(index), response(html)]
    assert child(http).staff_contacts()[0].id == 71
    http.get.assert_called_with(BASE + PREFIX + 'staff/71', use_cache=True)


def test_sfo_preserves_extra_table_number_and_auth_failure():
    html = SFO_HTML.replace('data-label="Telefon">1234 5678', 'data-label="Telefon">8765 4321')
    info = parse_tabulex_contacts(html)[0].contact_info
    assert info.phones == {'mobile':'1234 5678', 'contact':'8765 4321'}
    tabulex = Tabulex(Mock(), Mock())
    tabulex._page = Mock(return_value=response(status=403))
    with pytest.raises(NotAuthorizedError): tabulex.contacts()
