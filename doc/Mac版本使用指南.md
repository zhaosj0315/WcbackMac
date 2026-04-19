# Mac 版本使用指南

## 1. 前置条件

- macOS 12+，微信 4.x（已登录）
- Python 3.10 / 3.11 / 3.12
- Xcode Command Line Tools（提供 LLDB）

```bash
pip install -r requirements.txt
```

---

## 2. 完整流程

所有命令从**项目根目录**运行。默认数据库路径：`app/DataBase/MacMsg`。

### 步骤 1：探测环境

```bash
python3 scripts/mac_probe_wechat.py
```

确认微信进程、沙盒目录和数据库路径是否正常。

### 步骤 2：解密数据库（实验性）

```bash
python3 scripts/mac_auto_decrypt_export.py --quit-original
```

流程：退出微信 → 复制副本到 `/tmp/` → ad-hoc 重签 → 启动副本 → LLDB 扫描 key → 批量解密到 `app/DataBase/MacMsg/`。

> 依赖 macOS 调试权限和微信版本，属实验性流程。如果失败，检查 LLDB 权限或手动提供 key 文件。

已有 key 文件时可跳过 LLDB：

```bash
python3 scripts/mac_decrypt_from_keys.py \
  --keys /tmp/wechat_lldb_key_candidates.json \
  --output app/DataBase/MacMsg --verify
```

### 步骤 3：生成联系人映射

```bash
python3 scripts/mac_contact_mapper.py
```

输出 `data/mac_contact_mapping.json`，Web 查看器和导出脚本均依赖此文件。

### 步骤 4a：Web 查看器（推荐）

```bash
python3 scripts/mac_web_server.py
# 访问 http://127.0.0.1:5000
```

功能：
- 左侧会话列表，支持搜索（加载全部会话）
- 消息类型筛选：文字 / 图片 / 语音 / 视频 / 文件 / 表情 / 系统消息
- 正序 / 倒序切换
- 语音在线播放（wav）、视频在线播放、文件下载
- 点击顶部统计栏查看联系人列表和朋友圈

### 步骤 4b：命令行全量导出

```bash
# 全量导出（含媒体文件复制，耗时 30-60 分钟，磁盘占用较大）
python3 scripts/mac_export_by_session.py --output data

# 只导出聊天记录，跳过媒体复制（推荐先用此方式验证）
python3 scripts/mac_export_by_session.py --output data --no-global-media

# 单个会话测试
python3 scripts/mac_export_by_session.py \
  --wxid wxid_xxx --output data/test --no-global-media
```

---

## 3. 导出结构

```
data/
├── image/          ← 全量图片（.dat 自动识别格式）
├── video/          ← 全量视频（.mp4）
├── voice/          ← 全量语音（silk→wav）
├── files/          ← 全量文件附件
├── 朋友圈/sns.json
├── 聊天统计/analysis.json
└── 聊天记录/
    └── 昵称(wxid)/
        ├── 昵称_chat.txt   ← 文本记录
        ├── 昵称.html       ← HTML（含图片/语音/视频）
        ├── 昵称.csv        ← CSV（含完整字段）
        ├── 昵称.txt        ← TXT（简洁格式）
        ├── 昵称_N.docx     ← Word（每 500 条一个文件）
        ├── 昵称_train.json ← AI 训练数据
        ├── 昵称_dev.json   ← AI 验证数据
        ├── avatar/
        └── image/ voice/ video/ file/
```

---

## 4. 技术说明

### 消息解码

Mac 微信 4.x 消息内容使用 **zstd 压缩**（魔数 `0x28B52FFD`），格式为 `wxid:\n<正文或XML>`。

### 数据库分片

消息按年份分片：`message_0.db`（最新）到 `message_9.db`（最旧）。每个分片有独立的 `Name2Id` 表，同一联系人在不同分片中的 `real_sender_id` 不同，工具按分片独立映射发送者。

### 媒体定位

1. 先查 `MessageTemp/<会话md5>/Image(Video)/`，按 `local_id + create_time` 精确匹配
2. 找不到时降级为只按 `create_time` 匹配（local_id 跨分片不一致）
3. 再兜底查 `attach/<会话md5>/` 目录（按 md5 或时间戳匹配）

### my_wxid 自动检测

工具自动从 `xwechat_files/wxid_*` 目录名推断当前登录账号，也可通过 `--my-wxid` 手动指定。

---

## 5. 已知限制

| 限制 | 说明 |
|------|------|
| 图片占位 | 微信清理本地缓存后旧图片无法找到，显示占位，属正常现象 |
| 视频不可用 | 视频只在微信下载后才有本地缓存，未下载的无法播放 |
| emoji/music/icon 目录为空 | 当前代码创建目录骨架但不填充内容 |
| 朋友圈无图片 | 只导出 XML 元数据，图片需从 CDN 下载（未实现） |
| 无增量更新 | 每次全量重跑，中断后需重新开始 |
| 解密依赖 LLDB 权限 | 部分 macOS 版本或 SIP 配置下可能失败 |

---

## 6. 常见问题

**Q：FTS 数据库完整性检查失败？**  
`*_fts.db` 是全文检索索引，不影响消息导出，忽略即可。

**Q：联系人显示 wxid 而非昵称？**  
先运行 `python3 scripts/mac_contact_mapper.py` 生成联系人映射文件。

**Q：Web 查看器启动报 ModuleNotFoundError？**  
必须在 `scripts/` 目录下运行，或从项目根目录运行 `python3 scripts/mac_web_server.py`。
