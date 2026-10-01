from __future__ import annotations

import json
from dataclasses import replace
from html import escape
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from requests import Response

from pyskoleintra import ReadingContractBook, ReadingContractEntry
from pyskoleintra.child import Child
from pyskoleintra.exceptions import NetworkError, NotAuthorizedError, ParseError
from pyskoleintra.http import HttpSession
from pyskoleintra.models import ChildInfo
from pyskoleintra.parsers.reading_contract import (
    extract_contracts_api_url,
    extract_data_provider_settings,
    parse_reading_contract_entries_json,
    parse_reading_contracts_json,
)

PREFIX = '/parent/1/Child/readingcontracts/'
PROVIDER = {
    'GetStudentClassReadingContracts': PREFIX + 'GetStudentReadingContracts',
    'GetRecordsForBook': PREFIX + 'GetRecordsForBook',
    'SaveRecord': PREFIX + 'AddReadingProgressToTheContract',
    'UpdatePagesCountOfProgressRecordUrl': PREFIX + 'UpdatePagesCountOfProgressRecord',
    'DeleteReadingProgressRecordUrl': PREFIX + 'DeleteReadingProgressRecord',
}
TITLE = 'Blå bog & eventyr'
AUTHOR = 'A. Æble'
OLD = {'Id': 71, 'Date': '24. sep. 2026', 'Pages': 30}
NEW = {'Id': 92, 'Date': '1. okt. 2026', 'Pages': 1}
CONTRACT = {
    'Id': 12, 'StudentId': 42, 'Category': 'Frilæsning', 'IsActive': True,
    'IsReadOnly': False, 'IsPageUsedForCount': False, 'PagesToRead': 500,
    'Books': [{'Title': TITLE, 'Author': AUTHOR, 'ReadPagesCount': 30}],
}


def _page(provider):
    return '<div id="sk-reading-contracts" data-clientlogic-settings-ReadingContracts="' + escape(
        json.dumps({'DataProviderSettings': provider}), quote=True,
    ) + '"></div>'


def _response(data='', status=200):
    response = Response()
    response.status_code = status
    response._content = (data if isinstance(data, str) else json.dumps(data)).encode('utf-8')
    response.encoding = 'utf-8'
    return response


class FakeHttp:
    def __init__(self, records=None):
        self.provider = dict(PROVIDER)
        self.contracts = [dict(CONTRACT)]
        self.records = list(records if records is not None else [OLD])
        self.gets = []
        self.posts = []
        self.after_write = None
        self.get_status = 200
        self.post_status = 200
        self.post_error = None

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        path = urlsplit(url).path
        if path.endswith('/Index'):
            return _response(_page(self.provider), self.get_status)
        if path.endswith('/GetStudentReadingContracts'):
            return _response(self.contracts, self.get_status)
        if path.endswith('/GetRecordsForBook'):
            return _response(self.records, self.get_status)
        raise AssertionError(f'Unexpected GET: {url}')

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data, kwargs))
        if self.post_error:
            raise self.post_error
        if self.after_write is not None:
            self.records = self.after_write
        return _response(status=self.post_status)


def _child(http):
    return Child(ChildInfo('Child', 1, '/parent/1/Child'), 'https://school', http)


def _entry(record=OLD):
    return parse_reading_contract_entries_json(
        json.dumps([record]), contract_id=12, title=TITLE, author=AUTHOR,
    )[0]


def test_provider_discovery_handles_case_group_and_missing_settings():
    assert extract_data_provider_settings(_page(PROVIDER)) == PROVIDER
    group = {'GetStudentGroupReadingContracts': PROVIDER['GetStudentClassReadingContracts']}
    assert extract_contracts_api_url(_page(group)) == group['GetStudentGroupReadingContracts']
    for html in ('', '<div id="sk-reading-contracts"></div>', _page(None), _page([])):
        assert extract_data_provider_settings(html) == {}
        assert extract_contracts_api_url(html) is None


def test_contract_books_have_identity_and_preserve_legacy_constructors():
    contract = parse_reading_contracts_json(json.dumps([CONTRACT]))[0]
    assert contract.student_id == 42
    assert not contract.is_read_only
    assert contract.books == [ReadingContractBook(TITLE, AUTHOR, 30)]
    assert ReadingContractEntry('yesterday', 'book', '5', 'note').comment == 'note'
    assert _child(FakeHttp()).reading_contract_books(12) == contract.books


