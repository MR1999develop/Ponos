# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Every threshold and coefficient of the nutrition model, in one place.

These are project defaults taken from published guidelines (see the project skill
`ponos-allenamento`, `references/alimentazione.md`, for the sources); they are general guidance,
never medical advice. Changing a value changes the advice of the whole app: nothing else in the
package holds a number of this kind.

Sources, by abbreviation:

- MSJ: Mifflin et al. 1990, resting energy expenditure equation.
- PAL: activity multipliers of the basal metabolic rate, here WITHOUT sport (project defaults:
  the classic Harris-Benedict factors 1.375-1.725 already include exercise, and the app adds the
  recorded sport on top, so they would count it twice).
- KEYTEL: Keytel et al. 2005, energy expenditure from heart rate, age, weight and sex.
- COMPENDIUM: Compendium of Physical Activities (METs by activity).
- ISSN: International Society of Sports Nutrition position stands on nutrient timing,
  protein and diets/body composition.
- ACSM: ACSM/AND/DC joint position statement on nutrition and athletic performance (2016).
- BOER: Boer 1984, estimation of lean body mass from weight, height and sex.
- IOC: IOC consensus statement on sports nutrition.
- GJSN: Jeukendrup and Gleeson, Sport Nutrition 3rd ed. (rapid refuelling per hour, protein doses of
  0.4 g/kg, energy availability, weight loss in athletes).
- CSN: Burke and Deakin, Clinical Sports Nutrition 5th ed. (energy availability 30-45 only for
  brief planned periods; measured lean mass over an estimate).
- MININTAKE: the lower limits of daily energy for unsupervised weight loss that health
  organisations commonly quote (NHLBI and similar: 1,200 kcal for women, 1,500 kcal for men).
