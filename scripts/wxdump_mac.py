#!/usr/bin/env python3
"""
Mac 版本统一 CLI 入口
类似 PyWxDump 的 wxdump 命令
"""
import argparse
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

VERSION = "1.0.0"

ASCII_ART = r"""
███╗   ███╗ █████╗  ██████╗    ██╗    ██╗██╗  ██╗██████╗ ██╗   ██╗███╗   ███╗██████╗ 
████╗ ████║██╔══██╗██╔════╝    ██║    ██║╚██╗██╔╝██╔══██╗██║   ██║████╗ ████║██╔══██╗
██╔████╔██║███████║██║         ██║ █╗ ██║ ╚███╔╝ ██║  ██║██║   ██║██╔████╔██║██████╔╝
██║╚██╔╝██║██╔══██║██║         ██║███╗██║ ██╔██╗ ██║  ██║██║   ██║██║╚██╔╝██║██╔═══╝ 
██║ ╚═╝ ██║██║  ██║╚██████╗    ╚███╔███╔╝██╔╝ ██╗██████╔╝╚██████╔╝██║ ╚═╝ ██║██║     
╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝     ╚══╝╚══╝ ╚═╝  ╚═╝╚═════╝  ╚═════╝ ╚═╝     ╚═╝╚═╝     
"""


def create_parser():
    parser = argparse.ArgumentParser(
        description=f'Mac 微信数据导出工具 v{VERSION}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{ASCII_ART}

更多信息: https://github.com/xaoyaoo/PyWxDump (Windows 版本)
Mac 版本适配: 解密、导出、分析和富媒体处理
        """
    )
    
    parser.add_argument('-V', '--version', action='version', version=f'MacWxDump v{VERSION}')
    
    subparsers = parser.add_subparsers(dest='command', help='可用命令', required=True)
    
    # 1. info - 获取微信信息（Mac 版本使用 LLDB）
    info_parser = subparsers.add_parser('info', help='获取微信信息（需要 LLDB）')
    info_parser.add_argument('--quit-original', action='store_true', help='自动退出原微信')
    info_parser.add_argument('--save', help='保存信息到 JSON 文件')
    
    # 2. decrypt - 解密数据库
    decrypt_parser = subparsers.add_parser('decrypt', help='解密微信数据库')
    decrypt_parser.add_argument('--keys', required=True, help='密钥 JSON 文件路径')
    decrypt_parser.add_argument('--output', default='app/Database/MacMsg', help='输出目录')
    decrypt_parser.add_argument('--verify', action='store_true', help='验证解密结果')
    
    # 3. merge - 合并数据库
    merge_parser = subparsers.add_parser('merge', help='合并消息数据库')
    merge_parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    merge_parser.add_argument('--output', default='data/merged_messages.db', help='输出文件')
    merge_parser.add_argument('--no-contact', action='store_true', help='不包含联系人')
    merge_parser.add_argument('--no-session', action='store_true', help='不包含会话')
    
    # 4. export - 导出聊天记录
    export_parser = subparsers.add_parser('export', help='导出聊天记录')
    export_parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    export_parser.add_argument('--output', required=True, help='输出目录')
    export_parser.add_argument('--format', choices=['csv', 'json', 'html', 'word', 'txt', 'all'], 
                               default='all', help='导出格式')
    export_parser.add_argument('--limit', type=int, help='每个会话最多导出消息数')
    export_parser.add_argument('--mapping', default='data/mac_contact_mapping.json', 
                               help='联系人映射文件')
    
    # 5. contact - 生成联系人映射
    contact_parser = subparsers.add_parser('contact', help='生成联系人映射')
    contact_parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    contact_parser.add_argument('--output', default='data/mac_contact_mapping.json', 
                                help='输出文件')
    
    # 6. all - 一键完整流程
    all_parser = subparsers.add_parser('all', help='一键完整流程（解密+合并+导出）')
    all_parser.add_argument('--quit-original', action='store_true', help='自动退出原微信')
    all_parser.add_argument('--output', help='输出根目录')
    all_parser.add_argument('--format', choices=['csv', 'json', 'html', 'word', 'txt', 'all'], 
                           default='all', help='导出格式')
    all_parser.add_argument('--limit', type=int, help='每个会话最多导出消息数')
    
    # 7. dbexport - 导出数据库文件
    dbexport_parser = subparsers.add_parser('dbexport', help='批量导出数据库文件')
    dbexport_parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    dbexport_parser.add_argument('--output', help='输出目录')
    
    # 8. analyze - 聊天统计分析
    analyze_parser = subparsers.add_parser('analyze', help='聊天统计分析')
    analyze_parser.add_argument('--db', required=True, help='合并后的数据库路径')
    analyze_parser.add_argument('--output', default='data/chat_analysis.json', help='输出报告')
    analyze_parser.add_argument('--mapping', default='data/mac_contact_mapping.json', help='联系人映射')
    
    return parser


def cmd_info(args):
    """获取微信信息"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 获取微信信息...")
    
    from app.decrypt.macos_provider import build_probe, print_probe
    probe = build_probe()
    if args.save:
        import json
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
    print_probe(probe)
    return probe["feasibility"]["level"] in {"possible", "semi_auto", "import_ready"}