def test_entries_include_numeric_id_amount_and_exact_book_identity():
    http = FakeHttp()
    entry = _child(http).reading_contract_entries(12, title=TITLE, author=AUTHOR)[0]
    assert (entry.id, entry.contract_id, entry.author, entry.title) == (71, 12, AUTHOR, TITLE)
    assert (entry.date, entry.pages, entry.read_pages_count) == ('24. sep. 2026', '30', 30)
    query = parse_qs(urlsplit(http.gets[-1][0]).query)
    assert query == {'contractId': ['12'], 'author': [AUTHOR], 'title': [TITLE]}
    assert all(options['use_cache'] is False for _, options in http.gets)


@pytest.mark.parametrize('data', [None, {}, [None], [{'Id': 1}], [dict(OLD, Id=True)],
    [dict(OLD, Id=0)], [dict(OLD, Pages='30')], [dict(OLD, Pages=-1)],
    [dict(OLD, Date=None)], [OLD, OLD]])
def test_entries_fail_closed_on_unusable_or_duplicate_ids(data):
    with pytest.raises(ParseError):
        parse_reading_contract_entries_json(
            json.dumps(data), contract_id=12, title=TITLE, author=AUTHOR,
        )


def test_add_posts_once_and_returns_only_the_new_server_id():
    http = FakeHttp()
    http.after_write = [NEW, OLD]
    entry = _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    assert entry == _entry(NEW)
    assert http.posts == [('https://school' + PROVIDER['SaveRecord'], {
        'contractId': 12, 'studentId': 42,
        'progressRecordModel[Author]': AUTHOR, 'progressRecordModel[Title]': TITLE,
        'progressRecordModel[ReadPagesCount]': 1,
    }, {})]
    assert all(options['use_cache'] is False for _, options in http.gets)


def test_add_new_book_uses_the_same_single_entry_request():
    http = FakeHttp(records=[])
    http.after_write = [NEW]
    entry = _child(http).add_reading_contract_entry(12, title='New book', author=AUTHOR, amount=1)
    assert entry.title == 'New book'
    assert entry.id == NEW['Id']
    assert len(http.posts) == 1


@pytest.mark.parametrize('amount', [0, -1, 1000, True, 1.5, '10', [1, 2]])
def test_invalid_amount_never_reaches_the_server(amount):
    http = FakeHttp()
    with pytest.raises(ValueError):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=amount)
    assert http.gets == http.posts == []


@pytest.mark.parametrize('contract_id', [0, -1, True, '12', [12]])
def test_invalid_contract_id_never_reaches_the_server(contract_id):
    http = FakeHttp()
    with pytest.raises(ValueError):
        _child(http).reading_contract_books(contract_id)
    assert http.gets == []


@pytest.mark.parametrize('title,author', [('', AUTHOR), (TITLE, ' '), (None, AUTHOR)])
def test_invalid_book_never_reaches_the_server(title, author):
    http = FakeHttp()
    with pytest.raises(ValueError):
        _child(http).add_reading_contract_entry(12, title=title, author=author, amount=1)
    assert http.gets == http.posts == []


@pytest.mark.parametrize('after', [[OLD], [OLD, NEW, dict(NEW, Id=93)],
    [OLD, dict(NEW, Pages=2)], [NEW]])
def test_ambiguous_write_never_retries_or_deletes_any_entry(after):
    http = FakeHttp()
    http.after_write = after
    with pytest.raises(ParseError, match='uniquely identify'):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    assert len(http.posts) == 1
    assert http.posts[0][0].endswith('AddReadingProgressToTheContract')


def test_write_network_error_is_not_retried():
    http = FakeHttp()
    http.post_error = NetworkError('timeout')
    with pytest.raises(NetworkError):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    assert len(http.posts) == 1


@pytest.mark.parametrize('status', [302, 401, 403, 500])
def test_mutation_errors_are_reported_and_not_retried(status):
    http = FakeHttp()
    http.post_status = status
    with pytest.raises(ParseError, match=f'HTTP {status}'):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    assert len(http.posts) == 1


def test_read_only_contract_and_unrelated_contract_cannot_be_mutated():
    http = FakeHttp()
    http.contracts[0]['IsReadOnly'] = True
    with pytest.raises(NotAuthorizedError):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    with pytest.raises(ValueError, match="this child's"):
        _child(http).add_reading_contract_entry(999, title=TITLE, author=AUTHOR, amount=1)
    assert http.posts == []


