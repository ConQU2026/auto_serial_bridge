#!/bin/bash

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' 

# --- 修正点 1: 更稳健的 Root 权限检查 ---
# 使用 id -u 替代 $EUID，确保兼容性
if [ "$(id -u)" -ne 0 ]; then
  echo -e "${RED}[错误] 请使用 sudo 运行此脚本！${NC}"
  echo "示例: sudo ./auto_udev.sh"
  exit 1
fi

echo -e "${CYAN}=== 自动 Udev 规则生成器 ===${NC}"

# 获取用户输入
read -p "请输入当前的设备路径 (例如 /dev/ttyACM0): " SOURCE_DEV

if [ ! -e "$SOURCE_DEV" ]; then
    echo -e "${RED}[错误] 找不到设备: $SOURCE_DEV ${NC}"
    echo "请检查插入是否正确，或使用 ls /dev/tty* 查看。"
    exit 1
fi

# 获取目标别名
read -p "请输入想要绑定的别名 (不带 /dev/, 例如 mcu): " ALIAS_NAME

if [ -z "$ALIAS_NAME" ]; then
    echo -e "${RED}[错误] 别名不能为空！${NC}"
    exit 1
fi

if ! [[ "$ALIAS_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo -e "${RED}[错误] 别名只能包含字母、数字、点、下划线和连字符。${NC}"
    exit 1
fi

# 提取设备信息（用属性查询而非解析 -a 树形输出，避免误取上级 hub 的属性）
echo -e "\n正在读取 $SOURCE_DEV 的硬件信息..."

DEV_PROPS=$(udevadm info -q property -n "$SOURCE_DEV")
VID=$(echo "$DEV_PROPS" | sed -n 's/^ID_VENDOR_ID=//p' | head -n 1)
PID=$(echo "$DEV_PROPS" | sed -n 's/^ID_MODEL_ID=//p' | head -n 1)
SERIAL=$(echo "$DEV_PROPS" | sed -n 's/^ID_SERIAL_SHORT=//p' | head -n 1)

# 检查是否成功获取
if [ -z "$VID" ] || [ -z "$PID" ]; then
    echo -e "${RED}[错误] 无法读取设备的 Vendor ID 或 Product ID。${NC}"
    echo "这可能不是一个标准的 USB 设备。"
    exit 1
fi

echo -e "捕获到的硬件信息:"
echo -e "  Vendor ID : ${GREEN}$VID${NC}"
echo -e "  Product ID: ${GREEN}$PID${NC}"
echo -e "  Serial No : ${GREEN}$SERIAL${NC}"


RULES_FILE="/etc/udev/rules.d/99-${ALIAS_NAME}.rules"

echo -e "\n正在生成规则文件: $RULES_FILE ..."

UACCESS_TAG=', TAG+="uaccess"'

if [ -n "$SERIAL" ]; then
    RULE_CONTENT="KERNEL==\"tty*\", SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"$VID\", ATTRS{idProduct}==\"$PID\", ATTRS{serial}==\"$SERIAL\", MODE=\"0660\", GROUP=\"dialout\"${UACCESS_TAG}, ENV{ID_MM_DEVICE_IGNORE}=\"1\", SYMLINK+=\"$ALIAS_NAME\""
else
    echo -e "${CYAN}[风险提示] 设备没有序列号，只能通过 VID/PID 匹配；连接多个相同设备时会发生规则冲突。${NC}"
    RULE_CONTENT="KERNEL==\"tty*\", SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"$VID\", ATTRS{idProduct}==\"$PID\", MODE=\"0660\", GROUP=\"dialout\"${UACCESS_TAG}, ENV{ID_MM_DEVICE_IGNORE}=\"1\", SYMLINK+=\"$ALIAS_NAME\""
fi

# --- 修正点 2: 捕获写入错误 ---
# 如果写入失败（例如权限不足漏网、磁盘满等），直接退出
if ! echo "$RULE_CONTENT" > "$RULES_FILE"; then
    echo -e "${RED}[致命错误] 无法写入规则文件！请确保你拥有写入 /etc/udev/rules.d/ 的权限。${NC}"
    exit 1
fi

# 5. 重载规则
echo -e "正在重载 Udev 规则..."
udevadm control --reload-rules
udevadm trigger

# 6. 验证
sleep 1 # 等一秒让系统反应
if [ -e "/dev/$ALIAS_NAME" ]; then
    echo -e "\n${GREEN}[成功] 映射已建立！${NC}"
    ls -l "/dev/$ALIAS_NAME"
else
    echo -e "\n${RED}[警告] 规则已生成，但暂未检测到 /dev/$ALIAS_NAME。${NC}"
    echo "1. 请尝试重新拔插设备。"
    echo "2. 检查是否有其他规则冲突。"
fi
