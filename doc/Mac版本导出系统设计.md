# Mac 版本微信聊天导出系统 — 总体设计

## 1. 现状总结

### 1.1 数据来源

| 数据 | 路径 | 大小 | 状态 |
|------|------|------|------|
| 消息数据库（已解密） | `app/DataBase/MacMsg/message/message_*.db` | 1.6 GB | ✅ 已就绪 |
| 联系人数据库 | `app/DataBase/MacMsg/contact/contact.db` | 33 MB | ✅ 已就绪 |
| 朋友圈数据库 | `app/DataBase/MacMsg/sns/sns.db` | 23 MB | ✅ 已就绪 |
| 收藏数据库 | `app/DataBase/MacMsg/favorite/favorite.db` | 1.3 MB | ✅ 已就绪 |
| 图片缓存 | `~/Library/.../MessageTemp/*/Image/` | ~28 GB | ✅ 本地可用 |
| 视频缓存 | `~/Library/.../MessageTemp/*/Video/` | ~12 GB | ✅ 本地可用 |
| 语音数据 | `app/DataBase/MacMsg/message/media_0.db` | 内嵌 | ✅ 可提取 |
| 文件附件 | `~/Library/.../msg/file/` | 9.7 GB | ✅ 本地可用 |

### 1.2 数据规模

实际规模因用户而异，取决于微信使用年限和聊天量。典型情况下：

- 消息数据库（10 个分片）总大小约 1-2 GB
- 图片缓存约 10-30 GB（取决于微信是否清理过本地缓存）
- 语音数据存储在 `media_0.db` 的 `VoiceInfo` 表

### 1.3 已知限制

1. **图片历史缓存**：微信会自动清理本地图片缓存，旧消息图片可能无法找到，只能显示占位
2. **没有增量更新**：每次全量重跑，无法只导出新消息
3. **没有进度持久化**：中断后无法续跑
4. **emoji/music/icon 目录**：导出时创建目录骨架，但当前代码不填充内容
5. **朋友圈图片**：只导出 XML 元数据，图片需从 CDN 下载（未实现）

---

## 2. 目标输出结构

完全对齐 Windows 版本，并在此基础上增强：

```
data/
├── image/          ← 全量图片（.dat 自动识别格式）
├── video/          ← 全量视频（.mp4）
├── voice/          ← 全量语音（silk→wav）
├── files/          ← 全量文件附件
├── emoji/          ← 目录骨架（当前不填充）
├── music/          ← 目录骨架（当前不填充）
├── 朋友圈/
│   └── sns.json
├── 聊天统计/
│   └── analysis.json
└── 聊天记录/
    └── 昵称(wxid)/
        ├── 昵称_chat.txt    ← 文本记录
        ├── 昵称.html        ← HTML（含图片/语音/视频）
        ├── 昵称.csv         ← CSV（含完整字段）
        ├── 昵称.txt         ← TXT（简洁格式）
        ├── 昵称_N.docx      ← Word（每500条一个文件）
        ├── 昵称_train.json  ← AI 训练数据
        ├── 昵称_dev.json    ← AI 验证数据
        ├── avatar/          ← 双方头像（从网络下载）
        ├── image/           ← 该会话图片
        ├── voice/           ← 该会话语音
        ├── video/           ← 该会话视频
        ├── emoji/
        ├── file/
        ├── music/
        └── icon/
```

---

## 3. 系统架构

### 3.1 分层设计

```
┌─────────────────────────────────────────────────────┐
│                    用户入口层                         │
│  mac_export_by_session.py  /  mac_web_server.py      │
│  （命令行全量导出）          （Web 实时查看）          │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                    业务逻辑层                         │
│  mac_message_utils.py  ← 核心：消息解析/媒体定位      │
│  mac_contact_mapper.py ← 联系人映射                  │
│  mac_realtime_monitor.py ← 实时监听                  │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                    数据访问层                         │
│  app/DataBase/MacMsg/  ← 解密后的 SQLite 数据库       │
│  ~/Library/.../MessageTemp/  ← 本地媒体缓存           │
└─────────────────────────────────────────────────────┘
```

### 3.2 核心模块职责

| 模块 | 职责 | 状态 |
|------|------|------|
| `mac_message_utils.py` | zstd 解码、消息类型解析、媒体文件定位 | ✅ 已完成 |
| `mac_contact_mapper.py` | wxid→昵称映射、群聊名称 | ✅ 已完成 |
| `mac_export_by_session.py` | 按会话分目录全量导出（主入口） | ✅ 已完成 |
| `mac_web_server.py` | FastAPI Web 服务，实时查看 | ✅ 已完成 |
| `mac_realtime_monitor.py` | 轮询新消息 | ✅ 已完成 |
| `mac_auto_decrypt_export.py` | 一键解密流程 | ✅ 已完成 |
| `mac_memoai_prepare.py` | 生成 Ollama 训练数据 | ✅ 已完成 |

---

## 4. 关键技术决策

### 4.1 消息解码

Mac 微信 4.x 使用 **zstd 压缩**（魔数 `0x28B52FFD`），格式为：
```
wxid:\n<正文或XML>
```
不能直接 `bytes.decode()`，必须先 zstd 解压再去掉发送者前缀。

