# app/services/system_info_service.py (修改后)

import subprocess
import json
import psutil
import platform
import re
import threading
import time
import shutil
import os
import enum
from datetime import datetime, timezone
from collections import deque

# 【注意】这里不再需要导入 current_app，因为我们将通过 self._app 来创建上下文
# from flask import current_app

try:
    import pynvml

    NVIDIA_SMI_AVAILABLE = True
except ImportError:
    NVIDIA_SMI_AVAILABLE = False


# --- S.M.A.R.T. 相关定义，从原 disk_health_monitor_v2.py 移植 ---
class HealthStatus(enum.Enum):
    """
    硬盘健康状态的枚举。
    提供了比纯字符串更安全、更规范的状态表示。
    """
    HEALTHY = "健康 (PASSED)"
    WARNING = "警告 (WARNING)"
    FAILED = "危险 (FAILED)"
    UNKNOWN = "未知 (UNKNOWN)"


# 定义需要特别关注的关键S.M.A.R.T.属性 (主要针对 ATA 硬盘)
# 格式: {属性ID: ("属性名", "描述", 是否raw_value > 0就值得警惕)}
CRITICAL_ATA_ATTRIBUTES = {
    5: ("Reallocated_Sector_Ct",
        "已重映射扇区数。当硬盘发现读/写/校验错误时，会将该扇区标记为“已重映射”并使用备用扇区替换。非零值是磁盘表面退化的明确信号。",
        True),
    187: ("Reported_Uncorrect", "无法修正的错误计数。非零值表示存在数据损坏风险。", True),
    188: ("Command_Timeout", "命令超时计数。可能与电源或线缆问题有关。", True),
    197: (
        "Current_Pending_Sector_Ct",
        "当前待处理扇区数。这些是不稳定扇区，等待被重映射。非零值是即将发生故障的强烈预警。",
        True),
    198: ("Offline_Uncorrectable", "脱机无法校正的错误计数。与197类似，是严重问题的标志。", True),
}


