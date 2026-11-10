"""受控代码屏幕视频：真实Python报错+已标注的声音对照；不是课堂实拍准确率测试。"""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import Settings
from app.services.videos import VideoService
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient
from app.services.transcript_quality import transcript_warnings

DATA = ROOT / 'datasets/code_debug_eval'
CASES = {
    'dev_name': {'split':'dev','code':'prices = [10, 20]\nprint(sum(price))\n',
                 'question':'我想计算总价，代码运行报错。请结合代码和终端帮我定位、给最小修改步骤。',
                 'speech':None,
                 'expected_error':'NameError','expected_check':'变量price与prices不一致，改为prices'},
    'test_type': {'split':'test','code':'first = "12"\nsecond = 8\ntotal = first + second\nprint(total)\n',
                  'question':'我希望把两个数字相加得到20，但运行报错。请看视频，说明原因、需要改哪里以及怎么验证。',
                  'speech':None,'mismatch_audio':True,
                  'expected_error':'TypeError','expected_check':'字符串与整数直接相加，按求和目的先转换为整数'},
    'test_unreadable': {'split':'test','code':'prices = [10, 20]\nprint(sum(price))\n',
                       'question':'代码报错了，请看这段视频帮我定位。', 'speech':None,
                       'expected_error':None,'expected_check':'画面模糊，说明信息不足并索要清楚画面或文本','blur':True},
}
CODE = ['backend/app/services/' + name for name in ['videos.py','chat.py','audio_evidence.py','transcript_quality.py',
        'video_debug.py','video_tasks.py','frame_quality.py','video_selection.py']]
CODE += ['backend/app/prompts/' + name for name in ['video.md','audio.md','video_debug.md']]
CODE += ['frontend/src/' + name for name in ['VideoCodeDebug.jsx','VideoChatPanel.jsx','VideoWorkspace.jsx','AudioEvidence.jsx','code-debug.mjs','chat-workflow.mjs']]
CODE += ['tools/evaluate_code_video.py']
CODE += ['backend/app/api/routes/chat.py','backend/app/core/config.py','frontend/src/code-debug.mjs']


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def verify():
    frozen=json.loads((DATA/'freeze.json').read_text(encoding='utf8'))
    for name,checksum in frozen['code_sha256'].items():
        assert sha(ROOT/name)==checksum, '冻结后代码改变：'+name


def freeze():
    assert not (DATA/'freeze.json').exists(), '禁止覆盖冻结'
    write(DATA/'freeze.json',{'at':datetime.now(timezone.utc).isoformat(),'code_sha256':{name:sha(ROOT/name) for name in CODE},
        'policy':'dev用于调试，test为预先定义的受控检查题，冻结后生成/推理；非自然实拍、非盲测或专业标注集。'})


