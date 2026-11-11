# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Injury texts: zones, steps, warning signs, the state of a record, the notices.

The domain gives categories; here we choose the words, in plain sentences. Never a diagnosis, never
the name of a disease, never "healed": the app speaks of load, tolerance and steps, and of a doctor
when a warning sign asks for one. Every sentence is whole, so the translation never composes
fragments. Context "InjuryTexts" written as a literal: lupdate does not follow aliases.
Health data: these texts serve only the window, never the logs.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.injury.domain import (
    BodyZone,
    HaltReason,
    IllnessKind,
    Onset,
    Overdue,
    PainFreeActivity,
    Side,
    Step,
    Verdict,
    WarningSign,
)
from ponos_coach.planning.domain import NoSessionPossible, PlanAdapted, PlanHalted
from ponos_coach.ui.injury_presentation import (
    HealthNotice,
    NoticeKind,
    ProblemKind,
    ProblemPhase,
    ProblemSpan,
    ProblemSummary,
    StatusFacts,
)


def date_text(day: date) -> str:
    """A date in the locale's short format."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


# --- Vocabulary ----------------------------------------------------------------------------------


def zone_text(zone: BodyZone) -> str:
    """Where it hurts, in the user's words."""
    texts = {
        BodyZone.FOOT: QCoreApplication.translate("InjuryTexts", "Foot or heel"),
        BodyZone.ANKLE: QCoreApplication.translate("InjuryTexts", "Ankle"),
        BodyZone.ACHILLES: QCoreApplication.translate("InjuryTexts", "Achilles tendon"),
        BodyZone.CALF: QCoreApplication.translate("InjuryTexts", "Calf"),
        BodyZone.SHIN: QCoreApplication.translate("InjuryTexts", "Shin"),
        BodyZone.KNEE: QCoreApplication.translate("InjuryTexts", "Knee"),
        BodyZone.THIGH: QCoreApplication.translate("InjuryTexts", "Thigh"),
        BodyZone.HIP: QCoreApplication.translate("InjuryTexts", "Hip or groin"),
        BodyZone.LOWER_BACK: QCoreApplication.translate("InjuryTexts", "Lower back"),
        BodyZone.UPPER_BODY: QCoreApplication.translate(
            "InjuryTexts", "Upper body (shoulder, arm, neck)"
        ),
    }
    return texts[zone]


def side_text(side: Side) -> str:
    """The side, as the end of a phrase."""
    texts = {
        Side.LEFT: QCoreApplication.translate("InjuryTexts", "left"),
        Side.RIGHT: QCoreApplication.translate("InjuryTexts", "right"),
        Side.BOTH: QCoreApplication.translate("InjuryTexts", "both sides"),
    }
    return texts[side]


def side_choice_text(side: Side) -> str:
    """The side, as a choice."""
    texts = {
        Side.LEFT: QCoreApplication.translate("InjuryTexts", "Left"),
        Side.RIGHT: QCoreApplication.translate("InjuryTexts", "Right"),
        Side.BOTH: QCoreApplication.translate("InjuryTexts", "Both sides"),
    }
    return texts[side]


def onset_text(onset: Onset) -> str:
    """How it began."""
    texts = {
        Onset.SUDDEN: QCoreApplication.translate("InjuryTexts", "Suddenly"),
        Onset.GRADUAL: QCoreApplication.translate("InjuryTexts", "Little by little"),
    }
    return texts[onset]


def pain_free_activity_text(activity: PainFreeActivity) -> str:
    """An activity that does not hurt."""
    texts = {
        PainFreeActivity.WALKING: QCoreApplication.translate("InjuryTexts", "Walking"),
        PainFreeActivity.CYCLING: QCoreApplication.translate("InjuryTexts", "Cycling"),
        PainFreeActivity.SWIMMING: QCoreApplication.translate("InjuryTexts", "Swimming"),
        PainFreeActivity.WATER_RUNNING: QCoreApplication.translate("InjuryTexts", "Water running"),
    }
    return texts[activity]


def illness_kind_text(kind: IllnessKind) -> str:
    """The two groups of symptoms, as a choice."""
    texts = {
        IllnessKind.NECK_UP: QCoreApplication.translate(
            "InjuryTexts", "Symptoms only above the neck, no fever"
        ),
        IllnessKind.SYSTEMIC: QCoreApplication.translate(
            "InjuryTexts", "Fever, general malaise or aching muscles"
        ),
    }
    return texts[kind]