class SystemInfoService:
    """
    一个常驻服务，用于在后台收集系统信息。
    - 硬件信息在启动时收集一次。
    - 实时状态（CPU, 内存, GPU）定期收集。
    - 磁盘和S.M.A.R.T.信息每日收集。

    通过在初始化时传入配置，服务线程可以独立于 Flask 应用上下文运行，
    所有数据访问都通过锁来保证线程安全。
    """

    def __init__(self):
        self._hardware_info = {}
        self._realtime_history = deque()
        self._disk_usage = {}
        self._smart_info = {}
        self._config = {}
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._realtime_thread = None
        self._daily_thread = None
        # 【核心修改 I】新增一个实例变量来持有 Flask app 对象
        self._app = None

    def get_hardware_info(self):
        """获取静态硬件信息（线程安全）。"""
        with self._lock:
            return self._hardware_info.copy()

    def get_realtime_history(self):
        """获取最近30分钟的实时状态历史记录（线程安全）。"""
        with self._lock:
            return list(self._realtime_history)

    def get_disk_usage(self):
        """获取每日磁盘空间占用统计（线程安全）。"""
        with self._lock:
            return self._disk_usage.copy()

    def get_smart_info(self):
        """获取每日硬盘 S.M.A.R.T. 健康信息（线程安全）。"""
        with self._lock:
            return self._smart_info.copy()

    def start(self, app):
        """
        启动后台收集线程。
        这个方法应该在 Flask app context 中被调用一次。

        :param app: Flask 应用实例，用于一次性读取所有需要的配置，并用于后续创建上下文。
        """
        if self._realtime_thread is not None and self._realtime_thread.is_alive():
            print("SystemInfoService is already running.")
            return

        # 【核心修改 II】保存 app 对象，以便后台线程可以创建自己的上下文
        self._app = app

        # 从 app.config 读取配置并存储在实例变量中，减少对 app context 的依赖
        self._config = {
            'REALTIME_COLLECTION_INTERVAL': app.config['REALTIME_COLLECTION_INTERVAL'],
            'REALTIME_RETENTION_MINUTES': app.config['REALTIME_RETENTION_MINUTES'],
            'DISK_USAGE_PATHS': app.config['DISK_USAGE_PATHS']
        }

        interval = self._config['REALTIME_COLLECTION_INTERVAL']
        retention_mins = self._config['REALTIME_RETENTION_MINUTES']
        max_len = (retention_mins * 60) // interval
        with self._lock:
            self._realtime_history = deque(maxlen=max_len)

        self._shutdown_event.clear()
        self._realtime_thread = threading.Thread(target=self._realtime_collection_loop, daemon=True)
        self._daily_thread = threading.Thread(target=self._daily_collection_loop, daemon=True)
        self._realtime_thread.start()
        self._daily_thread.start()
        print("SystemInfoService started.")

    def stop(self):
        """平滑地停止后台线程。"""
        self._shutdown_event.set()
        if self._realtime_thread and self._realtime_thread.is_alive():
            self._realtime_thread.join(timeout=2)
        if self._daily_thread and self._daily_thread.is_alive():
            self._daily_thread.join(timeout=2)
        self._realtime_thread = None
        self._daily_thread = None
        print("SystemInfoService stopped.")

    def _run_command(self, command, use_sudo=False):
        """
        执行一个 shell 命令并返回其标准输出。
        :param command: 命令列表 (e.g., ['lsblk'])
        :param use_sudo: 是否使用 sudo 执行命令。
        :return: (bool, str) -> (成功与否, stdout 或 stderr)
        """
        full_command = ['sudo'] + command if use_sudo else command
        try:
            result = subprocess.run(
                full_command, capture_output=True, text=True, check=True, encoding='utf-8',
                timeout=60
            )
            return True, result.stdout.strip()
        except FileNotFoundError as e:
            return False, f"命令未找到: {e.filename}。请确保相关工具已安装。"
        except subprocess.TimeoutExpired:
            return False, f"命令 '{' '.join(full_command)}' 执行超时。"
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else e.stdout.strip()
            return False, f"命令 '{' '.join(full_command)}' 执行失败: {error_msg} (Exit Code: {e.returncode})"
        except Exception as e:
            return False, f"执行命令时发生未知错误: {e}"

    def _get_dir_size(self, path: str) -> int:
        """递归计算一个目录的总大小，会跳过无权限访问的文件。"""
        total_size = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        try:
                            total_size += os.path.getsize(fp)
                        except (FileNotFoundError, PermissionError):
                            continue
        except PermissionError:
            return 0
        return total_size

    def _collect_hardware_info(self):
        """收集静态硬件信息，只在启动时运行一次。"""
        # 注意：此方法在 _daily_collection_loop 中被调用，该循环已经处理了 app context，
        # 所以这里不需要再次创建上下文。
        info = {}
        # ... (内部逻辑不变) ...
        os_release = platform.freedesktop_os_release() if hasattr(platform, 'freedesktop_os_release') else {}
        info['os'] = {
            'system': platform.system(), 'release': platform.release(),
            'distro': os_release.get('PRETTY_NAME', 'N/A')
        }
        success, cpu_info_raw = self._run_command(['lscpu'])
        if success and cpu_info_raw:
            details = {k.strip(): v.strip() for line in cpu_info_raw.split('\n') if ':' in line for k, v in
                       [line.split(':', 1)]}
            info['cpu'] = {
                'model_name': details.get('Model name'),
                'architecture': details.get('Architecture'),
                'cores': details.get('Core(s) per socket'),
                'threads': details.get('Thread(s) per core')
            }
        else:
            info['cpu'] = {'model_name': 'N/A', 'architecture': 'N/A', 'cores': 'N/A', 'threads': 'N/A'}

        success, board_raw = self._run_command(['dmidecode', '-t', 'baseboard'], use_sudo=True)
        if success and board_raw:
            lines = board_raw.split('\n')
            board_info = {}
            for line in lines:
                if ':' in line:
                    key, val = line.strip().split(':', 1)
                    if key in ['Manufacturer', 'Product Name', 'Serial Number']:
                        board_info[key.strip().lower().replace(' ', '_')] = val.strip()
            info['motherboard'] = board_info
        else:
            info['motherboard'] = {'manufacturer': 'N/A', 'product_name': 'N/A', 'serial_number': 'N/A'}

        mem = psutil.virtual_memory()
        info['memory'] = {'total_bytes': mem.total}

        gpus = []
        if NVIDIA_SMI_AVAILABLE:
            try:
                pynvml.nvmlInit()
                device_count = pynvml.nvmlDeviceGetCount()
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    gpus.append(
                        {'id': i, 'model': pynvml.nvmlDeviceGetName(handle), 'vram_total_bytes': mem_info.total})
                pynvml.nvmlShutdown()
            except pynvml.NVMLError as e:
                # 这里的 current_app.logger 需要在上下文中执行
                self._app.logger.warning(f"Failed to query NVIDIA GPUs: {e}")
        info['gpus'] = gpus

        success, disk_raw = self._run_command(['lsblk', '-d', '-o', 'NAME,MODEL,SIZE', '--bytes', '--noheadings'])
        disks = []
        if success and disk_raw:
            for line in disk_raw.split('\n'):
                try:
                    name, model, size = re.split(r'\s{2,}', line.strip(), 2)
                    disks.append({'device': f"/dev/{name}", 'model': model, 'size_bytes': int(size)})
                except ValueError:
                    continue
        info['disks'] = disks

        with self._lock:
            self._hardware_info = {'timestamp': datetime.now(timezone.utc).isoformat(), 'data': info}

    def _realtime_collection_loop(self):
        """[后台线程] 定期收集实时状态信息。"""
        interval = self._config['REALTIME_COLLECTION_INTERVAL']
        while not self._shutdown_event.is_set():
            # 【核心修改 III】在执行任务前，为当前线程推入应用上下文
            with self._app.app_context():
                status = {}
                # CPU
                status['cpu'] = {'usage_percent_total': psutil.cpu_percent(interval=None)}
                # Memory
                mem = psutil.virtual_memory()
                status['memory'] = {'total_bytes': mem.total, 'used_bytes': mem.used}
                # GPUs
                gpus = []
                if NVIDIA_SMI_AVAILABLE:
                    try:
                        pynvml.nvmlInit()
                        device_count = pynvml.nvmlDeviceGetCount()
                        for i in range(device_count):
                            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                            fan = pynvml.nvmlDeviceGetFanSpeed(handle) if pynvml.nvmlDeviceGetFanSpeed(
                                handle) != -1 else 0
                            gpus.append({
                                'id': i, 'utilization_percent': util.gpu, 'vram_used_bytes': mem_info.used,
                                'temperature_celsius': temp, 'fan_speed_percent': fan
                            })
                        pynvml.nvmlShutdown()
                    except pynvml.NVMLError as e:
                        # 现在可以安全地使用 logger
                        self._app.logger.warning(f"Failed to query real-time NVIDIA GPU status: {e}")
                status['gpus'] = gpus

                with self._lock:
                    self._realtime_history.append({'timestamp': datetime.now(timezone.utc).isoformat(), 'data': status})

            # 将 sleep 放在上下文之外
            self._shutdown_event.wait(timeout=interval)

    def _daily_collection_loop(self):
        """[后台线程] 每日收集磁盘和S.M.A.R.T.信息。"""
        # 【核心修改 IV】在每次循环迭代时创建应用上下文
        # 首次运行时立即执行
        with self._app.app_context():
            self._collect_hardware_info()
            self._collect_smart_info_data()
            self._collect_daily_data()

        while not self._shutdown_event.is_set():
            if self._shutdown_event.wait(timeout=24 * 3600):
                break
            # 在下一次迭代中再次创建上下文
            with self._app.app_context():
                self._collect_smart_info_data()
                self._collect_daily_data()

    def _collect_daily_data(self):
        """
        实际执行每日收集磁盘占用任务的函数。
        """
        paths_to_check = self._config.get('DISK_USAGE_PATHS', [])
        usage_data = []
        for path in paths_to_check:
            if not os.path.isdir(path):
                # 此处的 logger 现在可以安全地在 app context 中工作
                self._app.logger.warning(f"Disk usage path '{path}' does not exist or is not a directory.")
                continue

            try:
                disk_partition_usage = shutil.disk_usage(path)
                partition_total_bytes = disk_partition_usage.total
                partition_used_bytes = disk_partition_usage.used
            except FileNotFoundError:
                self._app.logger.error(f"Partition information for '{path}' not found.")
                continue
            except Exception as e:
                self._app.logger.error(f"Error getting partition info for '{path}': {e}")
                continue

            user_details = []
            try:
                for item_name in os.listdir(path):
                    full_path = os.path.join(path, item_name)
                    if os.path.isdir(full_path) and not os.path.islink(full_path):
                        size_bytes = self._get_dir_size(full_path)
                        user_details.append({'name': item_name, 'path': full_path, 'used_bytes': size_bytes})
            except PermissionError:
                self._app.logger.warning(f"Permission denied to list contents of '{path}' for disk usage.")
            except Exception as e:
                self._app.logger.error(f"Error listing directory or getting size for '{path}': {e}")

            usage_data.append({
                "path": path,
                "partition_total_bytes": partition_total_bytes,
                "partition_used_bytes": partition_used_bytes,
                "users": user_details
            })

        with self._lock:
            self._disk_usage = {'timestamp': datetime.now(timezone.utc).isoformat(), 'data': usage_data}

    # --- S.M.A.R.T. 信息收集方法 ---

    def _get_smart_device_list(self):
        """
        使用 `smartctl --scan-open` 发现所有可监控的物理设备。
        """
        devices_found = []
        success, output = self._run_command(['smartctl', '--scan-open', '-j'], use_sudo=True)
        if not success:
            # 这里的 logger 也需要上下文
            self._app.logger.error(f"smartctl --scan-open failed: {output}")
            return False, output

        try:
            json_output = json.loads(output)
            for device in json_output.get('devices', []):
                path = device.get('name')
                device_info = {'path': path, 'type_args': []}
                devices_found.append(device_info)
            return True, devices_found
        except json.JSONDecodeError:
            lines = output.strip().splitlines()
            for line in lines:
                if line.startswith('#') or not line.strip():
                    continue
                match = re.match(r'^(\S+)\s+(-d\s+\S+)?.*', line)
                if match:
                    path = match.group(1)
                    type_arg_str = match.group(2)
                    device_info = {'path': path, 'type_args': []}
                    if type_arg_str:
                        device_info['type_args'] = type_arg_str.split()
                    devices_found.append(device_info)
            return True, devices_found

    def _get_raw_smart_data_for_device(self, device_info):
        """
        为单个设备获取原始的、未修改的S.M.A.R.T. JSON数据。
        """
        device_path = device_info['path']
        command = ['smartctl', '-a', '-j'] + device_info['type_args'] + [device_path]
        success, output = self._run_command(command, use_sudo=True)

        if not success:
            return None, f"无法获取设备 {device_path} 的S.M.A.R.T.信息: {output}"
        try:
            return json.loads(output), None
        except json.JSONDecodeError:
            return None, f"解析设备 {device_path} 的S.M.A.R.T. JSON数据失败。"

    def _analyze_smart_data(self, raw_data):
        """
        根据原始S.M.A.R.T.数据生成分析报告对象。
        """
        # ... (内部逻辑不变) ...
        if not raw_data:
            status = HealthStatus.UNKNOWN
            return {
                "health_status": status.name,
                "health_status_description": status.value,
                "summary": ["无法获取S.M.A.R.T.数据。"],
                "critical_attributes": [],
                "recommendations": ["请检查设备连接和权限。"]
            }

        summary = []
        recommendations = []
        critical_attributes = []
        status = HealthStatus.HEALTHY

        if not raw_data.get("smart_status", {}).get("passed", False):
            status = HealthStatus.FAILED
            summary.append("❌ 整体S.M.A.R.T.自检状态为失败！")
            recommendations.append("立即备份所有重要数据并准备更换此硬盘！")
        else:
            summary.append("✅ 整体S.M.A.R.T.自检状态为通过。")

        if "nvme_smart_health_information_log" in raw_data:
            nvme_log = raw_data["nvme_smart_health_information_log"]
            if nvme_log.get("critical_warning", 0) > 0:
                status = HealthStatus.FAILED
                summary.append("❌ NVMe 发现严重警告。")
                recommendations.append("立即检查SSD状态，备份数据。")

            percentage_used = nvme_log.get("percentage_used", 0)
            if percentage_used > 85:
                if status == HealthStatus.HEALTHY: status = HealthStatus.WARNING
                summary.append(f"⚠️ NVMe SSD 已用寿命达到 {percentage_used}%。")
                recommendations.append("考虑在不久的将来更换此SSD。")

        if "ata_smart_attributes" in raw_data:
            for attr in raw_data["ata_smart_attributes"]["table"]:
                attr_id = attr["id"]
                if attr_id in CRITICAL_ATA_ATTRIBUTES:
                    name, desc, check_raw = CRITICAL_ATA_ATTRIBUTES[attr_id]
                    raw_value = int(attr.get("raw", {}).get("value", 0))
                    normalized_value = attr.get("value")
                    worst_value = attr.get("worst")
                    threshold_value = attr.get("thresh")

                    if check_raw and raw_value > 0:
                        if status == HealthStatus.HEALTHY: status = HealthStatus.WARNING
                        msg = f"{name} (ID {attr_id}) 的值为 {raw_value}。"
                        summary.append(f"⚠️ 发现关键属性异常：{msg}")
                        recommendations.append(f"监控 {name} 的值。如果持续增长，请准备更换硬盘。")
                        critical_attributes.append({
                            "id": attr_id, "name": name, "raw_value": raw_value,
                            "description": desc, "value": normalized_value,
                            "worst": worst_value, "thresh": threshold_value
                        })

        temp = raw_data.get("temperature", {}).get("current")
        if temp is not None and temp > 55:
            if status == HealthStatus.HEALTHY: status = HealthStatus.WARNING
            summary.append(f"🔥 温度偏高 ({temp}°C)！")
            recommendations.append("检查机箱通风和散热情况。")

        if len(summary) == 1 and "✅" in summary[0]:
            summary.append("✅ 未发现明显的S.M.A.R.T.警告属性。")
            recommendations.append("继续保持良好使用习惯，定期检查。")

        return {
            "health_status": status.name, "health_status_description": status.value,
            "summary": summary, "critical_attributes": critical_attributes,
            "recommendations": recommendations,
        }

    def _collect_all_smart_info(self):
        """
        获取所有已发现硬盘的完整S.M.A.R.T.信息和分析结果。
        """
        final_report = {"device_list": [], "disk_info": {}}
        success, devices_or_msg = self._get_smart_device_list()
        if not success:
            self._app.logger.error(f"Failed to discover S.M.A.R.T. devices: {devices_or_msg}")
            return final_report

        for device_info in devices_or_msg:
            device_path = device_info['path']
            raw_data, error_msg = self._get_raw_smart_data_for_device(device_info)
            if error_msg:
                self._app.logger.error(f"Error getting S.M.A.R.T. data for {device_path}: {error_msg}")
                analysis_data = self._analyze_smart_data(None)
            else:
                analysis_data = self._analyze_smart_data(raw_data)

            final_report["disk_info"][device_path] = {
                "raw_smart_data": raw_data,
                "analysis": analysis_data
            }

        final_report["device_list"] = list(final_report["disk_info"].keys())
        return final_report

    def _collect_smart_info_data(self):
        """
        包装器，用于收集和存储 S.M.A.R.T. 信息到服务状态。
        """
        smart_data = self._collect_all_smart_info()
        with self._lock:
            self._smart_info = {'timestamp': datetime.now(timezone.utc).isoformat(), 'data': smart_data}


system_info_service = SystemInfoService()
