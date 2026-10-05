"""Native local launcher: directory picker + local browser; no admin privileges."""
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog,messagebox
import httpx

def main():
    window=tk.Tk();window.title('DiskSage');window.geometry('470x230')
    path=tk.StringVar(value=str(Path.home()))
    tk.Label(window,text='DiskSage 磁盘分析',font=('Segoe UI',18)).pack(pady=18)
    tk.Entry(window,textvariable=path,width=60).pack(padx=18)
    tk.Button(window,text='选择目录',command=lambda:path.set(filedialog.askdirectory() or path.get())).pack(pady=10)
    def open_app():
        try:
            with socket.create_connection(('127.0.0.1',8765),timeout=.5):pass
        except OSError:
            command=[sys.executable,'--serve'] if getattr(sys,'frozen',False) else [sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8765']
            subprocess.Popen(command,cwd=Path(__file__).parent.parent,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            for _ in range(50):
                try:
                    with socket.create_connection(('127.0.0.1',8765),timeout=.2):break
                except OSError:time.sleep(.1)
        try:
            response=httpx.post('http://127.0.0.1:8765/api/scans',json={'path':path.get()},timeout=5);response.raise_for_status()
            webbrowser.open('http://127.0.0.1:8765?scan='+response.json()['id'])
        except Exception:messagebox.showerror('无法开始','检查路径或等待当前扫描完成')
    tk.Button(window,text='只读分析并打开工作台',command=open_app).pack(pady=8)
    window.mainloop()

if __name__=='__main__':
    if '--serve' in sys.argv:
        import uvicorn
        from app.main import app
        port=int(sys.argv[sys.argv.index('--port')+1]) if '--port' in sys.argv else 8765
        uvicorn.run(app,host='127.0.0.1',port=port,log_config=None)
    else:main()
