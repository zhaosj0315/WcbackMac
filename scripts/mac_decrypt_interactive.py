#!/usr/bin/env python3
"""
改进的自动解密脚本 - 修复 LLDB 扫描问题
"""
import subprocess
import time
import sys
from pathlib import Path

def main():
    print("=" * 70)
    print("Mac 微信自动解密工具（改进版）")
    print("=" * 70)
    print()
    
    # 1. 退出原微信
    print("[1/5] 退出原微信...")
    subprocess.run(["osascript", "-e", 'quit app "WeChat"'], check=False)
    time.sleep(3)
    
    # 2. 复制并重签名
    print("[2/5] 复制并重签名微信...")
    copy_app = Path("/tmp/WeChat-resign-test.app")
    if copy_app.exists():
        import shutil
        shutil.rmtree(copy_app)
    
    subprocess.run(["ditto", "/Applications/WeChat.app", str(copy_app)], check=True)
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(copy_app)], check=True)
    
    # 3. 启动副本微信
    print("[3/5] 启动副本微信...")
    subprocess.run(["open", str(copy_app)], check=True)
    
    # 4. 等待用户登录
    print()
    print("⚠️  重要：请在新打开的微信中登录")
    print("   登录完成后，等待 10 秒让数据库完全加载")
    print()
    input("按回车继续 LLDB 扫描 > ")
    
    # 5. LLDB 扫描
    print("[4/5] LLDB 扫描密钥...")
    time.sleep(2)  # 额外等待
    
    # 查找进程
    result = subprocess.run(
        ["pgrep", "-f", "WeChat-resign-test"],
        capture_output=True,
        text=True
    )
    
    if not result.stdout.strip():
        print("❌ 未找到微信进程，请确保微信已启动")
        return 1
    
    pid = result.stdout.strip().split()[0]
    print(f"   找到进程 PID: {pid}")
    
    # 运行 LLDB
    project_root = Path(__file__).parent.parent
    subprocess.run([
        "lldb",
        "-b",
        "-p", pid,
        "-o", f"command script import {project_root}/scripts/lldb_scan_wechat_keys.py",
        "-o", "detach",
        "-o", "quit"
    ], check=True)
    
    # 6. 解密数据库
    print("[5/5] 解密数据库...")
    keys_file = "/tmp/wechat_lldb_key_candidates.json"
    
    if not Path(keys_file).exists():
        print(f"❌ 密钥文件不存在: {keys_file}")
        return 1
    
    subprocess.run([
        "python3", str(project_root / "scripts/mac_decrypt_from_keys.py"),
        "--keys", keys_file,
        "--output", "app/Database/MacMsg",
        "--verify"
    ], check=True)
    
    print()
    print("✅ 解密完成！")
    print(f"   解密后的数据库: app/Database/MacMsg/")
    print()
    
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
