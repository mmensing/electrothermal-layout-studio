# SPDX-License-Identifier: MPL-2.0
"""Spawn-isolated compute jobs. The GUI can cancel Gmsh or a sparse factorization.

Only trusted in-memory dataclasses cross the multiprocessing queue. User project
archives are loaded by the versioned JSON/NPZ persistence layer, never pickle.
"""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import traceback

from PySide6.QtCore import QObject, QTimer, Signal


def _worker(messages, project, operation, argument, execution=None):
    from etlayout.simulation.execution import execution_context
    try:
        with execution_context(execution):
            _execute_worker(messages, project, operation, argument)
    except BaseException as error:
        messages.put(("error", (str(error), traceback.format_exc())))


def _execute_worker(messages, project, operation, argument):
    def progress(fraction, message):
        messages.put(("progress", (float(fraction), str(message))))
    try:
        verify_source = operation in {"prepare", "hierarchy", "mesh", "validate", "layout:load", "layout:edit", "refinement-guidance"}
        if verify_source:
            from etlayout.simulation.runner import _verify_layout
            _verify_layout(project)
        if operation in {"layout:load", "layout:edit"}:
            from etlayout.geometry.polygon_edit import edit_polygon, editable_polygons
            output = (editable_polygons(project, progress=progress) if operation == "layout:load" else
                      edit_polygon(project, *argument, progress=progress))
        elif operation.startswith("power:"):
            from etlayout.power.workflows import execute_operation
            output = execute_operation(project, operation.split(":", 1)[1], argument, progress=progress)
        elif operation in {"prepare", "import", "hierarchy"}:
            from etlayout.geometry.layout import LayoutDocument
            from etlayout.geometry.prepare import prepare_geometry
            from etlayout.io.project import file_hash
            path = argument if operation == "import" else project.layout.path
            document = LayoutDocument(path) if path else None
            if operation == "hierarchy":
                output = document.instances_page(**argument)
            else:
                metadata = None if document is None else {
                    "path": str(document.path), "cells": document.cells, "top_cells": document.top_cells,
                    "layers": document.layers, "bounds": document.bounds, "dbu_um": document.dbu_um,
                    "cell_bounds": {cell: document.cell_bounds(cell) for cell in document.cells},
                    "source_sha256": file_hash(document.path),
                }
                if operation == "import":
                    output = metadata
                else:
                    masks = {}
                    geometry = prepare_geometry(project, document=document, masks_out=masks, progress=progress) if project.layers else None
                    if document and not project.layers:
                        masks = document.extract(project.layout.cell, project.layout.instance_path, progress=progress)
                    output = {"geometry": geometry, "masks": masks, "document": metadata}
        elif operation == "mesh":
            from etlayout.geometry.prepare import prepare_geometry
            from etlayout.meshing.generate import generate_mesh
            geometry = prepare_geometry(project)
            mesh = generate_mesh(project, geometry, progress=progress)
            output = (geometry, mesh)
        elif operation == "validate":
            from etlayout.connectivity.analyze import analyze_connectivity
            from etlayout.geometry.prepare import prepare_geometry
            from etlayout.technology.validation import validate_project
            options = argument or {}
            validate_project(project, initial_state_supplied=options.get("initial_state_supplied", False))
            geometry = prepare_geometry(project)
            output = (geometry, analyze_connectivity(project, geometry, start_time=options.get("start_time", 0.)))
        elif operation == "refinement-guidance":
            from etlayout.geometry.prepare import prepare_geometry
            from etlayout.meshing.refinement import suggest_refinements
            geometry = prepare_geometry(project, progress=progress)
            plan = suggest_refinements(project, geometry=geometry, progress=progress, **(argument or {}))
            output = {"plan": plan.to_dict(), "geometry": geometry}
        elif operation == "sweep":
            from etlayout.simulation.sweep import run_sweep
            options = dict(argument) if isinstance(argument, dict) else {"spec": argument}
            output = run_sweep(project, options.pop("spec"), progress=progress, **options)
        elif operation == "compare":
            from etlayout.simulation.sweep import compare_projects
            options = dict(argument) if isinstance(argument, dict) else {"projects": argument}
            output = compare_projects(options.pop("projects"), mode=project.solver.mode, progress=progress, **options)
        elif operation in {"convergence", "local-refine"}:
            from etlayout.simulation.sweep import mesh_refinement_study
            if isinstance(argument, dict):
                options = {key: value for key, value in argument.items() if key != "sizes"}
                output = mesh_refinement_study(project, argument["sizes"], progress=progress, **options)
            else:
                output = mesh_refinement_study(project, argument, progress=progress)
        elif operation == "time_convergence":
            from etlayout.simulation.sweep import time_step_convergence_study
            if isinstance(argument, dict):
                options = {key: value for key, value in argument.items() if key != "steps"}
                output = time_step_convergence_study(project, argument["steps"], progress=progress, **options)
            else:
                output = time_step_convergence_study(project, argument, progress=progress)
        else:
            from etlayout.simulation.runner import run_project
            output = run_project(project, progress=progress, **(argument or {}))
        if verify_source:
            # Reject a source replaced while the background operation was reading
            # it as well as one changed before launch. Import is an explicit new
            # source selection and intentionally does not retain the old hash.
            _verify_layout(project)
        messages.put(("result", output))
    except BaseException as error:
        messages.put(("error", (str(error), traceback.format_exc())))


