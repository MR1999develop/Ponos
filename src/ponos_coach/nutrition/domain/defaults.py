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
- ACSM: ACSM/AND/DC joint position statement on nutrition and athletic performance.
- IOC: IOC consensus statement on sports nutrition.
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
MAX_WEEKLY_LOSS_G: Final = 1000  # ACSM: 0.5-1 kg per week is the usual upper range
MAX_DEFICIT_FRACTION: Final = 0.20  # of the day's expenditure
MAX_DEFICIT_KCAL: Final = 750
ADULT_AGE_YEARS: Final = 18  # below this age the model gives no numbers at all
MIN_BMI_FOR_DEFICIT: Final = 18.5  # no deficit below this body mass index (WHO underweight limit)
# MININTAKE: the deficit never takes the target below the larger of the basal metabolic rate and
# this absolute minimum (project defaults, by sex).
MIN_INTAKE_KCAL_FEMALE: Final = 1200
MIN_INTAKE_KCAL_MALE: Final = 1500

# --- Day load level -----------------------------------------------------------------------------
# A day's level is the higher of the one given by the load and by the training time. Project
# defaults that map the ISSN/ACSM bands (about 1 h, 1-3 h, over 4 h of training per day) onto the
# app's load (100 = one hour at threshold).
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
POST_PROTEIN_G_PER_KG: Final = 0.3
POST_CARBS_G_PER_KG: Final = 1.1  # 1-1.2 g/kg
NEXT_SESSION_WITHIN_MIN: Final = 24 * 60
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
