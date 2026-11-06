# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Test di contratto delle porte.

Ogni modulo `contract_*.py` ha classi base (`*Contract`) con i test; per provare
un'implementazione si deriva una classe `Test...` che fornisce la fixture `harness`
(vedi `harness.py`). `test_fakes.py` le applica ai fake in memoria; i test degli adattatori
reali le applicano a SQLite e al disco.
"""
