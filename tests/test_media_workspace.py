"""统一入口的服务端边界：单图数量、30秒、混合历史；不调用模型。"""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from test_image_upload import make_image
from test_video import make_video
from app.core.config import Settings,get_settings
from app.main import app
from app.services.chat_store import ChatStore


class MediaBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.config=Settings(_env_file=None,image_storage_dir=root/'images',video_storage_dir=root/'videos',
                             chat_database_path=root/'chat.sqlite3',role_database_path=root/'roles.sqlite3',audio_enabled=False)
        app.dependency_overrides[get_settings]=lambda:self.config
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app)

    def test_multiple_image_parts_rejected_with_exact_text_without_saving(self):
        image=make_image()
        response=self.client.post('/api/images',files=[('file',('a.png',image,'image/png')),('file',('b.png',image,'image/png'))])
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.json()['detail'],'目前只支持单张图片上传')
        self.assertFalse(self.config.image_storage_dir.exists())
        response=self.client.post('/api/images',files={'file':('single.png',image,'image/png')})
        self.assertEqual(response.status_code,201)

    def test_actual_31_second_video_rejected_and_exact_30_accepted(self):
        for seconds,status in [(31,422),(30,201)]:
            path=Path(self.temp.name)/f'clip-{seconds}.mp4';make_video(path,seconds=seconds,fps=2,varying=True)
            response=self.client.post('/api/videos',files={'file':(path.name,path.read_bytes(),'video/mp4')})
            self.assertEqual(response.status_code,status,response.text)
            if seconds==31:self.assertEqual(response.json()['detail'],'超过视频时长上限，上限为30秒')
            else:self.assertEqual(response.json()['duration_seconds'],30)

    def test_all_history_keeps_image_video_and_plain_text_without_changing_legacy_filters(self):
        store=ChatStore(self.config.chat_database_path);store.initialize()
        for index,media in enumerate([{'image_id':'a'*32},{'video_id':'b'*64},{}],1):
            store.finish_turn(conversation=None,conversation_id=str(index)*32,image_id=media.get('image_id'),video_id=media.get('video_id'),
                message=f'话题{index}',model_reply={'answer':'已保存','model':'test','usage':None,'truncated':False},request_id=str(index+4)*32,
                fingerprint='test'+str(index),persona_id='test')
        self.assertEqual(len(self.client.get('/api/chat/conversations?kind=all').json()),3)
        self.assertEqual(len(self.client.get('/api/chat/conversations?kind=video').json()),1)
        self.assertEqual(len(self.client.get('/api/chat/conversations?kind=image').json()),2)