def prepare(split):
    if split=='test':verify()
    mono=ImageFont.truetype('C:/Windows/Fonts/consola.ttf',32)
    small=ImageFont.truetype('C:/Windows/Fonts/consola.ttf',23)
    for name,case in CASES.items():
        if case['split']!=split:continue
        directory=DATA/'media'/name;directory.mkdir(parents=True,exist_ok=True)
        source=directory/'lesson.py';source.write_text(case['code'],encoding='utf8')
        # 仅执行本脚本内固定、可审阅的四行测试程序；不执行用户/模型提供的代码。
        process=subprocess.run([sys.executable,str(source)],capture_output=True,text=True,timeout=5)
        assert process.returncode!=0 and ('NameError' in process.stderr or 'TypeError' in process.stderr)
        code=Image.new('RGB',(1920,1080),'#161b22');draw=ImageDraw.Draw(code)
        draw.text((60,45),'Lesson example  |  Python source',font=mono,fill='#79c0ff')
        for index,line in enumerate(case['code'].splitlines(),1):
            draw.text((65,160+index*70),f'{index:2}  {line}',font=mono,fill='#e6edf3')
        terminal=Image.new('RGB',(1920,1080),'#181818');draw=ImageDraw.Draw(terminal)
        draw.text((60,45),'Terminal  |  python lesson.py',font=mono,fill='#79c0ff')
        for index,line in enumerate(process.stderr.splitlines()):
            draw.text((60,170+index*50),line,font=small,fill='#f0b9b9')
        if case.get('blur'):
            code=code.filter(ImageFilter.GaussianBlur(18));terminal=terminal.filter(ImageFilter.GaussianBlur(18))
        target=directory/'clip.mp4';audio=None;duration=16
        if case.get('mismatch_audio'):
            from prepare_video_eval import read_audio
            source_audio=ROOT/'datasets/video_eval_v2/test/widya_nan.mp4'
            values=read_audio(source_audio,duration)
            audio=np.zeros(16000*duration,dtype=np.float32)
            audio[16000:]=values[:len(audio)-16000]
        code.save(directory/'code.png');terminal.save(directory/'terminal.png')
        if not target.exists():
            with av.open(str(target),'w') as output:
                stream=output.add_stream('libx264',rate=8);stream.width=1920;stream.height=1080;stream.pix_fmt='yuv420p';stream.options={'crf':'18','preset':'fast'}
                sound=output.add_stream('aac',rate=16000) if audio is not None else None
                if sound:sound.layout='mono'
                for index in range(duration*8):
                    image=code if index/8<7 else terminal
                    frame=av.VideoFrame.from_image(image);frame.pts=index;frame.time_base=Fraction(1,8)
                    for packet in stream.encode(frame):output.mux(packet)
                for packet in stream.encode():output.mux(packet)
                if audio is not None:
                    for start in range(0,len(audio),1024):
                        frame=av.AudioFrame.from_ndarray(audio[start:start+1024][None,:],format='fltp',layout='mono')
                        frame.sample_rate=16000;frame.pts=start;frame.time_base=Fraction(1,16000)
                        for packet in sound.encode(frame):output.mux(packet)
                    for packet in sound.encode():output.mux(packet)
        write(DATA/f'{name}_manifest.json',{'id':name,**case,'file':str(target.relative_to(DATA)).replace('\\','/'),
            'sha256':sha(target),'actual_stderr':process.stderr,'speech_start':1 if audio is not None else None,
            'audio_source': {'page':'https://commons.wikimedia.org/wiki/File:Widya_20210426_nan.ogg',
                'author':'Wikitongues','license':'CC-BY-SA-4.0','prepared_parent':'datasets/video_eval_v2/test/widya_nan.mp4',
                'prepared_parent_sha256':sha(ROOT/'datasets/video_eval_v2/test/widya_nan.mp4')} if audio is not None else None,
            'note':'程序合成屏幕视频，不是课堂实拍；test_type混入Wikitongues Widya公开视频的无关讲话作声音负对照，未合成/声称有人朗读代码问题。'})
        print('prepared '+name,flush=True)


def settings():
    runtime=DATA/'runtime'
    return Settings().model_copy(update={'video_storage_dir':runtime/'videos','chat_database_path':runtime/'chat.sqlite3',
                                        'role_database_path':runtime/'roles.sqlite3'})


def evaluate(name):
    if CASES[name]['split']=='test':verify()
    record=DATA/'reports'/f'{name}.json'
    if record.exists():print('reused '+name);return
    receipt=DATA/'reports'/f'{name}_receipt.json'
    assert not receipt.exists(),'已有调用回执，不自动重复付费'
    manifest=json.loads((DATA/f'{name}_manifest.json').read_text(encoding='utf8'))
    path=DATA/manifest['file'];assert sha(path)==manifest['sha256']
    config=settings();videos=VideoService(config)
    with path.open('rb') as source:video=videos.upload(source)
    class RecordingClient(DeepSeekClient):
        def complete(self,messages,**kwargs):
            reply=super().complete(messages,**kwargs);write(receipt,{'reply':reply.model_dump()});return reply
    client=RecordingClient(config);videos.client=client;chat=ChatService(config,client,videos)
    request_id=hashlib.sha256(('code-video-eval:'+name).encode()).hexdigest()[:32]
    try:
        result=chat.send(conversation_id=None,request_id=request_id,message=CASES[name]['question'],image_id=None,
                         video_id=video.video_id,debug_code=True)
        output={'id':name,'status':'accepted','turn':result,'audio_warnings':transcript_warnings(video.audio)}
    except Exception as exc:
        output={'id':name,'status':'rejected','error':str(exc)}
    output['usage']=json.loads(receipt.read_text(encoding='utf8'))['reply']['usage'] if receipt.exists() else None
    output['video_id']=video.video_id
    write(record,output)
    print(json.dumps({'id':name,'status':output['status'],'usage':output['usage']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare-dev','prepare-test','freeze','dev_name','test_type','test_unreadable'])
    args=parser.parse_args()
    if args.action.startswith('prepare-'):prepare(args.action.split('-')[1])
    elif args.action=='freeze':freeze()
    else:evaluate(args.action)
