# 🎯 astrbot_plugin_extra_wake_tracker

> **AstrBot 动态额外唤醒词与关注用户发言追踪插件**  
> 增减自己的额外唤醒词（严格保护自带配置唤醒词），追踪特定用户发言直接唤醒 AI，AI 自主选择说话或保持沉默。

---

## ✨ 核心特性

1. **动态额外唤醒词**
   - 可以自由增加任意业务词、话题词或特殊代号作为额外唤醒词；
   - 消息中只要包含额外唤醒词，AI 便会被立即唤醒进入认知研判。
2. **基础配置自带唤醒词永久保护**
   - 绝不修改或删除 AstrBot 主配置中自带的原生唤醒前缀（如 `/`）；
   - 无论是指令误操作还是大模型误调用 `untrack`，系统均会坚决拒绝并拦截。
3. **关注用户发言追踪**
   - 被记录的 QQ 用户一发言，无需加前缀或 @，直接唤醒 AI。
4. **AI 自决“选择说话或沉默”（0 扰民围观）**
   - 被唤醒后，AI 会根据群聊当前上下文研判是否需要介入；
   - 若有必要发言，正常输出内容；
   - 若无需介入，输出 `[SILENCE]`，插件自动拦截并跳过发送，QQ 端完全无感。
5. **双向增删控制**
   - **大模型自主权**：提供标准 `@filter.llm_tool`，AI 可在对话中自主决定开始追踪或结束追踪；
   - **群友/管理员指令**：支持 `/track` 和 `/untrack` 简洁指令。

---

## 🛠️ 聊天前缀指令

| 指令 | 示例 | 说明 |
| :--- | :--- | :--- |
| `/track word <词语> [原因]` | `/track word 显卡` | 添加额外唤醒词，群友提及该词即刻唤醒 |
| `/track user <QQ/@某人> [原因]` | `/track user 12345678` | 追踪指定用户，该用户发言直接唤醒 AI |
| `/track list` | `/track list` | 查看当前激活的所有额外词、关注用户与受保护系统词 |
| `/untrack word <词语>` | `/untrack word 显卡` | 取消对额外词语的唤醒追踪（系统词受保护不可删） |
| `/untrack user <QQ/@某人>` | `/untrack user 12345678` | 取消对指定用户的发言追踪 |

---

## 🤖 大模型函数调用工具 (LLM Tools)

* **`track_target(target_type, target_value, reason, is_global)`**
  * 参数：`target_type` ("word" 或 "user")，`target_value` (词语或 QQ号)，`reason` (原因)
  * 功能：AI 自主决定对某个关键词或用户开启追踪。
* **`untrack_target(target_type, target_value)`**
  * 参数：`target_type` ("word" 或 "user")，`target_value`
  * 功能：AI 自主取消追踪。系统原生唤醒词受保护不可删除。
* **`list_tracked_targets()`**
  * 功能：AI 查看当前正处于监控列表中的词和用户名单。

---

## ⚙️ 配置项说明 (_conf_schema.json)

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :---: | :---: | :--- |
| `allow_ai_self_track` | bool | `true` | 是否允许 AI 自主调用工具增减追踪规则 |
| `enable_silence_keyword` | bool | `true` | 是否启用 `[SILENCE]` 拦截机制，支持 AI 静默围观 |
| `admin_only_command` | bool | `false` | 聊天前缀指令是否仅限管理员使用 |
| `default_scope` | string | `"group"` | 默认追踪范围（`group` 当前群或 `global` 全局） |

---

## 📄 开源许可证

本项目遵循 MIT 协议开源。
