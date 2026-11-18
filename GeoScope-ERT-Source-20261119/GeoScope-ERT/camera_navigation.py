"""Reference perspective navigation to visible geometry instead of stale focus.

World-space rays bypass the old near plane, which can hide nearby surfaces
during repeated zooms. The navigation observers never consume editing events.
"""
import weakref
import numpy as np
from vtkmodules.vtkRenderingCore import vtkCellPicker


class StableNavigationController:
    def __init__(self,plotter):
        self._plotter=weakref.ref(plotter)
        self._observer=plotter.iren.style.AddObserver('StartInteractionEvent',self._start,1.0)
        self._end_observer=plotter.iren.style.AddObserver('EndInteractionEvent',self._clip,1.0)

    def _start(self,*args):
        self.sync_focus()

    def _clip(self,*args):
        plotter=self._plotter()
        if plotter is None or getattr(plotter,'_closed',True):return
        camera=plotter.camera
        if camera.parallel_projection:return
        near,far=camera.clipping_range
        if not np.isfinite([near,far,camera.distance]).all() or camera.distance<=0:return
        desired=max(min(near,camera.distance*.01),far*1e-10)
        if 0<desired<far:camera.clipping_range=(desired,far)

    def sync_focus(self):
        plotter=self._plotter()
        if plotter is None or getattr(plotter,'_closed',True):return False
        camera=plotter.camera
        if camera.parallel_projection:return False
        position=np.array(camera.position)
        direction=np.array(camera.GetDirectionOfProjection())
        if not np.isfinite([position,direction]).all():return False
        renderer=plotter.renderer
        width,height=renderer.GetSize()
        if width<2 or height<2:return False
        origin_x,origin_y=renderer.GetOrigin()
        candidates=[]
        for actor in tuple(renderer.actors.values()):
            if not actor.IsA('vtkActor') or not actor.GetVisibility():continue
            mapper=actor.GetMapper()
            dataset=mapper.GetInput() if mapper is not None and hasattr(mapper,'GetInput') else None
            bounds=actor.GetBounds()
            if (dataset is not None and dataset.GetNumberOfCells()>0 and bounds is not None
                    and np.isfinite(bounds).all()):candidates.append(actor)
        if not candidates:return False
        picker=vtkCellPicker();picker.SetTolerance(.001);picker.PickFromListOn()
        permissions=[]
        farthest=max(np.linalg.norm(position-np.array([x,y,z]))
                     for actor in candidates
                     for x in actor.GetBounds()[0:2]
                     for y in actor.GetBounds()[2:4]
                     for z in actor.GetBounds()[4:6])*1.05
        try:
            for actor in candidates:
                permissions.append((actor,actor.GetPickable()))
                actor.PickableOn();picker.AddPickList(actor)
            for dx,dy in ((0,0),(-.2,0),(.2,0),(0,-.2),(0,.2)):
                ray=direction
                if dx or dy:
                    renderer.SetDisplayPoint(origin_x+width*(.5+dx),origin_y+height*(.5+dy),.5)
                    renderer.DisplayToWorld()
                    world=np.array(renderer.GetWorldPoint())
                    if not np.isfinite(world).all() or abs(world[3])<1e-12:continue
                    ray=world[:3]/world[3]-position
                    length=np.linalg.norm(ray)
                    if not np.isfinite(length) or length<=0:continue
                    ray=ray/length
                if not picker.Pick3DPoint(position,position+ray*farthest,renderer):continue
                point=np.asarray(picker.GetPickPosition())
                depth=float(np.dot(point-position,direction))
                if np.isfinite(depth) and depth>max(np.linalg.norm(position),1)*1e-12:
                    camera.focal_point=position+depth*direction
                    self._clip()
                    return True
            return False
        finally:
            for actor,permission in permissions:actor.SetPickable(permission)


def install_navigation(plotter):
    controller=getattr(plotter,'_stable_navigation',None)
    if controller is None:
        controller=StableNavigationController(plotter)
        plotter._stable_navigation=controller
    return controller
