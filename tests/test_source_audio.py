import httpx
import pytest
from easel_cli.client import EaselClient, EaselError, MAX_UPLOAD_BYTES
from easel_cli.cli import build_parser


def make_client(capability, calls, *, model='ltx-2.5', fail=False):
    def handler(request):
        calls.append(request)
        if request.method == 'GET':
            assert str(request.url) == f'https://easel.test/v1/videos/capabilities?model={model}'
            return httpx.Response(200, json={'object': 'video.capabilities', 'schema_version': 1, 'model': model, 'source_audio': capability})
        if fail:
            raise httpx.ReadError('lost receipt')
        return httpx.Response(200, json={'id': 'video_test', 'status': 'queued'})
    return EaselClient('https://easel.test', 'key', http_client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize('extension,mime', [('wav','audio/wav'), ('mp3','audio/mpeg'), ('flac','audio/flac'), ('ogg','audio/ogg'), ('m4a','audio/mp4')])
def test_one_audio_part_exact_model(extension, mime, tmp_path):
    path = tmp_path / ('speech.'+extension)
    path.write_bytes(b'audio')
    calls = []
    client = make_client({'available': True}, calls)
    assert client.create_video('speaking', source_audio=path)['id'] == 'video_test'
    assert [r.method for r in calls] == ['GET', 'POST']
    assert calls[1].content.count(b'name="source_audio"') == 1
    assert ('Content-Type: '+mime).encode() in calls[1].content
    assert build_parser().parse_args(['video','create','speaking','--source-audio',str(path)]).source_audio == path


@pytest.mark.parametrize('capability', [None, {}, {'available':False}, {'available':1}])
def test_old_or_unavailable_blocks_only_audio(capability, tmp_path):
    path = tmp_path / 'speech.wav'; path.write_bytes(b'audio')
    calls=[]; client=make_client(capability,calls)
    with pytest.raises(EaselError, match='source_audio'):
        client.create_video('speaking', source_audio=path)
    assert len(calls) == 1
    client.create_video('speaking')
    assert calls[-1].method == 'POST'
    assert b'name="source_audio"' not in calls[-1].content


def test_mismatched_model_blocks_audio(tmp_path):
    path=tmp_path/'speech.wav'; path.write_bytes(b'audio')
    calls=[]; client=make_client({'available':True},calls,model='other')
    # The handler verifies exact routing, so use its own transport for a wrong response.
    client._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,json={'object':'video.capabilities','schema_version':1,'model':'other','source_audio':{'available':True}})))
    with pytest.raises(EaselError, match='source_audio'):
        client.create_video('speaking',source_audio=path)


@pytest.mark.parametrize('kind', ['empty','oversize','wrong','missing','directory'])
def test_bad_audio_does_not_post(kind,tmp_path):
    path=tmp_path/('speech.txt' if kind=='wrong' else 'speech.wav')
    if kind=='directory': path.mkdir()
    elif kind!='missing':
        path.write_bytes(b'' if kind=='empty' else b'audio')
        if kind=='oversize':
            with path.open('r+b') as handle: handle.truncate(MAX_UPLOAD_BYTES+1)
    calls=[]; client=make_client({'available':True},calls)
    with pytest.raises(EaselError): client.create_video('speaking',source_audio=path)
    assert all(r.method=='GET' for r in calls)


def test_independent_image_audio_limits(tmp_path, monkeypatch):
    monkeypatch.setattr('easel_cli.client.MAX_UPLOAD_BYTES', 8)
    monkeypatch.setattr('easel_cli.client.MAX_AUDIO_BYTES', 8)
    image=tmp_path/'first.png'; image.write_bytes(b'x'*8)
    audio=tmp_path/'speech.wav'; audio.write_bytes(b'y'*8)
    calls=[];client=make_client({'available':True},calls)
    client.create_video('speaking',input_reference=image,source_audio=audio)
    assert calls[-1].content.count(b'name="source_audio"')==1


def test_ambiguous_receipt_does_not_retry(tmp_path):
    audio=tmp_path/'speech.wav';audio.write_bytes(b'audio')
    calls=[];client=make_client({'available':True},calls,fail=True)
    with pytest.raises(EaselError,match='ambiguous'):client.create_video('speaking',source_audio=audio)
    assert sum(r.method=='POST' for r in calls)==1


def test_current_video_receipt_metadata_is_preserved():
    job_id = 'video_1791800278_bHR4LTIuNQ:49:512x320_contract-prompt-1'
    client = EaselClient('https://easel.test', 'key')
    client._validate_receipt({'id': job_id, 'status': 'queued'}, 'video')
    assert client._job_path('video', job_id) == '/v1/videos/'+job_id
    for invalid in ['video_x/../models', 'video_x?other', 'video_x#fragment', 'video_x%2fescape']:
        with pytest.raises(EaselError):
            client._job_path('video', invalid)
    with pytest.raises(EaselError):
        client._job_path('image', 'image_job_x:metadata')
    client.close()


@pytest.mark.parametrize('fail', [False, True])
def test_source_audio_handle_closed_after_submission(tmp_path, monkeypatch, fail):
    audio=tmp_path/'speech.wav'; audio.write_bytes(b'audio')
    handles=[]
    original=EaselClient._upload
    def capture(stack,path,**kwargs):
        result=original(stack,path,**kwargs)
        handles.append(result[1])
        return result
    monkeypatch.setattr(EaselClient,'_upload',staticmethod(capture))
    client=make_client({'available':True},[],fail=fail)
    if fail:
        with pytest.raises(EaselError): client.create_video('speaking',source_audio=audio)
    else:
        client.create_video('speaking',source_audio=audio)
    assert handles and all(handle.closed for handle in handles)
