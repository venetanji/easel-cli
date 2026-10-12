import httpx
import pytest
from easel_cli.client import EaselClient, EaselError
from easel_cli.cli import build_parser


def test_exact_guide_route_and_payload():
    payload = {'model': 'minimax-h3', 'mode': 'base', 'text': 'reference', 'sources': ['https://source.test']}
    def handler(request):
        assert str(request.url) == 'https://easel.test/v1/models/minimax-h3/guide?mode=base'
        assert request.headers['authorization'] == 'Bearer key'
        return httpx.Response(200, json=payload)
    client = EaselClient('https://easel.test/v1', 'key', http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.get_model_guide('minimax-h3', 'base') == payload
    assert build_parser().parse_args(['model-guide', 'minimax-h3', '--mode', 'base']).mode == 'base'


@pytest.mark.parametrize('status,body', [(302, {}), (404, {'error': {'code': 'model_guide_unavailable'}}), (200, {'text': 'x'*16384})])
def test_reject_unavailable_redirect_and_oversize(status, body):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={'Location': 'https://other.test'})
    client = EaselClient('https://easel.test', 'key', http_client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    with pytest.raises(EaselError):
        client.get_model_guide('minimax-h3', 'base')
    assert len(calls) == 1


@pytest.mark.parametrize('model,mode', [('https://other.test', 'base'), ('../model', 'base'), ('minimax-h3', '../mode')])
def test_invalid_route_never_requests(model, mode):
    client = EaselClient('https://easel.test', 'key', http_client=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('unexpected request'))))
    with pytest.raises(EaselError):
        client.get_model_guide(model, mode)


def test_cli_guide_dispatch_and_sources(monkeypatch, capsys):
    from easel_cli import cli
    payload={'model':'minimax-h3','mode':'base','sources':['https://source.test'],'text':'reference'}
    def client(*args, **kwargs):
        return EaselClient(*args, **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,json=payload))))
    monkeypatch.setattr(cli,'EaselClient',client)
    monkeypatch.setenv('EASEL_API_KEY','test-key')
    assert cli.main(['model-guide','minimax-h3','--mode','base','--base-url','https://easel.test'])==0
    import json
    assert json.loads(capsys.readouterr().out)==payload


def test_bounded_stream_does_not_read_remainder():
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * (16 * 1024 + 1)
            pytest.fail('read beyond guide limit')
    http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,stream=Chunks())))
    client=EaselClient('https://easel.test','key',http_client=http)
    with pytest.raises(EaselError,match='16 KiB'):client.get_model_guide('minimax-h3','base')


@pytest.mark.parametrize('case', ['success', 'oversize', 'unavailable'])
def test_gzip_guide_decoded_once_and_bounded(case):
    import gzip
    import json
    payload = {'model': 'minimax-h3', 'mode': 'base', 'text': 'reference',
               'sources': ['https://source.test']}
    status = 200
    if case == 'oversize':
        payload['text'] = 'x' * (16 * 1024)
    elif case == 'unavailable':
        status = 404
        payload = {'error': {'message': 'No reviewed guide',
                             'code': 'model_guide_unavailable', 'param': 'mode'}}
    compressed = gzip.compress(json.dumps(payload).encode())
    def handler(request):
        return httpx.Response(status, content=compressed, headers={
            'Content-Encoding': 'gzip', 'Content-Length': str(len(compressed)),
            'Content-Type': 'application/json', 'Retry-After': '2'})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EaselClient('https://easel.test', 'key', http_client=http)
    if case == 'success':
        assert client.get_model_guide('minimax-h3', 'base') == payload
    elif case == 'oversize':
        assert len(compressed) < 16 * 1024
        with pytest.raises(EaselError, match='16 KiB'):
            client.get_model_guide('minimax-h3', 'base')
    else:
        with pytest.raises(EaselError) as error:
            client.get_model_guide('minimax-h3', 'base')
        assert error.value.status_code == 404
        assert error.value.code == 'model_guide_unavailable'
        assert error.value.param == 'mode'
        assert error.value.retry_after == 2
