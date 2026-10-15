# SPDX-License-Identifier: MPL-2.0
"""Small shared Apply/Discard/Stay policy; independent of widgets and projects."""


def resolve_pending(*, changed, choose, apply, discard):
    if not changed:
        return True
    decision = choose()
    if decision == "apply":
        return bool(apply())
    if decision == "discard":
        discard()
        return True
    return False


def ask_pending(parent, title, text):
    from PySide6.QtWidgets import QMessageBox
    prompt = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    apply = prompt.addButton("Apply", QMessageBox.ButtonRole.AcceptRole)
    discard = prompt.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
    prompt.addButton("Stay", QMessageBox.ButtonRole.RejectRole)
    prompt.exec()
    return "apply" if prompt.clickedButton() is apply else "discard" if prompt.clickedButton() is discard else "stay"
