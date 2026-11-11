# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract of `TrackBoxIndex`: the box of each indexed track, read and filled.

An activity added with a readable position gets its box at once; one whose polyline cannot be read
has none until the fill stores it. Run on the in-memory fake and on SQLite.
"""

from __future__ import annotations

from contextlib import closing
from datetime import timedelta

from ponos_coach.activities.domain import (
    ActivitySummary,
    GeoBox,
    GeoPoint,
    encode_polyline,
)
from tests.builders import T0, fit_identity, make_record
from tests.contracts.contract_storage import create
from tests.contracts.harness import StorageHarness

START = GeoPoint(45.0, 9.0)
POLYLINE = encode_polyline([START, GeoPoint(45.5, 9.2), GeoPoint(45.2, 8.8)])
UNREADABLE = "_"  # a polyline that stops in the middle of a value


class TrackBoxIndexContract:
    """Reading and filling the track boxes."""

    def test_new_index_has_nothing(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            index = profile.track_boxes
            assert index.list_by_start() == []
            assert index.count_without_box() == 0
            assert index.candidates_without_box(0, 10) == []

    def test_a_located_activity_gets_its_box_when_added(self, harness: StorageHarness) -> None:
        record = make_record(summary=ActivitySummary(start_point=START, track_polyline=POLYLINE))
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            (item,) = profile.track_boxes.list_by_start()
            assert item.record == record
            assert item.track_box == GeoBox(45.0, 8.8, 45.5, 9.2)
            assert profile.track_boxes.count_without_box() == 0

    def test_an_activity_without_position_has_no_box_and_needs_none(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(summary=ActivitySummary(distance_m=1000.0)))

            (item,) = profile.track_boxes.list_by_start()
            assert item.track_box is None
            assert profile.track_boxes.count_without_box() == 0

    def test_a_box_across_the_antimeridian_keeps_west_above_east(
        self, harness: StorageHarness
    ) -> None:
        points = [GeoPoint(-17.0, 179.9), GeoPoint(-17.1, -179.9)]
        record = make_record(
            summary=ActivitySummary(start_point=points[0], track_polyline=encode_polyline(points))
        )
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            (item,) = profile.track_boxes.list_by_start()
            assert item.track_box == GeoBox(-17.1, 179.9, -17.0, -179.9)

    def test_records_come_by_start_with_their_own_box(self, harness: StorageHarness) -> None:
        later = make_record(
            fit_identity(2),
            start=T0 + timedelta(days=1),
            summary=ActivitySummary(start_point=GeoPoint(10.0, 10.0)),
        )
        earlier = make_record(fit_identity(1), summary=ActivitySummary(start_point=START))
        with closing(create(harness)) as profile:
            profile.activities.add(later)
            profile.activities.add(earlier)

            items = profile.track_boxes.list_by_start()
            assert [i.record for i in items] == [earlier, later]
            assert [i.track_box for i in items] == [
                GeoBox(45.0, 9.0, 45.0, 9.0),
                GeoBox(10.0, 10.0, 10.0, 10.0),
            ]

    def test_an_unreadable_polyline_leaves_a_candidate_for_the_fill(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(summary=ActivitySummary(start_point=START, track_polyline=UNREADABLE))
        with closing(create(harness)) as profile:
            profile.activities.add(record)
            index = profile.track_boxes

            assert index.count_without_box() == 1
            (candidate,) = index.candidates_without_box(0, 10)
            assert candidate.start_point == START
            assert candidate.track_polyline == UNREADABLE
            with index.transaction():
                index.store_boxes({candidate.row_id: GeoBox(45.0, 9.0, 45.0, 9.0)})
            assert index.count_without_box() == 0
            assert index.list_by_start()[0].track_box == GeoBox(45.0, 9.0, 45.0, 9.0)

    def test_candidates_are_paged_by_row_id_and_limited(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            for number in range(1, 6):
                summary = ActivitySummary(start_point=START, track_polyline=UNREADABLE)
                profile.activities.add(make_record(fit_identity(number), summary=summary))
            index = profile.track_boxes

            first = index.candidates_without_box(0, 2)
            second = index.candidates_without_box(first[-1].row_id, 2)
            rest = index.candidates_without_box(second[-1].row_id, 10)

            ids = [c.row_id for c in (*first, *second, *rest)]
            assert len(first) == len(second) == 2
            assert len(rest) == 1
            assert ids == sorted(set(ids))

    def test_a_box_already_stored_is_not_overwritten_by_the_fill(
        self, harness: StorageHarness
    ) -> None:
        unreadable = ActivitySummary(start_point=START, track_polyline=UNREADABLE)
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1), summary=unreadable))
            profile.activities.add(
                make_record(
                    fit_identity(2),
                    start=T0 + timedelta(days=1),
                    summary=ActivitySummary(start_point=START, track_polyline=POLYLINE),
                )
            )
            index = profile.track_boxes
            stored = {c.row_id for c in index.candidates_without_box(0, 10)}
            assert len(stored) == 1  # only the first one lacks a box
            row_ids = [1, 2]  # both rows, one of which already has its box

            with index.transaction():
                index.store_boxes({row_id: GeoBox(0.0, 0.0, 1.0, 1.0) for row_id in row_ids})

            boxes = [item.track_box for item in index.list_by_start()]
            assert boxes == [GeoBox(0.0, 0.0, 1.0, 1.0), GeoBox(45.0, 8.8, 45.5, 9.2)]

    def test_the_revision_changes_when_an_activity_or_a_box_is_added(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            index = profile.track_boxes
            empty = index.revision()
            profile.activities.add(
                make_record(summary=ActivitySummary(start_point=START, track_polyline=UNREADABLE))
            )
            without_box = index.revision()
            (candidate,) = index.candidates_without_box(0, 1)
            with index.transaction():
                index.store_boxes({candidate.row_id: GeoBox(45.0, 9.0, 45.0, 9.0)})
            with_box = index.revision()

            assert len({empty, without_box, with_box}) == 3
            assert index.revision() == with_box  # reading changes nothing