class ComputeJob(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(self, project, operation="solve", argument=None, parent=None, *, execution=None):
        super().__init__(parent)
        context = mp.get_context("spawn")
        self.messages = context.Queue()
        self.process = context.Process(target=_worker, args=(self.messages, project, operation, argument, execution))
        # Deserialize large histories away from the GUI thread. This local queue
        # transfers object references only; all widget updates still run in poll.
        self._received = queue.Queue()
        self._receiver_stop = threading.Event()
        self._receiver = None
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.poll)
        self.done = False

    def start(self):
        self.process.start()
        # Only the child sends. Closing our inherited sending endpoint lets a
        # receiver blocked in a partial large transfer observe EOF on cancel.
        self.messages._writer.close()
        self._receiver = threading.Thread(target=self._receive, daemon=True, name="etlayout-results")
        self._receiver.start()
        self.timer.start()

    def _receive(self):
        while not self._receiver_stop.is_set():
            try:
                message = self.messages.get(timeout=.1)
            except queue.Empty:
                if self.process.is_alive():
                    continue
                # The child can flush its final messages and exit after get()
                # times out. Normal exit joins its Queue feeder, so check the
                # pipe again before treating the completed worker as empty.
                try:
                    message = self.messages.get_nowait()
                except (queue.Empty, EOFError, OSError, ValueError):
                    return
            except (EOFError, OSError, ValueError):
                return
            self._received.put(message)
            if message[0] in {"result", "error"}:
                return

    def poll(self):
        try:
            while True:
                kind, payload = self._received.get_nowait()
                if kind == "progress":
                    self.progress.emit(*payload)
                elif kind == "result":
                    self.done = True
                    self.timer.stop()
                    self.finished.emit(payload)
                    self.cleanup()
                    return
                elif kind == "error":
                    self.done = True
                    self.timer.stop()
                    self.failed.emit(*payload)
                    self.cleanup()
                    return
        except queue.Empty:
            pass
        if (not self.process.is_alive() and self.process.exitcode is not None and not self.done
                and (self._receiver is None or not self._receiver.is_alive()) and self._received.empty()):
            self.done = True
            self.timer.stop()
            self.failed.emit(f"Worker exited unexpectedly (code {self.process.exitcode}).", "")
            self.cleanup()

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        if self.process.is_alive():
            import psutil
            try:
                parent = psutil.Process(self.process.pid)
                # Freeze the coordinator before enumeration so it cannot launch
                # another study point between collecting children and shutdown.
                parent.suspend()
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
                psutil.wait_procs(children, timeout=1.)
            except psutil.NoSuchProcess:
                pass
            finally:
                if self.process.is_alive():
                    self.process.terminate()
            self.process.join(timeout=2.)
        self.cancelled.emit()
        self.cleanup()

    def cleanup(self):
        self._receiver_stop.set()
        if self.process.is_alive():
            self.process.join(timeout=.1)
        if self._receiver is not None:
            self._receiver.join(timeout=.2)
        self.messages.close()
        self.messages.cancel_join_thread()
