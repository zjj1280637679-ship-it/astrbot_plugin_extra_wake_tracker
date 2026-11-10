import asyncio
import json
import os
import sys
import unittest
from typing import Dict, Any, List
from PIL import Image

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ASTRBOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", ".."))

if ASTRBOT_DIR not in sys.path:
    sys.path.insert(0, ASTRBOT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from astrbot.core.star.updater import _PluginUpdater
from astrbot.api.all import AstrBotConfig, MessageChain, Plain, At
from tracker_store import TrackerStore, TrackerError, ProtectedWakeWordError
from main import ExtraWakeTrackerPlugin, SILENCE_TOKENS


class MockEventResult:
    def __init__(self, chain):
        self.chain = chain


class MockEvent:
    def __init__(self, message_str: str = "", sender_id: str = "10001", group_id: str = "20001"):
        self.message_str = message_str
        self._sender_id = sender_id
        self._group_id = group_id
        self.is_at_or_wake_command = False
        self.is_wake = False
        self._extra: Dict[str, Any] = {}
        self._result = MockEventResult(MessageChain([Plain(message_str)]))

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_group_id(self) -> str:
        return self._group_id

    def set_extra(self, key: str, value: Any) -> None:
        self._extra[key] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extra.get(key, default)

    def get_result(self):
        return self._result

    def get_messages(self):
        return [Plain(self.message_str)]

    def plain_result(self, text: str):
        class EventResult:
            def __init__(self, msg):
                self.msg = msg
        return EventResult(text)


class MockContext:
    def __init__(self):
        self.astrbot_config = {
            "wake_prefix": ["/"],
            "admins_id": ["2973338092"],
        }


class TrackerAuditTestSuite(unittest.TestCase):
    def setUp(self):
        self.plugin_dir = CURRENT_DIR
        self.test_store_path = os.path.join(CURRENT_DIR, "data", "test_store.json")
        if os.path.exists(self.test_store_path):
            os.remove(self.test_store_path)
        self.mock_ctx = MockContext()
        self.plugin = ExtraWakeTrackerPlugin(
            self.mock_ctx,
            config={
                "allow_ai_self_track": True,
                "enable_silence_keyword": True,
                "admin_only_command": False,
                "default_scope": "global",
            },
        )
        # 隔离测试存储
        self.plugin.store = TrackerStore(
            data_path=self.test_store_path,
            base_wake_prefixes=["/"],
        )

    def tearDown(self):
        if os.path.exists(self.test_store_path):
            try:
                os.remove(self.test_store_path)
            except Exception:
                pass

    # =========================================================================
    # 门禁一：官方目录结构、元数据与 Schema 规范
    # =========================================================================

    def test_01_official_inspect_plugin_directory(self):
        """1.1 官方 _PluginUpdater.inspect_plugin_directory 校验 (必须 0 Error)"""
        res = _PluginUpdater.inspect_plugin_directory(self.plugin_dir)
        self.assertIsNotNone(res)
        meta = res.get("metadata", {})
        self.assertEqual(meta.get("name"), "astrbot_plugin_extra_wake_tracker")
        self.assertEqual(meta.get("version"), "1.0.0")
        self.assertEqual(meta.get("author"), "zjj1280637679-ship-it")
        print("  [PASS] 1.1 官方 _PluginUpdater 规范校验通过 (0 Error)")

    def test_02_config_schema_validation(self):
        """1.2 _conf_schema.json 解析与 AstrBotConfig 默认值转换"""
        schema_path = os.path.join(self.plugin_dir, "_conf_schema.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        bot_cfg = AstrBotConfig()
        default_cfg = bot_cfg._config_schema_to_default_config(schema)
        self.assertTrue(default_cfg["allow_ai_self_track"])
        self.assertTrue(default_cfg["enable_silence_keyword"])
        self.assertEqual(default_cfg["default_scope"], "group")
        print("  [PASS] 1.2 _conf_schema.json 结构解析合法")

    def test_03_logo_verification(self):
        """1.3 logo.png 存在且尺寸严格为 256x256 PNG"""
        logo_path = os.path.join(self.plugin_dir, "logo.png")
        self.assertTrue(os.path.exists(logo_path))
        with Image.open(logo_path) as img:
            self.assertEqual(img.size, (256, 256))
            self.assertEqual(img.format, "PNG")
        print("  [PASS] 1.3 logo.png 符合官方 256x256 标准")

    # =========================================================================
    # 门禁二：基础唤醒词永久保护机制
    # =========================================================================

    def test_04_base_wake_word_protection(self):
        """2.1 基础配置唤醒词只读保护：严禁删除 '/' 基础唤醒词"""
        store = self.plugin.store
        self.assertTrue(store.is_base_wake_prefix("/"))

        # 尝试删除主配置唤醒词，必须抛出 ProtectedWakeWordError
        with self.assertRaises(ProtectedWakeWordError):
            store.remove_word("/")

        # 尝试添加已有的原生唤醒词，拒绝重复冗余
        with self.assertRaises(TrackerError):
            store.add_word("/")

        print("  [PASS] 2.1 基础配置自带唤醒词保护机制严密")

    # =========================================================================
    # 门禁三：动态额外唤醒词与用户追踪引擎
    # =========================================================================

    def test_05_dynamic_extra_words_and_user_tracking(self):
        """3.1 动态添加/删除额外唤醒词与关注用户"""
        store = self.plugin.store

        # 1. 初始未匹配
        res0 = store.match_message("我想买显卡", sender_id="10001")
        self.assertFalse(res0.matched)

        # 2. 添加额外唤醒词 "显卡"
        store.add_word(word="显卡", added_by="admin", reason="关注显卡交易")
        res1 = store.match_message("有人出二手显卡吗？", sender_id="10001")
        self.assertTrue(res1.matched)
        self.assertEqual(res1.matched_type, "word")
        self.assertEqual(res1.matched_value, "显卡")

        # 3. 添加关注追踪用户 "88888"
        store.add_user(user_id="88888", added_by="admin", reason="重点关注对象")
        # 该用户只要发言（哪怕是任意无唤醒词文本），立刻命中唤醒！
        res2 = store.match_message("今天天气真好", sender_id="88888")
        self.assertTrue(res2.matched)
        self.assertEqual(res2.matched_type, "user")
        self.assertEqual(res2.matched_value, "88888")

        # 4. 删除额外唤醒词后不再触发
        store.remove_word("显卡")
        res3 = store.match_message("有人出二手显卡吗？", sender_id="10001")
        self.assertFalse(res3.matched)

        # 5. 删除用户后不再触发
        store.remove_user("88888")
        res4 = store.match_message("今天天气真好", sender_id="88888")
        self.assertFalse(res4.matched)

        print("  [PASS] 3.1 额外唤醒词与关注用户增删及匹配引擎准确")

    # =========================================================================
    # 门禁四：消息拦截唤醒管道与 AI [SILENCE] 静默决策
    # =========================================================================

    def test_06_intercept_and_silence_decision(self):
        """4.1 消息拦截自动置位 is_at_or_wake_command 与 [SILENCE] 拦截清空"""
        async def run_test():
            store = self.plugin.store
            store.add_word("求助")

            # 模拟群友消息包含 "求助"
            ev = MockEvent("群友们江湖求助啊", sender_id="10001")
            self.assertFalse(ev.is_at_or_wake_command)

            # 执行拦截管道
            await self.plugin.intercept_message(ev)
            self.assertTrue(ev.is_at_or_wake_command)
            self.assertTrue(ev.is_wake)
            self.assertEqual(ev.get_extra("tracker_woken")["value"], "求助")

            # 模拟 AI 研判后选择说话
            speak_ev = MockEvent("群友们江湖求助啊")
            speak_ev.get_result().chain = MessageChain([Plain("我在呢，请问有什么可以帮您？")])
            await self.plugin.handle_silence_decision(speak_ev)
            self.assertEqual(len(speak_ev.get_result().chain.chain), 1)

            # 模拟 AI 研判后选择保持静默输出 [SILENCE]
            silent_ev = MockEvent("群友们江湖求助啊")
            silent_ev.get_result().chain = MessageChain([Plain("[SILENCE]")])
            await self.plugin.handle_silence_decision(silent_ev)
            # 消息链被清空，AstrBot 自动跳过发送！
            self.assertEqual(len(silent_ev.get_result().chain.chain), 0)

        asyncio.run(run_test())
        print("  [PASS] 4.1 拦截管道唤醒与 AI [SILENCE] 静默决策机制验证通过")

    # =========================================================================
    # 门禁五：大模型工具契约与指令端到端
    # =========================================================================

    def test_07_llm_tools_and_commands(self):
        """5.1 LLM 工具调用 (track_target / untrack_target / list_tracked_targets)"""
        async def run_tools():
            ev = MockEvent()

            # AI 调用工具添加词语
            res_add_w = await self.plugin.track_target(ev, target_type="word", target_value="考研", reason="备考话题")
            self.assertIn("成功添加", res_add_w)

            # AI 调用工具添加用户
            res_add_u = await self.plugin.track_target(ev, target_type="user", target_value="99999", reason="组长发言")
            self.assertIn("成功将用户", res_add_u)

            # AI 查询清单
            res_list = await self.plugin.list_tracked_targets(ev)
            self.assertIn("考研", res_list)
            self.assertIn("99999", res_list)
            self.assertIn("保护的原生系统唤醒词", res_list)

            # AI 尝试删除保护词 '/'，受保护拦截
            res_fail = await self.plugin.untrack_target(ev, target_type="word", target_value="/")
            self.assertIn("受保护", res_fail)

            # AI 正常取消追踪
            res_untrack = await self.plugin.untrack_target(ev, target_type="word", target_value="考研")
            self.assertIn("成功取消", res_untrack)

        asyncio.run(run_tools())
        print("  [PASS] 5.1 LLM 工具契约增删查与保护端到端验证通过")

    def test_08_chat_commands(self):
        """6.1 聊天前缀指令 (/track word/user/list 与 /untrack) 端到端测试"""
        async def run_cmds():
            ev = MockEvent("测试消息", sender_id="2973338092", group_id="123456")

            # 1. /track word 指令
            gen1 = self.plugin.track_word_cmd(ev, word="机器人", reason="呼叫测试")
            res1 = [r async for r in gen1]
            self.assertTrue(len(res1) > 0)
            self.assertIn("成功添加额外唤醒词", res1[0].msg)

            # 2. /track user 指令
            gen2 = self.plugin.track_user_cmd(ev, target="7777777", reason="特邀嘉宾")
            res2 = [r async for r in gen2]
            self.assertTrue(len(res2) > 0)
            self.assertIn("成功添加追踪用户", res2[0].msg)

            # 3. /track list 指令
            gen3 = self.plugin.track_list_cmd(ev)
            res3 = [r async for r in gen3]
            self.assertTrue(len(res3) > 0)
            self.assertIn("机器人", res3[0].msg)
            self.assertIn("7777777", res3[0].msg)

            # 4. /untrack word 尝试删除受保护词 '/'
            gen4_fail = self.plugin.untrack_word_cmd(ev, word="/")
            res4_fail = [r async for r in gen4_fail]
            self.assertTrue(len(res4_fail) > 0)
            self.assertIn("受永久保护", res4_fail[0].msg)

            # 5. /untrack word 正常删除
            gen4_ok = self.plugin.untrack_word_cmd(ev, word="机器人")
            res4_ok = [r async for r in gen4_ok]
            self.assertTrue(len(res4_ok) > 0)
            self.assertIn("成功取消", res4_ok[0].msg)

            # 6. /untrack user 正常删除
            gen5 = self.plugin.untrack_user_cmd(ev, target="7777777")
            res5 = [r async for r in gen5]
            self.assertTrue(len(res5) > 0)
            self.assertIn("成功取消", res5[0].msg)

        asyncio.run(run_cmds())
        print("  [PASS] 6.1 聊天指令组 (/track 与 /untrack) 端到端全覆盖验证通过")


if __name__ == "__main__":
    unittest.main()
