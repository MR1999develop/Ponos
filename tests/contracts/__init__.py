# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract tests for the ports.

Each `contract_*.py` module has base classes (`*Contract`) with the tests; to try
an implementation, derive a `Test...` class that provides the `harness` fixture
(see `harness.py`). `test_fakes.py` applies them to the in-memory fakes; the real
adapters' tests apply them to SQLite and to the disk.
"""
