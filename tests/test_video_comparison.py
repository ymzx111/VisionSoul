from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from PIL import Image, ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core.config import Settings
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient,DeepSeekError
from app.services.video_comparison import clarity_score,compact_times
from app.services.videos import VideoService
from test_video import make_video,result_payload


def comparison():
    return {'target':'画面中的主体','category':'object','verdict':'likely','conclusion':'暂时更接近候选A，仍需细节。',
        'features':[{'frame_index':0,'detail':'可见红色区域'},{'frame_index':1,'detail':'随后可见蓝色区域'}],
        'candidates':[{'name':'候选A','supporting_features':[0,1],'differences':'色彩相符，纹理仍看不清。'}],
        'missing_features':['近距离纹理'],'next_shot':'请靠近主体拍清纹理。','basis':'visual_inference'}


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.fixtures.cleanup)
        path=Path(cls.fixtures.name)/'clip.mp4';make_video(path)
        cls.data=path.read_bytes()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.settings=Settings(_env_file=None,video_storage_dir=root/'videos',chat_database_path=root/'chat.sqlite3',deepseek_api_key='test-key-not-real')
        self.calls=[];self.payload={**result_payload(),'comparison':comparison()}
        def handler(request):
            body=json.loads(request.content);self.calls.append(body)
            answer=json.dumps(self.payload,ensure_ascii=False) if body.get('response_format') else '沿用暂定候选，仍缺纹理。'
            return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':answer},'finish_reason':'stop'}]})
        self.client=DeepSeekClient(self.settings,httpx.MockTransport(handler))
        self.service=VideoService(self.settings,self.client)
        self.video=self.service.upload(BytesIO(self.data))

    def test_local_clarity_remains_compatible_with_saved_manifests(self):
        image=Image.new('RGB',(160,160),'gray')
        flat=clarity_score(image);draw=ImageDraw.Draw(image)
        for x in range(0,160,8):draw.line((x,0,x,160),fill='white',width=3)
        self.assertGreater(clarity_score(image),flat)

    def test_compact_timestamps_never_connect_separated_occurrences(self):
        self.assertEqual(compact_times([0,.5,1,3,3.5,4,4.4]),'0-1;3-4;4.4')
        self.assertEqual(compact_times([0,.52,1,1.52,2,3]),'0-2;3')

    def test_comparison_schema_only_in_inspection_and_high_images_stay_at_four(self):
        self.service._observe(self.video)
        self.assertNotIn('supporting_features',self.calls[-1]['messages'][0]['content'])
        path=Path(self.temp.name)/'distinct.mp4';make_video(path,seconds=5,varying=True)
        video=self.service.upload(BytesIO(path.read_bytes()))
        self.service.inspect(video.video_id,'比对',[])
        self.assertIn('supporting_features',self.calls[-1]['messages'][0]['content'])
        images=[part for part in self.calls[-1]['messages'][-1]['content'] if part['type']=='image_url']
        self.assertEqual(len(images),6)
        self.assertGreater(sum(part['image_url']['detail']=='high' for part in images),0)
        self.assertLessEqual(sum(part['image_url']['detail']=='high' for part in images),4)

    def test_enhanced_single_request_uses_bounded_detail_and_returns_grounded_candidates(self):
        snapshot,reply=self.service.inspect(self.video.video_id,'辨识并比较两个候选。',[])
        self.assertEqual(len(self.calls),1)
        images=[part for part in self.calls[0]['messages'][-1]['content'] if part['type']=='image_url']
        self.assertEqual(len(images),len(self.video.frames))
        self.assertLessEqual(sum(part['image_url']['detail']=='high' for part in images),4)
        self.assertGreater(sum(part['image_url']['detail']=='high' for part in images),0)
        self.assertEqual(snapshot['comparison']['basis'],'visual_inference')
        self.assertTrue(reply.answer)
        self.assertIn('没有联网检索',self.calls[0]['messages'][0]['content'])

    def test_unknown_frame_or_candidate_feature_is_rejected_without_retry(self):
        for mutate in [lambda report:report['features'][0].update(frame_index=29),
                       lambda report:report['candidates'][0].update(supporting_features=[9])]:
            self.payload['comparison']=comparison();mutate(self.payload['comparison'])
            count=len(self.calls)
            with self.assertRaises(DeepSeekError):self.service.inspect(self.video.video_id,'辨识',[])
            self.assertEqual(len(self.calls),count+1)

    def test_insufficient_evidence_can_have_no_candidates(self):
        report=comparison();report.update(verdict='insufficient',candidates=[],conclusion='无法区分具体种类。')
        self.payload['comparison']=report
        snapshot,_=self.service.inspect(self.video.video_id,'这是什么品种？',[])
        self.assertEqual(snapshot['comparison']['candidates'],[])

    def test_legacy_video_without_details_still_works_and_never_claims_extra_detail(self):
        directory=self.settings.video_storage_dir/self.video.video_id
        for path in directory.glob('detail-*.jpg'):path.unlink()
        self.service.inspect(self.video.video_id,'比较',[])
        self.assertIn('本轮只有概览图',self.calls[0]['messages'][0]['content'])
        self.assertNotIn('"detail": "high"',json.dumps(self.calls[-1]))

    def test_followup_inspection_reopens_images_saves_report_and_replay_is_free(self):
        chat=ChatService(self.settings,self.client,self.service)
        first=chat.send(conversation_id=None,request_id='1'*32,message='我在杭州分享这段视频。',image_id=None,video_id=self.video.video_id)
        self.payload['comparison']=comparison()
        body={'conversation_id':first['conversation_id'],'request_id':'2'*32,'message':'能不能细看并比对？','image_id':None,'inspect_video':True}
        second=chat.send(**body)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(any(p['type']=='image_url' for p in self.calls[-1]['messages'][-1]['content']))
        self.assertEqual(second['video_context']['opening_message'],'我在杭州分享这段视频。')
        self.assertEqual(second['video_context']['comparison_question'],body['message'])
        self.assertIsNotNone(json.loads(chat.store.get(first['conversation_id'])['video_context'])['comparison'])
        self.assertTrue(chat.send(**body)['replayed']);self.assertEqual(len(self.calls),2)
        chat.send(conversation_id=first['conversation_id'],request_id='3'*32,message='还有什么没拍清楚？',image_id=None)
        self.assertNotIn('image_url',json.dumps(self.calls[-1]))
        self.assertIn('候选A',json.dumps(self.calls[-1],ensure_ascii=False))

    def test_inspection_does_not_leak_question_or_report_into_other_topic(self):
        self.service.inspect(self.video.video_id,'只属于此话题的秘密城市。',[])
        self.calls.clear()
        self.service.start_conversation(self.video.video_id,'这是新话题。',[])
        self.assertNotIn('秘密城市',json.dumps(self.calls[-1],ensure_ascii=False))

    def test_missing_comparison_in_inspection_does_not_save_half_chat(self):
        self.payload=result_payload()
        chat=ChatService(self.settings,self.client,self.service)
        with self.assertRaises(DeepSeekError):chat.send(conversation_id=None,request_id='4'*32,message='比对',image_id=None,video_id=self.video.video_id,inspect_video=True)
        self.assertEqual(chat.store.recent(),[])
        self.assertEqual(len(self.calls),1)
