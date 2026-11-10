import os
import re
from typing import Optional, List, Dict, Any

from astrbot.api.star import Star, register, Context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, At
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core import logger
from astrbot.core.star.filter.event_message_type import EventMessageType

try:
    from .tracker_store import TrackerStore, TrackerError, ProtectedWakeWordError
except ImportError:
    from tracker_store import TrackerStore, TrackerError, ProtectedWakeWordError

PLUGIN_NAME = "astrbot_plugin_extra_wake_tracker"
SILENCE_TOKENS = {"[SILENCE]", "[静默]", "[IGNORE]", "[PASS]", "[NO_REPLY]"}


@register(
    "astrbot_plugin_extra_wake_tracker",
    "zjj1280637679-ship-it",
    "AstrBot 动态额外唤醒词与关注用户发言追踪插件",
    "1.0.0",
    "https://github.com/zjj1280637679-ship-it/astrbot_plugin_extra_wake_tracker",
)
class ExtraWakeTrackerPlugin(Star):
    """
    额外唤醒词与关注用户发言追踪插件。
    - 动态增减额外唤醒词，严格保护系统基础配置唤醒词；
    - 追踪特定用户，发言即刻唤醒 AI；
    - 支持 AI 自主调用工具增删追踪，唤醒后可自决发言或保持沉默（回复 [SILENCE] 静默）；
    - 支持聊天前缀指令管理。
    """

    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.config = config or {}

        # 1. 获取并锁定基础配置自带的唤醒词（只读保护）
        base_wake = []
        try:
            if hasattr(context, "astrbot_config") and context.astrbot_config:
                base_wake = list(context.astrbot_config.get("wake_prefix", ["/"]))
            else:
                base_wake = ["/"]
        except Exception:
            base_wake = ["/"]

        # 2. 初始化本地独立持久化数据存储
        plugin_data_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data"
        )
        os.makedirs(plugin_data_dir, exist_ok=True)
        data_file = os.path.join(plugin_data_dir, "tracker_store.json")

        self.store = TrackerStore(
            data_path=data_file,
            base_wake_prefixes=base_wake,
        )

        # 3. 读取配置项
        self.allow_ai_self_track: bool = self.config.get("allow_ai_self_track", True)
        self.enable_silence_keyword: bool = self.config.get("enable_silence_keyword", True)
        self.admin_only_command: bool = self.config.get("admin_only_command", False)

        logger.info(
            f"[{PLUGIN_NAME}] 插件初始化成功: 受保护系统唤醒词={base_wake}, "
            f"当前追踪词数={len(self.store.extra_words)}, 关注用户数={len(self.store.tracked_users)}"
        )

    # =========================================================================
    # 消息总线高优先级感知管道（检测额外唤醒词与关注用户）
    # =========================================================================

    @filter.event_message_type(EventMessageType.ALL, priority=1000)
    async def intercept_message(self, event: AstrMessageEvent):
        """
        拦截每条进来的消息，毫秒级快速匹配。
        若命中额外唤醒词或关注用户，将事件标志位设置为唤醒状态。
        """
        # 如果消息已经被官方唤醒机制唤醒（如自带唤醒词、@机器人），直接放行
        if getattr(event, "is_at_or_wake_command", False):
            return

        sender_id = str(event.get_sender_id() or "").strip()
        group_id = str(event.get_group_id() or "").strip()
        message_str = event.message_str or ""

        # 执行匹配
        res = self.store.match_message(
            message_str=message_str,
            sender_id=sender_id,
            group_id=group_id,
        )

        if res.matched:
            # 命中追踪规则！唤醒 AI 完整决策管道
            event.is_at_or_wake_command = True
            event.is_wake = True
            event.set_extra(
                "tracker_woken",
                {
                    "type": res.matched_type,
                    "value": res.matched_value,
                    "reason": res.reason,
                    "scope": res.scope,
                },
            )
            logger.info(
                f"[{PLUGIN_NAME}] 消息命中追踪规则 (类型={res.matched_type}, "
                f"目标={res.matched_value}, 原因={res.reason or '无'}) -> 执行唤醒！"
            )

    # =========================================================================
    # 结果装饰阶段：处理 AI “选择说话还是沉默”
    # =========================================================================

    @filter.on_decorating_result()
    async def handle_silence_decision(self, event: AstrMessageEvent):
        """
        当由于追踪机制被唤醒时，若 AI 选择回复 [SILENCE] 或纯空白，
        清空消息链，跳过发送阶段，实现 0 扰民绝对静默。
        """
        if not self.enable_silence_keyword:
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        # 提取纯文本内容
        if hasattr(result.chain, "get_plain_text"):
            text_content = result.chain.get_plain_text() or ""
        elif hasattr(result.chain, "chain"):
            text_content = "".join([getattr(c, "text", "") for c in result.chain.chain if getattr(c, "text", None)])
        else:
            text_content = str(result.chain)

        # 检查是否包含静默暗号
        upper_text = text_content.strip().upper()
        is_silence = False
        if upper_text in SILENCE_TOKENS:
            is_silence = True
        elif any(token in upper_text for token in SILENCE_TOKENS) and len(upper_text) < 30:
            is_silence = True

        if is_silence:
            logger.info(f"[{PLUGIN_NAME}] AI 研判后自主选择保持沉默 ([SILENCE])，清空回复链")
            if hasattr(result.chain, "chain") and isinstance(result.chain.chain, list):
                result.chain.chain.clear()
            elif hasattr(result.chain, "clear"):
                result.chain.clear()

    # =========================================================================
    # 大模型工具契约 (LLM Tools)
    # =========================================================================

    @filter.llm_tool(name="track_target")
    async def track_target(
        self,
        event: AstrMessageEvent,
        target_type: str,
        target_value: str,
        reason: str = "",
        is_global: bool = True,
    ) -> str:
        """【工具是什么】
        动态唤醒追踪规则添加工具。

        【功能】
        注册新的消息唤醒关键词或重点追踪用户 QQ 号。当目标词语在聊天中出现或目标用户发言时，触发消息唤醒。

        【返回状态】
        操作成功提示（包含规则类型、目标值、作用域及备注）；若规则受保护或已禁用则返回错误/拦截说明。

        【最小例子】
        track_target(target_type="word", target_value="显卡")
        track_target(target_type="user", target_value="12345678", is_global=False)

        Args:
            target_type(string): 追踪类型，'word' (关键词) 或 'user' (用户QQ号)
            target_value(string): 要追踪的词语（如 '显卡'）或用户 QQ 号（如 '12345678'）
            reason(string): 可选，追踪原因或意图备注
            is_global(boolean): 可选，是否全局生效（True 为所有群聊和私聊，False 仅限当前群，默认 True）
        """
        if not self.allow_ai_self_track:
            return "系统配置已禁用大模型自主增减追踪规则。"

        ttype = str(target_type).strip().lower()
        tval = str(target_value).strip()
        scope = "global"
        if not is_global and event.get_group_id():
            scope = f"group:{event.get_group_id()}"

        sender = str(event.get_sender_id() or "ai")

        try:
            if ttype in ("word", "词语", "关键词"):
                info = self.store.add_word(
                    word=tval,
                    added_by=sender,
                    scope=scope,
                    reason=reason,
                )
                return f"已成功添加额外唤醒词 '{info['word']}' (作用域: {scope}, 原因: {reason or '无'})。以后聊天中包含该词将自动唤醒你。"
            elif ttype in ("user", "用户", "qq"):
                # 清洗用户 ID 中的 @ 标记
                clean_uid = re.sub(r"\D", "", tval)
                if not clean_uid:
                    return f"错误：无效的用户 QQ 号 '{tval}'"
                info = self.store.add_user(
                    user_id=clean_uid,
                    added_by=sender,
                    scope=scope,
                    reason=reason,
                )
                return f"已成功将用户 {clean_uid} 加入重点追踪名单 (作用域: {scope}, 原因: {reason or '无'})。该用户以后发言将直接唤醒你。"
            else:
                return f"错误：未知的追踪类型 '{target_type}'，必须为 'word' 或 'user'。"
        except ProtectedWakeWordError as e:
            return f"拒绝操作：{e}"
        except TrackerError as e:
            return f"添加失败：{e}"

    @filter.llm_tool(name="untrack_target")
    async def untrack_target(
        self,
        event: AstrMessageEvent,
        target_type: str,
        target_value: str,
    ) -> str:
        """【工具是什么】
        动态唤醒追踪规则移除工具。

        【功能】
        移除已注册的额外唤醒词或重点关注用户 QQ 号。系统核心原生唤醒词受保护不可移除。

        【返回状态】
        移除成功提示；若目标未找到或属于受保护原生唤醒词则返回失败或拦截说明。

        【最小例子】
        untrack_target(target_type="word", target_value="显卡")
        untrack_target(target_type="user", target_value="12345678")

        Args:
            target_type(string): 追踪类型，'word' (关键词) 或 'user' (用户QQ号)
            target_value(string): 要取消追踪的词语或用户 QQ 号
        """
        if not self.allow_ai_self_track:
            return "系统配置已禁用大模型自主增减追踪规则。"

        ttype = str(target_type).strip().lower()
        tval = str(target_value).strip()

        try:
            if ttype in ("word", "词语", "关键词"):
                removed = self.store.remove_word(tval)
                return f"已成功取消对额外唤醒词 '{removed['word']}' 的追踪。"
            elif ttype in ("user", "用户", "qq"):
                clean_uid = re.sub(r"\D", "", tval)
                if not clean_uid:
                    return f"错误：无效的用户 QQ 号 '{tval}'"
                removed = self.store.remove_user(clean_uid)
                return f"已成功取消对用户 {clean_uid} 的发言追踪。"
            else:
                return f"错误：未知的追踪类型 '{target_type}'，必须为 'word' 或 'user'。"
        except ProtectedWakeWordError as e:
            return f"受保护拦截：{e}"
        except TrackerError as e:
            return f"取消失败：{e}"

    @filter.llm_tool(name="list_tracked_targets")
    async def list_tracked_targets(
        self,
        event: AstrMessageEvent,
    ) -> str:
        """【工具是什么】
        当前活跃追踪与唤醒规则清单查询工具。

        【功能】
        查询当前生效的所有额外唤醒词、重点关注用户 QQ 号及受保护的原生系统唤醒词。

        【返回状态】
        包含额外唤醒词列表、关注用户名单及原生唤醒词的结构化文本清单。

        【最小例子】
        list_tracked_targets()
        """
        group_id = str(event.get_group_id() or "").strip()
        data = self.store.list_all(group_id=group_id if group_id else None)

        words_desc = ", ".join([w["word"] for w in data["extra_words"]]) or "无"
        users_desc = ", ".join([u["user_id"] for u in data["tracked_users"]]) or "无"
        base_desc = ", ".join(data["base_wake_prefixes"]) or "无"

        return (
            f"【当前追踪与唤醒清单】\n"
            f"1. 额外唤醒词 ({len(data['extra_words'])} 个): {words_desc}\n"
            f"2. 关注用户 ({len(data['tracked_users'])} 位): {users_desc}\n"
            f"3. 保护的原生系统唤醒词: {base_desc}"
        )

    # =========================================================================
    # 用户聊天指令 (/track 与 /untrack)
    # =========================================================================

    def _check_admin_perm(self, event: AstrMessageEvent) -> bool:
        if not self.admin_only_command:
            return True
        sender = str(event.get_sender_id() or "")
        admins = []
        try:
            if hasattr(self.context, "astrbot_config") and self.context.astrbot_config:
                admins = [str(a) for a in self.context.astrbot_config.get("admins_id", [])]
        except Exception:
            pass
        return sender in admins

    def _extract_target_user_id(self, event: AstrMessageEvent, arg: str) -> str:
        # 支持 @ 某人 解析
        if hasattr(event, "get_messages"):
            for comp in event.get_messages():
                if isinstance(comp, At) and getattr(comp, "qq", None):
                    return str(comp.qq).strip()
        # 纯数字正则解析
        digits = re.sub(r"\D", "", arg)
        return digits

    @filter.command_group("track")
    def track_cmd_group(self):
        """追踪管理指令组"""
        pass

    @track_cmd_group.command("word")
    async def track_word_cmd(self, event: AstrMessageEvent, word: str, reason: str = ""):
        """添加额外唤醒词：/track word <关键词> [原因]"""
        if not self._check_admin_perm(event):
            yield event.plain_result("权限不足：该指令仅管理员可用。")
            return
        if not word or not word.strip():
            yield event.plain_result("格式错误：请输入要追踪的词语，例如：/track word 显卡")
            return
        try:
            scope = f"group:{event.get_group_id()}" if event.get_group_id() else "global"
            info = self.store.add_word(
                word=word.strip(),
                added_by=str(event.get_sender_id() or "user"),
                scope=scope,
                reason=reason.strip(),
            )
            yield event.plain_result(
                f"[√] 成功添加额外唤醒词 '{info['word']}'\n"
                f"范围: {scope}\n"
                f"备注: {reason or '无'}\n"
                f"现在群友在群聊中发送包含该词的消息即可唤醒 AI。"
            )
        except ProtectedWakeWordError as e:
            yield event.plain_result(f"[!] {e}")
        except TrackerError as e:
            yield event.plain_result(f"[×] 添加失败: {e}")

    @track_cmd_group.command("user")
    async def track_user_cmd(self, event: AstrMessageEvent, target: str = "", reason: str = ""):
        """添加追踪关注用户：/track user <QQ/@用户> [原因]"""
        if not self._check_admin_perm(event):
            yield event.plain_result("权限不足：该指令仅管理员可用。")
            return
        uid = self._extract_target_user_id(event, target)
        if not uid:
            yield event.plain_result("格式错误：请输入有效的 QQ 号或 @目标，例如：/track user 12345678")
            return
        try:
            scope = f"group:{event.get_group_id()}" if event.get_group_id() else "global"
            info = self.store.add_user(
                user_id=uid,
                added_by=str(event.get_sender_id() or "user"),
                scope=scope,
                reason=reason.strip(),
            )
            yield event.plain_result(
                f"[√] 成功添加追踪用户 {info['user_id']}\n"
                f"范围: {scope}\n"
                f"备注: {reason or '无'}\n"
                f"该用户在群聊中发言将直接唤醒 AI！"
            )
        except TrackerError as e:
            yield event.plain_result(f"[×] 添加失败: {e}")

    @track_cmd_group.command("list")
    async def track_list_cmd(self, event: AstrMessageEvent):
        """查看当前所有追踪条目：/track list"""
        group_id = str(event.get_group_id() or "").strip()
        data = self.store.list_all(group_id=group_id if group_id else None)

        lines = ["=== 📋 唤醒词与追踪用户清单 ==="]
        lines.append(f"🔒 保护的系统唤醒词: {', '.join(data['base_wake_prefixes']) or '无'}")

        lines.append(f"\n🎯 额外唤醒词 ({len(data['extra_words'])} 个):")
        if data["extra_words"]:
            for i, w in enumerate(data["extra_words"], 1):
                lines.append(f"  {i}. [{w['word']}] (范围: {w.get('scope', 'global')}, 备注: {w.get('reason') or '无'})")
        else:
            lines.append("  (暂无额外唤醒词)")

        lines.append(f"\n👤 关注追踪用户 ({len(data['tracked_users'])} 位):")
        if data["tracked_users"]:
            for i, u in enumerate(data["tracked_users"], 1):
                lines.append(f"  {i}. 用户 {u['user_id']} (范围: {u.get('scope', 'global')}, 备注: {u.get('reason') or '无'})")
        else:
            lines.append("  (暂无关注追踪用户)")

        lines.append("\n💡 提示：AI 命中追踪唤醒后可自主回复 [SILENCE] 保持静默围观。")
        yield event.plain_result("\n".join(lines))

    @filter.command_group("untrack")
    def untrack_cmd_group(self):
        """取消追踪指令组"""
        pass

    @untrack_cmd_group.command("word")
    async def untrack_word_cmd(self, event: AstrMessageEvent, word: str):
        """取消额外唤醒词：/untrack word <关键词>"""
        if not self._check_admin_perm(event):
            yield event.plain_result("权限不足：该指令仅管理员可用。")
            return
        if not word or not word.strip():
            yield event.plain_result("格式错误：请输入要取消的词语，例如：/untrack word 显卡")
            return
        try:
            removed = self.store.remove_word(word.strip())
            yield event.plain_result(f"[√] 成功取消对额外唤醒词 '{removed['word']}' 的追踪。")
        except ProtectedWakeWordError as e:
            yield event.plain_result(f"[!] {e}")
        except TrackerError as e:
            yield event.plain_result(f"[×] 取消失败: {e}")

    @untrack_cmd_group.command("user")
    async def untrack_user_cmd(self, event: AstrMessageEvent, target: str = ""):
        """取消关注追踪用户：/untrack user <QQ/@用户>"""
        if not self._check_admin_perm(event):
            yield event.plain_result("权限不足：该指令仅管理员可用。")
            return
        uid = self._extract_target_user_id(event, target)
        if not uid:
            yield event.plain_result("格式错误：请输入有效的 QQ 号或 @目标，例如：/untrack user 12345678")
            return
        try:
            removed = self.store.remove_user(uid)
            yield event.plain_result(f"[√] 成功取消对用户 {removed['user_id']} 的发言追踪。")
        except TrackerError as e:
            yield event.plain_result(f"[×] 取消失败: {e}")
