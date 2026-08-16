"""连通测试面板 —— 整合集合管理、目标管理、连通测试、测试历史为子标签页。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.csv_handler import export_targets_to_csv, parse_targets_csv
from src.database import Database
from src.excel_handler import export_targets_to_excel, parse_targets_excel
from src.ui.collection_sidebar import CollectionSidebarBase
from src.ui.test_panel import TestPanel
from src.ui.result_panel import ResultPanel


class _CollectionListTab(CollectionSidebarBase):
    """连通测试集合列表 —— 分类树形结构：未分类 / 自定义集合。"""

    new_target_requested = Signal(int)    # 右键菜单 → 添加目标（携带集合 ID）

    # ── 集合访问方法（接入连通测试数据表）────────────────────

    def _get_all_collections(self):
        return self._db.get_all_collections()

    def _ensure_uncat(self):
        for c in self._db.get_all_collections():
            if c.name == "未分类":
                return c
        cid = self._db.add_collection("未分类")
        return self._db.get_collection(cid)

    def _uncat_node_id(self) -> int:
        return 0

    def _count_targets(self, cid) -> int:
        return len(self._db.get_targets(cid))

    def _get_collection(self, cid):
        return self._db.get_collection(cid)

    def _add_collection(self, name: str) -> int:
        return self._db.add_collection(name)

    def _update_collection(self, cid: int, name: str):
        self._db.update_collection(cid, name)

    def _delete_collection(self, cid: int):
        self._db.delete_collection(cid)

    def _save_collections_order(self, ordered_ids: list[int]):
        self._db.update_collections_order(ordered_ids)

    def _new_collection_prefix(self) -> str:
        return "连通性测试"

    def _copy_collection_targets(self, src_cid: int, new_cid: int):
        """把源集合的全部目标复制到新集合。"""
        for t in self._db.get_targets(src_cid):
            self._db.add_target(
                ip=t.ip, port=t.port, description=t.description,
                collection_id=new_cid,
            )

    # ── 右键菜单 ───────────────────────────────────────────

    def _build_collection_menu(self, menu, item, cid: int):
        menu.addAction("新建目标", lambda cid=cid: self.new_target_requested.emit(cid))
        menu.addSeparator()
        menu.addAction("刷新集合", self.refresh)
        menu.addAction("集合重命名", self._on_edit)
        menu.addAction("删除集合", self._on_delete)

    # ── 导入导出（连通测试目标）─────────────────────────────

    def _on_import(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入目标", "", "所有支持格式 (*.csv *.xlsx *.xls *.json);;表格文件 (*.csv *.xlsx *.xls);;JSON 文件 (*.json);;所有文件 (*)")
        if not filepath:
            return
        ext = Path(filepath).suffix.lower()
        try:
            if ext == ".json":
                targets, errors = _parse_connectivity_json(filepath)
            elif ext == ".csv":
                targets, errors = parse_targets_csv(filepath)
            else:
                targets, errors = parse_targets_excel(filepath)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return
        if not targets:
            QMessageBox.information(self, "提示", "文件中没有有效数据。")
            return
        if errors:
            QMessageBox.warning(self, "导入警告", f"部分数据解析失败:\n{chr(10).join(errors[:10])}")
        total = len(targets)
        if total > 1000:
            reply = QMessageBox.question(
                self, "确认导入", f"检测到 {total} 条数据，确定导入？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        # 统一转为 dict
        if targets and not isinstance(targets[0], dict):
            targets = [{"ip": t.ip, "port": t.port, "description": t.description,
                        "collection_name": t.collection_name} for t in targets]
        progress = QProgressDialog("正在导入...", "取消", 0, len(targets), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        count = 0
        collection_cache = {}
        for c in self._db.get_all_collections():
            collection_cache[c.name] = c.id
        for i, t in enumerate(targets):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            cname = t.get("collection_name", "")
            cid = collection_cache.get(cname) if cname else None
            if cname and cid is None:
                cid = self._db.add_collection(cname)
                collection_cache[cname] = cid
            if not self._db.target_exists(t["ip"], t["port"], cid):
                self._db.add_target(t["ip"], t["port"], t.get("description", ""), cid)
                count += 1
        progress.setValue(len(targets))
        self.refresh()
        QMessageBox.information(self, "导入完成", f"成功导入 {count} 条记录。")

    def _on_export(self):
        selected = self._tree.selectedItems()
        valid = [it.data(0, Qt.UserRole) for it in selected
                 if it.data(0, Qt.UserRole) not in (None, 0)]
        coll_names = []
        if valid:
            targets = []
            for bid in valid:
                coll = self._get_collection(bid)
                if coll:
                    coll_names.append(coll.name)
                targets.extend(self._db.get_targets(bid))
        else:
            reply = QMessageBox.question(
                self, "导出确认",
                "未选中集合，是否导出所有目标数据？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return
            targets = self._db.get_targets(None)
        if not targets:
            QMessageBox.information(self, "提示", "没有可导出的数据。")
            return
        # 默认文件名：集合名称_导出时间(yyyyMMddHHmmss)，默认导出 JSON
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        if len(coll_names) == 1:
            default_name = f"{coll_names[0]}_{ts}.json"
        elif coll_names:
            default_name = f"{coll_names[0]}_等{len(coll_names)}个集合_{ts}.json"
        else:
            default_name = f"collections_{ts}.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出目标", default_name,
            "JSON 文件 (*.json);;Excel 文件 (*.xlsx);;CSV 文件 (*.csv)")
        if not filepath:
            return
        data = [{"ip": t.ip, "port": t.port, "description": t.description,
                 "collection_name": t.collection_name or ""} for t in targets]
        ext = Path(filepath).suffix.lower()
        if not ext:
            filepath += ".json"
            ext = ".json"
        if ext == ".json":
            ok, err = _export_connectivity_json(filepath, data)
        elif ext == ".csv":
            from src.csv_handler import export_targets_to_csv
            ok, err = export_targets_to_csv(filepath, data)
        else:
            from src.excel_handler import export_targets_to_excel
            ok, err = export_targets_to_excel(filepath, data)
        if ok:
            QMessageBox.information(self, "导出完成", f"成功导出 {len(data)} 条记录。")
        else:
            QMessageBox.critical(self, "导出失败", str(err))

# ── 连通测试 JSON 导入导出 ──────────────────────────────────


def _export_connectivity_json(filepath: str, data: list[dict]) -> tuple[bool, str]:
    """将连通测试目标数据导出为 JSON 文件。
    data: [{"ip": str, "port": int, "description": str, "collection_name": str}, ...]
    返回 (ok, error_message)。
    """
    import json
    # 按集合分组
    collections_map: dict[str, list[dict]] = {}
    ungrouped = []
    for d in data:
        cname = d.get("collection_name", "")
        if cname:
            collections_map.setdefault(cname, []).append({
                "ip": d["ip"], "port": d["port"], "description": d.get("description", ""),
            })
        else:
            ungrouped.append({
                "ip": d["ip"], "port": d["port"], "description": d.get("description", ""),
            })
    collections_list = []
    for name, targets in collections_map.items():
        collections_list.append({"name": name, "targets": targets})
    if ungrouped:
        collections_list.append({"name": "", "targets": ungrouped})
    doc = {
        "version": 1,
        "type": "connectivity_collections",
        "collections": collections_list,
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return True, ""
    except OSError as e:
        return False, str(e)


def _parse_connectivity_json(filepath: str) -> tuple[list[dict], list[str]]:
    """从 JSON 文件解析连通测试目标数据。
    返回 (targets_list, errors_list)。
    """
    import json
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return [], [f"文件读取失败: {e}"]

    if not isinstance(doc, dict):
        return [], ["JSON 格式错误：根节点应为对象"]

    version = doc.get("version", 0)
    if version != 1:
        return [], [f"不支持的版本: {version}"]

    doc_type = doc.get("type", "")
    targets: list[dict] = []
    errors: list[str] = []

    if doc_type == "connectivity_collections":
        raw_collections = doc.get("collections", [])
        if not isinstance(raw_collections, list):
            return [], ["collections 应为数组"]
        for ci, c in enumerate(raw_collections):
            if not isinstance(c, dict):
                errors.append(f"collections[{ci}] 不是对象")
                continue
            cname = c.get("name", "")
            raw_targets = c.get("targets", [])
            if not isinstance(raw_targets, list):
                errors.append(f"collections[{ci}].targets 不是数组")
                continue
            for ti, t in enumerate(raw_targets):
                if not isinstance(t, dict):
                    errors.append(f"collections[{ci}].targets[{ti}] 不是对象")
                    continue
                ip = t.get("ip", "").strip()
                port = t.get("port", 0)
                if not ip:
                    errors.append(f"collections[{ci}].targets[{ti}] IP 为空")
                    continue
                if not isinstance(port, int) or port < 1 or port > 65535:
                    errors.append(f"collections[{ci}].targets[{ti}] 端口无效: {port}")
                    continue
                targets.append({
                    "ip": ip, "port": port,
                    "description": t.get("description", ""),
                    "collection_name": cname,
                })
    elif doc_type == "connectivity_targets":
        # 简单格式：仅 targets 数组
        raw_targets = doc.get("targets", [])
        if not isinstance(raw_targets, list):
            return [], ["targets 应为数组"]
        for i, t in enumerate(raw_targets):
            if not isinstance(t, dict):
                errors.append(f"targets[{i}] 不是对象")
                continue
            ip = t.get("ip", "").strip()
            port = t.get("port", 0)
            if not ip:
                errors.append(f"targets[{i}] IP 为空")
                continue
            if not isinstance(port, int) or port < 1 or port > 65535:
                errors.append(f"targets[{i}] 端口无效: {port}")
                continue
            targets.append({
                "ip": ip, "port": port,
                "description": t.get("description", ""),
                "collection_name": t.get("collection_name", ""),
            })
    else:
        return [], [f"不支持的类型: {doc_type}"]

    return targets, errors


# ── 连通测试主面板 ──────────────────────────────────────────


class ConnectivityPanel(QWidget):
    """连通测试面板 —— 左侧固定集合分类 + 右侧 3 个子标签页。"""

    targets_changed = Signal()
    protocol_test_selected = Signal(str, int)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # 左侧: 集合分类（固定）
        self._collection_tab = _CollectionListTab(self._db)
        self._collection_tab.collection_selected.connect(self._on_collection_changed)
        # 目标列表拖拽到集合 → 移动到该集合
        self._collection_tab._tree.targets_dropped.connect(
            self._on_targets_dropped_to_collection
        )
        splitter.addWidget(self._collection_tab)

        # 右侧: 功能标签页
        self._tabs = QTabWidget()

        # Tab 0: 连通测试（内含目标列表，已集成目标管理）
        self._test_panel = TestPanel(self._db)
        self._test_panel.test_finished.connect(self._on_test_finished)
        self._test_panel.targets_changed.connect(self._on_targets_changed)
        self._test_panel.protocol_test_selected.connect(
            self.protocol_test_selected.emit
        )
        # 侧边栏右键菜单 → 添加目标（先选中集合，再打开对话框）
        self._collection_tab.new_target_requested.connect(
            lambda cid: (
                self._test_panel._target_panel.set_collection(cid),
                self._test_panel._target_panel._add_target(),
            )
        )
        self._tabs.addTab(self._test_panel, "连通测试")

        # Tab 1: 测试历史
        self._result_panel = ResultPanel(self._db)
        self._tabs.addTab(self._result_panel, "测试历史")

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 860])

        layout.addWidget(splitter)

        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._collection_tab.refresh()

    # ── 信号转发 ────────────────────────────────────────────

    def _on_tab_changed(self, idx: int):
        if idx == 1:  # 测试历史
            self._result_panel.refresh()

    def _on_collection_changed(self, collection_id):
        self._test_panel.set_collection(collection_id)

    def _on_targets_changed(self):
        self._collection_tab.refresh()
        self.targets_changed.emit()

    def _on_test_finished(self):
        self._result_panel.refresh()
        self.targets_changed.emit()

    def _on_targets_dropped_to_collection(self, coll_id: int, target_ids: list):
        """拖拽目标到集合 → 移动目标到该集合内。"""
        if coll_id == 0:
            # "未分类"节点：归入真实的"未分类"集合（0 仅作为界面标识）
            uncat = next((c for c in self._db.get_all_collections()
                          if c.name == "未分类"), None)
            coll_id = uncat.id if uncat else None
        self._db.move_targets_to_collection(target_ids, coll_id)
        self._test_panel._target_panel.refresh()
        self._collection_tab.refresh()
        self.targets_changed.emit()

    # ── 公共接口 ────────────────────────────────────────────

    def refresh_collection_list(self):
        self._collection_tab.refresh()

    def load_temporary_targets(self, targets: list[dict]) -> None:
        """加载协议测试转来的临时目标列表（不写入数据库）。"""
        self._tabs.setCurrentIndex(0)  # 连通测试
        self._test_panel.set_temporary_targets(targets)

    def is_test_running(self) -> bool:
        return self._test_panel.is_running()

    def stop_test(self):
        if self._test_panel.is_running():
            self._test_panel._cancel_test()