def test_delete_only_the_selected_entry_and_leave_existing_entry_intact():
    http = FakeHttp(records=[OLD, NEW])
    http.after_write = [OLD]
    _child(http).delete_reading_contract_entry(_entry(NEW))
    assert http.posts == [('https://school' + PROVIDER['DeleteReadingProgressRecordUrl'], {
        'recordToDelete[studentId]': 42,
        'recordToDelete[contractId]': 12,
        'recordToDelete[recordId]': 92,
    }, {})]
    assert http.records == [OLD]
    assert all(options['use_cache'] is False for _, options in http.gets)


@pytest.mark.parametrize('entry', [replace(_entry(), id=0), replace(_entry(), id=999),
    replace(_entry(), contract_id=999), replace(_entry(), date='wrong date'),
    replace(_entry(), read_pages_count=999), [_entry()]])
def test_delete_never_substitutes_an_existing_entry_for_wrong_or_stale_input(entry):
    http = FakeHttp()
    with pytest.raises(ValueError):
        _child(http).delete_reading_contract_entry(entry)
    assert http.posts == []
    assert http.records == [OLD]


@pytest.mark.parametrize('after', [[OLD, NEW], [], [dict(OLD, Pages=1)]])
def test_delete_reports_failed_verification_without_further_mutations(after):
    http = FakeHttp(records=[OLD, NEW])
    http.after_write = after
    with pytest.raises(ParseError):
        _child(http).delete_reading_contract_entry(_entry(NEW))
    assert len(http.posts) == 1


@pytest.mark.parametrize('target', ['https://other.example/path',
    '/parent/2/Other/readingcontracts/DeleteReadingProgressRecord', '/messages/delete'])
def test_discovered_write_endpoint_cannot_escape_child_scope(target):
    http = FakeHttp()
    http.provider['SaveRecord'] = target
    with pytest.raises(ParseError):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    assert http.posts == []


def test_login_redirect_on_read_does_not_look_like_an_empty_book():
    http = FakeHttp()
    http.get_status = 302
    with pytest.raises(ParseError, match='HTTP 302'):
        _child(http).add_reading_contract_entry(12, title=TITLE, author=AUTHOR, amount=1)
    assert http.posts == []


def test_no_cache_reads_fetch_live_data_without_changing_shared_session_cache(tmp_path):
    http = HttpSession(cache_dir=str(tmp_path))
    url = 'https://school' + PROVIDER['GetRecordsForBook']
    http._cache_write('GET', url, _response([OLD]))
    http.session.request = Mock(return_value=_response([OLD, NEW]))
    assert len(json.loads(http.get(url).text)) == 1
    http.session.request.assert_not_called()
    assert len(json.loads(http.get(url, use_cache=False).text)) == 2
    http.session.request.assert_called_once()
    assert len(json.loads(http.get(url).text)) == 1


def test_contract_refresh_bypasses_both_settings_and_data_cache():
    http = FakeHttp()
    _child(http).reading_contracts(refresh=True)
    assert all(options['use_cache'] is False for _, options in http.gets)


@pytest.mark.parametrize('amount', [0, 2, 999])
@pytest.mark.parametrize('pages_used', [False, True])
def test_update_sets_one_amount_and_preserves_identity_and_other_entries(amount, pages_used):
    http = FakeHttp(records=[OLD, NEW])
    http.contracts[0]['IsPageUsedForCount'] = pages_used
    http.after_write = [OLD, dict(NEW, Pages=amount)]
    original = _entry(NEW)

    updated = _child(http).update_reading_contract_entry(original, amount=amount)

    assert updated == replace(original, pages=str(amount), read_pages_count=amount)
    assert original == _entry(NEW)
    assert http.posts == [('https://school' + PROVIDER['UpdatePagesCountOfProgressRecordUrl'], {
        'recordToUpdate[studentId]': 42,
        'recordToUpdate[contractId]': 12,
        'recordToUpdate[recordId]': 92,
        'pagesCount': amount,
    }, {})]
    assert http.records[0] == OLD
    assert all(options['use_cache'] is False for _, options in http.gets)


