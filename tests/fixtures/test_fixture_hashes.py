# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The versioned fixtures do not change byte for byte: the tests of other packages depend on them."""

from __future__ import annotations

import hashlib
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

# SHA-256 of the phase 0 fixtures. New fixtures are added; these are not touched.
FROZEN = {
    "fit-ostili/bad_file_crc.fit": "35fced8cc32012fa29da972f30a21bc5eecea5725257b5ae169a0294e8b58cb0",
    "fit-ostili/bad_header_crc.fit": "a95d1be0e16fa25c39aeebe7e3db4125f6f36fd472872f573e68401a3e871ac6",
    "fit-ostili/bad_signature.fit": "d11783ecb2681c5f5f6e1f7441f791cb15ec92245cc630e31a945104fe06d029",
    "fit-ostili/empty.fit": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "fit-ostili/header_size_huge.fit": "70a5338a37538139b510ff60b5d584f5490321c28bac4fe6307479cea4f7fade",
    "fit-ostili/header_size_max_uint32.fit": "c90fe25cdc8d7b41cda9d1a6d3296361c0b22d2801392da5ada04eb096da718f",
    "fit-ostili/header_size_too_large.fit": "e571301dec2c9c6d5242b1e75fd58a1fa48f24566c15f19b822c2704a0203d3c",
    "fit-ostili/header_size_too_small.fit": "9f8f6c10a4c25495133f016dac57b31ae59e38e0f844d18ad62e01d74d563815",
    "fit-ostili/huge_field_definition.fit": "6b6d93a6508f94598b8176b4615d794e7c0c51fbfa3fab508084b9eb3abc0aff",
    "fit-ostili/truncated_in_header.fit": "0ec8f356a3ae71c85a0470d22bd74aa820aa4727e0bf924e5e465cc1150528c2",
    "fit-ostili/truncated_mid_record.fit": "34a0e6903fb9508680f4c3251c2e6467bb051527adda43729bdbf2b9d13892f0",
    "fit-ostili/truncated_no_crc.fit": "483dc828bbac43beef0a17cd0642870db53a66a5e4fa1a48bc9b4c425a9bffa0",
    "fit-ostili/undefined_local_message.fit": "248876d945dd8e24317b35bf6b6924f719588b00ad3bbd6eb4e5ceb701b32c7f",
    "fit/bike_easy.fit": "8233842e0fd4b4c0e147cf1d960eabccd9161e72588c01c36d2ea103f7dceeea",
    "fit/copy_of_run_constant_160.fit": "723f8f478e917cc12eecb6517b49c0daed085594d794bd3d7a1a9c2671232a19",
    "fit/hike_24h.fit": "5e93b3e3aee66f26bbde185537df500ad9305e3a5ce2182be78aff394e6546fa",
    "fit/run_constant_160.fit": "723f8f478e917cc12eecb6517b49c0daed085594d794bd3d7a1a9c2671232a19",
    "fit/run_hr_gaps.fit": "90629e34a26fd848c9038cd3494c0d88ca951bb341a00a1f08b6d489d6e0c0e3",
    "fit/run_hr_out_of_range.fit": "a2047e1c7ebf0b3cac0a7c1cc77f9ecfe7570c8c27c4a304c3c312064ea909ca",
    "fit/run_intervals.fit": "c5c0066e5b806a0e9edd27c440cb7b849f34d4ffeadcfe22020873719d35751a",
    "fit/run_one_second.fit": "c94bc3410ceb53e34a231425fb4749db3043503fd16b46675d0a0251e2f532f5",
    "fit/run_threshold_no_distance.fit": "13f6ad0f56136c9443c3fd286b9a7698dc8e50cf0317838aba910ce6942d8e17",
    "fit/run_threshold_paused.fit": "4d964e05da78beecc4c9fe893c8548e945e9a275ad811b63ee470eb7ba4e0216",
    "fit/run_threshold_speed_only.fit": "2d39729036cac92a6aacdebb1a67e964702ca7ca6d5c4716fa2ea94c0e3821d2",
    "fit/run_threshold_test.fit": "b62f559ad3daf74c3bce2d80a3091931e3b70239bd54c6385c759aa78bddeee2",
    "fit/strength_low_hr.fit": "637ad55500694e34e640f682ac11880a91ab1ad232668eda619b2b932b090988",
    "fit/walk_no_hr.fit": "b8f625697a099fd7a0d8f5a5674a73222e7138cb2bcb1e947595ab87f0699485",
}


def test_existing_fixture_bytes_are_unchanged() -> None:
    changed = [
        name
        for name, digest in sorted(FROZEN.items())
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != digest
    ]

    assert not changed, f"frozen fixtures changed: {', '.join(changed)}"
