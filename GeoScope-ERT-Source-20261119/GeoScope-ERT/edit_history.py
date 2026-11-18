"""Bounded geometry snapshots. Physics results are deliberately not commands."""
import copy
import json


def geometry_key(state):
    return json.dumps({**{key:state[key] for key in ('line','points','points_follow_line')},'line_endpoint_overrides':state.get('line_endpoint_overrides') or {},'ert_packet':state.get('ert_packet'),'simulation_enabled':state.get('simulation_enabled',False),'region_state':state.get('region_state'),'survey_state':state.get('survey_state')},
                      sort_keys=True,ensure_ascii=False)


class GeometryHistory:
    def __init__(self,limit=100):
        self.limit=limit
        self.past=[]
        self.future=[]

    @property
    def undo_count(self):
        return len(self.past)

    @property
    def redo_count(self):
        return len(self.future)

    def record(self,before,after,label):
        if geometry_key(before)==geometry_key(after):
            return False
        self.past.append((copy.deepcopy(before),copy.deepcopy(after),label))
        self.past=self.past[-self.limit:]
        self.future.clear()
        return True

    def undo(self):
        if not self.past:
            return None
        command=self.past.pop()
        self.future.append(command)
        return copy.deepcopy(command[0]),command[2]

    def redo(self):
        if not self.future:
            return None
        command=self.future.pop()
        self.past.append(command)
        return copy.deepcopy(command[1]),command[2]

    def clear(self):
        self.past.clear()
        self.future.clear()