"""

from __future__ import annotations

from typing import Final

from ponos_coach.activities.domain import Sport

# --- Person limits (the same plausible range as the profile data) -------------------------------
MIN_AGE_YEARS: Final = 0
MAX_AGE_YEARS: Final = 130
MIN_HEIGHT_CM: Final = 100
MAX_HEIGHT_CM: Final = 250
MIN_WEIGHT_KG: Final = 20.0
MAX_WEIGHT_KG: Final = 300.0

# --- Basal metabolic rate (MSJ), kcal/day -------------------------------------------------------
MSJ_PER_KG: Final = 10.0
MSJ_PER_CM: Final = 6.25
MSJ_PER_YEAR: Final = 5.0
MSJ_MALE_OFFSET: Final = 5.0
MSJ_FEMALE_OFFSET: Final = -161.0

# --- Activity level outside sport: multiplier of the basal metabolic rate (PAL) -----------------
# Project defaults for a day WITHOUT the sport recorded by the app: desk life, some walking, a
# standing or walking job, a heavy manual job.
PAL_SEDENTARY: Final = 1.2
PAL_LIGHT: Final = 1.3
PAL_ACTIVE: Final = 1.4
PAL_VERY_ACTIVE: Final = 1.5
DEFAULT_ACTIVITY_PAL: Final = PAL_SEDENTARY  # no level set: the most conservative

# --- Activity energy ----------------------------------------------------------------------------
SECONDS_PER_DAY: Final = 86_400.0
KJ_PER_KCAL: Final = 4.184
# KEYTEL: kJ/min from heart rate (bpm), weight (kg) and age (years).
KEYTEL_MALE: Final = (-55.0969, 0.6309, 0.1988, 0.2017)  # constant, HR, weight, age
KEYTEL_FEMALE: Final = (-20.4022, 0.4472, -0.1263, 0.074)
# A declared total above this many kcal per hour of moving time is not credible (the highest
# Compendium value is about 23 METs: 23 x 100 kg = 2,300 kcal/h for a very large person; 1,500 is
# a generous project default for the people the app is for) and is replaced by an estimate.
MAX_ACTIVITY_KCAL_PER_HOUR: Final = 1500.0
MIN_USABLE_BPM: Final = 40.0  # an average HR outside this range is ignored (MET estimate instead)
MAX_USABLE_BPM: Final = 250.0
# COMPENDIUM (gross METs; 1 MET = 1 kcal per kg per hour): running 6 mph 9.8; running cross
# country 9.0; bicycling 12-13.9 mph 8.0; hiking cross country 6.0; walking 3 mph 3.5;
# resistance training 3.5. OTHER is a project default.
SPORT_MET: Final[dict[Sport, float]] = {
    Sport.RUNNING: 9.8,
    Sport.TRAIL: 9.0,
    Sport.CYCLING: 8.0,
    Sport.HIKING: 6.0,
    Sport.WALKING: 3.5,
    Sport.STRENGTH: 3.5,
    Sport.OTHER: 4.0,
}

# --- Weight loss: deficit and its safety limits -------------------------------------------------
KCAL_PER_KG_BODY_MASS: Final = 7700.0  # project default (the usual 7700 kcal per kg approximation)
DAYS_PER_WEEK: Final = 7
MIN_WEEKLY_LOSS_G: Final = 100
MAX_WEEKLY_LOSS_G: Final = 1000  # project default: the largest pace the app offers
# ACSM: stay under 1% of body weight per week to keep lean mass (a study cited there found 0.7%
# per week better than 1.4%). The pace actually used is the smallest of the goal, the line above
# and this share of the day's weight.
MAX_WEEKLY_LOSS_FRACTION: Final = 0.01
MAX_DEFICIT_FRACTION: Final = 0.20  # of the day's expenditure
MAX_DEFICIT_KCAL: Final = 750
ADULT_AGE_YEARS: Final = 18  # below this age the model gives no numbers at all
MIN_BMI_FOR_DEFICIT: Final = 18.5  # no deficit below this body mass index (WHO underweight limit)
# MININTAKE: the deficit never takes the target below the larger of the basal metabolic rate and
# this absolute minimum (project defaults, by sex).
MIN_INTAKE_KCAL_FEMALE: Final = 1200
MIN_INTAKE_KCAL_MALE: Final = 1500
# Energy availability, EA = (energy intake - exercise energy) / fat-free mass, kcal per kg of
# fat-free mass per day. ACSM: 45 is energy balance for a healthy person, under 30 is harmful
# (low energy availability). The deficit never leaves the day's EA under the minimum.
BALANCED_ENERGY_AVAILABILITY_KCAL_PER_KG_FFM: Final = 45
MIN_ENERGY_AVAILABILITY_KCAL_PER_KG_FFM: Final = 30
# BOER: fat-free mass in kg (weight in kg, height in cm), as (per_kg, per_cm, constant):
# men 0.407 * weight + 0.267 * height - 19.2; women 0.252 * weight + 0.473 * height - 48.3.
# The estimate is kept in
# [MIN_FFM_FRACTION, 1] of the body weight (project default: a formula outside the range it was
# fitted on must not give an absurd mass).
BOER_MALE: Final = (0.407, 0.267, -19.2)
BOER_FEMALE: Final = (0.252, 0.473, -48.3)
MIN_FFM_FRACTION: Final = 0.5
# Measured lean mass (CSN: a measurement beats the estimate). Absolute limits in tenths of a kg,
# and the plausible share of the body weight on the day it is used (project defaults): over 95%
# means under 5% fat (GJSN: not below 5% for men, 12% for women), under 40% means over 60% fat.
MIN_LEAN_MASS_TENTHS_KG: Final = 150
MAX_LEAN_MASS_TENTHS_KG: Final = 2000
MIN_LEAN_MASS_FRACTION: Final = 0.40
MAX_LEAN_MASS_FRACTION: Final = 0.95
# CSN, GJSN: an energy availability between 30 and 45 kcal/kg of fat-free mass is tolerable only for
# a short, planned weight loss. Neither source gives the number of weeks (CSN says "8 to 12"):
# project default 12 weeks, after which a weight-loss period in progress with an availability
# under the balanced value gets an advisory suggesting a pause at maintenance.
LONG_WEIGHT_LOSS_WEEKS: Final = 12

# --- Day load level -----------------------------------------------------------------------------
# A day's level is the higher of the one given by the load and by the training time. Project
# defaults that map the ISSN/ACSM bands (about 1 h, 1-3 h, over 4 h of training per day) onto the
# load (TRIMPeq: 100 = one hour at the threshold of the sport).
LOAD_LIGHT_FROM: Final = 0.0  # any training is at least light
LOAD_MODERATE_FROM: Final = 60.0
LOAD_HIGH_FROM: Final = 120.0
LOAD_VERY_HIGH_FROM: Final = 200.0
MINUTES_MODERATE_FROM: Final = 60.0
MINUTES_HIGH_FROM: Final = 120.0
MINUTES_VERY_HIGH_FROM: Final = 240.0
# Deficit multiplier by level (rest, light, moderate, high, very high): reduced on heavy days.
DEFICIT_FACTORS: Final = (1.0, 1.0, 1.0, 0.5, 0.25)

# --- Macronutrients (ISSN, ACSM, IOC) -----------------------------------------------------------
# Carbohydrates, g per kg of body weight per day, (low, high) per level.
CARBS_G_PER_KG: Final = ((3.0, 5.0), (3.0, 5.0), (5.0, 7.0), (6.0, 10.0), (8.0, 12.0))
PROTEIN_G_PER_KG: Final = 1.6
PROTEIN_G_PER_KG_DEFICIT: Final = 2.0
FAT_MIN_FRACTION: Final = 0.20  # of the day's energy
KCAL_PER_G_CARBS: Final = 4
KCAL_PER_G_PROTEIN: Final = 4
KCAL_PER_G_FAT: Final = 9

# --- Nutrition around training ------------------------------------------------------------------
MIN_MOMENT_SESSION_MIN: Final = 30.0  # shorter sessions need nothing special
PRE_MIN_SESSION_MIN: Final = 60.0  # strictly longer sessions get a pre-training intake
PRE_CARBS_G_PER_KG: Final = 1.0
PRE_LEAD_MIN: Final = 120  # 1-4 h before: 2 h
PRE_SHORT_LEAD_MIN: Final = 60  # when 2 h before would be too early in the morning
EARLIEST_PRE_MIN: Final = 300  # 05:00
DURING_MIN_SESSION_MIN: Final = 75.0  # strictly longer sessions get an intake during
LONG_SESSION_MIN: Final = 90.0  # "long": post-training carbohydrates and more
LONG_EFFORT_MIN: Final = 150.0  # 2.5 h
DURING_CARBS_G_PER_H: Final = 45  # 30-60 g/h up to 2.5 h
# Beyond 2.5 h the IOC range reaches 90 g/h, but only with glucose-fructose blends, which the food
# table does not tell apart: the ceiling is the 60 g/h of a single carbohydrate.
DURING_CARBS_G_PER_H_LONG: Final = 60
DURING_FLUID_ML_PER_H: Final = 600  # indicative, ACSM: 0.4-0.8 L/h
DURING_SODIUM_MG_PER_H: Final = 300  # indicative, 0.5 g/L of sodium in the drink
POST_LEAD_MIN: Final = 30  # within one hour of the end
POST_PROTEIN_G_PER_KG: Final = 0.4  # GJSN: 0.4 g/kg covers the individual variability
POST_CARBS_G_PER_KG: Final = 1.1  # 1-1.2 g/kg
# ACSM: rapid refuelling (1-1.2 g/kg/h in the first 4 h) when two demanding sessions are less than
# 8 hours apart. Project choice: it applies to any following session within 8 h, however short,
# because extra carbohydrates are not a risk and the rule stays simple.
SPEEDY_REFUEL_WITHIN_MIN: Final = 8 * 60
# Within that time the "after" is a rapid refuel (ACSM, ISSN, GJSN): 1.0-1.2 g/kg of carbohydrates
# an hour for the first 4 hours after the end (project default: 1.1, the middle). Project choice
# (user request of 2026-11-09: one intake an hour was unmanageable): the 4 hours are served as
# REFUEL_INTAKES intakes REFUEL_STEP_MIN apart, each with the carbohydrates of its hours (with an
# adequate total, frequent small feedings and fewer larger ones refuel alike). The first comes
# POST_LEAD_MIN after the end and is always given; the last is at least REFUEL_LAST_BEFORE_NEXT_MIN
# before the next session. They count inside the day's carbohydrate budget and merge with a meal
# that is close.
REFUEL_CARBS_G_PER_KG_H: Final = 1.1
REFUEL_HOURS: Final = 4
REFUEL_STEP_MIN: Final = 120
REFUEL_INTAKES: Final = REFUEL_HOURS * 60 // REFUEL_STEP_MIN  # 2
REFUEL_LAST_BEFORE_NEXT_MIN: Final = 30
# Lunch and dinner always keep at least this much carbohydrate (project default), however long the
# training of the day: the intakes around it give way first (see `meals.py`).
MAIN_MEAL_MIN_CARBS_G_PER_KG: Final = 1.0

# --- Meals --------------------------------------------------------------------------------------
MINUTES_PER_DAY: Final = 1440
LAST_MINUTE_OF_DAY: Final = MINUTES_PER_DAY - 1
MEAL_SHARES: Final = (0.25, 0.40, 0.35)  # breakfast, lunch, dinner
DEFAULT_MEAL_MINUTES: Final = (7 * 60 + 30, 13 * 60, 20 * 60)  # 07:30, 13:00, 20:00
MIN_MEAL_GAP_MIN: Final = 120  # typical meal times at least this far apart
EATING_BUFFER_MIN: Final = 60  # no regular meal from this long before the start to after the end
LATEST_MEAL_MIN: Final = 23 * 60  # a meal is not moved after 23:00
FUSE_GAP_MIN: Final = 90  # intakes closer than this become one

# --- Food examples ------------------------------------------------------------------------------
FOOD_TOLERANCE_FRACTION: Final = 0.15  # of each macro of the meal...
FOOD_TOLERANCE_MIN_G: Final = 5  # ...but at least this many grams
FOOD_PORTION_STEP_G: Final = 5
FOOD_MAX_EXAMPLES: Final = 3
FOOD_VEGETABLE_SIDE_G: Final = 150
FOOD_MIN_MACRO_G: Final = 5  # a macro below this is not worth a food
