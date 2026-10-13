"""H3 adapts the current server contract without LTX wire defaults."""
import json
from pathlib import Path

import httpx
import pytest

from easel_cli import client as module
from easel_cli.cli import build_parser
from easel_cli.client import EaselClient, EaselError
from test_video_controls import parts


def capabilities():
    return {'object': 'video.capabilities', 'schema_version': 1, 'model': 'minimax-h3',
        'fps': 24, 'frames': {'min': 124, 'max': 362, 'default': 124, 'step': 17, 'offset': 5},
        'default_size': '864x480', 'seed': {'min': '0', 'max': str(2**64-1), 'encoding': 'decimal_string'},
        'size_presets': [{'size': '1280x720', 'width': 1280, 'height': 704, 'max_frames': 175}],
        'size_constraints': {'custom_sizes': True, 'format': 'WIDTHxHEIGHT', 'min_dimension': 256,
            'max_dimension': 2048, 'dimension_multiple': 32, 'max_pixels': 2097152,
            'max_pixel_frames': 160000000, 'aliases': {'1280x720': '1280x704'}},
        'profiles': {name: {'supported': True, 'available': True} for name in ('fl8','ia2v_fl8','ref20','temporal_ref20')},
        'semantic_references': {'max_count': 2, 'fields': ['image_index']},
        'temporal_groups': {'max_count': 3, 'image_counts': [1,5,22,39], 'fields': ['frame_index','image_indices']},
        'uploads': {'field': 'images', 'mime_types': ['image/png','image/jpeg'], 'max_total_bytes': 33554432,
            'max_count': 119, 'max_image_pixels': 32000000, 'max_total_pixels': 64000000},
        'source_audio': {'available': True, 'field': 'source_audio', 'max_bytes': 33554432,
            'mime_types': ['audio/wav','audio/mpeg','audio/flac','audio/ogg','audio/mp4']}}


def api(cap=None, fail=False):
    calls=[]
    def handle(request):
        calls.append(request)
        if request.method == 'GET':
            assert request.url.params['model'] == 'minimax-h3'
            if request.url.path.endswith('/loras'):
                return httpx.Response(200,json={'model':'minimax-h3','data':[]})
            return httpx.Response(200,json=cap if cap is not None else capabilities())
        if fail: raise httpx.ReadError('lost receipt')
        return httpx.Response(200,json={'id':'video_1_bWluaW1heC1oMw:124:768x512_native','status':'queued'})
    return EaselClient('https://easel.test','key',http_client=httpx.Client(transport=httpx.MockTransport(handle))),calls


def image(tmp_path,name='first.png',data=b'image'):
    path=tmp_path/name;path.write_bytes(data);return path


def fields(request):
    return {name: body.decode() for name,filename,_,body in parts(request) if filename is None}


def test_h3_portrait_speech_wire_and_submit_once(tmp_path):
    client,calls=api()
    receipt=client.create_video('articulation; closed lips during pauses',model='minimax-h3',frames=124,
        size='768x512',seed=2**64-1,input_reference=image(tmp_path),source_audio=image(tmp_path,'speech.wav',b'audio'))
    assert ':' in receipt['id']
    assert len(calls)==2
    assert fields(calls[-1])=={'prompt':'articulation; closed lips during pauses','model':'minimax-h3',
        'frames':'124','size':'768x512','seed':str(2**64-1)}
    assert [name for name,filename,_,_ in parts(calls[-1]) if filename]==['input_reference','source_audio']


def test_h3_runtime_defaults_grid_seed_and_custom_sizes():
    cap=capabilities();cap['frames']={'min':16,'max':40,'default':24,'step':8,'offset':0}
    cap['default_size']='320x288';cap['seed']['max']='100'
    client,calls=api(cap);client.create_video('shot',model='minimax-h3')
    assert fields(calls[-1])['frames']=='24' and fields(calls[-1])['size']=='320x288'
    for kw in [{'frames':25},{'frames':48},{'seed':101},{'size':'319x288'}]:
        with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',**kw)
    client.create_video('shot',model='minimax-h3',frames=32,size='352x288',seed=100)


@pytest.mark.parametrize('kw',[{'seconds':4},{'camera_lora':'static'},{'camera_lora_strength':1},
    {'loras':[]},{'motion_speed':.5},{'lora_reference':Path('x.png')},{'lora_reference_strength':.5},
    {'guiding_frames':[]}])
def test_h3_rejects_every_ltx_argument(kw):
    client,calls=api()
    with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',**kw)
    assert not calls


