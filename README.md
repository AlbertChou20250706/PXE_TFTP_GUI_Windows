# PXE GUI（Windows / Python）+ DHCP Option 66/67 完整使用手冊 — Albert Style v1.0.0

> 本文件提供：在 **Windows 筆電** 上以 **Python GUI** 方式提供 **TFTP 伺服器** 以支援 PXE，並說明 **DHCP Option 66/67** 的設定方法與常見平台操作範例。  
> 風格：Albert Style（條列清楚、實作導向、含彩色狀態說明、附驗證清單與疑難排解）。

---

## 1. 快速結論（TL;DR）
- **Option 66（TFTP Server Name）**：填你的 **TFTP 伺服器 IP**（例 `192.168.101.53`）。  
- **Option 67（Bootfile Name）**：填 **開機檔名**（UEFI 常用 `bootx64.efi`；Legacy BIOS 用 `pxelinux.0`）。  
- **同一網段只允許 1 個 DHCP**（不要同時開路由器 DHCP + Tftpd64 DHCP）。  
- 本專案的 Python GUI 僅提供 **TFTP**，**不內建 DHCP**；請在你現有 DHCP 設定 Option 66/67 指向本機。

---

## 2. 環境與需求
- 作業系統：**Windows 10/11**（建議系統管理員權限執行）  
- Python：**3.10+**（安裝時勾選 *Add Python to PATH*）  
- 網路：你的筆電與被測端（PXE 客戶端）在 **同一 L2 網段**  
- 防火牆：開放 **UDP/69**（TFTP 控制）與 **高位臨時埠**（TFTP 資料傳輸）

---

## 3. 檔案與目錄
```text
PXE_TFTP_GUI_Windows.py    # 主程式（TFTP + GUI）
TFTP-Root\                 # TFTP 根目錄（可自訂）
  ├─ bootx64.efi           # (UEFI) 推薦 NBP
  ├─ ipxe.efi              # (UEFI 選用)
  ├─ grubx64.efi           # (UEFI 選用)
  ├─ pxelinux.0            # (BIOS) NBP
  ├─ ldlinux.c32           # (BIOS) pxelinux 所需
  └─ pxelinux.cfg\default  # (BIOS) 資料夾與設定檔
```

> **注意**：Option 67 僅填「檔名」，**不要**寫 `C:\TFTP-Root\bootx64.efi`。TFTP 會以根目錄映射來找檔案。

---

## 4. 安裝與啟動
1. 安裝 Python 3.10+  
2. 以系統管理員開啟 PowerShell：
   ```powershell
   cd <你的資料夾>
   python PXE_TFTP_GUI_Windows.py
   ```
3. 在 GUI：
   - **Server IP (NIC)**：選你的 PXE 網段 IP（例 `192.168.101.53`）  
   - **TFTP Root**：選 `C:\TFTP-Root` 或其他資料夾（請先放好 `bootx64.efi`）  
   - **Bootfile (Option 67)**：填 `bootx64.efi` 或 `pxelinux.0`  
   - 按 **Start** 啟動 TFTP 監聽（UDP/69）  
4. **在你的 DHCP** 設定 Option 66/67（下一節有多平台範例）。  
5. 被測端改成網路開機（PXE）→ GUI 右側會顯示 RRQ/傳輸日誌。

---

## 5. 什麼是 Option 66/67？
- **Option 66 = TFTP Server Name**：告訴 PXE 客戶端「到哪台 TFTP 拿檔」。**通常填 IP**。  
- **Option 67 = Bootfile Name**：告訴 PXE 客戶端「要拿哪一個檔案」。**填檔名**。

> UEFI 建議 `bootx64.efi`；若是 BIOS（Legacy）才使用 `pxelinux.0`。UEFI 機器給 `pxelinux.0` 常會卡住。

---

## 6. 在哪裡設定 Option 66/67？（擇一你的環境）

### 6.1 Tftpd64 當 DHCP
1. 打開 **Tftpd64** → **DHCP server** 分頁。  
2. 設欄位：
   - IP pool / Mask / Gateway：依網段設定  
   - **Boot File**（= Opt 67）：`bootx64.efi`  
   - **Next Server**（= Opt 66）：`192.168.101.53`
3. 套用後，確保 **網段內沒有其他 DHCP**。

