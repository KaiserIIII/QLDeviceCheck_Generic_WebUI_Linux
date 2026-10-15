"""Event-controlled real loopback teardown, no devices or external transport."""
import http.client
import json
import socket
import threading

import pytest

from inspection.http import Handler, HttpError, MAX_BODY, make_server
from inspection.service import InspectionService


@pytest.mark.parametrize('headers,expected', [
    ('Authorization: Bearer wrong\r\n',401),
    ('Authorization: Bearer fixture\r\nOrigin: https://foreign.invalid\r\n',403),
])
def test_rejected_post_retains_error_while_draining_already_sent_body(tmp_path,monkeypatch,headers,expected):
    admitted=threading.Event()
    body_sent=threading.Event()
    finished=threading.Event()
    original_access=Handler._access
    def gated_access(handler,path):
        try:
            return original_access(handler,path)
        except HttpError:
            admitted.set()
            assert body_sent.wait(2),'test never sent rejected request body'
            raise
    monkeypatch.setattr(Handler,'_access',gated_access)
    service=InspectionService(tmp_path/'jobs.db',demo=True)
    server=make_server('127.0.0.1',0,service,token='fixture')
    get_request=server.get_request
    discarded=[]
    class TrackedSocket:
        def __init__(self,raw):self.raw=raw
        def __getattr__(self,name):return getattr(self.raw,name)
        def recv(self,size,*args):
            data=self.raw.recv(size,*args)
            discarded.append(data)
            return data
        def shutdown(self,how):return self.raw.shutdown(how)
    def tracked_request():
        raw,address=get_request()
        return TrackedSocket(raw),address
    server.get_request=tracked_request
    original_close=server.close_request
    def close_request(request):
        try:return original_close(request)
        finally:finished.set()
    server.close_request=close_request
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    body=b' '*(32*1024)
    try:
        with socket.create_connection(server.server_address,timeout=2) as client:
            request=('POST /api/jobs HTTP/1.1\r\nHost: 127.0.0.1:'+str(server.server_port)+'\r\nContent-Length: '+str(len(body))+'\r\n'+headers+'\r\n')
            client.sendall(request.encode('ascii'))
            assert admitted.wait(2),'server did not reach header admission'
            # Header parser has already stopped: these bytes remain in TCP,
            # not in a speculative BufferedReader header read.
            client.sendall(body)
            body_sent.set()
            response=http.client.HTTPResponse(client)
            response.begin()
            assert response.status==expected
            assert json.loads(response.read())['ok'] is False
        assert finished.wait(2),'server did not finish bounded teardown'
        assert b''.join(discarded)==body,'unread rejected body was closed without bounded drain'
        assert service.list_jobs()['total']==0
    finally:
        body_sent.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        service.close()


def test_discard_is_byte_bounded_even_for_unending_input():
    consumed=[]
    class Socket:
        def shutdown(self,how):assert how==socket.SHUT_WR
        def settimeout(self,value):assert 0<value<=.1
        def recv(self,size):
            consumed.append(size)
            return b'x'*size
    handler=object.__new__(Handler)
    handler.connection=Socket()
    handler._drain_rejected_input()
    assert sum(consumed)==MAX_BODY+1


@pytest.mark.parametrize('epoch',[100.0,1_000_000.0,1_000_000_000.0])
def test_discard_deadline_does_not_reset_after_each_chunk(monkeypatch,epoch):
    from inspection import http
    times=iter([epoch,epoch,epoch+.04,epoch+.11])
    monkeypatch.setattr(http.time,'monotonic',lambda:next(times))
    timeouts=[]
    class Socket:
        def shutdown(self,how):pass
        def settimeout(self,value):timeouts.append(value)
        def recv(self,size):return b'x'
    handler=object.__new__(Handler)
    handler.connection=Socket()
    handler._drain_rejected_input()
    assert len(timeouts)==2
    assert 0<timeouts[1]<timeouts[0]<=.1
