from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading
import time

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'repo_bootstrap.py').is_file())
sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                            QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
                            QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
                            QFormLayout, QMessageBox)
from half_linac.src.shared.machine_profile import load_app_context, require_workflow_write_allowed
from half_linac.src.shared.machine_profile.control_lock import EnergyControlLock
from half_linac.src.shared.machine_profile.app_runtime import new_app_run_dir
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from half_linac.src.apps.magnet_cycle.model import Parameters
from half_linac.src.apps.magnet_cycle.profile_runtime import KINDS, load_magnets
from half_linac.src.apps.magnet_cycle.runner import Simulator, EpicsIO, Stopped, run_cycles


class Worker(QThread):
    progress = pyqtSignal(str, float, float, float, str)
    outcome = pyqtSignal(str)

    def __init__(self, context, magnets, parameters, simulate):
        super().__init__()
        self.context, self.magnets, self.parameters, self.simulate = context, magnets, parameters, simulate
        self.stop = threading.Event()

    def run(self):
        io = None
        lock = None
        message = '完成：已恢复初始设定'
        try:
            mode = self.context.control_backend.name
            if not self.simulate:
                require_workflow_write_allowed(self.context, 'magnet_cycle', 'Magnet cycle')
                lock = EnergyControlLock(Path('/tmp') / f'half_linac_{self.context.machine.id}_{mode}_magnet_cycle.lock', {'app': 'magnet_cycle'})
                lock.acquire()
            folder = new_app_run_dir(Path(__file__).parent, self.context, kind='simulation' if self.simulate else 'cycle')
            folder.mkdir(parents=True, exist_ok=False)
            with (folder / 'events.jsonl').open('w', encoding='utf-8') as stream:
                def record(event):
                    stream.write(json.dumps({'time': time.time(), **event}, ensure_ascii=False) + '\n')
                    stream.flush()
                record(dict(event='start', simulation=self.simulate, backend=mode,
                            machine=self.context.machine.id, parameters=asdict(self.parameters)))
                try:
                    io = Simulator(self.magnets) if self.simulate else EpicsIO()
                    run_cycles(self.magnets, self.parameters, io, self.stop, self.progress.emit, record)
                except Exception as exc:
                    record(dict(event='stopped' if isinstance(exc, Stopped) else 'error', reason=str(exc)))
                    raise
                record(dict(event='complete'))
            message += f'；日志：{folder}'
        except Stopped as exc:
            message = str(exc)
        except Exception as exc:
            message = f'已停止：{exc}；未自动恢复初始设定'
        finally:
            try:
                if io is not None:
                    io.close()
            except Exception as exc:
                message += f'；连接清理失败：{exc}'
            finally:
                try:
                    if lock is not None:
                        lock.release()
                except Exception as exc:
                    message += f'；锁清理失败：{exc}'
                self.outcome.emit(message)