def test_update_unchanged_amount_reads_fresh_entry_without_posting():
    http = FakeHttp()
    original = _entry()
    current = _child(http).update_reading_contract_entry(original, amount=30)
    assert current == original
    assert current is not original
    assert http.gets
    assert all(options['use_cache'] is False for _, options in http.gets)
    assert http.posts == []


@pytest.mark.parametrize('amount', [-1, 1000, True, False, 1.5, '10', [1, 2], None])
def test_update_invalid_amount_makes_no_request(amount):
    http = FakeHttp()
    with pytest.raises(ValueError, match='amount'):
        _child(http).update_reading_contract_entry(_entry(), amount=amount)
    assert http.gets == http.posts == []


@pytest.mark.parametrize('entry', [replace(_entry(), id=0), replace(_entry(), id=999),
    replace(_entry(), contract_id=999), replace(_entry(), date='wrong date'),
    replace(_entry(), read_pages_count=999), [_entry()]])
def test_update_wrong_or_stale_entry_does_not_mutate(entry):
    http = FakeHttp()
    with pytest.raises(ValueError):
        _child(http).update_reading_contract_entry(entry, amount=2)
    assert http.posts == []
    assert http.records == [OLD]


def test_update_noop_still_rejects_stale_entry():
    http = FakeHttp()
    with pytest.raises(ValueError, match='has changed'):
        _child(http).update_reading_contract_entry(
            replace(_entry(), read_pages_count=2), amount=2,
        )
    assert http.posts == []


def test_update_read_only_contract_is_rejected_without_posting():
    http = FakeHttp()
    http.contracts[0]['IsReadOnly'] = True
    with pytest.raises(NotAuthorizedError):
        _child(http).update_reading_contract_entry(_entry(), amount=2)
    assert http.posts == []


@pytest.mark.parametrize('target', [None, 'https://other.example/update',
    '/parent/2/Other/readingcontracts/UpdatePagesCountOfProgressRecord'])
def test_update_requires_an_available_endpoint_in_this_childs_scope(target):
    http = FakeHttp()
    http.provider['UpdatePagesCountOfProgressRecordUrl'] = target
    with pytest.raises(ParseError):
        _child(http).update_reading_contract_entry(_entry(), amount=2)
    assert http.posts == []


@pytest.mark.parametrize('after', [
    [OLD, NEW],  # The server did not apply the new amount.
    [OLD],  # The target disappeared.
    [OLD, dict(NEW, Pages=2, Id=93)],  # A different ID must not be substituted.
    [OLD, dict(NEW, Pages=2, Date='another date')],
    [dict(NEW, Pages=2)],  # An existing reading disappeared.
    [dict(OLD, Pages=99), dict(NEW, Pages=2)],
    [OLD, dict(NEW, Pages=2), dict(NEW, Pages=2)],
    '<html>Login</html>',
])
def test_update_unverifiable_result_never_retries_rolls_back_or_deletes(after):
    http = FakeHttp(records=[OLD, NEW])
    http.after_write = after
    with pytest.raises(ParseError):
        _child(http).update_reading_contract_entry(_entry(NEW), amount=2)
    assert len(http.posts) == 1
    assert http.posts[0][0].endswith('/UpdatePagesCountOfProgressRecord')


@pytest.mark.parametrize('status', [302, 401, 403, 500])
def test_update_http_error_never_retries(status):
    http = FakeHttp()
    http.post_status = status
    with pytest.raises(ParseError, match=f'HTTP {status}'):
        _child(http).update_reading_contract_entry(_entry(), amount=2)
    assert len(http.posts) == 1


def test_update_network_failure_never_retries():
    http = FakeHttp()
    http.post_error = NetworkError('timeout')
    with pytest.raises(NetworkError):
        _child(http).update_reading_contract_entry(_entry(), amount=2)
    assert len(http.posts) == 1


def test_updated_entry_can_be_deleted_but_original_stale_object_is_rejected():
    http = FakeHttp(records=[OLD, NEW])
    http.after_write = [OLD, dict(NEW, Pages=2)]
    child = _child(http)
    original = _entry(NEW)
    updated = child.update_reading_contract_entry(original, amount=2)

    with pytest.raises(ValueError, match='has changed'):
        child.delete_reading_contract_entry(original)
    assert len(http.posts) == 1

    http.after_write = [OLD]
    child.delete_reading_contract_entry(updated)
    assert len(http.posts) == 2
    assert http.posts[-1][1]['recordToDelete[recordId]'] == updated.id
    assert http.records == [OLD]
