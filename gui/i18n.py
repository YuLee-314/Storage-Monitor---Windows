"""Internationalization - translation dictionaries for EN and ZH."""

TRANSLATIONS = {
    "en": {
        "app_title": "Storage Monitor",
        "refresh_btn": "Refresh Now",
        "settings": "Settings",
        "language": "Language",
        "lang_en": "English",
        "lang_zh": "中文",
        "show_window": "Show",
        "exit": "Exit",
        "drive_fixed": "Fixed",
        "drive_removable": "Removable",
        "drive_remote": "Remote",
        "drive_cdrom": "CD-ROM",
        "drive_ramdisk": "RAM Disk",
        "drive_other": "Other",
        "label_used": "Used",
        "label_free": "Free",
        "label_total": "Total",
        "no_label": "(No Label)",
        "status_error_module": "Error: disk_collector module not found.",
        "status_updated": "Last updated",
        # Tabs
        "tab_overview": "Overview",
        "tab_history": "History",
        "tab_files": "File Analysis",
        # History
        "label_drive": "Drive",
        "label_range": "Range",
        "range_24h": "24 Hours",
        "range_7d": "7 Days",
        "range_30d": "30 Days",
        "label_usage_pct": "Usage (%)",
        "status_no_data": "No data yet. Collecting...",
        # File analyzer
        "label_top_n": "Top N",
        "btn_scan": "Scan",
        "btn_cancel": "Cancel",
        "col_name": "Name",
        "col_size": "Size",
        "col_percent": "%",
        "col_path": "Path",
        "status_scanning": "Scanning...",
        "status_items": "Items",
        # Directory tree
        "btn_collapse": "Collapse",
        "status_loading": "Loading...",
        "group_larger": "First half",
        "group_smaller": "Second half",
        "group_others": "Others",
        "label_items": "items",
        "label_folders": "folders",
        "label_files": "files",
        "status_empty": "(empty)",
        # Sunburst
        "btn_back": "Back",
        "label_free": "Free",
        "hint_sunburst": "Click a segment to drill down  |  ← Back to go up",
        "hint_no_data": "Expand directories in Overview first to see data here",
    },
    "zh": {
        "app_title": "存储监测",
        "refresh_btn": "立即刷新",
        "settings": "设置",
        "language": "语言",
        "lang_en": "English",
        "lang_zh": "中文",
        "show_window": "显示窗口",
        "exit": "退出",
        "drive_fixed": "固定磁盘",
        "drive_removable": "可移动磁盘",
        "drive_remote": "网络磁盘",
        "drive_cdrom": "光盘",
        "drive_ramdisk": "内存盘",
        "drive_other": "其他",
        "label_used": "已用",
        "label_free": "可用",
        "label_total": "总计",
        "no_label": "(无卷标)",
        "status_error_module": "错误：未找到 disk_collector 模块。",
        "status_updated": "上次刷新",
        # Tabs
        "tab_overview": "概览",
        "tab_history": "历史趋势",
        "tab_files": "文件分析",
        # History
        "label_drive": "磁盘",
        "label_range": "范围",
        "range_24h": "24小时",
        "range_7d": "7天",
        "range_30d": "30天",
        "label_usage_pct": "使用率 (%)",
        "status_no_data": "暂无数据，正在采集...",
        # File analyzer
        "label_top_n": "前 N 项",
        "btn_scan": "扫描",
        "btn_cancel": "取消",
        "col_name": "名称",
        "col_size": "大小",
        "col_percent": "%",
        "col_path": "路径",
        "status_scanning": "正在扫描...",
        "status_items": "项",
        # Directory tree
        "btn_collapse": "收起",
        "status_loading": "加载中...",
        "group_larger": "较大项",
        "group_smaller": "较小项",
        "group_others": "其他项",
        "label_items": "项",
        "label_folders": "文件夹",
        "label_files": "文件",
        "status_empty": "(空)",
        # Sunburst
        "btn_back": "返回",
        "label_free": "空闲",
        "hint_sunburst": "点击扇区深入  |  ← 返回上级",
        "hint_no_data": "请先在概览中展开目录，数据将在此展示",
    },
}

# Map English drive type names returned by Rust to i18n keys
DRIVE_TYPE_MAP = {
    "Fixed": "drive_fixed",
    "Removable": "drive_removable",
    "Remote": "drive_remote",
    "CD-ROM": "drive_cdrom",
    "RAM Disk": "drive_ramdisk",
    "Other": "drive_other",
}


def make_tr(lang: str):
    """Return a translation function bound to the given language."""
    translations = TRANSLATIONS.get(lang, TRANSLATIONS["en"])

    def tr(key: str) -> str:
        return translations.get(key, key)

    return tr
