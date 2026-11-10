# Changelog

本文件记录 `astrbot_plugin_extra_wake_tracker` 插件的所有重要更新。

## [1.0.0] - 2026-11-10

### 🚀 初始版本发布

- **动态额外唤醒词库 (Dynamic Extra Wake Words)**
  - 支持动态添加关键词（如特定话题、专业术语或特殊呼号），当消息中包含该关键词时立即唤醒 AI。
  - **基础配置永久只读保护**：严格保护 AstrBot 系统自带的核心唤醒词（如 `/`），防止误删造成机器人瘫痪。
- **关注用户发言追踪 (Tracked User IDs)**
  - 支持将指定 QQ 用户加入关注名单。
  - 目标用户在群聊或私聊中一发言，无需任何唤醒词或 @ 即可直接唤醒 AI。
- **大模型自主权与双向工具契约**
  - 提供 `@filter.llm_tool` 工具：`track_target`、`untrack_target`、`list_tracked_targets`。
  - AI 可在交谈中自主决定关注或取消关注特定词语与用户。
- **AI 自决“选择说话还是沉默”机制**
  - 命中追踪唤醒后，AI 研判语境若认为无需介入或应当保持围观，输出 `[SILENCE]` 即可。
  - 插件在装饰器阶段清空回复链，AstrBot 自动跳过发送，实现完全 0 扰民静默。
- **聊天管理指令组**
  - 提供 `/track word <词语>`、`/track user <QQ>`、`/track list` 以及 `/untrack` 指令。
  - 支持群组独立作用域与全局作用域。
