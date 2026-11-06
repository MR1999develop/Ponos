# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Testi della barra di ogni grafico (esporta e strumenti di Plotly), uguali in tutti i grafici."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from ponos_coach.ui.chart_payload import ChartChrome

# Contesto "Charts" scritto come letterale: lupdate non segue gli alias.


def chart_chrome() -> ChartChrome:
    """I titoli dei bottoni della barra, nella lingua dell'interfaccia."""
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
