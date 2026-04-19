#!/usr/bin/env python3
"""
Mac 版本统一导出入口
一键导出所有格式：数据库、CSV、JSON、HTML、Word、TXT
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(
        description='Mac 微信一键导出工具（类比 Windows 版本）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 导出所有格式
  python3 scripts/mac_export_all.py --all
  
  # 只导出数据库、CSV 和 JSON
  python3 scripts/mac_export_all.py --db --csv --json
  
  # 导出 HTML 和 Word（每个会话最多 1000 条）
  python3 scripts/mac_export_all.py --html --word --limit 1000
  
  # 指定输出目录
  python3 scripts/mac_export_all.py --all --output data/export_20260418
        """
    )
    
    parser.add_argument('--db-dir', default='app/Database/MacMsg', 
                        help='解密后的数据库目录')
    parser.add_argument('--output', help='输出根目录（默认自动生成时间戳目录）')
    parser.add_argument('--limit', type=int, help='每个会话最多导出消息数（用于测试）')
    
    # 导出格式选项
    parser.add_argument('--all', action='store_true', help='导出所有格式')
    parser.add_argument('--db', action='store_true', help='导出数据库文件')
    parser.add_argument('--csv', action='store_true', help='导出 CSV')
    parser.add_argument('--json', action='store_true', help='导出 JSON')
    parser.add_argument('--html', action='store_true', help='导出 HTML')
    parser.add_argument('--word', action='store_true', help='导出 Word')
    parser.add_argument('--txt', action='store_true', help='导出 TXT')
    
    # 高级选项
    parser.add_argument('--skip-mapping', action='store_true', 
                        help='跳过联系人映射（更快但没有昵称）')
    parser.add_argument('--mapping', default='data/mac_contact_mapping.json',
                        help='联系人映射文件路径')
    
    args = parser.parse_args()
    
    # 如果没有指定任何格式，默认导出所有
    if not any([args.all, args.db, args.csv, args.json, args.html, args.word, args.txt]):
        args.all = True
    
    if args.all:
        args.db = args.csv = args.json = args.html = args.word = args.txt = True
    
    # 确定输出目录
    if args.output:
        output_root = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_root = Path(f"data/mac_export_{timestamp}")
    
    output_root.mkdir(parents=True, exist_ok=True)
    
    print("=" * 70)
    print("Mac 微信一键导出工具")
    print("=" * 70)
    print(f"数据库目录: {args.db_dir}")
    print(f"输出目录: {output_root.absolute()}")
    print(f"导出格式: ", end="")
    formats = []
    if args.db: formats.append("数据库")
    if args.csv: formats.append("CSV")
    if args.json: formats.append("JSON")
    if args.html: formats.append("HTML")
    if args.word: formats.append("Word")
    if args.txt: formats.append("TXT")
    print(", ".join(formats))
    if args.limit:
        print(f"消息限制: 每个会话最多 {args.limit} 条")
    print("=" * 70)
    print()
    
    # 添加脚本目录到 Python 路径
    sys.path.insert(0, str(Path(__file__).parent))
    
    # 步骤 1: 生成联系人映射
    contact_mapper = None
    if not args.skip_mapping:
        print("📋 步骤 1/7: 生成联系人映射...")
        try:
            from mac_contact_mapper import MacContactMapper
            
            mapping_path = Path(args.mapping)
            if mapping_path.exists():
                print(f"  ✅ 使用现有映射: {mapping_path}")
                contact_mapper = MacContactMapper.load_mapping(str(mapping_path))
            else:
                print(f"  🔄 生成新映射...")
                contact_mapper = MacContactMapper(args.db_dir)
                contact_mapper.load_contacts()
                contact_mapper.load_chatrooms()
                contact_mapper.save_mapping(str(mapping_path))
            
            print(f"  ✅ 联系人: {len(contact_mapper.wxid_to_remark)}")
            print(f"  ✅ 群聊: {len(contact_mapper.chatroom_to_name)}")
        except Exception as e:
            print(f"  ⚠️  映射失败: {e}")
            print(f"  继续导出（将使用 wxid）...")
    else:
        print("📋 步骤 1/7: 跳过联系人映射")
    
    print()
    
    # 步骤 2: 导出数据库
    if args.db:
        print("📦 步骤 2/7: 导出数据库文件...")
        try:
            from mac_export_databases import export_databases
            db_output = output_root / "databases"
            export_databases(args.db_dir, str(db_output))
        except Exception as e:
            print(f"  ❌ 导出失败: {e}")
    else:
        print("📦 步骤 2/7: 跳过数据库导出")
    
    print()
    
    # 步骤 3: 导出 CSV
    if args.csv:
        print("📊 步骤 3/7: 导出 CSV...")
        try:
            from mac_export_messages import iter_rows, write_csv
            csv_output = output_root / "csv" / "messages.csv"
            rows = iter_rows(Path(args.db_dir), latest=0)
            count = write_csv(rows, csv_output, contact_mapper)
            print(f"  ✅ 导出 {count} 条消息到 {csv_output}")
        except Exception as e:
            print(f"  ❌ 导出失败: {e}")
    else:
        print("📊 步骤 3/7: 跳过 CSV 导出")
    
    print()
    
    # 步骤 4: 导出 JSON
    if args.json:
        print("🧾 步骤 4/7: 导出 JSON...")
        try:
            from mac_export_json import MacJSONExporter
            json_output = output_root / "json" / "messages.json"
            exporter = MacJSONExporter(args.db_dir, contact_mapper)
            exporter.export_messages(str(json_output), args.limit)
        except Exception as e:
            print(f"  ❌ 导出失败: {e}")
    else:
        print("🧾 步骤 4/7: 跳过 JSON 导出")

    print()

    # 步骤 5: 导出 HTML
    if args.html:
        print("🌐 步骤 5/7: 导出 HTML...")
        try:
            from mac_export_html import MacHTMLExporter
            html_output = output_root / "html"
            exporter = MacHTMLExporter(args.db_dir, contact_mapper)
            exporter.export_all_conversations(str(html_output), args.limit)
        except Exception as e:
            print(f"  ❌ 导出失败: {e}")
    else:
        print("🌐 步骤 5/7: 跳过 HTML 导出")
    
    print()
    
    # 步骤 6: 导出 Word
    if args.word:
        print("📄 步骤 6/7: 导出 Word...")
        try:
            from mac_export_word import MacWordExporter
            word_output = output_root / "word"
            exporter = MacWordExporter(args.db_dir, contact_mapper)
            exporter.export_all_conversations(str(word_output), args.limit)
        except Exception as e:
            print(f"  ❌ 导出失败: {e}")
            if "python-docx" in str(e):
                print(f"  💡 提示: 安装 python-docx 以支持 Word 导出")
                print(f"     pip install python-docx")
    else:
        print("📄 步骤 6/7: 跳过 Word 导出")
    
    print()
    
    # 步骤 7: 导出 TXT
    if args.txt:
        print("📝 步骤 7/7: 导出 TXT...")
        try:
            from mac_export_txt import MacTXTExporter
            txt_output = output_root / "txt"
            exporter = MacTXTExporter(args.db_dir, contact_mapper)
            exporter.export_all_conversations(str(txt_output), args.limit)
        except Exception as e:
            print(f"  ❌ 导出失败: {e}")
    else:
        print("📝 步骤 7/7: 跳过 TXT 导出")
    
    print()

    # 步骤 8: 导出朋友圈
    print("📸 步骤 8/10: 导出朋友圈...")
    try:
        from mac_export_sns import export_sns
        sns_db = Path(args.db_dir).parent / "sns" / "sns.db"
        if sns_db.exists():
            count = export_sns(str(sns_db), str(output_root / "sns.json"))
            print(f"  ✅ 导出 {count} 条朋友圈")
        else:
            print(f"  ⚠️  sns.db 不存在: {sns_db}")
    except Exception as e:
        print(f"  ❌ 导出失败: {e}")

    print()

    # 步骤 9: 导出收藏
    print("⭐ 步骤 9/10: 导出收藏...")
    try:
        from mac_export_favorite import export_favorite
        fav_db = Path(args.db_dir).parent / "favorite" / "favorite.db"
        if fav_db.exists():
            count = export_favorite(str(fav_db), str(output_root / "favorites.json"))
            print(f"  ✅ 导出 {count} 条收藏")
        else:
            print(f"  ⚠️  favorite.db 不存在: {fav_db}")
    except Exception as e:
        print(f"  ❌ 导出失败: {e}")

    print()

    # 步骤 10: 统计分析
    print("📊 步骤 10/10: 统计分析...")
    try:
        from mac_chat_analysis import analyze
        import json as _json
        result = analyze(str(Path(args.db_dir) / "message"), args.mapping)
        analysis_out = output_root / "analysis.json"
        analysis_out.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✅ 总消息: {result['total_messages']:,}，结果: {analysis_out}")
    except Exception as e:
        print(f"  ❌ 分析失败: {e}")

    print()
    print("=" * 70)
    print("✅ 导出完成!")
    print(f"📁 输出目录: {output_root.absolute()}")
    print("=" * 70)
    
    # 生成导出报告
    report_path = output_root / "EXPORT_REPORT.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("Mac 微信导出报告\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"数据库目录: {Path(args.db_dir).absolute()}\n")
        f.write(f"输出目录: {output_root.absolute()}\n\n")
        f.write("导出格式:\n")
        if args.db: f.write("  ✅ 数据库文件\n")
        if args.csv: f.write("  ✅ CSV\n")
        if args.json: f.write("  ✅ JSON\n")
        if args.html: f.write("  ✅ HTML\n")
        if args.word: f.write("  ✅ Word\n")
        if args.txt: f.write("  ✅ TXT\n")
        f.write("\n")
        
        if contact_mapper:
            f.write(f"联系人映射:\n")
            f.write(f"  联系人: {len(contact_mapper.wxid_to_remark)}\n")
            f.write(f"  群聊: {len(contact_mapper.chatroom_to_name)}\n")
        
        f.write("\n目录结构:\n")
        for item in sorted(output_root.rglob("*")):
            if item.is_file():
                rel_path = item.relative_to(output_root)
                size_mb = item.stat().st_size / 1024 / 1024
                f.write(f"  {rel_path} ({size_mb:.2f} MB)\n")
    
    print(f"📋 导出报告: {report_path}")


if __name__ == '__main__':
    main()
