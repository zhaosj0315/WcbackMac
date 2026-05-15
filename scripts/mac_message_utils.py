#!/usr/bin/env python3
import base64
import hashlib
import mimetypes
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import zstd
except ImportError:
    zstd = None

try:
    import lz4.block
except ImportError:
    lz4 = None


IMAGE_NAME_RE = re.compile(r"^(\d+)(\d{10})_\.pic(?:_hd|_thumb)?\.(jpg|jpeg|png|gif|webp)$", re.I)
CACHE_IMAGE_NAME_RE = re.compile(r"^(\d+)_(\d{10})(?:_(?:thumb|hd|b))?\.(jpg|jpeg|png|gif|webp|dat)$", re.I)
VIDEO_NAME_RE = re.compile(r"^(\d+)_(\d{10})\.(mp4|mov|m4v)$", re.I)


def parse_image_name(name: str) -> tuple[int, int] | None:
    match = IMAGE_NAME_RE.match(name)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = CACHE_IMAGE_NAME_RE.match(name)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def decode_message_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return str(value)
    if not value:
        return ""

    if zstd is not None and value[:4] == b"\x28\xb5\x2f\xfd":
        try:
            return zstd.decompress(value).decode("utf-8", errors="replace")
        except Exception:
            pass

    if lz4 is not None:
        try:
            return lz4.block.decompress(value, uncompressed_size=len(value) << 10).decode(
                "utf-8", errors="replace"
            ).replace("\x00", "")
        except Exception:
            pass

    for encoding in ("utf-8", "gb18030", "utf-16le"):
        try:
            return value.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    return ""


def strip_sender_prefix(text: str) -> str:
    if "\n" in text:
        first, rest = text.split("\n", 1)
        if ":" in first and len(first) < 100:
            return rest
    return text


def safe_text(value: str) -> str:
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", value or "")


def xml_root(text: str):
    text = text.strip()
    if not text.startswith("<"):
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def normalize_message_type(msg_type: int) -> int:
    """Mac local_type sometimes packs appmsg subtype in the high 32 bits."""
    try:
        value = int(msg_type)
    except (TypeError, ValueError):
        return 0
    if value > 0xFFFFFFFF:
        return value & 0xFFFFFFFF
    return value


@dataclass
class ParsedMessage:
    type_name: str
    text: str
    xml: str = ""
    title: str = ""
    description: str = ""
    url: str = ""
    media_path: str = ""
    media_kind: str = ""
    media_mime: str = ""
    voice_length_ms: int | None = None
    file_size: int | None = None


