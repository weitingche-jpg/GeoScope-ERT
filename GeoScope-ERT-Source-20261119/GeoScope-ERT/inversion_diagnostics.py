"""Record actual engine callbacks without changing inversion decisions."""
import math
import numpy as np
from ert_diagnostics import sanitize_history


def _metric(inv,name):
    try:
        value=getattr(inv,name)
        value=float(value() if callable(value) else value)
        return value if math.isfinite(value) else None
    except (AttributeError,TypeError,ValueError,RuntimeError):return None


class InversionDiagnostics:
    def __init__(self):
        self.rows=[];self.errors=[]

    def callback(self,iteration,inv):
        try:
            row=dict(iteration=int(iteration),chi2=_metric(inv,'chi2'),
                rrms_percent=_metric(inv,'relrms'),lam=_metric(inv,'lam'),
                phi_data=_metric(inv,'phiData'),phi_model=_metric(inv,'phiModel'))
            if self.rows and self.rows[-1]['iteration']==row['iteration']:self.rows[-1]=row
            else:self.rows.append(row)
        except Exception as error:
            self.errors.append(type(error).__name__)

    def finish(self,manager,max_iterations):
        inv=manager.inv
        rows=sanitize_history(self.rows)
        if not rows:
            raw=getattr(inv,'chi2History',[])
            rows=sanitize_history([dict(iteration=i,chi2=value,
                rrms_percent=None,lam=_metric(inv,'lam')) for i,value in enumerate(raw)])
        iteration=_metric(inv,'iter')
        chi2=_metric(inv,'chi2')
        if chi2 is not None and chi2<=1:stop='达到当前误差模型下的 chi² 目标；不代表模型唯一或地质解释已验证。'
        elif iteration is not None and iteration>=max_iterations:stop='达到最大迭代次数；需结合残差和误差假设检查拟合。'
        else:stop='求解器提前停止；可能涉及目标函数变化阈值，请结合实际迭代记录检查。'
        coverage=np.array([],dtype=float);coverage_error=''
        try:
            with np.errstate(divide='ignore',invalid='ignore'):
                coverage=np.asarray(manager.coverage(),dtype=float)
            if coverage.ndim!=1 or len(coverage)!=manager.paraDomain.cellCount():
                raise ValueError('coverage/cell count mismatch')
        except Exception as error:
            coverage=np.array([],dtype=float);coverage_error=type(error).__name__+': '+str(error)
        report=dict(schema=1,history=rows,history_source='pyGIMLi callback / chi2History',
            final_chi2=chi2,final_rrms_percent=_metric(inv,'relrms'),
            requested_max_iterations=max_iterations,iterations_completed=iteration,
            stop_description=stop,callback_errors=self.errors,
            coverage_available=bool(len(coverage)),coverage_error=coverage_error,
            coverage_description='pyGIMLi 对数覆盖指标；仅用于相对约束提示，不等同于DOI或可靠探测深度。')
        return report,coverage
