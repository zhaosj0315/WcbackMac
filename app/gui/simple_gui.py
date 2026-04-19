#!/usr/bin/env python3
"""简化的 GUI 界面"""
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import subprocess
    from pathlib import Path
    
    class WeChatExporterGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Mac 微信数据导出工具")
            self.root.geometry("600x400")
            
            # 标题
            title = tk.Label(root, text="Mac 微信数据导出工具", font=("Arial", 16, "bold"))
            title.pack(pady=20)
            
            # 按钮区域
            btn_frame = tk.Frame(root)
            btn_frame.pack(pady=20)
            
            tk.Button(btn_frame, text="1. 解密数据库", command=self.decrypt_db, width=20, height=2).pack(pady=5)
            tk.Button(btn_frame, text="2. 合并数据库", command=self.merge_db, width=20, height=2).pack(pady=5)
            tk.Button(btn_frame, text="3. 导出 CSV", command=self.export_csv, width=20, height=2).pack(pady=5)
            tk.Button(btn_frame, text="4. 统计分析", command=self.analyze, width=20, height=2).pack(pady=5)
            
            # 状态栏
            self.status = tk.Label(root, text="就绪", bd=1, relief=tk.SUNKEN, anchor=tk.W)
            self.status.pack(side=tk.BOTTOM, fill=tk.X)
        
        def decrypt_db(self):
            self.status.config(text="正在解密...")
            self.root.update()
            subprocess.run(["python3", "scripts/mac_decrypt_interactive.py"])
            self.status.config(text="解密完成")
        
        def merge_db(self):
            self.status.config(text="正在合并...")
            self.root.update()
            subprocess.run(["python3", "scripts/mac_merge_db.py", "--test"])
            self.status.config(text="合并完成")
        
        def export_csv(self):
            output = filedialog.asksaveasfilename(defaultextension=".csv")
            if output:
                self.status.config(text="正在导出...")
                self.root.update()
                subprocess.run(["python3", "scripts/mac_export_messages.py", "--output", output])
                self.status.config(text=f"导出完成: {output}")
        
        def analyze(self):
            db = filedialog.askopenfilename(filetypes=[("Database", "*.db")])
            if db:
                self.status.config(text="正在分析...")
                self.root.update()
                subprocess.run(["python3", "scripts/mac_chat_analysis.py", "--db", db])
                self.status.config(text="分析完成")
    
    if __name__ == '__main__':
        root = tk.Tk()
        app = WeChatExporterGUI(root)
        root.mainloop()
except ImportError:
    print("Tkinter 未安装，GUI 功能不可用")
    print("请使用命令行工具")
