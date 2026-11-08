"""角色草稿、生效范围、预览隔离、来源验证和多卡预留的行为验证。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from fastapi.testclient import TestClient
from app.api.routes.roles import service as get_role_service
from app.core.config import Settings,get_settings
from app.core.persona import PERSONA_CORE,load_persona
from app.core.role_card import RoleProfile
from app.main import app
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient,DeepSeekError
from app.services.role_store import RoleConflict,RoleStore
from app.services.roles import RoleService


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="visionsoul-roles-")
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.settings=Settings(_env_file=None,chat_database_path=root/"chat.sqlite3",role_database_path=root/"roles.sqlite3",deepseek_api_key="test-key-not-real")
        self.calls=[]
        self.result=None
        self.fail=False

        def handler(request):
            body=json.loads(request.content)
            self.calls.append(body)
            if self.fail:
                return httpx.Response(503,json={"error":"private-upstream-diagnostic"})
            result=self.result or "我记得你喜欢夜景。"
            return httpx.Response(200,json={"model":"deepseek-flash","choices":[{"message":{"content":json.dumps(result,ensure_ascii=False) if isinstance(result,dict) else result},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":30,"total_tokens":130}})
        self.client=DeepSeekClient(self.settings,httpx.MockTransport(handler))
        self.roles=RoleService(self.settings,self.client)

    def chat(self,message="我喜欢看夜景。",request="a",conversation=None):
        return ChatService(self.settings,self.client).send(conversation_id=conversation,request_id=request*32,message=message,image_id=None)

    def test_default_only_xiaoling_and_reserved_role_never_activated(self):
        self.assertEqual(load_persona(self.settings)[0],PERSONA_CORE)
        self.roles.store.create_reserved("future",RoleProfile(name="未来角色"))
        self.assertEqual(RoleStore(self.settings.roles_path,"future").current()["enabled"],0)
        self.assertEqual(self.roles.store.current()["id"],"xiaoling")
        self.assertEqual(load_persona(self.settings)[0],PERSONA_CORE)

    def test_draft_does_not_change_runtime_publish_does_and_history_can_restore(self):
        draft=RoleProfile(traits="冷静、有主见，喜欢星空。")
        self.roles.store.save(draft,0)
        self.assertEqual(load_persona(self.settings)[0],PERSONA_CORE)
        published=self.roles.store.save(draft,1,publish=True)
        self.assertIn("喜欢星空",load_persona(self.settings)[0])
        self.assertEqual(load_persona(self.settings)[2],"xiaoling-v2-card1")
        self.assertEqual([row["revision"] for row in published["versions"]],[1,0])
        self.roles.store.save(RoleProfile.model_validate(published["versions"][-1]["profile"]),2,publish=True)
        self.assertEqual(self.roles.store.current()["revision"],2)

    def test_stale_draft_is_rejected_without_overwriting(self):
        self.roles.store.save(RoleProfile(traits="已保存的性格"),0)
        with self.assertRaises(RoleConflict):
            self.roles.store.save(RoleProfile(traits="过期的修改"),0,publish=True)
        self.assertEqual(self.roles.store.current()["draft"]["traits"],"已保存的性格")

    def test_builder_is_one_request_draft_only_and_completed_retry_is_cached(self):
        profile=RoleProfile(traits="有主见，俏皮但不刻薄。")
        self.result={"reply":"已整理到草稿，你希望她怎样表达不同意见？","profile":profile.model_dump()}
        first=self.roles.build("希望她更有主见。",0,"1"*32)
        self.assertEqual(first["card"]["revision"],0)
        self.assertEqual(first["card"]["draft"]["traits"],profile.traits)
        repeated=self.roles.build("希望她更有主见。",0,"1"*32)
        self.assertTrue(repeated["cached"])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0]["max_tokens"],1000)
        self.assertEqual(self.roles.store.memories(),[])
        with self.assertRaises(RoleConflict):
            self.roles.build("另一项要求",1,"1"*32)

    def test_preview_has_fixed_identity_and_never_changes_draft_or_formal_memory(self):
        profile=RoleProfile(traits="独立、有点俏皮。")
        self.roles.preview(profile,"预览用：我叫测试名字。","2"*32)
        self.assertIn("AI",self.calls[0]["messages"][0]["content"])
        self.assertIn("隔离的角色预览",self.calls[0]["messages"][0]["content"])
        self.assertEqual(self.roles.store.current()["draft_revision"],0)
        self.assertEqual(self.roles.store.current()["messages"],[])
        self.assertEqual(self.roles.store.memories(),[])
        self.assertFalse(self.settings.chat_database_path.exists())

    def test_builder_invalid_response_or_failure_does_not_save_or_retry(self):
        self.result={"reply":"不完整","profile":{"name":"小灵"}}
        with self.assertRaises(DeepSeekError):
            self.roles.build("改性格",0,"3"*32)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.roles.store.current()["draft_revision"],0)
        self.fail=True
        with self.assertRaises(DeepSeekError):
            self.roles.preview(RoleProfile(),"你好","4"*32)
        self.assertEqual(len(self.calls),2)

    def test_manual_memory_is_used_then_pause_or_remove_stops_injection(self):
        memory_id=self.roles.store.add_memory("preference","用户喜欢安静的夜景。")
        self.chat("你记得我喜欢什么吗？")
        self.assertIn("用户喜欢安静的夜景",json.dumps(self.calls[-1],ensure_ascii=False))
        self.roles.store.change_memory(memory_id,1,status="pending")
        self.chat("新话题",request="b")
        self.assertNotIn("用户喜欢安静的夜景",json.dumps(self.calls[-1],ensure_ascii=False))
        self.roles.store.change_memory(memory_id,2,delete=True)
        self.assertEqual(self.roles.store.memories(),[])

    def test_extraction_only_user_quotes_candidates_pending_and_repeat_is_free(self):
        turn=self.chat()
        user_id=turn["user_message"]["id"]
        assistant_id=turn["assistant_message"]["id"]
        self.calls.clear()
        self.result={"summary":"用户表达了夜景偏好。","memories":[
            {"category":"preference","content":"用户喜欢夜景。","source_message_id":user_id,"quote":"喜欢看夜景"},
            {"category":"fact","content":"错误的助手推断。","source_message_id":assistant_id,"quote":"喜欢夜景"},
            {"category":"fact","content":"没有原话支持。","source_message_id":user_id,"quote":"去了火星"}]}
        first=self.roles.extract(turn["conversation_id"],"5"*32)
        self.assertEqual(first["skipped_count"],2)
        memories=self.roles.store.memories()
        self.assertEqual(len(memories),2)
        self.assertTrue(all(row["status"]=="pending" for row in memories))
        self.assertEqual(self.roles.store.memory_context(turn["conversation_id"],"喜欢什么"),"")
        repeated=self.roles.extract(turn["conversation_id"],"6"*32)
        self.assertTrue(repeated["cached"])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.roles.store.memories()),2)
        preference=next(m for m in memories if m["category"]=="preference")
        self.roles.store.change_memory(preference["id"],1,status="confirmed")
        self.assertIn("用户喜欢夜景",self.roles.store.memory_context(None,"喜欢什么"))

    def test_confirmed_summary_is_only_used_for_its_source_topic(self):
        self.roles.store.add_memory("summary","这个话题在讨论西湖。",conversation_id="a"*32)
        self.assertIn("西湖",self.roles.store.memory_context("a"*32,"继续聊"))
        self.assertNotIn("西湖",self.roles.store.memory_context("b"*32,"继续聊"))

    def test_published_profile_applies_to_new_chat_without_restart(self):
        self.roles.store.save(RoleProfile(traits="有自己的主见，喜欢观察星星。"),0,publish=True)
        reply=self.chat("你好")
        self.assertIn("观察星星",self.calls[-1]["messages"][0]["content"])
        self.assertEqual(reply["persona_id"],"xiaoling-v2-card1")

    def test_api_validation_versions_and_source_preservation(self):
        turn=self.chat()
        app.dependency_overrides[get_settings]=lambda:self.settings
        app.dependency_overrides[get_role_service]=lambda:self.roles
        self.addCleanup(app.dependency_overrides.clear)
        root="/api/roles/xiaoling"
        with TestClient(app) as client:
            card=client.get(root).json()["card"]
            self.assertEqual(card["name"],"小灵")
            self.assertEqual(client.get(root+"/sources").json()[0]["id"],turn["conversation_id"])
            wrong={**RoleProfile().model_dump(),"name":"另一个角色"}
            self.assertEqual(client.post(root+"/publish",json={"profile":wrong,"expected":0}).status_code,200)
            self.assertEqual(client.get('/api/chat/status').json()['name'],'另一个角色')
            self.assertEqual(client.post(root+"/discuss",json={"message":"改性格","expected":0,"request_id":"7"*32,"system":"inject"}).status_code,422)
            created=client.post(root+"/memories",json={"category":"fact","content":"用户手动指定的信息。"}).json()["id"]
            self.assertEqual(client.delete(root+f"/memories/{created}?expected=1").status_code,200)
            self.assertEqual(len(self.roles.chats.messages(turn["conversation_id"])),2)
            self.assertEqual(client.get("/api/roles/another").status_code,404)

    def test_free_definition_replaces_all_old_personality_rules_in_chat_and_preview(self):
        definition = "  她是月港档案馆的守夜人，沉静寡言。\n\n" + "完整世界观可自由编写。"*40 + "  "
        profile = RoleProfile(name="望舒",mode="free",definition=definition,traits="旧性格不应生效")
        self.roles.store.save(profile,0,publish=True)
        self.assertEqual(self.roles.store.current()['profile']['definition'],definition)
        self.chat("晚上好")
        prompt = self.calls[-1]['messages'][0]['content']
        self.assertIn(definition,prompt)
        self.assertIn('望舒',prompt)
        for old in ['活泼俏皮','轻度幽默','旧性格不应生效','通常一到三个短段落','不每次结尾反问']:
            self.assertNotIn(old,prompt)
        self.roles.preview(profile,'打个招呼','8'*32)
        self.assertIn(definition,self.calls[-1]['messages'][0]['content'])
        self.assertNotIn('旧性格不应生效',self.calls[-1]['messages'][0]['content'])
        from app.services.videos import VideoService
        self.assertIn(definition,VideoService(self.settings,self.client).prompt)
        self.assertNotIn('活泼俏皮',VideoService(self.settings,self.client).prompt)

    def test_blank_free_draft_can_save_but_cannot_publish_or_preview(self):
        profile=RoleProfile(mode='free',definition='',identity='',traits='',speaking_style='')
        app.dependency_overrides[get_role_service]=lambda:self.roles
        self.addCleanup(app.dependency_overrides.clear)
        root='/api/roles/xiaoling'
        with TestClient(app) as client:
            self.assertEqual(client.put(root+'/draft',json={'profile':profile.model_dump(),'expected':0}).status_code,200)
            self.assertEqual(client.post(root+'/publish',json={'profile':profile.model_dump(),'expected':1}).status_code,409)
            self.assertEqual(client.post(root+'/preview',json={'profile':profile.model_dump(),'message':'你好','request_id':'9'*32}).status_code,409)
        self.assertEqual(load_persona(self.settings)[0],PERSONA_CORE)
        self.assertEqual(len(self.calls),0)

    def test_old_json_without_new_fields_remains_readable_and_versions_preserve_original(self):
        legacy=RoleProfile().model_dump()
        del legacy['mode'],legacy['definition']
        with self.roles.store.connection() as conn:
            conn.execute('UPDATE role_cards SET profile_json=?,draft_json=? WHERE id=?',(json.dumps(legacy),json.dumps(legacy),'xiaoling'))
        self.assertEqual(self.roles.store.current()['profile']['mode'],'legacy')
        self.assertEqual(load_persona(self.settings)[0],PERSONA_CORE)
        new=RoleProfile(name='新人物',mode='free',definition='她来自未来都市，说话克制。')
        self.roles.store.save(new,0,publish=True)
        self.assertEqual(self.roles.store.current()['name'],'新人物')
        self.roles.store.save(RoleProfile.model_validate(legacy),1,publish=True)
        self.assertEqual(self.roles.store.current()['name'],'小灵')
        self.assertNotIn('未来都市',load_persona(self.settings)[1])


if __name__=="__main__":
    unittest.main()
