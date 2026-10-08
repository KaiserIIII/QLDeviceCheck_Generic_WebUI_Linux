import json
import re

import pytest

from inspection.reports import export_job
from test_http import api
from test_workbench import service, wait_finished


def test_english_html_report_translates_labels_and_preserves_evidence(service):
    job = wait_finished(service, service.create({'station_id': '英文工位 <script>x</script>',
                        'notes': '工控设备验收报告：原始备注', 'device_ids': ['DEMO_RELAY']})['id'])
    content, mime = export_job(job, 'html', lang='en')
    report = content.decode('utf8')
    assert '<html lang="en">' in report
    assert '<title>Industrial device acceptance report</title>' in report
    assert 'Selected devices only' in report and '1 / 3' in report
    assert 'SIMULATED' in report and 'cannot be used as live acceptance evidence' in report
    assert 'A partial retest does not establish whole-unit recovery.' in report
    assert all(not re.search('[\u3400-\u9fff]', heading) for heading in re.findall(r'<(?:h[124]|th|dt)>(.*?)</(?:h[124]|th|dt)>', report))
    assert '英文工位 &lt;script&gt;x&lt;/script&gt;' in report
    assert '<script>x</script>' not in report
    assert '工控设备验收报告：原始备注' in report
    assert job['results'][0]['name'] in report
    assert job['config_hash'] in report and job['results'][0]['response'] in report
    assert '@media print' in report and mime.startswith('text/html')


def test_default_chinese_and_machine_exports_remain_compatible(service):
    job = wait_finished(service, service.create({'notes': '原始测试证据'})['id'])
    assert b'<html lang="zh-CN">' in export_job(job, 'html')[0]
    assert '工控设备验收报告' in export_job(job, 'html', lang='zh')[0].decode()
    for format in ('json', 'csv'):
        assert export_job(job, format, lang='en') == export_job(job, format)
    raw = json.loads(export_job(job, 'json', lang='en')[0])
    assert raw['config_snapshot'] == job['config_snapshot']
    assert raw['results'] == job['results'] and raw['metadata'] == job['metadata']


def test_report_language_route(api):
    svc, server, request = api
    job = wait_finished(svc, svc.create({})['id'])
    path = '/api/jobs/' + job['id'] + '/export?format=html'
    status, content = request('GET', path + '&lang=en')
    assert status == 200 and b'<html lang="en">' in content
    assert '工控设备验收报告' in request('GET', path + '&lang=zh')[1].decode()
    assert request('GET', path + '&lang=fr')[0] == 400
    assert request('GET', path + '&lang=')[0] == 400


def test_language_resource_is_a_fixed_public_asset(api):
    svc, server, request = api
    server.access_token = 'secret'
    assert request('GET', '/assets/i18n.js')[0] == 200
    assert request('GET', '/assets/%2e%2e/config/device_list.json')[0] == 404


@pytest.mark.parametrize('lang', ['fr', '', None])
def test_unsupported_report_language_is_rejected(service, lang):
    job = wait_finished(service, service.create({})['id'])
    with pytest.raises(ValueError, match='Unsupported report language'):
        export_job(job, 'html', lang=lang)
