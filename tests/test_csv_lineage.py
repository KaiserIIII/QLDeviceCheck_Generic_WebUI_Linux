"""Append-only CSV provenance for ordinary tasks and retest chains."""
import copy
import csv
import io
import json
from pathlib import Path

import pytest

from inspection.reports import export_job
from inspection.service import InspectionService
from test_http import api  # Existing temporary Demo/loopback fixture.
from test_workbench import wait_finished

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def retest_record():
    return json.loads((FIXTURES / 'csv-lineage-retest.json').read_text(encoding='utf-8'))


def read_csv(data):
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'),newline=''))
    return reader.fieldnames,list(reader)


def test_csv_appends_parent_column_after_every_existing_column(retest_record):
    legacy_columns,_ = read_csv((FIXTURES / 'csv-lineage-legacy29.csv').read_bytes())
    columns,_ = read_csv(export_job(retest_record,'csv')[0])
    assert len(legacy_columns) == 29
    assert columns == legacy_columns + ['parent_job_id']


def test_all_legacy_cells_and_input_evidence_remain_unchanged(retest_record):
    original = copy.deepcopy(retest_record)
    old_rows = list(csv.reader(io.StringIO((FIXTURES / 'csv-lineage-legacy29.csv').read_text(encoding='utf-8-sig'),newline='')))
    new_rows = list(csv.reader(io.StringIO(export_job(retest_record,'csv')[0].decode('utf-8-sig'),newline='')))
    assert [row[:-1] for row in new_rows] == old_rows
    assert retest_record == original


def test_parent_identity_is_consistent_across_csv_json_html(retest_record):
    _,rows = read_csv(export_job(retest_record,'csv')[0])
    raw = json.loads(export_job(retest_record,'json')[0])
    html = export_job(retest_record,'html',lang='en')[0].decode('utf-8')
    assert rows[0].get('parent_job_id') == raw['parent_job_id'] == retest_record['parent_job_id']
    assert raw['parent_job_id'] in html
    assert rows[0]['job_id'] == raw['id']


@pytest.mark.parametrize('form',['null','absent'])
def test_ordinary_or_legacy_task_exports_empty_parent(retest_record,form):
    if form == 'null':
        retest_record['parent_job_id'] = None
    else:
        del retest_record['parent_job_id']
    columns,rows = read_csv(export_job(retest_record,'csv')[0])
    assert columns[-1] == 'parent_job_id'
    assert all(row['parent_job_id'] == '' for row in rows)


def test_device_result_cannot_replace_task_level_parent(retest_record):
    retest_record['results'][0]['parent_job_id'] = 'SYNTHETIC-DEVICE-RELATION'
    _,rows = read_csv(export_job(retest_record,'csv')[0])
    assert rows[0].get('parent_job_id') == retest_record['parent_job_id']


def test_immediate_retest_chain_is_joinable_using_only_csv(tmp_path):
    service = InspectionService(tmp_path / 'jobs.db',demo=True)
    try:
        first = wait_finished(service,service.create({'scenario':'faults','station_id':'SYNTHETIC-CSV-CHAIN'})['id'])
        second = wait_finished(service,service.retest(first['id'],{'scenario':'faults'})['id'])
        third = wait_finished(service,service.retest(second['id'],{'scenario':'healthy'})['id'])
        edges = {}
        for task in [first,second,third]:
            _,rows = read_csv(export_job(task,'csv')[0])
            assert all('parent_job_id' in row for row in rows)
            assert len({row['parent_job_id'] for row in rows}) == 1
            edges[rows[0]['job_id']] = rows[0]['parent_job_id']
        assert edges == {first['id']:'',second['id']:first['id'],third['id']:second['id']}
        assert service.get(first['id']) == first and service.get(second['id']) == second
    finally:
        service.close()


def test_csv_provenance_is_independent_of_ui_language(retest_record):
    chinese = export_job(retest_record,'csv',lang='zh')[0]
    english = export_job(retest_record,'csv',lang='en')[0]
    assert chinese == english
    assert read_csv(chinese)[1][0].get('parent_job_id') == retest_record['parent_job_id']


def test_loopback_export_route_preserves_actual_persisted_parent(api):
    service,server,request = api
    status,body = request('POST','/api/jobs',{'scenario':'faults','station_id':'SYNTHETIC-CSV-HTTP'})
    assert status == 202
    first = wait_finished(service,body['job']['id'])
    status,body = request('POST','/api/jobs/' + first['id'] + '/retest',{'scenario':'healthy'})
    assert status == 202
    second = wait_finished(service,body['job']['id'])
    status,content = request('GET','/api/jobs/' + second['id'] + '/export?format=csv&lang=en')
    assert status == 200
    _,rows = read_csv(content)
    assert {row.get('parent_job_id') for row in rows} == {first['id']}
    assert service.get(first['id']) == first
