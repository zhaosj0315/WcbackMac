# Mac 版本功能总结

## 1. 版本状态

审计日期：2026-04-19  
代码状态：以当前目录代码为准，本文档仅描述已实现和已验证能力。

## 2. 功能状态表

| 模块 | 状态 | 当前依据 | 主要限制 |
|------|------|----------|----------|
| 环境探测 | 可用 | `scripts/mac_probe_wechat.py` | 只探测，不解密 |
| LLDB key 扫描 | 实验性 | `scripts/lldb_scan_wechat_keys.py`、`scripts/mac_auto_decrypt_export.py` | 依赖副本重签、调试权限、微信登录态 |
| WCDB raw-key 解密 | 已验证 | `scripts/mac_decrypt_wcdb_raw.py`、`scripts/mac_decrypt_from_keys.py` | 适配当前 Mac WeChat 4.x 页面格式 |
| 批量解密 | 已验证 | `scripts/mac_decrypt_from_keys.py` | FTS 索引库可能失败，不影响核心消息 |
| CSV 导出 | 已验证 | `scripts/mac_export_messages.py` | 复杂 XML 消息只做基础文本化 |
| HTML 导出 | 已验证 | `scripts/mac_export_html.py`、`scripts/mac_message_utils.py` | 图片/视频依赖 `MessageTemp` 本地缓存，语音导出为 silk 附件 |
| Word 导出 | 已验证 | `scripts/mac_export_word.py`、`scripts/mac_message_utils.py` | 图片可嵌入 docx；语音/视频导出为同名 `_media` 附件 |
| TXT 导出 | 可用 | `scripts/mac_export_txt.py`、`scripts/mac_message_utils.py` | 文本归档为主，媒体仅输出说明和本地路径 |
| JSON 导出 | 已验证 | `scripts/mac_export_json.py`、`scripts/mac_message_utils.py` | 输出结构化消息和媒体附件路径 |
| 联系人映射 | 可用 | `scripts/mac_contact_mapper.py` | 依赖 `contact/contact.db` |
| 数据库合并 | 可用 | `scripts/mac_merge_db.py` | 合并表是项目自定义结构 |
| 收藏导出 | 已验证 | `scripts/mac_export_favorite.py` | 适配 Mac 4.x `fav_db_item` 表，支持 zstd 解码 |
| 朋友圈导出 | 已验证 | `scripts/mac_export_sns.py` | 适配 Mac 4.x `SnsTimeLine` 表，解析 XML 内容 |
| 统计分析 | 已验证 | `scripts/mac_chat_analysis.py` | 直接读取 `message_*.db` 分片，不依赖合并库 |
| 实时消息监听 | 可用 | `scripts/mac_realtime_monitor.py` | 轮询 `message_*.db` 变化，替代 Windows `realTime.exe` |
| FastAPI Web 服务 | 可用 | `scripts/mac_web_server.py` | 联系人/会话/消息/媒体/朋友圈/收藏 REST API |
| GUI | 可用但非主流程 | `app/gui/main_window.py`、`app/gui/simple_gui.py` | 当前文档主流程仍推荐命令行 |
| 统一 CLI | 可用 | `scripts/wxdump_mac.py` | 复杂排障时仍建议直接运行专项脚本 |

## 3. 已验证事实

- 已找到 Mac 微信 4.x 数据目录。
- 已扫描到数据库 key 候选。
- 已批量解密核心数据库。
- 已导出 `data/mac_messages.csv`。
- 已验证 zstd 压缩消息可解码。
- 已验证朋友圈导出适配 Mac 4.x `SnsTimeLine` 表（4394 条）。
- 已验证收藏导出适配 Mac 4.x `fav_db_item` 表（293 条）。
- 已验证统计分析直接读取 `message_*.db` 分片（1,300,493 条消息）。
- 已验证实时消息监听轮询逻辑可正常初始化和运行。
- 已验证 FastAPI Web 服务可启动，提供联系人/会话/消息/媒体/朋友圈/收藏 API。
- 已验证 HTML 可在本地图片缓存命中时内嵌真实图片。
- 已验证 Word 可嵌入图片，并可导出语音 silk、视频 mp4 附件。
- 已验证 JSON 可导出结构化消息，并可为语音/视频生成附件路径。
- 已验证 Mac 组合消息类型可按低 32 位归一化解析，分享/小程序不再误判为未知类型。
- 已验证单会话 HTML/Word/TXT 会聚合多个 `message_*.db` 分片，避免漏导同一会话的历史消息。
- 已验证聊天分析和词云脚本可在当前合并库上生成输出。

## 4. 不能继续使用的旧结论

以下旧说法已经从长期文档中删除：

- “100% 复刻 PyWxDump”。
- “与 Windows 版本生产等价”。
- “所有 HTML 图片都能真实展示”。
- “统一 CLI 是唯一推荐入口”。
- “CSV 中所有复杂消息都已完整结构化解析”。
- “单会话导出只需要读取第一个包含该表的数据库分片”。

## 5. 当前主流程

推荐主流程：

```bash
python3 scripts/mac_auto_decrypt_export.py --quit-original --export-output data/mac_messages.csv
```

推荐导出流程：

```bash
python3 scripts/mac_export_all.py --all --db-dir app/Database/MacMsg --output data/export
```

## 6. 遗留风险

1. macOS 安全策略或微信版本变化可能导致 LLDB 附加失败。
2. `app/Database/MacMsg` 与 `app/DataBase/MacMsg` 并存，文档统一推荐脚本默认的 `app/Database/MacMsg`。
3. 图片和视频展示依赖本机 `MessageTemp` 缓存，历史媒体可能已经被微信清理。
4. 语音已可从 `media_0.db` 导出为 silk；播放或转码为通用音频仍需外部 silk 解码工具。
5. 复杂 XML 消息、引用、撤回、文件附件等消息类型仍可能需要专项解析。
