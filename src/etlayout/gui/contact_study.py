# SPDX-License-Identifier: MPL-2.0
"""Read-only transfer-length fit from real generated finite-contact coupons."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


class ContactStudyDialog(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Finite-contact transfer-length study')
        self.resize(860,600)
        layout=QVBoxLayout(self)
        intro=QLabel('Four actual finite-contact coupons separate sheet, contact and external feed resistance. '
            'Saved gap folders contain editable layouts, projects and calculated fields. Fit residuals describe numerical consistency; they are not experimental calibration.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        fit=report['fit']
        form=QFormLayout()
        for key,label,factor,unit in (
            ('Rs_ohm_per_square','Sheet resistance',1.,'Ω/□'),
            ('Rc_ohm','One contact resistance',1.,'Ω'),
            ('rho_c_ohm_m2','Specific contact resistivity',1.,'Ω·m²'),
            ('LT_m','Transfer length',1e6,'µm')):
            value=QLabel(f'{fit[key]*factor:.6g} {unit}')
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label,value)
        layout.addLayout(form)
        tree=QTreeWidget()
        tree.setHeaderLabels(['Evidence / quantity','Value'])
        tree.setColumnWidth(0,350)
        def add(parent,key,value):
            item=QTreeWidgetItem(parent,[str(key).replace('_',' '),''])
            if isinstance(value,dict):
                for child,detail in value.items():
                    add(item,child,detail)
            elif isinstance(value,list):
                for index,detail in enumerate(value):
                    add(item,str(index+1),detail)
            else:
                item.setText(1,json.dumps(value,ensure_ascii=False) if not isinstance(value,str) else value)
        for key,value in report.items():
            add(tree,key,value)
        tree.expandToDepth(0)
        tree.setAccessibleName('Actual coupon measurements, analytical reference and fit uncertainty')
        layout.addWidget(tree)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

class EngineeringObservationsDialog(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Device and fixture engineering observations')
        self.resize(860,600)
        layout=QVBoxLayout(self)
        note=QLabel('Values belong to the selected calculated state. P/I² is an operating-point quantity. '
            'Feed-deembedded power excludes all external feed loss; modeled thermal input still includes the declared local fraction. '
            'GaN sheet current and metal volumetric current density remain distinct. '+report['resistance_applicability'])
        note.setWordWrap(True)
        layout.addWidget(note)
        form=QFormLayout()
        for key,label,factor,unit in (
            ('current_A','Delivered current',1.,'A'),
            ('terminal_resistance_ohm','Terminal operating-point resistance',1000.,'mΩ'),
            ('feed_deembedded_internal_resistance_ohm','Internal resistance with feed removed',1000.,'mΩ'),
            ('total_input_power_W','Total input power',1.,'W'),
            ('internal_power_W','Internal electrical loss',1.,'W'),
            ('metal_sheet_power_W','Routing and other metal loss',1.,'W'),
            ('channel_access_power_W','Lateral channel and access loss',1.,'W'),
            ('intrinsic_device_power_W','Distributed intrinsic device loss',1.,'W'),
            ('via_power_W','Physical via loss',1.,'W'),
            ('external_feed_power_W','External feed electrical loss',1.,'W'),
            ('locally_deposited_feed_W','Feed heat deposited in the sheets',1.,'W'),
            ('excluded_external_fixture_heat_W','External fixture heat outside model',1.,'W'),
            ('active_device_current_density_A_cm2','Active-device normal loading',1.,'A/cm²'),
            ('passive_kelvin_offset_from_source_reservoir_V','Passive Kelvin offset from power-source reservoir',1000.,'mV'),
            ('device_source_potential_spread_V','Local finger source-potential spread',1000.,'mV'),
            ('finger_current_spread_relative_to_mean','Finger current range / mean',100.,'%')):
            value=report.get(key)
            if value is None:
                continue
            if unit == 'W' and 0 < abs(value) < .001:
                factor,unit=1e6,'µW'
            elif unit == 'W' and 0 < abs(value) < 1.:
                factor,unit=1000.,'mW'
            elif unit == 'A' and 0 < abs(value) < 1.:
                factor,unit=1000.,'mA'
            field=QLabel(f'{value*factor:.6g} {unit}')
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label,field)
        layout.addLayout(form)
        tree=QTreeWidget()
        tree.setHeaderLabels(['Device / quantity','Value'])
        tree.setColumnWidth(0,350)
        def add(parent,key,value):
            item=QTreeWidgetItem(parent,[str(key).replace('_',' '),''])
            if isinstance(value,dict):
                for child,detail in value.items():
                    add(item,child,detail)
            else:
                item.setText(1,f'{value:.6g}' if isinstance(value,float) else 'Unavailable' if value is None else str(value))
        for key in ('banks','heat_deposition'):
            add(tree,key,report[key])
        tree.expandToDepth(0)
        layout.addWidget(tree)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