def illness_kind_hint(kind: IllnessKind) -> str:
    """What the plan does for each group, so the choice is informed."""
    texts = {
        IllnessKind.NECK_UP: QCoreApplication.translate(
            "InjuryTexts", "The plan keeps only a short easy session, with no hard work."
        ),
        IllnessKind.SYSTEMIC: QCoreApplication.translate(
            "InjuryTexts",
            "The plan has no sessions until you say you feel well. A doctor's visit is advised.",
        ),
    }
    return texts[kind]


def step_text(step: Step) -> str:
    """A step of the path back."""
    texts = {
        Step.ACTIVATION: QCoreApplication.translate("InjuryTexts", "Gentle activation"),
        Step.WALKING: QCoreApplication.translate("InjuryTexts", "Walking"),
        Step.RUN_WALK: QCoreApplication.translate("InjuryTexts", "Run and walk"),
        Step.CONTINUOUS_EASY: QCoreApplication.translate("InjuryTexts", "Easy continuous running"),
        Step.VOLUME: QCoreApplication.translate("InjuryTexts", "Longer running"),
        Step.INTENSITY: QCoreApplication.translate("InjuryTexts", "Full training"),
    }
    return texts[step]


def step_allowance_text(step: Step) -> str:
    """What the step allows."""
    texts = {
        Step.ACTIVATION: QCoreApplication.translate(
            "InjuryTexts", "Light movement, short and often, without pain. No running."
        ),
        Step.WALKING: QCoreApplication.translate("InjuryTexts", "Walking. No running yet."),
        Step.RUN_WALK: QCoreApplication.translate(
            "InjuryTexts", "Short easy runs alternated with walking, every other day."
        ),
        Step.CONTINUOUS_EASY: QCoreApplication.translate(
            "InjuryTexts", "Continuous easy running, every other day. No hard sessions."
        ),
        Step.VOLUME: QCoreApplication.translate(
            "InjuryTexts", "Easy running with longer sessions. No hard sessions yet."
        ),
        Step.INTENSITY: QCoreApplication.translate(
            "InjuryTexts", "All the training, with intensity, as long as it does not hurt."
        ),
    }
    return texts[step]


def sign_text(sign: WarningSign) -> str:
    """A warning sign as a plain statement the user can tick."""
    texts = {
        WarningSign.NIGHT_PAIN: QCoreApplication.translate(
            "InjuryTexts", "Pain at night that wakes me up"
        ),
        WarningSign.STRONG_REST_PAIN: QCoreApplication.translate(
            "InjuryTexts", "Strong pain even at rest"
        ),
        WarningSign.SWELLING_OR_DEFORMITY: QCoreApplication.translate(
            "InjuryTexts", "Swelling, or the area has changed shape"
        ),
        WarningSign.NUMBNESS_OR_WEAKNESS: QCoreApplication.translate(
            "InjuryTexts", "Tingling, numbness or loss of strength"
        ),
        WarningSign.CANNOT_BEAR_WEIGHT: QCoreApplication.translate(
            "InjuryTexts", "I cannot put my weight on it"
        ),
        WarningSign.BONE_PAIN_WALKING_OR_NIGHT: QCoreApplication.translate(
            "InjuryTexts", "Pain on one spot of the bone, also when walking or at night"
        ),
        WarningSign.CALF_SWOLLEN_AND_WARM: QCoreApplication.translate(
            "InjuryTexts", "Calf painful, swollen and warm, after a rest or a journey"
        ),
        WarningSign.CYCLING_STOP_PAIN: QCoreApplication.translate(
            "InjuryTexts", "Pain on the bike that forces me to stop and eases at once"
        ),
        WarningSign.BLADDER_BOWEL_OR_SPREADING_WEAKNESS: QCoreApplication.translate(
            "InjuryTexts", "Bladder or bowel problems, or weakness that spreads, with back pain"
        ),
        WarningSign.FEVER: QCoreApplication.translate("InjuryTexts", "Fever"),
        WarningSign.GENERAL_MALAISE: QCoreApplication.translate(
            "InjuryTexts", "General malaise, or weight or appetite lost without a reason"
        ),
        WarningSign.CHEST_PAIN: QCoreApplication.translate("InjuryTexts", "Chest pain"),
        WarningSign.PALPITATIONS: QCoreApplication.translate(
            "InjuryTexts", "Sudden palpitations, or with dizziness"
        ),
        WarningSign.FAINTING: QCoreApplication.translate(
            "InjuryTexts", "Fainting, during or after effort"
        ),
        WarningSign.DISPROPORTIONATE_BREATHLESSNESS: QCoreApplication.translate(
            "InjuryTexts", "New breathlessness or tiredness out of proportion to the effort"
        ),
    }
    return texts[sign]


