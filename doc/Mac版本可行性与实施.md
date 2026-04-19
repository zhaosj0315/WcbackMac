# Mac 版本可行性与实施记录

## 结论

可行，并且当前机器已经完成端到端验证。边界必须清晰：

- 可行：在 macOS 上启动 PyQt 桌面端、读取已解密数据库、展示聊天、导出数据、使用 MemoAI/Ollama 工作流。
- 可行：复制一份 WeChat.app 到临时目录，对副本做 ad-hoc 重签名，启动副本后用 LLDB 读取内存中的 WCDB raw key，再批量解密 Mac 微信数据库。
- 可行：把 TrailSnap 的本地 AI 数据中心思路迁移到微信数据，先围绕“导入、索引、检索、AI 对话”做 Mac 本地流程。
- 不能平移：Windows 版的 `WeChat.exe`、`WeChatWin.dll`、注册表、`pywin32`、`pymem` 取 key 方式在 Mac 上不可复用。Mac 必须使用独立的进程权限、WCDB 页面解密和 Mac 4.x 表结构适配层。

## 已实施

- 主入口 `main.py` 增加平台判断，避免 macOS 调用 `ctypes.windll`。
- Windows 专属依赖 `pywin32`、`pymem` 改为仅 Windows 安装。
- `app/util/path.py` 改为跨平台微信路径发现。
- `app/decrypt/get_wx_info.py` 在非 Windows 上可安全导入，并返回明确的不支持状态。
- 工具页在 Mac 上隐藏 Windows 偏移地址采集逻辑，显示 Mac 导入提示。
- 新增纯 Python `app/ui/menu/about_dialog.py`，替代 Windows `.pyd` 在 Mac 上无法加载的问题。
- 音频转换优先使用系统 `ffmpeg`，兼容 Homebrew 安装方式。
- 新增 `scripts/mac_import_decrypted.py`，用于导入已解密数据库并自动合并消息分片。
- 新增 `scripts/mac_probe_wechat.py`，用于探测 Mac 自动解密可行性。
- 新增 `scripts/mac_decrypt_wechat.py`，用于在已知密钥时自动扫描、校验、批量解密 Mac 微信数据库。
- 新增 `scripts/lldb_scan_wechat_keys.py`，用于在 LLDB 附加 WeChat 副本后扫描数据库 raw key。
- 新增 `scripts/mac_decrypt_wcdb_raw.py`，用于解密 Mac WeChat 4.x WCDB/SQLCipher raw-key 数据库。
- 新增 `scripts/mac_decrypt_from_keys.py`，用于根据 LLDB 扫描出的 key 候选 JSON 批量解密数据库。
- 新增 `scripts/mac_export_messages.py`，用于把 Mac 4.x 的 `message_*.db` / `biz_message_*.db` 导出为 CSV 或 JSONL。
- 新增 `scripts/mac_auto_decrypt_export.py`，用于把“复制副本、重签、启动、扫 key、批量解密、导出”串成实验性一键流程。
- Mac 桌面端“解密”页已支持自动定位 Mac 微信数据库、输入密钥后解密到 `app/Database/MacMsg`。

## 本机探测结论

2026-04-18 在当前 Mac 环境下探测结果：

