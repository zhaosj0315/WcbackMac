# WcbackMac — Mac 微信聊天记录导出工具

> 解密 macOS 微信本地数据库，导出聊天记录、媒体文件，并提供 Web 查看器和 AI 训练数据生成。

[![License](https://img.shields.io/github/license/zhaosj0315/WcbackMac)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS-lightgrey)](https://www.apple.com/macos/)

---

## 功能

| 功能 | 说明 |
|------|------|
| 🔓 数据库解密 | 通过 LLDB 扫描内存获取 key，解密 WCDB 加密数据库 |
| 💬 聊天记录导出 | 支持 TXT / CSV / HTML / Word / JSON 多格式 |
| 🖼 媒体文件导出 | 图片、视频、语音（silk→wav）、文件附件 |
| 🌐 Web 查看器 | 浏览器内还原微信聊天界面，支持语音播放、视频播放、文件下载 |
| 📊 聊天统计 | 消息数量、活跃时段、Top 联系人分析 |
| 🤖 AI 训练数据 | 生成 Ollama 兼容的对话训练集（train/dev JSON） |
| 📱 朋友圈导出 | 导出 SnsTimeLine 朋友圈内容 |
| ⭐ 收藏导出 | 导出微信收藏内容 |

---

## 环境要求

- macOS 12+
- Python 3.10+
- 微信 4.x（已登录状态）
- LLDB（Xcode Command Line Tools）

```bash
pip install -r requirements.txt
```

---

## 快速开始

### 1. 探测环境

```bash
python3 scripts/mac_probe_wechat.py
```

### 2. 一键解密

```bash
python3 scripts/mac_auto_decrypt_export.py --quit-original
```

解密后的数据库写入 `app/DataBase/MacMsg/`。

### 3. 启动 Web 查看器

```bash
cd scripts
python3 mac_web_server.py
# 访问 http://127.0.0.1:5000
```

Web 查看器功能：
- 左侧联系人/群聊列表，支持搜索
- 消息类型筛选（文字/图片/语音/视频/文件/表情）
- 正序/倒序切换
- 基于本地 Ollama 小模型（<=10B）的推荐回复，自动参考当前联系人的历史聊天风格
- 点击顶部统计栏查看联系人列表和朋友圈
- 语音在线播放、视频在线播放、文件下载

> 推荐回复依赖本地 Ollama 服务（默认 `http://127.0.0.1:11434`），网页会自动筛选本机已安装的 `<=10B` 模型，并优先使用 `my-wechat-ai`。

### 4. 命令行全量导出

```bash
cd scripts

# 全量导出（含媒体文件复制，耗时较长）
python3 mac_export_by_session.py --output ../data

# 只导出聊天记录，跳过媒体复制
python3 mac_export_by_session.py --output ../data --no-global-media

# 单个会话
python3 mac_export_by_session.py --wxid wxid_xxx --output ../data --no-global-media
```

导出结构：

```
data/
├── image/          ← 全量图片
├── video/          ← 全量视频
├── voice/          ← 全量语音（wav）
├── files/          ← 全量文件附件
├── 朋友圈/
├── 聊天统计/
└── 聊天记录/
    └── 昵称(wxid)/
        ├── 昵称_chat.txt
        ├── 昵称.html
        ├── 昵称.csv
        ├── 昵称.txt
        ├── 昵称_N.docx
        ├── 昵称_train.json
        ├── 昵称_dev.json
        ├── avatar/
        └── image/ voice/ video/ file/
```

---

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `mac_probe_wechat.py` | 探测微信版本和数据库路径 |
| `mac_auto_decrypt_export.py` | 一键解密流程 |
| `mac_decrypt_from_keys.py` | 从已有 key 文件批量解密 |
| `mac_web_server.py` | FastAPI Web 查看器 |
| `mac_export_by_session.py` | 按会话全量导出（主入口） |
| `mac_message_utils.py` | 消息解析、媒体定位基础库 |
| `mac_contact_mapper.py` | 生成联系人映射文件 |
| `mac_chat_analysis.py` | 聊天统计分析 |
| `mac_memoai_prepare.py` | 生成 AI 训练数据 |
| `mac_realtime_monitor.py` | 实时监听新消息 |

---

## 技术说明

- Mac 微信 4.x 消息使用 **zstd 压缩**（魔数 `0x28B52FFD`），需先解压再解析
- 消息数据库按年份分片：`message_0.db`（最新）到 `message_9.db`（最旧）
- 每个分片有独立的 `Name2Id` 表，发送者 rowid 在不同分片中不同
- 图片/视频按 `create_time` 匹配本地缓存，缓存不存在时从 `attach/` 目录兜底

---

## 注意事项

- 本工具仅用于导出**自己的**微信数据，请勿用于任何非法用途
- 解密过程需要微信处于登录状态
- 导出的数据（`data/` 目录）包含个人隐私，请妥善保管，不要上传到公开平台

---

## License

[GPLv3](./LICENSE)