### 4.2 消息类型归一化

Mac 的 `local_type` 有时是组合值（高 32 位为子类型），需要：
```python
base_type = msg_type & 0xFFFFFFFF if msg_type > 0xFFFFFFFF else msg_type
```

### 4.3 图片/视频定位

本地缓存路径规律：
```
MessageTemp/<会话md5>/Image/<local_id><create_time>_.pic.jpg
MessageTemp/<会话md5>/Video/<local_id>_<create_time>.mp4
```
首次扫描建立索引（约 15 万条），后续查询 O(1)。

### 4.4 语音提取

语音存储在 `media_0.db` 的 `VoiceInfo` 表，通过 `chat_name_id + local_id + create_time` 定位，用 `pysilk` 转为 WAV。

### 4.5 数据库分片

Mac 4.x 按年份分片：`message_0.db`（最新）到 `message_9.db`（最旧）。同一会话的 `Msg_xxx` 表可能跨多个分片，导出时必须聚合所有分片再排序。

---

## 5. 待完善的问题

### P0（影响数据完整性）

| 问题 | 影响 | 解决方案 |
|------|------|----------|
| 两个群聊历史消息缺失 | `18530047360@chatroom` 少 27,295 条，`24084416120@chatroom` 少 27,406 条 | 这些消息在 Mac 本地数据库中本来就不存在，需要从 Windows 备份合并 |
| 图片历史缓存缺失 | 旧消息图片本地缓存已被微信清理 | 无法恢复，只能显示占位 |

### P1（影响使用体验）

| 问题 | 影响 | 解决方案 |
|------|------|----------|
| 全量导出无进度持久化 | 中断后需重跑 | 增加 `export_state.json` 记录已完成会话 |
| 没有增量更新 | 每次全量重跑 | 记录每个会话最后导出的 `create_time` |
| 语音无法直接播放 | silk 格式需转码 | 已转 WAV，Web 服务已支持 `<audio>` 播放 |
| 引用/撤回/小程序消息解析不完整 | 显示为 XML 原文 | 需专项解析 type=49 子类型 |

### P2（增强功能）

| 功能 | 说明 |
|------|------|
| Windows 历史数据合并 | 把 `app/Database/Msg/MSG.db` 的历史消息补充进来 |
| 搜索功能 | 基于 FTS 或全文索引 |
| 统计图表 | 消息热力图、词云、Top 联系人 |
| 朋友圈图片下载 | 当前只有 XML，需要从 CDN 下载 |

---

## 6. 推荐使用流程

### 6.1 首次完整导出

```bash
# 步骤1：确认解密库已就绪
ls app/DataBase/MacMsg/message/message_*.db

# 步骤2：生成联系人映射（如果还没有）
python3 scripts/mac_contact_mapper.py

# 步骤3：全量导出（含全量媒体，约需 30-60 分钟）
python3 scripts/mac_export_by_session.py --output data

# 步骤4：启动 Web 查看器
python3 scripts/mac_web_server.py
# 访问 http://127.0.0.1:5000
```

### 6.2 日常增量更新（微信有新消息后）

```bash
# 重新解密（获取最新消息）
python3 scripts/mac_auto_decrypt_export.py --quit-original

# 重新导出（当前为全量，后续可改为增量）
python3 scripts/mac_export_by_session.py --output data --no-global-media
```

### 6.3 MemoAI 训练

```bash
python3 scripts/mac_memoai_prepare.py --db-dir app/DataBase/MacMsg/message --create-model
```

---

## 7. 脚本整理建议

当前 30 个脚本中，建议保留以下核心脚本，其余标记为废弃：

### 保留（核心）

| 脚本 | 用途 |
|------|------|
| `mac_message_utils.py` | 消息解析基础库 |
| `mac_export_by_session.py` | **主导出入口**（替代其他所有导出脚本） |
| `mac_web_server.py` | Web 查看器 |
| `mac_auto_decrypt_export.py` | 一键解密 |
| `mac_realtime_monitor.py` | 实时监听 |
| `mac_memoai_prepare.py` | AI 训练数据 |
| `mac_contact_mapper.py` | 联系人映射 |
| `mac_probe_wechat.py` | 环境探测 |
| `mac_chat_analysis.py` | 统计分析 |
| `mac_wordcloud.py` | 词云 |

### 可废弃（功能已被 mac_export_by_session.py 覆盖）

`mac_export_html.py`、`mac_export_txt.py`、`mac_export_word.py`、`mac_export_json.py`、`mac_export_messages.py`、`mac_export_csv_enhanced.py`、`mac_export_wechat_style_html.py`、`mac_export_all.py`、`mac_export_media.py`、`mac_export_sns.py`、`mac_export_favorite.py`

---

## 8. 下一步优先级

1. **P0**：测试 `mac_export_by_session.py` 全量导出，验证所有格式正确
2. **P1**：增加导出进度持久化（断点续跑）
3. **P1**：实现 Windows 历史数据合并（补充缺失的两个群聊历史）
4. **P2**：完善 type=49 子类型解析（引用、小程序、文件）
5. **P2**：朋友圈图片从 CDN 下载