@pytest.mark.parametrize('kw',[{'frames':124},{'semantic_references':[]},{'temporal_groups':[]}])
def test_ltx_rejects_h3_arguments(kw):
    client,calls=api()
    with pytest.raises(EaselError):client.create_video('shot',**kw)
    assert not calls


@pytest.mark.parametrize('fault',['old','model','frames','seed','sizes','uploads','references','groups','profiles','unavailable'])
def test_h3_unknown_or_unavailable_contract_never_posts(fault):
    cap=capabilities()
    if fault=='old': cap={'object':'video.capabilities','schema_version':1,'model':'minimax-h3'}
    elif fault=='model':cap['model']='ltx-2.5'
    elif fault=='unavailable':cap['profiles']['fl8']['available']=False
    else:cap[{'sizes':'size_constraints','references':'semantic_references','groups':'temporal_groups'}.get(fault,fault)]=None
    client,calls=api(cap)
    with pytest.raises(EaselError,match='capabilit|unavailable'):client.create_video('shot',model='minimax-h3')
    assert len(calls)==1


@pytest.mark.parametrize('frames,size',[(125,'768x512'),(123,'768x512'),(379,'768x512'),(362,'1280x720'),(124,'2048x2048')])
def test_h3_scalar_and_resource_admission(frames,size):
    client,calls=api()
    with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',frames=frames,size=size)
    assert len(calls)==1


def test_semantic_temporal_indexing_no_local_paths(tmp_path):
    client,calls=api();ref=image(tmp_path,'ref.jpg');still=image(tmp_path)
    client.create_video('shot',model='minimax-h3',semantic_references=[ref],
        temporal_groups=[module.TemporalGroup(20,[still]),module.TemporalGroup(0,[still]*5)])
    body=fields(calls[-1])
    assert json.loads(body['semantic_references'])==[{'image_index':0}]
    assert json.loads(body['temporal_groups'])==[{'frame_index':20,'image_indices':[1]},
        {'frame_index':0,'image_indices':[2,3,4,5,6]}]
    assert sum(name=='images' for name,filename,_,_ in parts(calls[-1]) if filename)==7
    assert str(tmp_path).encode() not in calls[-1].content


@pytest.mark.parametrize('fault',['refs','groups','count','overlap','bounds','bool','first','mime','audio','profile','audio-profile'])
def test_conditioning_bounds_and_readiness(tmp_path,fault):
    cap=capabilities();still=image(tmp_path);kw={}
    if fault=='refs':kw['semantic_references']=[still]*3
    elif fault=='groups':kw['temporal_groups']=[module.TemporalGroup(i,[still]) for i in range(4)]
    elif fault=='count':kw['temporal_groups']=[module.TemporalGroup(0,[still]*2)]
    elif fault=='overlap':kw['temporal_groups']=[module.TemporalGroup(0,[still]*5),module.TemporalGroup(4,[still])]
    elif fault=='bounds':kw['temporal_groups']=[module.TemporalGroup(124,[still])]
    elif fault=='bool':kw['temporal_groups']=[module.TemporalGroup(True,[still])]
    elif fault=='first':kw.update(input_reference=still,semantic_references=[still])
    elif fault=='mime':kw['input_reference']=image(tmp_path,'x.webp')
    elif fault=='audio':cap['source_audio']['available']=False;kw['source_audio']=image(tmp_path,'x.wav')
    elif fault=='audio-profile':cap['profiles']['ia2v_fl8']['available']=False;kw['source_audio']=image(tmp_path,'x.wav')
    else:cap['profiles']['ref20']['available']=False;kw['semantic_references']=[still]
    client,calls=api(cap)
    with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',**kw)
    assert all(call.method=='GET' for call in calls)


@pytest.mark.parametrize('fail',[False,True])
def test_h3_handles_closed_and_lost_receipt_is_single_post(tmp_path,monkeypatch,fail):
    handles=[];original=EaselClient._upload
    def capture(*args,**kwargs):
        result=original(*args,**kwargs);handles.append(result[1]);return result
    monkeypatch.setattr(EaselClient,'_upload',staticmethod(capture))
    client,calls=api(fail=fail)
    if fail:
        with pytest.raises(EaselError,match='ambiguous'):client.create_video('shot',model='minimax-h3',input_reference=image(tmp_path))
    else:client.create_video('shot',model='minimax-h3',input_reference=image(tmp_path))
    assert sum(c.method=='POST' for c in calls)==1
    assert handles and all(h.closed for h in handles)