def doctor_invitation() -> str:
    """The sentence that goes with every warning sign: a visit is advised, nothing is diagnosed."""
    return QCoreApplication.translate(
        "InjuryTexts",
        "This is a sign that deserves a doctor's visit before any training. "
        "Ponos Coach cannot tell what it means.",
    )


# --- A record ------------------------------------------------------------------------------------


def problem_title(item: ProblemSummary) -> str:
    """A record in a few words: "Knee, left" or "Illness: fever, malaise or aching muscles"."""
    if item.kind is ProblemKind.ILLNESS and item.illness_kind is not None:
        # {kind}: the group of symptoms.
        return QCoreApplication.translate("InjuryTexts", "Illness: {kind}").format(
            kind=illness_kind_text(item.illness_kind).lower()
        )
    if item.zone is None or item.side is None:
        return QCoreApplication.translate("InjuryTexts", "Injury")
    # {zone}: where it hurts; {side}: left, right or both sides.
    return QCoreApplication.translate("InjuryTexts", "{zone}, {side}").format(
        zone=zone_text(item.zone), side=side_text(item.side)
    )


def status_text(item: ProblemSummary) -> str:
    """Where a record stands, in a few words, for the table."""
    if item.phase is ProblemPhase.CLOSED:
        return QCoreApplication.translate("InjuryTexts", "Closed")
    if item.phase is ProblemPhase.HALTED:
        return QCoreApplication.translate("InjuryTexts", "See a doctor")
    if item.kind is ProblemKind.ILLNESS:
        if item.stops_plan:
            return QCoreApplication.translate("InjuryTexts", "Plan stopped")
        return QCoreApplication.translate("InjuryTexts", "Ongoing")
    return step_text(item.step) if item.step is not None else ""


def checkin_cell_text(item: ProblemSummary) -> str:
    """Whether today's check-in is missing, for the table."""
    if item.checkin_due:
        return QCoreApplication.translate("InjuryTexts", "Due today")
    if item.checkin_done:
        return QCoreApplication.translate("InjuryTexts", "Done today")
    return ""


def _phase_line(item: ProblemSummary) -> str:
    if item.phase is ProblemPhase.CLOSED and item.ended_on is not None:
        # {date}: the day it was closed.
        return QCoreApplication.translate("InjuryTexts", "Closed on {date}.").format(
            date=date_text(item.ended_on)
        )
    if item.phase is ProblemPhase.HALTED:
        return QCoreApplication.translate(
            "InjuryTexts",
            "Stopped by a warning sign: the plan has no sessions until a doctor has seen it.",
        )
    if item.kind is ProblemKind.ILLNESS:
        return (
            QCoreApplication.translate(
                "InjuryTexts", "Ongoing: the plan has no sessions until you say you feel well."
            )
            if item.stops_plan
            else QCoreApplication.translate(
                "InjuryTexts", "Ongoing: the plan keeps only a short easy session."
            )
        )
    # {step}: the current step of the path back.
    return QCoreApplication.translate("InjuryTexts", "Current step: {step}.").format(
        step=step_text(item.step) if item.step is not None else ""
    )


def _progress_lines(item: ProblemSummary) -> list[str]:
    lines: list[str] = []
    if item.ready_to_close:
        lines.append(
            QCoreApplication.translate(
                "InjuryTexts",
                "All the steps are done. When you and your doctor agree, you can close it.",
            )
        )
    elif item.good_days_needed:
        # {done}: the good days in a row; {needed}: the good days the step asks for.
        lines.append(
            QCoreApplication.translate(
                "InjuryTexts", "Good days in a row: {done} of {needed} for the next step."
            ).format(done=item.good_days, needed=item.good_days_needed)
        )
    if item.pain_free_needed:
        # {done}: the days with no pain at all; {needed}: the days needed before running again.
        lines.append(
            QCoreApplication.translate(
                "InjuryTexts", "Days with no pain at all: {done} of {needed} before running again."
            ).format(done=item.pain_free_days, needed=item.pain_free_needed)
        )
    return lines


