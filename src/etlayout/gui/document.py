# SPDX-License-Identifier: MPL-2.0
"""One transaction boundary for guided, expert and integrated study edits."""
from copy import deepcopy


class WorkspaceDocument:
    def __init__(self, window):
        self.window = window
        self.revision = 0

    @property
    def project(self):
        context = self.window.inspection_context
        return context["project"] if context else self.window.project

    def transact(self, label, mutation):
        window = self.window
        editor = getattr(getattr(window, "guided", None), "layout_editor", None)
        if editor and editor.dirty and not editor.finish():
            raise ValueError("Resolve the pending layout vertices before changing the project.")
        power = getattr(getattr(window, "guided", None), "power", None)
        if window.job or window.inspection_context or (power and power.job) or (editor and editor.job):
            raise ValueError("Return to the working setup and finish the active job before editing.")
        candidate = deepcopy(self.project)
        replacement = mutation(candidate)
        if replacement is not None:
            candidate = replacement
        if candidate.to_dict() == self.project.to_dict():
            return False
        window.before_edit()
        window.project = candidate
        self.revision += 1
        window.changed()
        window.log(label)
        return True
