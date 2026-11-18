"""Read-only plots of actual ERT output, including legacy-case limitations."""
import copy
import json
import numpy as np
from PySide6 import QtWidgets as Q
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.colors import LogNorm
from ert_diagnostics import response_diagnostics,pseudosection_positions,sanitize_history
from quality_dialog import theme_axes


class DiagnosticsDialog(Q.QDialog):
    def __init__(self,owner,arrays,metadata=None,details=None,packet=None,stale=False):
        super().__init__(owner)
        self.arrays={k:np.array(v,copy=True) for k,v in arrays.items()}
        self.metadata=copy.deepcopy(metadata or {});self.details=copy.deepcopy(details or {})
        self.packet=copy.deepcopy(packet);self.stale=stale
        self.setWindowTitle('电法反演诊断')
        screen=Q.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1200,screen.width()-40),min(850,screen.height()-60))
        a=self.arrays
        self.source_mapping_available='source_row_indices' in a or bool(packet is not None
            and len(packet.get('rows',[]))==len(a['observed'])
            and np.array_equal([r.get('rhoa') for r in packet['rows']],a['observed']))
        self.comparison=response_diagnostics(a['observed'],a['response'],a.get('source_row_indices'))
        layout=Q.QVBoxLayout(self)
        chi2=self.metadata.get('chi2')
        chi2_text=f'{chi2:.3g}' if isinstance(chi2,(int,float)) and np.isfinite(chi2) else '未记录'
        title=Q.QLabel(('旧结果已过期：以下是原计算时的数据，不能冒充当前观测。\n' if stale else '')+
            f"实际参与计算 {len(a['observed'])} 条 · χ² {chi2_text} · RRMS {self.comparison['rrms_percent']:.2f}%\n"
            'χ² 需结合误差模型解释；拟合指标不证明模型唯一。覆盖度为相对约束提示，不是可靠探测深度或DOI。')
        title.setWordWrap(True);layout.addWidget(title)
        if stale:title.setStyleSheet('color:#ffc46b;')
        self.tabs=Q.QTabWidget();layout.addWidget(self.tabs,1)
        self.tabs.setStyleSheet('QTabBar::tab {padding:8px 12px; margin-right:6px;}'
            'QTabBar::tab:selected {background:#1d3855; color:#e2f1ff;}')
        self.figures=[];self.messages=[]
        for name,draw in [('拟合与残差',self.draw_fit),('观测分布',self.draw_pseudosection),
                          ('收敛过程',self.draw_history),('覆盖度',self.draw_coverage)]:
            page=Q.QWidget();box=Q.QVBoxLayout(page)
            figure=Figure(facecolor='#0d121a',figsize=(10,4));canvas=FigureCanvasQTAgg(figure)
            box.addWidget(NavigationToolbar2QT(canvas,self));box.addWidget(canvas,1)
            self.figures.append(figure)
            try:draw(figure)
            except (ValueError,IndexError,KeyError,TypeError) as error:
                figure.clear();self.no_data(figure,'Diagnostic unavailable')
                self.messages.append(name+'不可用：'+str(error))
            for axes in figure.axes:axes.tick_params(which='both',colors='#aebed2',labelsize=8)
            canvas.draw_idle();self.tabs.addTab(page,name)
        self.add_table()
        self.status=Q.QLabel(self.details.get('stop_description','旧案例未记录停止原因或完整迭代过程。'))
        self.status.setWordWrap(True);layout.addWidget(self.status)
        missing=[]
        if not self.details.get('history'):missing.append('旧结果未记录收敛历史，不生成补画曲线')
        if not len(a.get('coverage_log10',[])):missing.append('该结果未记录覆盖度')
        if not self.source_mapping_available:missing.append('原行映射未记录，明细采用计算顺序')
        if self.packet and self.packet.get('array')=='dipole-dipole':
            missing.append('偶极—偶极分布图纵轴为隔离系数 n=gap/a（并按 a 分组标注），不是地下真实深度')
        if missing:self.status.setText(self.status.text()+'\n'+'；'.join(missing))
        if self.messages:self.status.setText(self.status.text()+'\n'+'；'.join(self.messages))
        row=Q.QHBoxLayout();row.addStretch()
        export=Q.QPushButton('导出诊断记录');export.clicked.connect(self.export_record);row.addWidget(export)
        close=Q.QPushButton('关闭');close.clicked.connect(self.accept);row.addWidget(close);layout.addLayout(row)

    def no_data(self,figure,text):
        axes=figure.add_subplot(111);theme_axes(axes)
        axes.text(.5,.5,text,ha='center',va='center',transform=axes.transAxes,color='#c5d4e6',wrap=True)
        axes.set_xticks([]);axes.set_yticks([])

    def draw_fit(self,figure):
        fit,residual=figure.subplots(1,2);d=self.comparison
        observed=np.asarray(d['observed']);response=np.asarray(d['response'])
        low=min(observed.min(),response.min());high=max(observed.max(),response.max())
        if low==high:low*=.8;high*=1.2
        fit.loglog(observed,response,'o',ms=4,color='#6fb2ff');fit.plot([low,high],[low,high],'--',color='#a1b2c8')
        fit.set_xlabel('Observed apparent resistivity (ohm m)');fit.set_ylabel('Computed (ohm m)')
        residual.axhline(0,color='#899bb2',lw=1)
        residual.scatter(np.asarray(d['source_indices'])+1,d['relative_residual_percent'],s=16,color='#f9b66b')
        residual.set_xlabel('Original observation index' if self.source_mapping_available else 'Computation row index')
        residual.set_ylabel('(computed-observed)/observed (%)')
        for axes in (fit,residual):theme_axes(axes)
        figure.tight_layout(pad=1.6)

    def draw_pseudosection(self,figure):
        if self.packet is None or self.packet.get('array') not in ('wenner','dipole-dipole'):
            self.no_data(figure,'No supported source snapshot; a pseudosection cannot be reconstructed.');return
        array=self.packet.get('array','wenner')
        a=self.arrays;indices=a.get('source_row_indices')
        if indices is None:
            if len(self.packet['rows'])!=len(a['observed']):
                self.no_data(figure,'Original row mapping was not saved in this legacy result.');return
            indices=list(range(len(a['observed'])))
        positions=pseudosection_positions(self.packet,[int(i) for i in indices])
        if array=='dipole-dipole':
            # DD levels are isolation factors n=gap/a, not depths.  The actual
            # dipole length a is stated in each panel title.
            from ert_arrays import dd_plot_levels
            y,ticks,tick_labels,grouped=dd_plot_levels(positions['dipole_length_m'],positions['separation_n'])
            ylabel='Groups (a, n), not depth' if grouped else 'Dipole separation n=gap/a (not depth)'
            lengths=sorted(set(positions['dipole_length_m']))
            prefix='Dipole-dipole, a=' + '/'.join(f'{value:g}' for value in lengths) + ' m; '
            axes=figure.subplots(1,3)
        else:
            y=positions['spacing_m'];ylabel='Spacing a (m), not depth';prefix=''
            axes=figure.subplots(1,3)
        low=float(min(a['observed'].min(),a['response'].min()));high=float(max(a['observed'].max(),a['response'].max()))
        if low==high:low*=.8;high*=1.2
        for axis,values,title in zip(axes[:2],[a['observed'],a['response']],['Observed','Computed']):
            artist=axis.scatter(positions['center_m'],y,c=values,
                norm=LogNorm(low,high),cmap='turbo_r',marker='s',s=32)
            axis.set_title(prefix+title+' (ohm m)');cb=figure.colorbar(artist,ax=axis,pad=.02)
            cb.ax.tick_params(colors='#bdccdd',labelsize=7)
        residual=np.asarray(self.comparison['relative_residual_percent']);limit=max(1,float(np.max(np.abs(residual))))
        artist=axes[2].scatter(positions['center_m'],y,c=residual,cmap='coolwarm',vmin=-limit,vmax=limit,marker='s',s=32)
        axes[2].set_title('Relative residual (%)');cb=figure.colorbar(artist,ax=axes[2],pad=.02)
        cb.ax.tick_params(colors='#bdccdd',labelsize=7)
        for axis in axes:
            axis.set_xlabel('Local distance (m)');axis.set_ylabel(ylabel);axis.invert_yaxis();theme_axes(axis)
            if array=='dipole-dipole':
                axis.set_yticks(ticks,tick_labels)
        figure.tight_layout(pad=1.4)

    def draw_history(self,figure):
        history=sanitize_history(self.details.get('history',[]))
        if not history:
            self.no_data(figure,'No iteration history in this legacy result. Re-run inversion to record it.');return
        chi,rms=figure.subplots(1,2)
        for axis,key,label in [(chi,'chi2','Solver chi2'),(rms,'rrms_percent','Solver RRMS (%)')]:
            values=[r for r in history if r.get(key) is not None]
            if values:
                axis.plot([r['iteration'] for r in values],[r[key] for r in values],'-o',color='#70b4ff',ms=4)
                if key=='chi2' and all(r[key]>0 for r in values):axis.set_yscale('log')
            else:axis.text(.5,.5,'Not recorded',ha='center',transform=axis.transAxes,color='#bdccdd')
            axis.set_xlabel('Actual solver iteration');axis.set_ylabel(label);theme_axes(axis)
        chi.axhline(1,color='#f2bd70',linestyle='--',lw=1)
        figure.tight_layout(pad=1.6)

    def draw_coverage(self,figure):
        a=self.arrays;values=a.get('coverage_log10')
        if values is None or len(values)!=len(a['cells']) or not np.any(np.isfinite(values)):
            self.no_data(figure,'Coverage unavailable in this result. It is not a depth-of-investigation estimate.');return
        axis=figure.add_subplot(111);polygons=a['nodes'][a['cells']]
        collection=PolyCollection(polygons,cmap='viridis',edgecolors='none')
        collection.set_array(np.ma.masked_invalid(values));axis.add_collection(collection);axis.autoscale_view()
        axis.set_xlabel('Local distance (m)');axis.set_ylabel('Model z (m)')
        axis.set_title('log10 coverage: relative constraint indicator, not DOI')
        colorbar=figure.colorbar(collection,ax=axis);colorbar.ax.tick_params(colors='#bdccdd',labelsize=8)
        theme_axes(axis);figure.tight_layout(pad=1.6)

    def add_table(self):
        d=self.comparison;table=Q.QTableWidget(len(d['observed']),4)
        table.setHorizontalHeaderLabels(['原观测序号' if self.source_mapping_available else '计算序号（原行未记录）','观测 Ω·m','计算 Ω·m','相对残差 %'])
        table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(Q.QHeaderView.Stretch)
        for i,values in enumerate(zip(np.asarray(d['source_indices'])+1,d['observed'],d['response'],d['relative_residual_percent'])):
            for j,value in enumerate(values):table.setItem(i,j,Q.QTableWidgetItem(f'{value:.6g}'))
        self.tabs.addTab(table,'计算明细');self.table=table

    def export_record(self):
        path,_=Q.QFileDialog.getSaveFileName(self,'导出诊断记录','','JSON (*.json)')
        if not path:return
        try:
            report=dict(metadata=self.metadata,solver_diagnostics=self.details,comparison=self.comparison,
                result_stale=self.stale,source_row_mapping_available=self.source_mapping_available,
                note='相对残差非求解器加权chi2；覆盖度不等于DOI；计算网格底界不等于可靠探测深度。')
            with open(path,'w',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False)
            self.status.setText('诊断记录已导出。')
        except (OSError,ValueError) as error:self.status.setText(str(error))
