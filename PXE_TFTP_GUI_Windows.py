#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 PXE_TFTP_GUI_Windows.py  —  Albert Style v1.0.0  (Windows / Python 3.10+)
 Author  : Albert.Chou Style
 Purpose : Lightweight TFTP server with GUI for PXE boot (RRQ only, octet mode)
 License : MIT
===============================================================================
 Features
   • GUI (tkinter): choose NIC IP, TFTP-Root, Bootfile, Start/Stop, live logs
   • Pure-stdlib TFTP server (UDP/69) — supports RRQ (octet), 512B blocks
   • Retransmit on timeout, simple rate/throughput logging
   • Exports logs (TXT + HTML, UTF-8) under ./Logs/PXE_YYYYMMDD_HHMMSS/
   • Quick “DHCP helper” panel (Option 66/67 提示)
 Notes
   • DHCP 不在本工具內：請用現有 DHCP（路由器、Windows Server、Tftpd64 等）
   • 務必開放防火牆 UDP/69 + 高位臨時埠（TFTP 資料通道）
   • 以系統管理員身分執行可避免權限/防火牆問題
===============================================================================
"""

import os, sys, socket, threading, time, datetime, queue, tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ========= Albert Color Tags (GUI log) =======================================
LVL_COLORS = {
    "INFO": "#3b82f6",
    "PASS": "#16a34a",
    "WARN": "#f59e0b",
    "FAIL": "#ef4444",
}

# ========= Small helpers =====================================================
def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def make_run_dir():
    base = os.path.join(os.getcwd(), "Logs")
    os.makedirs(base, exist_ok=True)
    run = os.path.join(base, "PXE_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run, exist_ok=True)
    return run

# ========= Minimal NIC discovery (pure stdlib) ===============================
def list_local_addrs():
    addrs = set()
    # 1) hostname resolves
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(socket.gethostname(), None):
            if fam == socket.AF_INET:
                addrs.add(sa[0])
    except Exception:
        pass
    # 2) connect-trick to pick primary
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    # always include localhost for debug
    addrs.add("127.0.0.1")
    return sorted(addrs)

# ========= TFTP Server (RRQ only) ============================================
class TFTPServer(threading.Thread):
    """
    RFC 1350 minimal TFTP (RRQ/octet) with 512B block size.
    • Listens on UDP/69; on RRQ spawns a session thread to serve the file
    • Retransmit DATA if ACK timeout (1s) up to N times
    """
    def __init__(self, bind_ip, root_dir, logq, bootfile_hint=""):
        super().__init__(daemon=True)
        self.bind_ip = bind_ip
        self.root = os.path.abspath(root_dir)
        self.logq = logq
        self.bootfile_hint = bootfile_hint
        self._stop = threading.Event()
        self.sock = None

    def log(self, lvl, msg):
        self.logq.put((lvl, msg))

    def stop(self):
        self._stop.set()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def run(self):
        # UDP/69 listen
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((self.bind_ip, 69))
            self.log("PASS", f"TFTP listening on {self.bind_ip}:69 (root={self.root})")
        except Exception as e:
            self.log("FAIL", f"Bind UDP/69 failed: {e}. Try run as Administrator or free the port.")
            return

        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                break  # socket closed
            if not data:
                continue

            op = int.from_bytes(data[0:2], "big")
            if op == 1:  # RRQ
                # Parse filename\0mode\0
                try:
                    parts = data[2:].split(b"\x00")
                    filename = parts[0].decode(errors="ignore")
                    mode = (parts[1] if len(parts) > 1 else b"octet").decode(errors="ignore").lower()
                except Exception:
                    self._send_error(addr, 0, "Malformed RRQ")
                    continue
                # Normalize & security: stay inside root
                if filename.startswith("/"):
                    filename = filename[1:]
                if ".." in filename:
                    self._send_error(addr, 2, "Access violation")
                    continue

                abspath = os.path.abspath(os.path.join(self.root, filename))
                if not abspath.startswith(self.root):
                    self._send_error(addr, 2, "Access violation")
                    continue
                if not os.path.exists(abspath):
                    # small helper: if client asks nothing, serve bootfile_hint
                    if filename in ("", None) and self.bootfile_hint:
                        abspath = os.path.join(self.root, self.bootfile_hint)
                    else:
                        self._send_error(addr, 1, "File not found")
                        self.log("WARN", f"RRQ {filename} from {addr[0]}:{addr[1]} -> NOT FOUND")
                        continue

                self.log("INFO", f"RRQ {filename or self.bootfile_hint} from {addr[0]}:{addr[1]}")
                threading.Thread(
                    target=self._serve_file, args=(abspath, addr), daemon=True
                ).start()
            else:
                # ignore WRQ/other
                self._send_error(addr, 4, "Illegal TFTP operation")

    def _send_error(self, addr, code, text):
        pkt = (5).to_bytes(2, "big") + code.to_bytes(2, "big") + text.encode() + b"\x00"
        try:
            self.sock.sendto(pkt, addr)
        except Exception:
            pass

    def _serve_file(self, path, client_addr):
        # new ephemeral socket/port per session (TFTP data channel)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        blocksize = 512
        block = 1
        retransmit = 0
        max_retx = 10

        size = os.path.getsize(path)
        sent_total = 0
        t0 = time.time()

        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(blocksize)
                    op_data = (3).to_bytes(2, "big")
                    pkt = op_data + block.to_bytes(2, "big") + chunk

                    # send & wait ACK
                    while True:
                        try:
                            s.sendto(pkt, client_addr)
                            data, _ = s.recvfrom(2048)
                            if len(data) >= 4 and int.from_bytes(data[0:2], "big") == 4 and \
                               int.from_bytes(data[2:4], "big") == block:
                                # ACK ok
                                sent_total += len(chunk)
                                break
                        except socket.timeout:
                            retransmit += 1
                            if retransmit > max_retx:
                                raise TimeoutError("Too many timeouts, aborting session.")
                            continue

                    # last block (<512) ends session
                    if len(chunk) < blocksize:
                        break
                    block = (block + 1) & 0xffff
                    if block == 0:  # wrap (rare for huge files)
                        block = 1
        except Exception as e:
            self.log("FAIL", f"Send error: {e}")
        finally:
            s.close()
            dt = max(0.001, time.time() - t0)
            mbps = (sent_total * 8 / 1e6) / dt
            self.log("PASS", f"Done {os.path.basename(path)} ({size} bytes) in {dt:.2f}s, ~{mbps:.2f} Mbps")

# ========= GUI ================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PXE TFTP GUI — Albert v1.0.0")
        self.geometry("880x520")
        self.resizable(True, True)

        self.nic_var = tk.StringVar(value="")
        self.root_var = tk.StringVar(value=os.path.abspath("TFTP-Root"))
        self.boot_var = tk.StringVar(value="bootx64.efi")
        self.status_var = tk.StringVar(value="Idle")
        self.server = None
        self.logq = queue.Queue()
        self.run_dir = make_run_dir()
        os.makedirs(self.root_var.get(), exist_ok=True)

        self._build_ui()
        self._populate_nics()
        self.after(200, self._drain_logs)

    # --- UI layout
    def _build_ui(self):
        frm_top = ttk.Frame(self); frm_top.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_top, text="Server IP (NIC)").grid(row=0, column=0, sticky="w")
        self.nic_cb = ttk.Combobox(frm_top, textvariable=self.nic_var, state="readonly", width=24)
        self.nic_cb.grid(row=0, column=1, padx=6)
        ttk.Button(frm_top, text="Refresh", command=self._populate_nics).grid(row=0, column=2, padx=4)

        ttk.Label(frm_top, text="TFTP Root").grid(row=1, column=0, sticky="w", pady=(6,0))
        ttk.Entry(frm_top, textvariable=self.root_var, width=48).grid(row=1, column=1, sticky="we", padx=6, pady=(6,0))
        ttk.Button(frm_top, text="Browse", command=self._browse_root).grid(row=1, column=2, padx=4, pady=(6,0))

        ttk.Label(frm_top, text="Bootfile (Option 67)").grid(row=2, column=0, sticky="w", pady=(6,0))
        ttk.Entry(frm_top, textvariable=self.boot_var, width=32).grid(row=2, column=1, sticky="w", padx=6, pady=(6,0))
        ttk.Button(frm_top, text="Open Folder", command=lambda: os.startfile(self.root_var.get())).grid(row=2, column=2, padx=4, pady=(6,0))

        btns = ttk.Frame(frm_top); btns.grid(row=0, column=3, rowspan=3, padx=(18,0))
        self.start_btn = ttk.Button(btns, text="Start", command=self.start_server); self.start_btn.pack(fill="x", pady=2)
        self.stop_btn  = ttk.Button(btns, text="Stop",  command=self.stop_server, state="disabled"); self.stop_btn.pack(fill="x", pady=2)
        ttk.Button(btns, text="Export Logs", command=self._export_logs).pack(fill="x", pady=(12,2))
        ttk.Button(btns, text="DHCP Helper", command=self._show_helper).pack(fill="x", pady=2)

        sep = ttk.Separator(self); sep.pack(fill="x", pady=4)

        # log area
        frm_log = ttk.Frame(self); frm_log.pack(fill="both", expand=True, padx=10, pady=6)
        self.txt = tk.Text(frm_log, wrap="none"); self.txt.pack(fill="both", expand=True)
        self._init_tags()
        self._log("INFO", "PXE TFTP GUI ready. Steps: set DHCP Option 66/67 → place bootfile in TFTP-Root → Start.")

        # status bar
        sb = ttk.Frame(self); sb.pack(fill="x", padx=10, pady=(0,8))
        ttk.Label(sb, textvariable=self.status_var).pack(side="left")

    def _init_tags(self):
        for lvl, col in LVL_COLORS.items():
            self.txt.tag_config(lvl, foreground=col)
        self.txt.tag_config("ts", foreground="#6b7280")

    def _populate_nics(self):
        addrs = list_local_addrs()
        self.nic_cb["values"] = addrs
        if addrs and not self.nic_var.get():
            self.nic_var.set(addrs[0])

    def _browse_root(self):
        p = filedialog.askdirectory(initialdir=self.root_var.get() or os.getcwd())
        if p:
            self.root_var.set(p)

    def _log(self, lvl, msg):
        line = f"{ts()} | [{lvl}] {msg}\n"
        self.txt.insert("end", f"{ts()} | ", "ts")
        self.txt.insert("end", f"[{lvl}] ", lvl)
        self.txt.insert("end", f"{msg}\n")
        self.txt.see("end")
        # also append to txt log
        with open(os.path.join(self.run_dir, "PXE_Log.txt"), "a", encoding="utf-8") as f:
            f.write(line)

    def _drain_logs(self):
        try:
            while True:
                lvl, msg = self.logq.get_nowait()
                self._log(lvl, msg)
        except queue.Empty:
            pass
        self.after(200, self._drain_logs)

    # --- Start/Stop
    def start_server(self):
        ip = self.nic_var.get().strip()
        root = self.root_var.get().strip()
        boot = self.boot_var.get().strip()
        if not ip:
            messagebox.showerror("Error", "Please select server IP (NIC).")
            return
        if not os.path.isdir(root):
            messagebox.showerror("Error", f"TFTP Root not found: {root}")
            return
        # UI state
        self.server = TFTPServer(ip, root, self.logq, bootfile_hint=boot)
        self.server.start()
        self.status_var.set(f"Running on {ip}:69 (root={root})")
        self._log("INFO", f"Expect DHCP Option66={ip}, Option67={boot}")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.status_var.set("Stopped")
        self._log("WARN", "TFTP server stopped.")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    # --- Export logs (TXT + HTML)
    def _export_logs(self):
        # build minimal HTML summary
        txt_path = os.path.join(self.run_dir, "PXE_Log.txt")
        html_path = os.path.join(self.run_dir, "PXE_Overview.html")
        ip = self.nic_var.get().strip()
        root = self.root_var.get().strip()
        boot = self.boot_var.get().strip()
        with open(html_path, "w", encoding="utf-8") as h:
            h.write(f"""<!doctype html><meta charset="utf-8">