- 已找到运行中的 WeChat 进程。
- 已找到 Mac 微信 4.x 数据目录：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files`。
- 已找到 `message_0.db`、`contact.db`、`media_0.db` 等 28 个加密数据库。
- 普通权限下 `task_for_pid` 返回 5，无法读取 WeChat 进程内存。
- Keychain 元数据探测未发现直接命中的 WeChat 服务项。

后续实验证明：不建议修改 `/Applications/WeChat.app` 原应用；直接重签原应用可能因为运行中、系统保护或 Framework 子组件权限失败。更稳妥的方式是复制到 `/tmp/WeChat-resign-test.app`，只重签副本，读取完成后退出副本并重新打开原微信。

2026-04-18 已验证结果：

- 副本 `/tmp/WeChat-resign-test.app` 可以 ad-hoc 重签并启动。
- LLDB 可以附加到重签副本并扫描到数据库 raw key 候选。
- `message/message_0.db` 使用扫描出的 raw key 解密后 `pragma integrity_check` 返回 `ok`。
- 已批量解密 24 个核心数据库到 `app/Database/MacMsg`，包括 `message_0.db` 到 `message_9.db`、`contact.db`、`session.db`、`media_0.db` 等。
- 3 个 FTS 索引库完整性检查失败：`contact_fts.db`、`favorite_fts.db`、`message_fts.db`。这些是全文检索索引库，不影响核心聊天记录导出。
- 已从解密后的 Mac 消息库导出全量消息 CSV：`data/mac_messages.csv`，共 1,348,842 行。
- 曾导出最新消息样例用于验证；临时样例文件已在材料清理中删除，后续可按需重新生成。
- 已修复 Mac 新版消息内容解码：`message_content` 使用 zstd 压缩，魔数为 `0x28B52FFD`，不能直接 `bytes.decode()`，也不能只用 Windows/lz4/zlib 逻辑。
- HTML 导出已支持本地缓存图片展示：按 `MessageTemp/<会话hash>/Image/<local_id><create_time>_.pic*.jpg` 精确匹配，命中时内嵌真实图片；本地缓存不存在时降级显示 `[图片 + 尺寸/md5]`，避免错图。

因此当前已经不是“只能输入 key 半自动”，而是“实验性自动取 key + 批量解密 + 导出”跑通。仍需保留实验性标记，因为它依赖 LLDB 调试权限、微信版本、macOS 安全策略和当前登录态。

## Windows 逻辑如何复用

可以复用的是上层工程逻辑：

- UI 流程：获取信息、解密、导入、导出、展示进度。
- 导出理念：把数据库解密到项目自己的 `app/Database/...` 目录，再由统一导出层处理。
- 资源处理：图片、视频、语音、联系人、会话的归档和导出入口可以继续沿用。
- AI/检索：解密后的聊天文本、联系人、会话可以进入现有索引和 MemoAI/Ollama 工作流。

不能直接复用的是底层 Windows 解密和数据库读取：

- Windows key 来源是 `WeChat.exe` / `WeChatWin.dll` / 版本偏移 / `pymem`。
- Mac key 来源需要 LLDB 或等价调试权限读取 WeChat 进程内存，不能走 Windows 偏移表。
- Windows 数据库多为 `MSG.db`、`MicroMsg.db` 这套结构。
- Mac WeChat 4.x 是 `db_storage/message/message_*.db`、`contact/contact.db`、`session/session.db`，并且聊天表是大量 `Msg_*` 分表。
- Mac WCDB 页面格式需要保留 80 字节 reserve 区，不能直接套 Windows SQLCipher 解密函数。

正确改造方式是增加 Mac adapter：

1. Mac key provider：负责复制、重签副本、启动副本、LLDB 扫 key。
2. Mac decrypt provider：负责 WCDB raw-key 页面解密。
3. Mac repository：负责读取 `message_*.db`、`contact.db`、`session.db`。
4. Normalize layer：把 Mac 的 `Msg_*` 行转换成项目现有导出层能理解的统一消息模型。
5. UI/export 复用：上层按钮、进度、CSV/HTML/Docx/TXT 导出尽量接现有 Windows 流程。

## 推荐路线

1. 第一阶段：保持 Mac 命令行解密和 CSV/HTML 导出链路稳定。
2. 第二阶段：继续完善图片、语音、视频资源定位，明确缓存缺失时的降级行为。
3. 第三阶段：把联系人、群聊、小程序、引用、撤回等复杂消息类型做结构化解析。
4. 第四阶段：如需接入 MemoAI/Ollama，应以导出结果为输入另行设计，不在当前解密导出链路中承诺。

## 日常使用方式

```bash
python3 -m pip install -r requirements.txt
```

探测 Mac 可行性：

```bash
python3 scripts/mac_probe_wechat.py
```

推荐的一键实验流程：

```bash
python3 scripts/mac_auto_decrypt_export.py --quit-original --export-output data/mac_messages.csv
```

这个命令会：

1. 退出当前微信。
2. 复制 `/Applications/WeChat.app` 到 `/tmp/WeChat-resign-test.app`。
3. 对副本重签名，不修改原应用。
4. 打开副本微信。
5. 附加 LLDB 扫描数据库 raw key。
6. 批量解密到 `app/Database/MacMsg`。
7. 导出聊天记录到 `data/mac_messages.csv`。

只导出最新 N 条用于快速检查：

```bash
python3 scripts/mac_auto_decrypt_export.py --quit-original --latest 100 --export-output data/mac_latest_100.csv
```

如果已经有 `/tmp/wechat_lldb_key_candidates.json`，可以跳过取 key，直接批量解密和导出：

```bash
python3 scripts/mac_decrypt_from_keys.py --keys /tmp/wechat_lldb_key_candidates.json --output app/Database/MacMsg --verify
python3 scripts/mac_export_messages.py --db-dir app/Database/MacMsg --output data/mac_messages.csv
```

如果只想导出最新 50 条：

```bash
python3 scripts/mac_export_messages.py --db-dir app/Database/MacMsg --output data/mac_latest_50.csv --latest 50
```

已知单个数据库 raw key 时，也可以单库解密：

```bash
python3 scripts/mac_decrypt_wcdb_raw.py --key "64位hex密钥" --input "/path/to/encrypted.db" --output "/tmp/decrypted.db"
```

导出的 CSV 目前字段包括：`db_file`、`table_name`、`local_id`、`server_id`、`local_type`、`sort_seq`、`real_sender_id`、`create_time`、`datetime`、`status`、`source`、`message_content`、`compress_content`。

当前导出层已经能拿到文本消息和部分结构化内容；后续要继续完善的是联系人昵称映射、群聊名称映射、图片/语音/视频资源关联、撤回/引用/小程序等消息类型解析。

图片展示说明：

- Mac 微信消息库只保存图片 XML、md5、尺寸、cdn 信息，不保证保存真实图片二进制。
- 真实图片通常在 `~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/*/*/Message/MessageTemp/<会话hash>/Image/`。
- 如果本地缓存还在，HTML 会直接嵌入图片。
- 如果缓存已被微信清理、没有下载原图，HTML 只能显示占位信息；这不是数据库解密失败。