def verdict_line(verdict: Verdict | None) -> str:
    """What the last check-in meant for the path (empty for a plain good day)."""
    texts = {
        Verdict.UP: QCoreApplication.translate(
            "InjuryTexts", "The last check-in was good enough to go up one step."
        ),
        Verdict.BACK: QCoreApplication.translate(
            "InjuryTexts", "The last check-in was worse than before: one step back."
        ),
        Verdict.HOLD: QCoreApplication.translate(
            "InjuryTexts", "The last check-in asked a little too much: the good days start again."
        ),
    }
    return texts.get(verdict, "") if verdict is not None else ""


def overdue_line(overdue: Overdue) -> str:
    """The recovery is slower than expected: a visit is advised."""
    texts = {
        Overdue.STEP: QCoreApplication.translate(
            "InjuryTexts", "You have stayed on this step for a long time: see a doctor."
        ),
        Overdue.TOTAL: QCoreApplication.translate(
            "InjuryTexts", "The recovery is taking longer than expected: see a doctor."
        ),
    }
    return texts[overdue]


def allowed_line(item: ProblemSummary) -> str:
    """What is allowed now, in a sentence."""
    if item.phase is ProblemPhase.CLOSED:
        return ""
    if item.phase is ProblemPhase.HALTED or item.stops_plan:
        return QCoreApplication.translate("InjuryTexts", "Allowed now: no training.")
    if item.kind is ProblemKind.ILLNESS:
        return QCoreApplication.translate(
            "InjuryTexts", "Allowed now: a short easy session, no hard work, no strength."
        )
    # {allowance}: what the step allows, as a sentence.
    return QCoreApplication.translate("InjuryTexts", "Allowed now: {allowance}").format(
        allowance=step_allowance_text(item.step) if item.step is not None else ""
    )


def checkin_line(item: ProblemSummary) -> str:
    """Whether today's check-in is missing or done."""
    if item.checkin_due:
        return QCoreApplication.translate("InjuryTexts", "Today's check-in is missing.")
    if item.checkin_done:
        return QCoreApplication.translate("InjuryTexts", "Today's check-in is done.")
    return ""


def detail_lines(item: ProblemSummary) -> tuple[str, ...]:
    """A record in full, in short sentences: where it stands, what is allowed, the progress, the
    check-in and, if any, the doctor's visit."""
    lines = [problem_title(item), _phase_line(item), allowed_line(item)]
    lines.extend(_progress_lines(item))
    lines.append(verdict_line(item.last_verdict))
    lines.append(checkin_line(item))
    if item.overdue is not None:
        lines.append(overdue_line(item.overdue))
    return tuple(line for line in lines if line)


# --- Notices -------------------------------------------------------------------------------------


def halt_texts(reason: HaltReason) -> tuple[str, str]:
    """Why the plan has no sessions, and what to do."""
    texts = {
        HaltReason.ILLNESS: (
            QCoreApplication.translate(
                "InjuryTexts",
                "You are ill with fever, malaise or aching muscles: the plan has no sessions.",
            ),
            QCoreApplication.translate(
                "InjuryTexts",
                "A doctor's visit is advised. When you feel well again, choose Close.",
            ),
        ),
        HaltReason.WHOLE_PLAN_SIGN: (
            QCoreApplication.translate(
                "InjuryTexts",
                "You reported a warning sign that concerns the whole body: "
                "the plan has no sessions.",
            ),
            QCoreApplication.translate(
                "InjuryTexts",
                "See a doctor before any training. When you have been seen, choose "
                "Medical review done.",
            ),
        ),
        HaltReason.ZONE_SIGN: (
            QCoreApplication.translate(
                "InjuryTexts",
                "You reported a warning sign: the plan has no sessions until it is seen to.",
            ),
            QCoreApplication.translate(
                "InjuryTexts",
                "See a doctor before any training. When you have been seen, choose "
                "Medical review done.",
            ),
        ),
    }
    return texts[reason]


def plan_warning_texts(warning: PlanHalted | PlanAdapted | NoSessionPossible) -> tuple[str, str]:
    """What the plan does because of the injuries and illnesses, and what to do."""
    if isinstance(warning, PlanHalted):
        return halt_texts(warning.reason)
    if isinstance(warning, PlanAdapted):
        return (
            QCoreApplication.translate(
                "InjuryTexts",
                "The plan leaves out what could load the affected area, and keeps the running "
                "gentle.",
            ),
            QCoreApplication.translate(
                "InjuryTexts", "Nothing to do: it goes back to normal as the path moves on."
            ),
        )
    return (
        QCoreApplication.translate(
            "InjuryTexts",
            "Nothing you chose can be planned without loading the affected area: "
            "rest days until the path allows more.",
        ),
        QCoreApplication.translate(
            "InjuryTexts",
            "Say what does not hurt when you report an injury, and choose an alternative "
            "session in Training settings.",
        ),
    )


