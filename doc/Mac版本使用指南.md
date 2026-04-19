# Mac 版本使用指南

## 1. 当前定位

本项目当前 Mac 版本已经跑通“复制微信副本、LLDB 扫描 key、批量解密数据库、导出聊天记录”的实验性流程。它不是 Windows 版逻辑的直接平移：Windows 依赖 `WeChat.exe`、`WeChatWin.dll`、注册表、`pywin32`、`pymem`；Mac 版本使用 WeChat 副本、ad-hoc 重签、LLDB、WCDB raw-key 解密和 Mac 4.x 消息表适配。

## 2. 前置条件

- macOS，当前验证环境为 Mac 微信 4.x。
- Python 3.10+。
- 当前用户可以访问微信沙盒目录。
- 微信账号已登录，且本机存在聊天数据库。
- 已安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

## 3. 推荐流程

### 3.1 探测环境

```bash
python3 scripts/mac_probe_wechat.py
```

用于确认微信进程、沙盒目录和数据库是否存在。

### 3.2 一键实验解密并导出 CSV

```bash
python3 scripts/mac_auto_decrypt_export.py --quit-original --export-output data/mac_messages.csv
```

该命令会：

1. 退出当前微信。
2. 复制 `/Applications/WeChat.app` 到 `/tmp/WeChat-resign-test.app`。
3. 对副本做 ad-hoc 重签名。
4. 启动副本微信。
5. 使用 LLDB 扫描数据库 raw key。
6. 批量解密数据库到 `app/Database/MacMsg`。
7. 导出消息 CSV 到 `data/mac_messages.csv`。

注意：该流程依赖 macOS 调试权限、微信版本和当前登录态，属于实验性自动化。

### 3.3 已有 key 候选时跳过 LLDB

如果已经存在 `/tmp/wechat_lldb_key_candidates.json`：

```bash
python3 scripts/mac_decrypt_from_keys.py --keys /tmp/wechat_lldb_key_candidates.json --output app/Database/MacMsg --verify
python3 scripts/mac_export_messages.py --db-dir app/Database/MacMsg --output data/mac_messages.csv
```

### 3.4 导出最新 N 条用于检查

```bash
python3 scripts/mac_export_messages.py --db-dir app/Database/MacMsg --output data/mac_latest_100.csv --latest 100
```

### 3.5 多格式导出

```bash
python3 scripts/mac_export_all.py --all --db-dir app/Database/MacMsg --output data/export
```

支持的格式由当前代码提供：

- 数据库文件复制。
- CSV。
- HTML。
- Word。
- TXT。
- JSON。

全量 HTML/Word/TXT 会按会话生成大量文件，耗时和磁盘占用会明显增加。

## 4. 富媒体导出规则

Mac 微信数据库中的图片、分享、小程序等消息通常是 zstd 压缩后的 XML，真实媒体文件不一定直接保存在消息库中。当前导出器按以下规则处理：

1. 解压 `message_content`，支持 Mac 新版 zstd 压缩。
2. 归一化 Mac 组合消息类型，例如高 32 位保存小程序子类型、低 32 位为基础类型 `49`。
3. 解析图片、语音、视频、表情包、分享/小程序 XML。
4. 在微信本地缓存 `MessageTemp/<会话hash>/Image/` 和 `MessageTemp/<会话hash>/Video/` 中按 `local_id + create_time` 匹配图片、视频。
5. 从 `message/media_0.db` 的 `VoiceInfo` 表提取语音 silk 数据。

各格式行为：

- HTML：图片和视频命中缓存时以内嵌 data URI 展示；语音导出到同名 `_media/voice` 目录并写入路径。
- Word：图片写入 docx 内部媒体；视频复制到同名 `_media` 目录；语音导出为 `.silk` 文件并在文档中标注路径。
- JSON：输出结构化字段 `type_name`、`content`、`title`、`description`、`url`、`xml`、`media`；语音/视频附件写入 JSON 同名目录下的 `media/`。
- TXT：输出文本化内容，命中图片/视频本地缓存时补充本地路径。

占位不代表数据库解密失败，通常表示本地图片缓存已被微信清理或原图未下载。

## 5. 统一 CLI

`scripts/wxdump_mac.py` 已适配当前 Mac 脚本接口，可作为统一入口使用。但排障和批量验证时仍建议直接调用上文列出的专项脚本，便于观察日志和定位失败环节。

## 6. 路径说明

脚本默认路径为 `app/Database/MacMsg`。当前目录中也存在 `app/DataBase/MacMsg`，这是历史兼容目录。使用命令时优先保持默认路径，除非你明确知道当前数据在哪个目录。

## 7. 常见问题

### Q1：可以直接重签原始 WeChat.app 吗？

不建议。已验证更稳妥的方式是复制到 `/tmp/WeChat-resign-test.app`，只重签副本，不修改原应用。

### Q2：为什么有些 FTS 数据库完整性检查失败？

`contact_fts.db`、`favorite_fts.db`、`message_fts.db` 是全文检索索引库。核心消息、联系人、会话数据库通过即可导出主要聊天记录。

### Q3：为什么 HTML 里有些图片还是占位？

因为真实图片依赖本机 `MessageTemp` 缓存。缓存不存在时只能展示数据库中保存的尺寸/md5 信息。

### Q4：Windows 逻辑能直接复用吗？

不能直接复用底层取 key 和数据库结构。可以复用的是导出理念、UI 流程和上层数据处理模式。
