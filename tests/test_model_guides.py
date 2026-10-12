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