class MacMediaResolver:
    def __init__(self, db_dir: str | Path = "app/Database/MacMsg"):
        self.db_dir = Path(db_dir)
        self.app_support = Path(
            "~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat"
        ).expanduser()
        self.xwechat = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        attach_candidates = sorted(self.xwechat.glob("wxid_*/msg/attach"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        self.attach_base = attach_candidates[0] if attach_candidates else Path("/nonexistent")
        self._image_index: dict[tuple[str, int, int], Path] | None = None
        self._video_index: dict[tuple[str, int, int], Path] | None = None
        self._voice_chat_ids: dict[str, int] | None = None
        self._sender_cache: dict[int, str] = {}
        self._load_contact_db()

    def _detect_my_wxid(self) -> str:
        """从 xwechat_files 目录名自动检测当前登录账号的 wxid"""
        xwechat = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        candidates = sorted(xwechat.glob("wxid_*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        if candidates:
            # 目录名格式为 wxid_xxx_7323，取 wxid 部分
            name = candidates[0].name
            wxid = name.rsplit("_", 1)[0] if "_" in name else name
            return wxid
        return ""

    def _load_contact_db(self) -> None:
        """每个分片的 Name2Id 是独立的局部映射，必须按分片建立 rowid->name 表"""
        # contact.db: wxid -> display_name
        contact_db = self.db_dir / "contact" / "contact.db"
        if not contact_db.exists():
            contact_db = self.db_dir / "contact.db"
        self._wxid_to_name: dict[str, str] = {}
        if contact_db.exists():
            try:
                with sqlite3.connect(contact_db) as conn:
                    for username, remark, nick_name in conn.execute(
                        "SELECT username, remark, nick_name FROM contact"
                    ):
                        self._wxid_to_name[username] = (remark or nick_name or username)
            except Exception:
                pass

        # 每个分片独立建立 rowid->name 和 my_rowid
        # _shard_map: db_stem -> {rowid: display_name}
        # _shard_my_rowid: db_stem -> my_rowid
        self._shard_map: dict[str, dict[int, str]] = {}
        self._shard_my_rowid: dict[str, int] = {}
        # 自动从 contact.db 检测当前登录账号的 wxid（local_type=1 的第一个账号）
        self._my_wxid = self._detect_my_wxid()

        msg_dir = self.db_dir / "message"
        if not msg_dir.exists():
            return
        for db_file in sorted(msg_dir.glob("message_*.db")):
            if "fts" in db_file.name:
                continue
            stem = db_file.stem  # e.g. "message_0"
            rowid_map: dict[int, str] = {}
            my_rowid = -1
            try:
                with sqlite3.connect(db_file) as conn:
                    has = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='Name2Id'"
                    ).fetchone()
                    if not has:
                        continue
                    for rowid, user_name in conn.execute("SELECT rowid, user_name FROM Name2Id"):
                        if not user_name:
                            continue
                        name = self._wxid_to_name.get(user_name, user_name)
                        rowid_map[int(rowid)] = name
                        if user_name == self._my_wxid:
                            my_rowid = int(rowid)
            except Exception:
                continue
            self._shard_map[stem] = rowid_map
            if my_rowid >= 0:
                self._shard_my_rowid[stem] = my_rowid

        # 兼容旧接口：_sender_cache 用 message_0 的映射
        self._sender_cache = self._shard_map.get("message_0", {})
        self._my_rowids = {v for v in self._shard_my_rowid.values()}

    def message_temp_roots(self) -> list[Path]:
        if not self.app_support.exists():
            return []
        return sorted(path for path in self.app_support.glob("*/*/Message/MessageTemp") if path.exists())

    def message_cache_roots(self) -> list[Path]:
        if not self.xwechat.exists():
            return []
        return sorted(
            (path for path in self.xwechat.glob("wxid_*/cache/*/Message") if path.exists()),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

    def _rank_image(self, path: Path) -> int:
        name = path.name.lower()
        if "_hd." in name:
            return 3
        if "_thumb." in name:
            return 1
        return 2

    def _build_indexes(self) -> None:
        images: dict[tuple[str, int, int], Path] = {}
        videos: dict[tuple[str, int, int], Path] = {}
        for root in self.message_temp_roots():
            for image_dir in root.glob("*/Image"):
                conv_hash = image_dir.parent.name
                if not image_dir.is_dir():
                    continue
                try:
                    image_paths = list(image_dir.iterdir())
                except OSError:
                    continue
                for path in image_paths:
                    parsed = parse_image_name(path.name)
                    if not parsed:
                        continue
                    key = (conv_hash, parsed[0], parsed[1])
                    previous = images.get(key)
                    if previous is None or self._rank_image(path) > self._rank_image(previous):
                        images[key] = path
            for video_dir in root.glob("*/Video"):
                conv_hash = video_dir.parent.name
                if not video_dir.is_dir():
                    continue
                try:
                    video_paths = list(video_dir.iterdir())
                except OSError:
                    continue
                for path in video_paths:
                    match = VIDEO_NAME_RE.match(path.name)
                    if not match:
                        continue
                    videos[(conv_hash, int(match.group(1)), int(match.group(2)))] = path
        for root in self.message_cache_roots():
            for folder_name in ("Thumb", "Image"):
                for image_dir in root.glob(f"*/{folder_name}"):
                    conv_hash = image_dir.parent.name
                    if not image_dir.is_dir():
                        continue
                    try:
                        image_paths = list(image_dir.iterdir())
                    except OSError:
                        continue
                    for path in image_paths:
                        parsed = parse_image_name(path.name)
                        if not parsed:
                            continue
                        key = (conv_hash, parsed[0], parsed[1])
                        previous = images.get(key)
                        if previous is None or self._rank_image(path) > self._rank_image(previous):
                            images[key] = path
            for video_dir in root.glob("*/Video"):
                conv_hash = video_dir.parent.name
                if not video_dir.is_dir():
                    continue
                try:
                    video_paths = list(video_dir.iterdir())
                except OSError:
                    continue
                for path in video_paths:
                    match = VIDEO_NAME_RE.match(path.name)
                    if not match:
                        continue
                    videos[(conv_hash, int(match.group(1)), int(match.group(2)))] = path
        self._image_index = images
        self._video_index = videos

    def _conv_hash(self, table_name: str) -> str:
        return table_name[4:] if table_name.startswith("Msg_") else table_name

    def find_image(self, table_name: str, local_id: int, create_time: int, sort_seq: int = 0) -> Path | None:
        if self._image_index is None:
            self._build_indexes()
        conv_hash = self._conv_hash(table_name)
        if not self._image_index:
            return None
        # 精确匹配
        for ts in filter(None, [create_time, sort_seq // 1000 if sort_seq > 10_000_000_000 else sort_seq]):
            path = self._image_index.get((conv_hash, int(local_id), int(ts)))
            if path:
                return path
        return None

    def get_sender_name(self, real_sender_id: int, my_name: str = '我', db_shard: str = 'message_0') -> tuple[str, bool]:
        """返回 (显示名称, is_sender)。db_shard 如 'message_0'、'message_1' 等"""
        shard_map = self._shard_map.get(db_shard, self._sender_cache)
        my_rowid = self._shard_my_rowid.get(db_shard, -1)
        is_me = real_sender_id == 0 or real_sender_id == my_rowid
        if is_me:
            return (my_name, True)
        name = shard_map.get(real_sender_id, str(real_sender_id))
        return (name, False)

    def find_image_with_fallback(self, table_name: str, local_id: int, create_time: int, sort_seq: int = 0, img_md5: str = '') -> Path | None:
        """先查 MessageTemp 缓存，找不到再查 attach/ 目录"""
        path = self.find_image(table_name, local_id, create_time, sort_seq)
        if path:
            return path
        conv_hash = self._conv_hash(table_name)
        attach_dir = self.attach_base / conv_hash
        if not attach_dir.exists():
            return None
        # 优先：<md5>_M.dat 或 <md5>_t.dat（原图优先，缩略图兜底）
        if img_md5:
            for suffix in (f"{img_md5}_M.dat", f"{img_md5}.dat", f"{img_md5}_t.dat"):
                candidate = attach_dir / suffix
                if candidate.exists():
                    return candidate
        # 兜底：按 local_id 模糊匹配
        for f in attach_dir.rglob(f"{local_id}_*.dat"):
            return f
        return None

    def find_video(self, table_name: str, local_id: int, create_time: int, sort_seq: int = 0) -> Path | None:
        if self._video_index is None:
            self._build_indexes()
        conv_hash = self._conv_hash(table_name)
        if not self._video_index:
            return None
        # 先精确匹配 (conv_hash, local_id, create_time)
        for ts in filter(None, [create_time, sort_seq // 1000 if sort_seq > 10_000_000_000 else sort_seq]):
            path = self._video_index.get((conv_hash, int(local_id), int(ts)))
            if path:
                return path
        # local_id 在不同分片里不同，降级为只按 create_time 匹配
        for ts in filter(None, [create_time, sort_seq // 1000 if sort_seq > 10_000_000_000 else sort_seq]):
            for (ch, _lid, _ts), path in self._video_index.items():
                if ch == conv_hash and _ts == int(ts):
                    return path
        return None

    def _voice_ids(self) -> dict[str, int]:
        if self._voice_chat_ids is not None:
            return self._voice_chat_ids
        result: dict[str, int] = {}
        media_db = self.db_dir / "message" / "media_0.db"
        if not media_db.exists():
            self._voice_chat_ids = result
            return result
        with sqlite3.connect(media_db) as conn:
            for rowid, user_name in conn.execute("select rowid, user_name from Name2Id"):
                result[hashlib.md5(user_name.encode()).hexdigest()] = int(rowid)
        self._voice_chat_ids = result
        return result

    def get_voice_data(self, table_name: str, local_id: int, create_time: int) -> bytes | None:
        media_db = self.db_dir / "message" / "media_0.db"
        if not media_db.exists():
            return None
        chat_id = self._voice_ids().get(self._conv_hash(table_name))
        if chat_id is None:
            return None
        with sqlite3.connect(media_db) as conn:
            row = conn.execute(
                "select voice_data from VoiceInfo where chat_name_id=? and local_id=? and create_time=? limit 1",
                (chat_id, int(local_id), int(create_time)),
            ).fetchone()
        return row[0] if row and isinstance(row[0], bytes) else None

    def copy_media(self, source: str | Path, output_dir: Path, prefix: str = "") -> Path:
        source = Path(source)
        output_dir.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}{source.name}" if prefix else source.name
        target = output_dir / name
        if not target.exists():
            shutil.copy2(source, target)
        return target

    def write_voice(self, voice_data: bytes, output_dir: Path, name: str) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / name
        target.write_bytes(voice_data)
        return target


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def parse_message(
    msg_type: int,
    content: Any,
    table_name: str = "",
    local_id: int = 0,
    create_time: int = 0,
    sort_seq: int = 0,
    resolver: MacMediaResolver | None = None,
) -> ParsedMessage:
    msg_type = normalize_message_type(msg_type)
    raw_text = decode_message_blob(content)
    text = safe_text(strip_sender_prefix(raw_text))
    root = xml_root(text)

    if msg_type == 1:
        return ParsedMessage("文本", text)

    if msg_type == 3:
        width = height = md5 = ""
        if root is not None:
            img = root.find("img")
            if img is not None:
                width = img.get("cdnthumbwidth", "")
                height = img.get("cdnthumbheight", "")
                md5 = img.get("md5", "")
        path = resolver.find_image_with_fallback(table_name, local_id, create_time, sort_seq, md5) if resolver else None
        media_path = str(path) if path else ""
        label = "[图片" + (f" {width}x{height}" if width and height else "") + "]"
        if md5:
            label += f" md5:{md5}"
        return ParsedMessage("图片", label, xml=text, media_path=media_path, media_kind="image", media_mime="image/jpeg")

    if msg_type == 34:
        length_ms = None
        if root is not None:
            voice = root.find("voicemsg")
            if voice is not None:
                try:
                    length_ms = int(voice.get("voicelength", "0"))
                except ValueError:
                    length_ms = None
        label = "[语音消息]" + (f" {length_ms / 1000:.1f}s" if length_ms else "")
        return ParsedMessage("语音", label, xml=text, media_kind="voice", voice_length_ms=length_ms)

    if msg_type == 43:
        path = resolver.find_video(table_name, local_id, create_time, sort_seq) if resolver else None
        media_path = str(path) if path else ""
        return ParsedMessage("视频", "[视频消息]", xml=text, media_path=media_path, media_kind="video", media_mime="video/mp4")

    if msg_type == 47:
        url = ""
        if root is not None:
            emoji = root.find(".//emoji")
            if emoji is not None:
                url = emoji.get("cdnurl", "") or emoji.get("thumburl", "")
        return ParsedMessage("表情包", "[表情包]", xml=text, url=url, media_kind="emoji")

    if msg_type == 49:
        title = desc = url = app_type = ""
        if root is not None:
            appmsg = root.find("appmsg")
            if appmsg is not None:
                title = appmsg.findtext("title", "") or ""
                desc = appmsg.findtext("des", "") or ""
                url = appmsg.findtext("url", "") or ""
                app_type = appmsg.findtext("type", "") or ""
        label = f"[分享/文件] {title}".strip() if title else f"[分享/文件 type={app_type}]" if app_type else "[分享/文件]"
        return ParsedMessage("分享/文件", label, xml=text, title=title, description=desc, url=url)

    if msg_type == 10000:
        return ParsedMessage("系统消息", text)

    return ParsedMessage(f"未知({msg_type})", f"[未知类型 {msg_type}]", xml=text)


def silk_to_wav(silk_data: bytes, sample_rate: int = 24000) -> bytes | None:
    """将 silk 原始数据转为 WAV bytes，失败返回 None。"""
    try:
        import io
        import wave
        import pysilk
        data = silk_data[1:] if silk_data[:1] == b"\x02" else silk_data
        inp = io.BytesIO(data)
        out = io.BytesIO()
        pysilk.decode(inp, out, sample_rate)
        pcm = out.getvalue()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()
    except Exception:
        return None
