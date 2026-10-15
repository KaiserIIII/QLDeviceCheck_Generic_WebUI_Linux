"""Unsupported catalog nodes must not disappear from acceptance denominators."""
import copy
import threading

import pytest

from core.config_manager import StandardDeviceConfig
from inspection.adapters import demo_config
from inspection.domain import catalog_devices, initial_result
from inspection.http import make_server
from inspection.service import InspectionService
from test_http import requester


def device_tree(depth=2, empty_children=None):
    snapshot = demo_config()
    parent = copy.deepcopy(snapshot['devices'][1])
    parent.update(device_id='SYNTH_PARENT', device_name='Synthetic parent', interface='')
    node = parent
    for index in range(1, depth + 1):
        child = copy.deepcopy(snapshot['devices'][1])
        child.update(device_id='SYNTH_CHILD_' + str(index), device_name='Synthetic child ' + str(index), interface='')
        node['child_devices'] = [child]
        node = child
    if empty_children is not None:
        node['child_devices'] = empty_children
    snapshot['devices'] = [parent]
    return snapshot


class AlwaysPassFake:
    def __init__(self):
        self.calls = 0

    def run(self, config, device_ids, scenario, cancelled, on_result):
        self.calls += 1
        for device in catalog_devices(config):
            if device['device_id'] in device_ids:
                result = initial_result(device, True)
                result.update(verdict='PASS', fault_code='', summary='Synthetic Fake response')
                on_result(result)


@pytest.mark.parametrize('depth', [2, 3])
def test_configuration_rejects_children_beyond_the_supported_execution_depth(depth):
    with pytest.raises(ValueError):
        StandardDeviceConfig(data=device_tree(depth))


@pytest.mark.parametrize('demo', [False, True])
def test_unsupported_tree_never_creates_record_or_calls_adapter(tmp_path, demo):
    adapter = AlwaysPassFake()
    service = InspectionService(tmp_path / 'jobs.db', demo=demo, adapter=adapter)
    try:
        with pytest.raises(ValueError):
            service.create({'station_id':'SYNTHETIC-ONLY'}, snapshot=device_tree())
        assert service.list_jobs()['total'] == 0
        assert adapter.calls == 0 and service.active_id is None
        assert not service.hardware_lock.locked()
    finally:
        service.close()


@pytest.fixture
def hierarchy_api(tmp_path, monkeypatch):
    adapter = AlwaysPassFake()
    service = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    monkeypatch.setattr(service, '_snapshot', device_tree)
    server = make_server('127.0.0.1', 0, service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, adapter, requester(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        service.close()


@pytest.mark.parametrize('endpoint,payload', [
    ('/api/config/validate', {'config':device_tree()}),
    ('/api/jobs', {'station_id':'SYNTHETIC-ONLY'}),
])
def test_http_rejects_unsupported_tree_before_reporting_valid_or_starting_job(hierarchy_api, endpoint, payload):
    service, adapter, request = hierarchy_api
    status, body = request('POST', endpoint, payload)
    assert status == 400 and body['ok'] is False
    assert service.list_jobs()['total'] == 0 and adapter.calls == 0


@pytest.mark.parametrize('empty_children', [None, [], ()])
def test_supported_parent_child_still_runs_with_complete_results(tmp_path, empty_children):
    # None means omitted; [] is the documented empty form. Existing tuples in
    # trusted in-process configurations are retained, not a new JSON feature.
    snapshot = device_tree(1, empty_children)
    adapter = AlwaysPassFake()
    service = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    try:
        job = service.create({}, snapshot=snapshot)
        service.worker.join(2)
        finished = service.get(job['id'])
        assert finished['status'] == 'completed'
        assert finished['summary']['expected'] == finished['summary']['passed'] == 2
        assert {result['device_id'] for result in finished['results']} == {'SYNTH_PARENT','SYNTH_CHILD_1'}
        assert adapter.calls == 1
    finally:
        service.close()