def cmd_decrypt(args):
    """解密数据库"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 解密数据库...")
    
    import subprocess
    cmd = ['python3', 'scripts/mac_decrypt_from_keys.py', '--keys', args.keys, '--output', args.output]
    if args.verify:
        cmd.append('--verify')
    return subprocess.run(cmd).returncode == 0


def cmd_merge(args):
    """合并数据库"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 合并数据库...")
    
    from scripts.mac_merge_db import MacDBMerger
    merger = MacDBMerger(args.db_dir)
    merger.merge_message_dbs(
        output_path=args.output,
        include_contact=not args.no_contact,
        include_session=not args.no_session
    )
    return True


def cmd_export(args):
    """导出聊天记录"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 导出聊天记录...")
    
    # 先生成联系人映射
    from scripts.mac_contact_mapper import MacContactMapper
    mapper = MacContactMapper(args.db_dir)
    mapper.load_contacts()
    mapper.load_chatrooms()
    mapper.save_mapping(args.mapping)
    
    # 根据格式导出
    if args.format == 'all':
        import subprocess
        cmd = [
            'python3', 'scripts/mac_export_all.py',
            '--all',
            '--db-dir', args.db_dir,
            '--output', args.output,
            '--mapping', args.mapping
        ]
        if args.limit:
            cmd.extend(['--limit', str(args.limit)])
        subprocess.run(cmd)
    else:
        # 单一格式导出
        format_map = {
            'csv': 'scripts/mac_export_messages.py',
            'json': 'scripts/mac_export_json.py',
            'html': 'scripts/mac_export_html.py',
            'word': 'scripts/mac_export_word.py',
            'txt': 'scripts/mac_export_txt.py'
        }
        script = format_map[args.format]
        import subprocess
        cmd = ['python3', script, '--db-dir', args.db_dir, '--output', args.output]
        if args.limit:
            cmd.extend(['--limit', str(args.limit)])
        subprocess.run(cmd)
    
    return True


def cmd_contact(args):
    """生成联系人映射"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 生成联系人映射...")
    
    from scripts.mac_contact_mapper import MacContactMapper
    mapper = MacContactMapper(args.db_dir)
    mapper.load_contacts()
    mapper.load_chatrooms()
    mapper.save_mapping(args.output)
    return True


def cmd_all(args):
    """一键完整流程"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 开始一键完整流程...")
    
    import subprocess
    from datetime import datetime
    
    # 确定输出目录
    if args.output:
        output_dir = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = f"data/mac_wxdump_{timestamp}"
    
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # 1. 解密（如果需要）
    keys_file = "/tmp/wechat_lldb_key_candidates.json"
    if not Path(keys_file).exists() or args.quit_original:
        print("\n[1/4] 获取密钥...")
        cmd = ['python3', 'scripts/mac_auto_decrypt_export.py', '--quit-original']
        subprocess.run(cmd)
    
    # 2. 合并数据库
    print("\n[2/4] 合并数据库...")
    merged_db = f"{output_dir}/merged_messages.db"
    subprocess.run([
        'python3', 'scripts/mac_merge_db.py',
        '--output', merged_db
    ])
    
    # 3. 生成联系人映射
    print("\n[3/4] 生成联系人映射...")
    mapping_file = f"{output_dir}/contact_mapping.json"
    subprocess.run([
        'python3', 'scripts/mac_contact_mapper.py',
        '--output', mapping_file
    ])
    
    # 4. 导出所有格式
    print("\n[4/4] 导出聊天记录...")
    cmd = [
        'python3', 'scripts/mac_export_all.py',
        '--db-dir', 'app/Database/MacMsg',
        '--output', output_dir,
        '--mapping', mapping_file
    ]
    
    if args.format != 'all':
        cmd.extend([f'--{args.format}'])
    else:
        cmd.append('--all')
    
    if args.limit:
        cmd.extend(['--limit', str(args.limit)])
    
    subprocess.run(cmd)
    
    print(f"\n✅ 完整流程完成！输出目录: {Path(output_dir).absolute()}")
    return True


def cmd_dbexport(args):
    """导出数据库文件"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 导出数据库文件...")
    
    from scripts.mac_export_databases import export_databases
    export_databases(args.db_dir, args.output)
    return True


def cmd_analyze(args):
    """聊天统计分析"""
    print(f"[*] MacWxDump v{VERSION}")
    print("[*] 聊天统计分析...")
    
    import subprocess
    cmd = [
        'python3', 'scripts/mac_chat_analysis.py',
        '--db', args.db,
        '--output', args.output,
        '--mapping', args.mapping
    ]
    result = subprocess.run(cmd)
    return result.returncode == 0


def main():
    parser = create_parser()
    
    # 如果没有参数，显示帮助
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    
    args = parser.parse_args()
    
    # 执行对应命令
    commands = {
        'info': cmd_info,
        'decrypt': cmd_decrypt,
        'merge': cmd_merge,
        'export': cmd_export,
        'contact': cmd_contact,
        'all': cmd_all,
        'dbexport': cmd_dbexport,
        'analyze': cmd_analyze,
    }
    
    try:
        success = commands[args.command](args)
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n\n[!] 用户中断")
        return 130
    except Exception as e:
        print(f"\n[!] 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
