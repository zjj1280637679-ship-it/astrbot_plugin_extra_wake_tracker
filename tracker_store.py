import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Set, Tuple


class TrackerError(Exception):
    """追踪器通用异常"""
    pass


class ProtectedWakeWordError(TrackerError):
    """试图删除基础配置自带唤醒词时抛出的受保护异常"""
    pass


@dataclass
class MatchResult:
    matched: bool
    matched_type: str  # "user" | "word" | ""
    matched_value: str
    target_info: Dict[str, Any]
    scope: str
    reason: str = ""


class TrackerStore:
    """
    额外唤醒词与关注用户发言追踪器的持久化存储与匹配引擎。
    具备线程安全、基础唤醒词保护、群组/全局隔离及快速匹配能力。
    """

    def __init__(self, data_path: str, base_wake_prefixes: Optional[List[str]] = None):
        self.data_path = os.path.abspath(data_path)
        self.lock = threading.RLock()
        self._base_wake_prefixes: Set[str] = set(
            p.strip().lower() for p in (base_wake_prefixes or ["/"]) if p and p.strip()
        )
        self.extra_words: Dict[str, Dict[str, Any]] = {}
        self.tracked_users: Dict[str, Dict[str, Any]] = {}
        self._load()

    def set_base_wake_prefixes(self, prefixes: List[str]) -> None:
        """更新受系统保护的基础配置唤醒词列表（只读保护，防止被删除）"""
        with self.lock:
            self._base_wake_prefixes = set(
                p.strip().lower() for p in prefixes if p and p.strip()
            )

    def is_base_wake_prefix(self, word: str) -> bool:
        """检查某个词是否是基础配置中原生自带的唤醒词"""
        w = word.strip().lower()
        return w in self._base_wake_prefixes

    def _load(self) -> None:
        with self.lock:
            if not os.path.exists(self.data_path):
                self._save_unlocked()
                return
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.extra_words = data.get("extra_words", {})
                self.tracked_users = data.get("tracked_users", {})
            except Exception:
                self.extra_words = {}
                self.tracked_users = {}

    def _save_unlocked(self) -> None:
        dir_name = os.path.dirname(self.data_path)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
        payload = {
            "extra_words": self.extra_words,
            "tracked_users": self.tracked_users,
            "updated_at": time.time(),
        }
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def add_word(
        self,
        word: str,
        added_by: str = "system",
        scope: str = "global",
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        添加一个额外唤醒词。
        :param word: 唤醒关键词
        :param added_by: 添加人（QQ号或 'ai'）
        :param scope: 作用域（'global' 或 'group:群号'）
        :param reason: 追踪原因
        """
        w = word.strip()
        if not w:
            raise TrackerError("唤醒词内容不能为空")
        key = w.lower()

        with self.lock:
            # 如果该词已经是原生配置里的唤醒词，无需重复添加
            if self.is_base_wake_prefix(w):
                raise TrackerError(f"词语 '{w}' 已经是 AstrBot 系统配置自带的原生唤醒词，默认已生效！")

            info = {
                "word": w,
                "added_by": str(added_by),
                "scope": scope,
                "reason": reason,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.extra_words[key] = info
            self._save_unlocked()
            return info

    def remove_word(self, word: str) -> Dict[str, Any]:
        """
        移除一个额外唤醒词。
        如果试图删除基础配置自带的唤醒词，将严格抛出 ProtectedWakeWordError。
        """
        w = word.strip()
        if not w:
            raise TrackerError("要删除的唤醒词不能为空")

        with self.lock:
            # 严格保护主配置自带唤醒词！
            if self.is_base_wake_prefix(w):
                raise ProtectedWakeWordError(
                    f"安全受限：'{w}' 是系统主配置中自带的原生唤醒词，受永久保护，严禁删除！"
                )

            key = w.lower()
            if key not in self.extra_words:
                raise TrackerError(f"未找到额外唤醒词 '{w}'，当前未追踪该词。")

            removed = self.extra_words.pop(key)
            self._save_unlocked()
            return removed

    def add_user(
        self,
        user_id: str,
        added_by: str = "system",
        scope: str = "global",
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        添加一个追踪用户。该用户在指定范围发言时将直接唤醒 AI。
        """
        uid = str(user_id).strip()
        if not uid or not uid.isdigit():
            raise TrackerError(f"无效的 QQ 用户 ID: '{user_id}'，必须为纯数字")

        with self.lock:
            info = {
                "user_id": uid,
                "added_by": str(added_by),
                "scope": scope,
                "reason": reason,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.tracked_users[uid] = info
            self._save_unlocked()
            return info

    def remove_user(self, user_id: str) -> Dict[str, Any]:
        """
        移除被追踪的用户 ID。
        """
        uid = str(user_id).strip()
        with self.lock:
            if uid not in self.tracked_users:
                raise TrackerError(f"用户 {uid} 当前不在追踪名单中。")
            removed = self.tracked_users.pop(uid)
            self._save_unlocked()
            return removed

    def list_all(self, group_id: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        列出当前处于追踪状态的所有词语与用户。
        若指定 group_id，则包含全局和该群专属的条目。
        """
        with self.lock:
            words = []
            for w_info in self.extra_words.values():
                s = w_info.get("scope", "global")
                if not group_id or s == "global" or s == f"group:{group_id}":
                    words.append(w_info)

            users = []
            for u_info in self.tracked_users.values():
                s = u_info.get("scope", "global")
                if not group_id or s == "global" or s == f"group:{group_id}":
                    users.append(u_info)

            return {
                "extra_words": words,
                "tracked_users": users,
                "base_wake_prefixes": sorted(list(self._base_wake_prefixes)),
            }

    def match_message(
        self,
        message_str: str,
        sender_id: str,
        group_id: str = "",
    ) -> MatchResult:
        """
        毫秒级检查消息是否命中额外唤醒词或关注用户。
        """
        sid = str(sender_id).strip()
        gid_scope = f"group:{group_id}" if group_id else ""

        with self.lock:
            # 1. 优先检查用户 ID 追踪：只要该用户发言就立即唤醒！
            if sid in self.tracked_users:
                u_info = self.tracked_users[sid]
                u_scope = u_info.get("scope", "global")
                if u_scope == "global" or (gid_scope and u_scope == gid_scope):
                    return MatchResult(
                        matched=True,
                        matched_type="user",
                        matched_value=sid,
                        target_info=u_info,
                        scope=u_scope,
                        reason=u_info.get("reason", "关注用户发言"),
                    )

            # 2. 检查额外关键词匹配
            if message_str:
                lower_msg = message_str.lower()
                for key, w_info in self.extra_words.items():
                    w_scope = w_info.get("scope", "global")
                    if w_scope == "global" or (gid_scope and w_scope == gid_scope):
                        # 包含关键词即可匹配唤醒
                        if key in lower_msg:
                            return MatchResult(
                                matched=True,
                                matched_type="word",
                                matched_value=w_info["word"],
                                target_info=w_info,
                                scope=w_scope,
                                reason=w_info.get("reason", "命中了关注词语"),
                            )

        return MatchResult(
            matched=False,
            matched_type="",
            matched_value="",
            target_info={},
            scope="",
        )
