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
