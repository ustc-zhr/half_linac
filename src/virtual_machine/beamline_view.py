"""Compact, occurrence-aware beamline schematic; coordinates are display-only."""
from collections import Counter

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QToolTip
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.patches import Rectangle, Ellipse, Polygon


def family(kind):
    kind = kind.upper()
    if 'DRIF' in kind:
        return 'Drift'
    if 'QUAD' in kind:
        return 'Quadrupole'
    if 'KICK' in kind or 'COR' in kind:
        return 'Corrector'
    if 'RF' in kind or 'CAV' in kind:
        return 'RF cavity'
    if 'WATCH' in kind:
        return 'Screen'
    if 'BEND' in kind:
        return 'Bend'
    return 'Other'


def schematic_spans(elements):
    """Keep every occurrence ordered, reserving room for thin devices."""
    cursor, spans = 0., []
    for element in elements:
        length = max(0., float(element['length']))
        drift = family(element['kind']) == 'Drift'
        width = min(length, .65) if drift else max(.28, min(length, 1.8))
        width = max(width, .08)
        spans.append((cursor, cursor + width, drift and length > .65))
        cursor += width + (.03 if drift else .12)
    return spans


class BeamlineView(QWidget):
    colors = {'Quadrupole': '#31af9a', 'Corrector': '#568dde',
              'RF cavity': '#ae83d0', 'Screen': '#d5a345',
              'Bend': '#d67c51', 'Other': '#86969e', 'Drift': '#86969e'}

    def __init__(self, select, parent=None):
        super().__init__(parent)
        self.select = select
        self.elements, self.spans = [], []
        self.selected = None
        self.dark = True
        self._press = None
        self._dragged = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.caption = QLabel('BEAMLINE  ·  Schematic · Not to scale · Long drifts compressed')
        self.caption.setStyleSheet('font-size: 11px; border: none; background: transparent;')
        row.addWidget(self.caption)
        row.addStretch()
        for text, slot in [('Fit Line', self.fit_line), ('Zoom to Selection', self.zoom_selection)]:
            button = QToolButton()
            button.setText(text)
            button.clicked.connect(slot)
            row.addWidget(button)
            if text == 'Zoom to Selection':
                self.zoom_button = button
                button.setEnabled(False)
        layout.addLayout(row)
        self.figure = Figure(figsize=(10, 1.5))
        self.figure.subplots_adjust(left=.015, right=.985, bottom=.1, top=.93)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        layout.addWidget(self.canvas, 1)
        self.hint = QLabel('Scroll to zoom · Drag to pan · Hover for position · Click to select')
        self.hint.setStyleSheet('font-size: 11px; border: none; background: transparent;')
        layout.addWidget(self.hint)
        self.setMinimumHeight(150)
        self.canvas.mpl_connect('button_press_event', self._down)
        self.canvas.mpl_connect('button_release_event', self._up)
        self.canvas.mpl_connect('motion_notify_event', self._move)
        self.canvas.mpl_connect('scroll_event', self._scroll)
        self.canvas.mpl_connect('resize_event', lambda event: self.draw())
        self.canvas.mpl_connect('figure_leave_event', lambda event: QToolTip.hideText())

    def update_line(self, elements, selected, dark, reset=False):
        self.elements = elements
        self.spans = schematic_spans(elements)
        self.selected, self.dark = selected, dark
        self.zoom_button.setEnabled(selected is not None)
        if reset:
            self.fit_line()
        else:
            self.draw()

    def fit_line(self):
        total = self.spans[-1][1] if self.spans else 1
        self.ax.set_xlim(-total*.015, total*1.015)
        self.draw()

    def zoom_selection(self):
        if self.selected is None or self.selected >= len(self.spans):
            return
        a, b, _ = self.spans[self.selected]
        radius = max(3., (b-a)*3)
        self.ax.set_xlim((a+b)/2-radius, (a+b)/2+radius)
        self.draw()

    def _hit(self, event):
        if event.inaxes is not self.ax or event.xdata is None or abs(event.ydata) > .52:
            return None
        scale = abs(self.ax.get_xlim()[1]-self.ax.get_xlim()[0])/max(self.ax.bbox.width, 1)
        hits = [(abs((a+b)/2-event.xdata), i) for i, (a,b,_) in enumerate(self.spans)
                if a-4*scale <= event.xdata <= b+4*scale]
        return min(hits)[1] if hits else None

    def _down(self, event):
        if event.inaxes is self.ax and event.button == 1:
            self._press = (event.x, self.ax.get_xlim())
            self._dragged = False

    def _up(self, event):
        if self._press is not None and not self._dragged:
            index = self._hit(event)
            if index is not None:
                self.select(index)
        self._press = None

    def _move(self, event):
        if self._press is not None and event.x is not None:
            start, (lo, hi) = self._press
            if abs(event.x-start) > 4:
                self._dragged = True
            if self._dragged:
                QToolTip.hideText()
                delta = (event.x-start)*(hi-lo)/max(self.ax.bbox.width, 1)
                self.ax.set_xlim(lo-delta, hi-delta)
                self.draw()
                return
        index = self._hit(event)
        if index is None:
            QToolTip.hideText()
            return
        e = self.elements[index]
        text = f"{self._name(index)} · {e['kind']}\ns = {e['s']:.4f} m · L = {e['length']:.4f} m"
        QToolTip.showText(self.canvas.mapToGlobal(self.canvas.rect().topLeft()) +
                         QPoint(int(event.x), self.canvas.height()-int(event.y)), text, self.canvas)

    def _scroll(self, event):
        if event.inaxes is not self.ax or event.xdata is None:
            return
        lo, hi = self.ax.get_xlim()
        factor = .8 if event.button == 'up' else 1.25
        total = self.spans[-1][1] if self.spans else 1
        if .5 <= (hi-lo)*factor <= max(total*2, 10):
            self.ax.set_xlim(event.xdata+(lo-event.xdata)*factor, event.xdata+(hi-event.xdata)*factor)
            self.draw()

    def _name(self, index):
        name = self.elements[index]['name']
        if self.counts[name] > 1:
            number = sum(e['name'] == name for e in self.elements[:index+1])
            return f'{name} [{number}/{self.counts[name]}]'
        return name

    def draw(self):
        lo, hi = self.ax.get_xlim()
        self.ax.clear()
        self.ax.set_xlim(lo, hi)
        self.ax.set_ylim(-1, 1.15)
        self.ax.set_axis_off()
        bg, fg = ('#10171c', '#d7e2ea') if self.dark else ('#fffdf9', '#314049')
        self.figure.set_facecolor(bg)
        self.counts = Counter(e['name'] for e in self.elements)
        if not self.elements:
            self.ax.text(.5, .5, 'No beamline loaded', transform=self.ax.transAxes, ha='center', color=fg)
            self.canvas.draw_idle()
            return
        self.ax.plot([0, self.spans[-1][1]], [0, 0], color='#71868f', lw=1, zorder=0)
        px = max(self.canvas.width()*.97, 1)/(hi-lo)
        labels = []
        for i, (e, (a,b,compressed)) in enumerate(zip(self.elements, self.spans)):
            if b < lo or a > hi:
                continue
            kind = family(e['kind'])
            color = self.colors[kind]
            x, w = (a+b)/2, b-a
            selected = i == self.selected
            if selected:
                self.ax.axvspan(a-.06, b+.06, color=self.colors['Quadrupole'], alpha=.18)
                self.ax.plot([x,x], [-.65,.62], color=fg, lw=.8, linestyle=':')
            if kind == 'Drift':
                if compressed and w*px >= 14:
                    self.ax.text(x, 0, '//', color=fg, ha='center', va='center', fontsize=9,
                                 bbox=dict(facecolor=bg, edgecolor='none', pad=0))
            elif kind == 'RF cavity':
                cells = 5 if w*px >= 18 else 1
                for n in range(cells):
                    self.ax.add_patch(Ellipse((a+w*(n+.5)/cells, 0), w/cells*.95, .52, facecolor=color, edgecolor=bg, lw=.6))
            elif kind == 'Quadrupole':
                for y in (-.28, .06):
                    self.ax.add_patch(Rectangle((a,y),w,.22,facecolor=color,edgecolor=color))
            elif kind == 'Corrector':
                self.ax.add_patch(Rectangle((a,-.18),w,.36,facecolor=color,alpha=.35))
                vertical = e['kind'].upper().startswith('V')
                dx, dy = (0, .27) if vertical else (w*.42, 0)
                if w*px >= 12:
                    self.ax.annotate('', xy=(x+dx,dy), xytext=(x-dx,-dy), arrowprops=dict(arrowstyle='<->', color=color, lw=1.4))
                else:
                    self.ax.plot([x,x],[-.16,.16],color=color,lw=1)
            elif kind == 'Screen':
                self.ax.plot([a,b],[-.3,.3], color=color, lw=2)
                self.ax.plot([x,x],[.3,.43],color=color,lw=1)
            elif kind == 'Bend':
                self.ax.add_patch(Polygon([(a,-.24),(b,-.15),(b,.24),(a,.15)],color=color))
            else:
                self.ax.plot([x,x],[-.13,.13],color=color,lw=2)
            if kind != 'Drift' or selected:
                priority = 0 if selected else 1 if kind == 'RF cavity' else 2 if kind in ('Screen','Bend') else 3
                labels.append((priority, i, x))
        occupied = []
        for priority, i, x in sorted(labels):
            text = self._name(i)
            width = len(text)*6.5+14
            center = (x-lo)*px
            if priority != 0 and (center-width/2 < 0 or center+width/2 > self.canvas.width()*.97):
                continue
            if priority != 0 and any(abs(center-c) < (width+w)/2 for c,w in occupied):
                continue
            occupied.append((center,width))
            self.ax.text(x,.66,text,ha='center',va='bottom',fontsize=8,color=fg,
                         fontweight='bold' if priority == 0 else 'normal',clip_on=True)
        # Physical positions are attached to occurrences, never to schematic ticks.
        for i in {0, len(self.elements)-1}:
            e = self.elements[i]
            x = self.spans[i][0]
            if lo <= x <= hi:
                self.ax.text(x,-.75,f"s = {e['s']:.2f} m",color=fg,fontsize=8,
                             ha='left' if i == 0 else 'right')
        legend = '   '.join(f'<span style="color:{color}">● {name}</span>' for name,color in self.colors.items() if name not in ('Drift','Other'))
        self.hint.setText(legend + '   ·   Scroll to zoom · Drag to pan')
        self.canvas.draw_idle()
