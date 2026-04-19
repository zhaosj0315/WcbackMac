#!/usr/bin/env python3
"""
Mac 版本数据库批量导出
将解密后的所有数据库复制到统一导出目录
"""
import shutil
from pathlib import Path
from datetime import datetime


def export_databases(source_dir: str = "app/Database/MacMsg", 
                     output_dir: str = None):
    """
    批量导出所有解密后的数据库
    
    Args:
        source_dir: 解密后的数据库目录
        output_dir: 导出目标目录
    """
    source = Path(source_dir)
    
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = f"data/exported_databases_{timestamp}"
    
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    
    if not source.exists():
        print(f"❌ 源目录不存在: {source}")
        return
    
    # 核心数据库列表（参考 Windows 版本）
    core_databases = [
        "contact/contact.db",           # 联系人
        "session/session.db",           # 会话
        "favorite/favorite.db",         # 收藏
        "group/group.db",               # 群组
        "chatroom_tools/chatroom_tools.db",  # 群工具
    ]
    
    exported_count = 0
    
    # 1. 导出核心数据库
    print("📦 导出核心数据库...")
    for db_path in core_databases:
        src = source / db_path
        if src.exists():
            dst = output / db_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  ✅ {db_path}")
            exported_count += 1
        else:
            print(f"  ⚠️  未找到: {db_path}")
    
    # 2. 导出所有 message_*.db
    print("\n📦 导出消息数据库...")
    message_dir = source / "message"
    if message_dir.exists():
        for db_file in message_dir.glob("message_*.db"):
            dst = output / "message" / db_file.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db_file, dst)
            exported_count += 1
        
        message_count = len(list((output / "message").glob("*.db")))
        print(f"  ✅ 导出 {message_count} 个消息库")
    
    # 3. 导出其他所有 .db 文件
    print("\n📦 扫描其他数据库...")
    for db_file in source.rglob("*.db"):
        rel_path = db_file.relative_to(source)
        dst = output / rel_path
        
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db_file, dst)
            print(f"  ✅ {rel_path}")
            exported_count += 1
    
    # 4. 创建导出清单
    manifest_path = output / "MANIFEST.txt"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        f.write(f"Mac 微信数据库导出清单\n")
        f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"源目录: {source.absolute()}\n")
        f.write(f"导出目录: {output.absolute()}\n")
        f.write(f"总文件数: {exported_count}\n\n")
        f.write("=" * 60 + "\n\n")
        
        for db_file in sorted(output.rglob("*.db")):
            rel_path = db_file.relative_to(output)
            size_mb = db_file.stat().st_size / 1024 / 1024
            f.write(f"{rel_path}\t{size_mb:.2f} MB\n")
    
    print(f"\n✅ 导出完成!")
    print(f"  总计: {exported_count} 个数据库")
    print(f"  目录: {output.absolute()}")
    print(f"  清单: {manifest_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Mac 微信数据库批量导出')
    parser.add_argument('--source', default='app/Database/MacMsg', 
                        help='解密后的数据库目录')
    parser.add_argument('--output', help='导出目标目录（默认自动生成时间戳目录）')
    args = parser.parse_args()
    
    export_databases(args.source, args.output)


if __name__ == '__main__':
    main()