class CycleWindow(QMainWindow):
    def __init__(self, context, simulate=True):
        super().__init__()
        self.context, self.simulate = context, simulate
        self.worker = None
        self.magnets = load_magnets(context, simulate=simulate)
        self.rows = {m.name: i for i, m in enumerate(self.magnets)}
        self.setWindowTitle('Magnet Cycle')
        self.resize(1080, 740)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        mode = '离线模拟（不连接 PV）' if simulate else f'EPICS · {context.control_backend.name.upper()}'
        layout.addWidget(QLabel(f'Magnet Cycle  |  {context.machine.display_name}  |  {mode}'))
        layout.addWidget(QLabel('下限 → [上限 → 下限] × N → 初始设定；双极磁铁使用完整正负范围。所有电流单位为 A。'))
        controls = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem('全部磁铁', '')
        for kind, label in KINDS.items():
            self.filter.addItem(label, kind)
        self.filter.currentIndexChanged.connect(self.filter_rows)
        controls.addWidget(self.filter)
        self.select = QPushButton('选择当前类型')
        self.select.clicked.connect(lambda: self.select_visible(True))
        controls.addWidget(self.select)
        self.clear = QPushButton('清空选择')
        self.clear.clicked.connect(lambda: self.select_visible(False, all_rows=True))
        controls.addWidget(self.clear)
        controls.addStretch()
        layout.addLayout(controls)
        self.table = QTableWidget(len(self.magnets), 8)
        self.table.setHorizontalHeaderLabels(['磁铁', '类型', '下限', '上限', '初始设定', '当前设定', '读回', '状态'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        for row, m in enumerate(self.magnets):
            values = [m.name, KINDS[m.kind], m.low, m.high, None, None, None, m.error or '就绪']
            for col, value in enumerate(values):
                item = QTableWidgetItem('—' if value is None else str(value))
                if col == 0:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsUserCheckable if not m.error else Qt.NoItemFlags))
                    if not m.error:
                        item.setCheckState(Qt.Unchecked)
                item.setToolTip(m.error or f'设定: {m.set_pv}\n读回: {m.read_pv}')
                self.table.setItem(row, col, item)
        layout.addWidget(self.table)
        self.parameters = QWidget()
        parameter_layout = QHBoxLayout(self.parameters)
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 100)
        self.cycles.setValue(3)
        parameter_layout.addWidget(QLabel('循环次数'))
        parameter_layout.addWidget(self.cycles)
        self.rate = self.spin(1, 0.001, 10000)
        parameter_layout.addWidget(QLabel('升降速率 A/s'))
        parameter_layout.addWidget(self.rate)
        self.hold = self.spin(2, 0.01, 3600)
        parameter_layout.addWidget(QLabel('端点保持 s'))
        parameter_layout.addWidget(self.hold)
        layout.addWidget(self.parameters)
        self.advanced = QGroupBox('高级设置')
        advanced = QFormLayout(self.advanced)
        self.tolerance = self.spin(0.05, 0.000001, 100, 6)
        self.stable = self.spin(1, 0.01, 3600)
        self.timeout = self.spin(30, 0.1, 10000)
        for label, widget in [('跟踪与到位容差 A', self.tolerance), ('连续稳定时间 s', self.stable), ('跟踪 / 端点等待超时 s', self.timeout)]:
            advanced.addRow(label, widget)
        self.advanced.setVisible(False)
        self.toggle = QPushButton('高级设置')
        self.toggle.clicked.connect(lambda: self.advanced.setVisible(not self.advanced.isVisible()))
        layout.addWidget(self.advanced)
        buttons = QHBoxLayout()
        buttons.addWidget(self.toggle)
        buttons.addStretch()
        self.start = QPushButton('开始模拟' if simulate else '开始 cycle')
        self.start.clicked.connect(self.start_cycle)
        self.stop = QPushButton('停止')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_cycle)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        layout.addLayout(buttons)
        self.status = QLabel('请选择磁铁。参数为初始建议值，实际电源需设置合适的速率和容差。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel('停止只终止后续写入，不自动归零或恢复；电源仍可能走到最后下发的设定值。'))

    @staticmethod
    def spin(value, low, high, decimals=3):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setValue(value)
        return widget

    def filter_rows(self):
        for i, m in enumerate(self.magnets):
            self.table.setRowHidden(i, bool(self.filter.currentData() and m.kind != self.filter.currentData()))

    def select_visible(self, checked, all_rows=False):
        for i, m in enumerate(self.magnets):
            if not m.error and (all_rows or not self.table.isRowHidden(i)):
                self.table.item(i, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def start_cycle(self):
        selected = [m for i, m in enumerate(self.magnets) if not m.error and self.table.item(i, 0).checkState() == Qt.Checked]
        try:
            if not selected:
                raise ValueError('请先选择磁铁')
            p = Parameters(self.cycles.value(), self.rate.value(), self.hold.value(), self.tolerance.value(), self.stable.value(), self.timeout.value())
            if not self.simulate:
                require_workflow_write_allowed(self.context, 'magnet_cycle', 'Magnet cycle')
                detail = '\n'.join(f'{m.name}: {m.low:g} → {m.high:g} A' for m in selected)
                if QMessageBox.question(self, '开始磁铁 cycle', f'{self.context.machine.id} / {self.context.control_backend.name}\n{detail}\n循环 {p.cycles} 次，速率 {p.rate:g} A/s，保持 {p.hold:g} s\n正常完成后恢复初始设定。开始？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            self.selected = selected
            for m in selected:
                self.table.item(self.rows[m.name], 7).setText('启动检查')
            self.set_running(True)
            self.worker = Worker(self.context, selected, p, self.simulate)
            self.worker.progress.connect(self.update_row)
            self.worker.outcome.connect(self.finished_message)
            self.worker.finished.connect(self.worker_finished)
            self.worker.start()
            self.status.setText(f'运行中：{len(selected)} 台磁铁；所有勾选项均参与，包括被筛选隐藏的项。')
        except Exception as exc:
            QMessageBox.warning(self, '无法开始', str(exc))

    def set_running(self, running):
        for widget in (self.table, self.parameters, self.advanced, self.select, self.clear, self.start):
            widget.setEnabled(not running)
        self.stop.setEnabled(running)

    def update_row(self, name, initial, setpoint, readback, status):
        row = self.rows[name]
        for col, value in enumerate((initial, setpoint, readback), 4):
            self.table.item(row, col).setText(f'{value:.6g}')
        self.table.item(row, 7).setText(status)

    def stop_cycle(self):
        if self.worker:
            self.worker.stop.set()
            self.stop.setEnabled(False)
            self.status.setText('正在停止；等待当前通信返回…')

    def finished_message(self, message):
        self.status.setText(message)
        for m in self.selected:
            item = self.table.item(self.rows[m.name], 7)
            if item.text() != '完成':
                item.setText('已停止，未恢复')

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_running(False)

    def closeEvent(self, event):
        if self.worker is not None:
            self.stop_cycle()
            event.ignore()
            self.status.setText('正在停止；停止完成后可关闭窗口。')
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description='Magnet cycle; default: offline simulation, VM mappings')
    parser.add_argument('--machine')
    parser.add_argument('--backend', choices=['vm', 'real'], help='EPICS: inherit Control Room/environment/profile; simulation: default vm')
    parser.add_argument('--epics', action='store_true', help='Enable actual PV connections and writes for the selected backend')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    try:
        context = load_app_context('magnet_cycle', machine_id=args.machine, control_backend=args.backend or (None if args.epics else "vm"))
        window = CycleWindow(context, simulate=not args.epics)
    except Exception as exc:
        QMessageBox.critical(None, 'Magnet Cycle', str(exc))
        return 1
    install_qt_window_raise_handler(window)
    window.show()
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