def notice_prefix(notice: HealthNotice) -> str:
    """The word that opens a notice, so it never relies on colour."""
    if notice.kind in {NoticeKind.WARNING_SIGN, NoticeKind.ILLNESS, NoticeKind.OVERDUE}:
        return QCoreApplication.translate("InjuryTexts", "See a doctor")
    if notice.kind is NoticeKind.READY_TO_CLOSE:
        return QCoreApplication.translate("InjuryTexts", "Last step")
    return QCoreApplication.translate("InjuryTexts", "Plan")


def notice_texts(notice: HealthNotice) -> tuple[str, str]:
    """The sentence of a notice and what to do about it (the second may be empty)."""
    if notice.warning is not None:
        return plan_warning_texts(notice.warning)
    item = notice.summary
    title = "" if item is None else problem_title(item)
    if notice.kind is NoticeKind.WARNING_SIGN:
        # {problem}: the injury; {invitation}: the sentence that invites a doctor's visit.
        headline = QCoreApplication.translate("InjuryTexts", "{problem}: {invitation}").format(
            problem=title, invitation=doctor_invitation()
        )
        return headline, halt_texts(HaltReason.ZONE_SIGN)[1]
    if notice.kind is NoticeKind.ILLNESS:
        # {problem}: the illness.
        headline = QCoreApplication.translate(
            "InjuryTexts", "{problem}: the plan has no sessions until you feel well."
        ).format(problem=title)
        return headline, halt_texts(HaltReason.ILLNESS)[1]
    if notice.kind is NoticeKind.OVERDUE and item is not None and item.overdue is not None:
        # {problem}: the injury; {reason}: why a visit is advised.
        headline = QCoreApplication.translate("InjuryTexts", "{problem}: {reason}").format(
            problem=title, reason=overdue_line(item.overdue)
        )
        return headline, QCoreApplication.translate(
            "InjuryTexts", "Do not go on with more of the same until a doctor has seen it."
        )
    # {problem}: the injury.
    return (
        QCoreApplication.translate(
            "InjuryTexts", "{problem}: all the steps are done and the last ones were good."
        ).format(problem=title),
        QCoreApplication.translate(
            "InjuryTexts", "When you and your doctor agree it is over, choose Close."
        ),
    )


def alert_title() -> str:
    """The title of the message shown when a warning sign stops the plan."""
    return QCoreApplication.translate("InjuryTexts", "Please see a doctor")


def alert_body() -> str:
    """The message shown when a warning sign stops the plan: a visit, no diagnosis."""
    return QCoreApplication.translate(
        "InjuryTexts",
        "You reported a warning sign. {invitation} The plan has no sessions until it is seen "
        "to, and you can keep using everything else.",
    ).format(invitation=doctor_invitation())


# --- Status bar ----------------------------------------------------------------------------------


def status_bar_text(facts: StatusFacts) -> str:
    """The sentence of the status bar: a visit is advised, and the check-ins missing today."""
    parts: list[str] = []
    if facts.see_doctor:
        parts.append(QCoreApplication.translate("InjuryTexts", "See a doctor"))
    if facts.checkins_due:
        # %n: the injuries without today's check-in.
        parts.append(
            QCoreApplication.translate(
                "InjuryTexts", "Check-in due today (%n)", "", facts.checkins_due
            )
        )
    if not parts:
        return ""
    # {facts}: what the injuries ask for, separated by commas.
    return QCoreApplication.translate("InjuryTexts", "Injuries: {facts}").format(
        facts=", ".join(parts)
    )


# --- The calendar --------------------------------------------------------------------------------


def span_cell_text(span: ProblemSpan) -> str:
    """A record on a day, for the cell: "Injury: Knee" or "Illness"."""
    if span.kind is ProblemKind.ILLNESS:
        return QCoreApplication.translate("InjuryTexts", "Illness")
    if span.zone is None:
        return QCoreApplication.translate("InjuryTexts", "Injury")
    # {zone}: where it hurts.
    return QCoreApplication.translate("InjuryTexts", "Injury: {zone}").format(
        zone=zone_text(span.zone)
    )
