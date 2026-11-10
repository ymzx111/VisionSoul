"""结构、音画可信度与代码排错连续对话；外部模型用模拟回执。"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import test_video_chat as video_chat_tests
from test_video import make_video, result_payload
from app.services.audio_evidence import AudioLink, AudioTranscript, SpeechSegment, audio_prompt, add_time_citations, normalize_model_links, model_context
from app.services.transcript_quality import transcript_warnings, trusted_transcript
from app.services.video_debug import CodeDebugReport
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.videos import SampleFrame
from app.services.videos import VideoError


def report():
    return {'status':'likely','visible_error':"NameError: name 'price' is not defined",'evidence':[
        {'frame_index':0,'kind':'code','excerpt':'prices = [10, 20]\nprint(sum(price))','line_number':None},
        {'frame_index':1,'kind':'error','excerpt':"NameError: name 'price' is not defined",'line_number':None}],
        'causes':[{'hypothesis':'price与已定义的prices名称不一致','evidence_ids':[0,1],'check':'改为prices后重新运行'}],
        'steps':['将sum(price)改为sum(prices)。','保存并重新运行，确认打印30。'],
        'suggested_code':'prices = [10, 20]\nprint(sum(prices))','missing':[]}


class TranscriptQualityTests(unittest.TestCase):
    def audio(self, text='这是一句循环转写' * 12, source='automatic'):
        return AudioTranscript(status='ready', source=source, segments=[SpeechSegment(id=0,start=1,end=1.05,text=text)])

    def test_repetition_and_impossible_density_mask_only_model_copy_not_original(self):
        audio=self.audio();original=audio.model_dump_json()
        self.assertTrue(transcript_warnings(audio))
        safe=trusted_transcript(audio)
        self.assertNotIn('循环转写',safe.segments[0].text)
        self.assertEqual(audio.model_dump_json(),original)
        frames=[SampleFrame(index=0,time_seconds=1,seen_at=[1],url='')]
        self.assertNotIn('循环转写',audio_prompt(audio,frames))
        self.assertIn('待校对',add_time_citations('回答','为什么',audio,[]))
        self.assertNotIn('循环转写',add_time_citations('回答','为什么',audio,[]))

    def test_corrected_and_normal_utterances_are_not_masked(self):
        corrected=self.audio(source='corrected')
        self.assertEqual(transcript_warnings(corrected),[])
        normal=self.audio('代码运行报错，请看看这个变量名。')
        normal.segments[0].end=5
        self.assertEqual(transcript_warnings(normal),[])
        self.assertEqual(trusted_transcript(normal).segments[0].text,normal.segments[0].text)

    def test_loop_split_across_short_segments_is_marked_for_review(self):
        audio=AudioTranscript(status='ready',segments=[SpeechSegment(id=i,start=i*3,end=i*3+2,text='我记得写了一句，我记得写了一句') for i in range(4)])
        self.assertEqual({r['segment_id'] for r in transcript_warnings(audio)},{0,1,2,3})
        self.assertEqual(len(trusted_transcript(audio).segments),4)

    def test_supports_need_explicit_content_and_reliable_speech(self):
        frames=[SampleFrame(index=0,time_seconds=1,seen_at=[1],url='')]
        links=[AudioLink(segment_id=0,candidate_id=0,relation='supports',detail='人物在讲话')]
        clean=self.audio('这里写着NameError');clean.segments[0].end=4
        normalized,notes=normalize_model_links(links,clean,frames)
        self.assertEqual(normalized[0].relation,'uncertain');self.assertTrue(notes)
        links[0].content_visible=True;links[0].detail='终端清楚显示NameError'
        self.assertEqual(normalize_model_links(links,clean,frames)[0][0].relation,'supports')
        self.assertEqual(normalize_model_links(links,self.audio(),frames)[0][0].relation,'uncertain')
        links[0].detail='对白内容无法由画面证实'
        self.assertEqual(normalize_model_links(links,clean,frames)[0][0].relation,'uncertain')

    def test_old_context_repetition_not_resent_but_visuals_and_corrections_survive(self):
        original={'audio':self.audio().model_dump(),'summary':'不可靠的旧对白总结','audio_links':[], 'observations':[{'description':'可见终端'}]}
        safe=model_context(original)
        self.assertNotIn('循环转写',json.dumps(safe,ensure_ascii=False))
        self.assertEqual(safe['observations'],original['observations'])
        self.assertIn('循环转写',original['audio']['segments'][0]['text'])


class DebugSchemaTests(unittest.TestCase):
    def test_unknown_frame_cause_source_and_blind_diagnosis_rejected(self):
        CodeDebugReport.model_validate(report()).check_references({0,1})
        with self.assertRaises(ValueError):CodeDebugReport.model_validate(report()).check_references({0})
        bad=report();bad['causes'][0]['evidence_ids']=[9]
        with self.assertRaises(ValueError):CodeDebugReport.model_validate(bad)
        with self.assertRaises(ValueError):CodeDebugReport.model_validate({'status':'insufficient','missing':[]})
        with self.assertRaises(ValueError):CodeDebugReport.model_validate({'status':'likely','missing':[]})

    def test_unreadable_report_requests_text_without_fake_evidence(self):
        value=CodeDebugReport.model_validate({'status':'insufficient','missing':['请放大终端错误或粘贴错误文本。']})
        self.assertEqual(value.evidence,[])


class DebugChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.fixtures.cleanup)
        path=Path(cls.fixtures.name)/'clip.mp4';make_video(path)
        cls.data=path.read_bytes()

    def setUp(self):
        video_chat_tests.VideoChatTests.setUp(self)
        self.debug_payload={**result_payload(),'code_debug':report()}
        def handler(request):
            payload=json.loads(request.content);self.calls.append(payload)
            text=json.dumps(self.debug_payload,ensure_ascii=False) if payload.get('response_format') else '把price改成prices后保存并重新运行。'
            return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':text},'finish_reason':'stop'}]})
        self.client=DeepSeekClient(self.settings,httpx.MockTransport(handler))
        self.chat.client=self.client;self.videos.client=self.client

    def test_debug_once_high_frames_then_followup_text_and_request_replay(self):
        args=dict(conversation_id=None,request_id='d'*32,message='代码运行报错，怎么改？',image_id=None,video_id=self.video.video_id,debug_code=True)
        first=self.chat.send(**args)
        images=video_chat_tests.pictures(self.calls[0])
        self.assertGreater(sum(row['image_url']['detail']=='high' for row in images),0)
        self.assertLessEqual(sum(row['image_url']['detail']=='high' for row in images),4)
        self.assertEqual(first['video_context']['code_debug']['visible_error'],report()['visible_error'])
        self.assertIn('可能原因',first['assistant_message']['content'])
        self.assertEqual(self.chat.send(**args)['replayed'],True)
        self.assertEqual(len(self.calls),1)
        second=self.chat.send(conversation_id=first['conversation_id'],request_id='e'*32,message='只改这个变量就行吗？',image_id=None)
        self.assertFalse(video_chat_tests.pictures(self.calls[1]))
        self.assertIn('code_debug',json.dumps(self.calls[1],ensure_ascii=False))
        self.assertEqual(second['conversation_id'],first['conversation_id'])
        with self.assertRaises(DeepSeekError):self.chat.send(**{**args,'debug_code':False})

    def test_bad_report_and_conflicting_modes_do_not_create_success_or_retry(self):
        self.debug_payload['code_debug']['evidence'][0]['frame_index']=60
        args=dict(conversation_id=None,request_id='f'*32,message='代码报错',image_id=None,video_id=self.video.video_id,debug_code=True)
        with self.assertRaises(DeepSeekError):self.chat.send(**args)
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(DeepSeekError):self.chat.send(**{**args,'inspect_video':True})
        self.assertEqual(len(self.calls),1)

    def test_corrupted_cached_debug_frame_is_rejected_without_new_model_call(self):
        self.videos.analyze(self.video.video_id)
        directory=self.settings.video_storage_dir/self.video.video_id
        path=self.videos._cache_path(directory)
        payload=json.loads(path.read_text(encoding='utf8'))
        payload['code_debug']['evidence'][0]['frame_index']=60
        path.write_text(json.dumps(payload),encoding='utf8')
        with self.assertRaises(VideoError):self.videos.info(self.video.video_id)
        self.assertEqual(len(self.calls),1)