<title>PXE Overview — Albert</title>
<style>
body{{font-family:Segoe UI,Roboto,Arial,sans-serif;background:#fafafa;margin:24px;color:#222}}
.card{{background:#fff;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,.08);padding:20px;max-width:980px}}
td{{padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top}} td.k{{color:#666;width:180px}}
pre{{background:#0b1020;color:#e5e7eb;padding:12px;border-radius:12px;white-space:pre-wrap}}
.badge{{display:inline-block;background:#eef;padding:2px 8px;border-radius:999px}}
</style>
<div class="card">
<h2>PXE Overview — Albert Style</h2>
<table>
<tr><td class="k">Server IP</td><td><span class="badge">{ip}</span></td></tr>
<tr><td class="k">TFTP Root</td><td>{root}</td></tr>
<tr><td class="k">Bootfile (Opt67)</td><td>{boot}</td></tr>
<tr><td class="k">Run Dir</td><td>{self.run_dir}</td></tr>
</table>
<h3>Recent Log</h3>
<pre>{open(txt_path,'r',encoding='utf-8').read() if os.path.exists(txt_path) else '(no log yet)'}</pre>
</div>""")
        self._log("PASS", f"Exported TXT/HTML to {self.run_dir}")
        os.startfile(self.run_dir)

    # --- DHCP helper
    def _show_helper(self):
        info = (
            "【DHCP 設定重點】\n"
            "1) 你的網段只能有 1 個 DHCP（避免衝突）。\n"
            "2) 在既有 DHCP 設：\n"
            "   - Option 66 (TFTP server) = 本機 IP（本畫面 NIC）\n"
            "   - Option 67 (Bootfile)    = 本畫面 Bootfile（如 bootx64.efi）\n"
            "3) 將對應 NBP 檔案放進 TFTP Root：\n"
            "   - UEFI:  bootx64.efi / ipxe.efi / grubx64.efi\n"
            "   - BIOS:  pxelinux.0 (+ ldlinux.c32 / pxelinux.cfg/)\n"
            "4) Windows 防火牆：允許 Python 與 UDP/69、以及高位臨時埠。\n"
        )
        messagebox.showinfo("DHCP Helper", info)

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