### 6.2 Windows Server DHCP
**圖形介面**：  
- DHCP 管理員 → IPv4 → Scope Options（或 Server Options）→ 右鍵 **Configure Options**  
- 勾選：
  - **066 Boot Server Host Name** = `192.168.101.53`
  - **067 Bootfile Name** = `bootx64.efi`

**PowerShell**：
```powershell
$ScopeId = "192.168.101.0"
Set-DhcpServerv4OptionValue -ScopeId $ScopeId -OptionId 66 -Value "192.168.101.53"
Set-DhcpServerv4OptionValue -ScopeId $ScopeId -OptionId 67 -Value "bootx64.efi"
```

### 6.3 dnsmasq（Linux）
```ini
dhcp-range=192.168.101.100,192.168.101.200,255.255.255.0,12h
dhcp-option=66,192.168.101.53
dhcp-option=67,bootx64.efi
enable-tftp
tftp-root=/srv/tftp
```
> `systemctl restart dnsmasq` 後生效。

### 6.4 ISC-DHCP (dhcpd)（Linux 企業版）
```conf
subnet 192.168.101.0 netmask 255.255.255.0 {
  range 192.168.101.100 192.168.101.200;
  option routers 192.168.101.1;
  option domain-name-servers 192.168.101.1;
  next-server 192.168.101.53;   # = Option 66
  filename "bootx64.efi";       # = Option 67
}
```
> 重新啟動：`systemctl restart isc-dhcp-server`。

### 6.5 路由器（pfSense / OPNSense / MikroTik / OpenWrt）
- **pfSense**：Services → DHCP Server → 介面 → **Network Booting**  
  - **Next Server** = `192.168.101.53`（Opt 66）  
  - **UEFI 64-bit** 或 **Default BIOS file** = `bootx64.efi`（Opt 67）
- **MikroTik**：
  ```bash
  /ip dhcp-server network set numbers=0 boot-file-name=bootx64.efi next-server=192.168.101.53
  ```
- **OpenWrt（LuCI）**：Network → DHCP and DNS → DHCP-Options  
  - 新增：`66,192.168.101.53` 與 `67,bootx64.efi`

---

## 7. 防火牆與權限
- Windows 防火牆允許：`python.exe`（或你打包後的 `.exe`）使用 **UDP/69** 與高位臨時埠。  
- 如遇到「Bind UDP/69 失敗」：請以 **系統管理員**身分執行，或確認沒有其他 TFTP 服務占用埠。

---

## 8. 驗證流程（Checklist）
1. **TFTP 自測（另一台主機）**  
   - Windows：啟用「TFTP Client」功能後執行：  
     ```cmd
     tftp 192.168.101.53 GET bootx64.efi
     ```
   - 能成功下載表示 TFTP 正常。  
2. **DHCP 確認**  
   - 用 Wireshark 抓 `bootp || dhcp`，應在 Offer/ACK 中看到 **Option 66/67**。  
3. **UEFI/BIOS 對應**  
   - UEFI 機器請用 `bootx64.efi`；BIOS 才用 `pxelinux.0`。  
4. **目錄與檔名**  
   - `TFTP-Root` 中檔名大小寫與副檔名正確，Option 67 只填檔名。

---

## 9. 疑難排解（FAQ）
**Q1：PXE 一直卡住？**  
A：檢查是否同網段有 **兩個 DHCP**；或 UEFI 機器卻下了 `pxelinux.0`。

**Q2：GUI 有 RRQ 紀錄但傳不完？**  
A：確認防火牆開放 **高位臨時埠**（TFTP 數據通道使用的隨機 UDP 埠）。

**Q3：要不要把檔案放完整路徑？**  
A：**不要**。只放在 `TFTP-Root`，Option 67 只寫 **檔名**。

**Q4：要支援寫入（WRQ）或大檔最佳化（blksize/tsize）？**  
A：目前版本僅 RRQ（夠 PXE 用）。如需進階可升級 v1.1 加 RFC 2348/2349。

---

## 10. 進階：打包成 EXE
```powershell
pip install pyinstaller
pyinstaller -F -w PXE_TFTP_GUI_Windows.py
# 產生 dist\PXE_TFTP_GUI_Windows.exe 可攜執行檔
```

---

## 11. 版本資訊（Changelog）
- **v1.0.0**：初版釋出（GUI + TFTP RRQ、即時日誌、TXT/HTML 日誌匯出、DHCP Helper 提示）。

---

## 12. 授權
MIT License — 可自由使用、修改、分發，請保留版權聲明。