def test_h3_server_upload_budgets_independent(tmp_path):
    cap=capabilities();cap['uploads']['max_total_bytes']=5;cap['source_audio']['max_bytes']=5
    client,calls=api(cap)
    client.create_video('shot',model='minimax-h3',input_reference=image(tmp_path,data=b'12345'),source_audio=image(tmp_path,'x.wav',b'12345'))
    with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',semantic_references=[image(tmp_path)]*2)
    with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',source_audio=image(tmp_path,'x.wav',b'123456'))
    assert sum(c.method=='POST' for c in calls)==1


def test_explicit_h3_discovery():
    client,calls=api()
    assert client.video_capabilities('minimax-h3')['model']=='minimax-h3'
    assert client.list_loras('minimax-h3')==[]
    assert len(calls)==2


def test_cli_h3_flags_and_model_specific_defaults():
    parser=build_parser();args=parser.parse_args(['video','create','shot','--model','minimax-h3','--frames','124',
        '--size','768x512','--semantic-reference','ref.png','--temporal-group','0','a.png','b.png'])
    assert args.seconds is None and args.frames==124 and args.semantic_reference==[Path('ref.png')]
    assert args.temporal_group==[['0','a.png','b.png']]
    assert parser.parse_args(['video','create','shot']).size is None
    assert parser.parse_args(['video','capabilities','--model','minimax-h3']).model=='minimax-h3'
    assert parser.parse_args(['video','loras','--model','minimax-h3']).model=='minimax-h3'


def test_h3_selected_profile_does_not_require_generated_audio(tmp_path):
    cap=capabilities();cap['profiles']['fl8']['available']=False
    client,calls=api(cap)
    client.create_video('shot',model='minimax-h3',input_reference=image(tmp_path),source_audio=image(tmp_path,'x.wav'))
    assert sum(c.method=='POST' for c in calls)==1


def test_h3_without_audio_does_not_require_new_audio_contract():
    cap=capabilities();cap.pop('source_audio')
    client,calls=api(cap)
    client.create_video('shot',model='minimax-h3')
    assert sum(c.method=='POST' for c in calls)==1


def test_explicit_ltx_catalog_preserves_server_without_model_metadata():
    calls=[]
    def handle(request):
        calls.append(request);return httpx.Response(200,json={'data':[]})
    client=EaselClient('https://easel.test','key',http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    assert client.list_loras('ltx-2.5')==[]
    assert calls[0].url.params['model']=='ltx-2.5'


@pytest.mark.parametrize('kind',['empty','missing','directory','fifo','mime','combined'])
def test_h3_bad_reference_file_never_posts(tmp_path,kind):
    import os
    path=tmp_path/'first.png';kw={}
    if kind=='directory':path.mkdir()
    elif kind=='fifo':os.mkfifo(path)
    elif kind=='mime':path=image(tmp_path,'bad.txt')
    elif kind!='missing':path.write_bytes(b'' if kind=='empty' else b'12345')
    cap=capabilities()
    if kind=='combined':cap['uploads']['max_total_bytes']=8;kw['semantic_references']=[path,path]
    else:kw['input_reference']=path
    client,calls=api(cap)
    with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',**kw)
    assert all(c.method=='GET' for c in calls)


def test_h3_client_image_audio_safety_limits_are_independent(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'MAX_UPLOAD_BYTES',5);monkeypatch.setattr(module,'MAX_AUDIO_BYTES',5)
    client,calls=api()
    client.create_video('shot',model='minimax-h3',input_reference=image(tmp_path,data=b'12345'),source_audio=image(tmp_path,'x.wav',b'12345'))
    for kw in [{'input_reference':image(tmp_path,data=b'123456')},{'source_audio':image(tmp_path,'x.wav',b'123456')}]:
        with pytest.raises(EaselError):client.create_video('shot',model='minimax-h3',**kw)
    assert sum(c.method=='POST' for c in calls)==1


@pytest.mark.parametrize('path,value',[(('frames','step'),True),(('frames','offset'),99),
    (('seed','max'),2**64-1),(('seed','max'),'NaN'),(('size_constraints','dimension_multiple'),0),
    (('size_constraints','custom_sizes'),'true'),(('uploads','max_total_bytes'),False),
    (('uploads','mime_types'),['image/webp']),(('profiles','fl8'),{'supported':True,'available':'yes'}),
    (('temporal_groups','image_counts'),[True]),(('semantic_references','max_count'),False)])
def test_malformed_runtime_values_fail_closed(path,value):
    cap=capabilities();cap[path[0]][path[1]]=value;client,calls=api(cap)
    with pytest.raises(EaselError,match='capabilit'):client.create_video('shot',model='minimax-h3')
    assert len(calls)==1
