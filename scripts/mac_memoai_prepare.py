#!/usr/bin/env python3
"""
MemoAI 数据准备：直接从解密后的 message_*.db 生成 Ollama 训练数据
用法:
  python3 scripts/mac_memoai_prepare.py --db-dir app/DataBase/MacMsg/message
  python3 scripts/mac_memoai_prepare.py --db-dir app/DataBase/MacMsg/message --create-model
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import zstd
except ImportError:
    zstd = None


def _decode(value) -> str:
    if not isinstance(value, bytes):
        return str(value or "")
    if zstd and value[:4] == b"\x28\xb5\x2f\xfd":
        try:
            text = zstd.decompress(value).decode("utf-8", errors="replace")
            if "\n" in text:
                first, rest = text.split("\n", 1)
                if ":" in first and len(first) < 80:
                    return rest
            return text
        except Exception:
            pass
    return value.decode("utf-8", errors="replace")


def _is_text(msg_type: int) -> bool:
    base = msg_type & 0xFFFFFFFF if msg_type > 0xFFFFFFFF else msg_type
    return base == 1


def build_conversations(db_dir: Path, max_msgs: int = 0, min_turns: int = 2, window: int = 6) -> list[dict]:
    """从 message_*.db 分片读取消息，按会话生成对话样本。"""
    # 按 table 收集消息
    sessions: dict[str, list[tuple]] = {}
    total = 0
    for db_file in sorted(db_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
            for (table,) in cur.fetchall():
                cur.execute(
                    f'select local_type, create_time, real_sender_id, message_content '
                    f'from "{table}" where create_time > 0 order by create_time asc'
                )
                rows = cur.fetchall()
                if rows:
                    sessions.setdefault(table, []).extend(rows)
                    total += len(rows)
            conn.close()
        except sqlite3.Error:
            continue
        if max_msgs and total >= max_msgs:
            break

    conversations = []
    for table, msgs in sessions.items():
        msgs.sort(key=lambda r: r[1])
        i = 0
        while i < len(msgs) - 1:
            chunk = msgs[i: i + window]
            turns = []
            for msg_type, create_time, sender_id, content in chunk:
                if not _is_text(msg_type):
                    continue
                text = _decode(content).strip()
                if not text or text.startswith("<") or len(text) < 2:
                    continue
                # sender_id==0 表示自己发送
                role = "assistant" if sender_id == 0 else "user"
                turns.append({"role": role, "content": text})
            if len(turns) >= min_turns:
                conversations.append({"conversations": turns})
            i += max(1, window // 2)

    return conversations


def write_train_dev(conversations: list[dict], output_dir: Path) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    split = max(1, int(len(conversations) * 0.9))
    train, dev = conversations[:split], conversations[split:]
    (output_dir / "train.json").write_text(
        json.dumps(train, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "dev.json").write_text(
        json.dumps(dev, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(train), len(dev)


def write_modelfile(output_dir: Path, model_name: str, base_model: str) -> Path:
    mf = output_dir / "Modelfile"
    mf.write_text(
        f"FROM {base_model}\n\n"
        "SYSTEM \"\"\"\n"
        f"你是一个基于微信聊天记录训练的个人AI助手，模型名称 {model_name}。\n"
        "你应该模仿聊天记录中的语言风格，保持自然、真实的表达方式。\n"
        "\"\"\"\n",
        encoding="utf-8",
    )
    return mf


def main():
    parser = argparse.ArgumentParser(description="MemoAI 数据准备：message_*.db → Ollama 训练数据")
    parser.add_argument("--db-dir", default="app/DataBase/MacMsg/message")
    parser.add_argument("--output-dir", default="MemoAI_Mac/wechat_exports")
    parser.add_argument("--base-model", default="qwen3:8b")
    parser.add_argument("--model-name", default="my-wechat-ai")
    parser.add_argument("--create-model", action="store_true", help="生成后自动调用 ollama create")
    parser.add_argument("--max-msgs", type=int, default=100000, help="最多读取消息数（0=全部）")
    args = parser.parse_args()

    db_dir = ROOT_DIR / args.db_dir
    if not db_dir.exists():
        print(f"❌ 数据库目录不存在: {db_dir}")
        sys.exit(1)

    print(f"📂 读取数据库: {db_dir}")
    conversations = build_conversations(db_dir, args.max_msgs)
    print(f"   生成 {len(conversations):,} 个对话样本")

    if not conversations:
        print("⚠️  没有生成任何对话，请确认数据库已解密且包含文本消息")
        sys.exit(1)

    output_dir = ROOT_DIR / args.output_dir
    train_n, dev_n = write_train_dev(conversations, output_dir)
    print(f"✅ 训练集: {train_n:,}，验证集: {dev_n:,} → {output_dir}")

    mf = write_modelfile(output_dir, args.model_name, args.base_model)
    print(f"✅ Modelfile: {mf}")
    print()

    if args.create_model:
        print(f"🤖 创建 Ollama 模型: {args.model_name}")
        ok = subprocess.run(["ollama", "create", args.model_name, "--file", str(mf)]).returncode == 0
        if ok:
            print(f"✅ 完成，运行: ollama run {args.model_name}")
        else:
            print("❌ 创建失败，请确认 ollama 已安装并运行")
    else:
        print("下一步：")
        print(f"  ollama create {args.model_name} --file {mf}")
        print(f"  ollama run {args.model_name}")
        print(f"  # 或 Web 界面:")
        print(f"  python3 MemoAI_Mac/scripts/web_interface.py --model {args.model_name}")


if __name__ == "__main__":
    main()
