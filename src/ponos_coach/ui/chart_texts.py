# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Toolbar texts of every chart (Plotly export and tools), the same in all charts."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from ponos_coach.ui.chart_payload import ChartChrome

# Context "Charts" written as a literal: lupdate does not follow aliases.


def chart_chrome() -> ChartChrome:
    """The toolbar button titles, in the interface language."""
    return ChartChrome(
        export_png=QCoreApplication.translate("Charts", "Save chart as PNG"),
        export_svg=QCoreApplication.translate("Charts", "Save chart as SVG"),
        modebar={
            "Zoom": QCoreApplication.translate("Charts", "Zoom"),
            "Pan": QCoreApplication.translate("Charts", "Pan"),
            "Zoom in": QCoreApplication.translate("Charts", "Zoom in"),
            "Zoom out": QCoreApplication.translate("Charts", "Zoom out"),
            "Autoscale": QCoreApplication.translate("Charts", "Autoscale"),
            "Reset axes": QCoreApplication.translate("Charts", "Reset axes"),
            "Toggle Spike Lines": QCoreApplication.translate("Charts", "Toggle spike lines"),
            "Show closest data on hover": QCoreApplication.translate(
                "Charts", "Show closest data on hover"
            ),
            "Compare data on hover": QCoreApplication.translate("Charts", "Compare data on hover"),
        },
    )
