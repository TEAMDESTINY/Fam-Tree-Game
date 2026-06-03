"""Procedural puzzle generators for work mini-games.

Each class exposes:
  - class-level game_code constants (GC_*)
  - .generate(user_id, cooldown_secs) -> (text, InlineKeyboardMarkup)

Callback format (7 parts for both doctor and police):
  work:{type}:{uid}:{expires_at}:{game_code}:{correct_idx}:{choice_idx}
"""

import random
import time

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Shared option labels used by all mini-games
OPT = "ABCD"


# ─── Doctor ───────────────────────────────────────────────────────────────────


class DoctorPuzzles:
    GC_TRIAGE = 0
    GC_TIME_ATTACK = 1
    GC_LAB = 2
    GC_DOSING = 3

    TYPES = ["triage", "time_attack", "lab_matching", "dosing"]
    WEIGHTS = [50, 20, 20, 10]
    TIME_ATTACK_SECONDS = 45

    DIAGNOSIS_POOL = [
        {
            "condition": "Acute Appendicitis",
            "symptoms": [
                "Right lower belly pain",
                "Low-grade fever",
                "Rebound tenderness near navel",
            ],
            "distractors": ["Gastric Ulcer", "Kidney Stones", "Gallstones"],
        },
        {
            "condition": "Anaphylaxis",
            "symptoms": [
                "Sudden hives",
                "Throat swelling",
                "BP drop after eating shellfish",
            ],
            "distractors": ["Asthma Attack", "Panic Attack", "Angioedema"],
        },
        {
            "condition": "Hypoglycemia",
            "symptoms": [
                "Sudden sweating",
                "Shaking hands",
                "Confusion in a diabetic patient",
            ],
            "distractors": [
                "Heat Stroke",
                "Ischemic Stroke",
                "Opioid Overdose",
            ],
        },
        {
            "condition": "Pulmonary Embolism",
            "symptoms": [
                "Sudden chest pain",
                "Shortness of breath",
                "Leg swelling after long flight",
            ],
            "distractors": ["Heart Attack", "Pneumonia", "Pleuritis"],
        },
        {
            "condition": "Diabetic Ketoacidosis",
            "symptoms": [
                "Fruity breath",
                "Rapid deep breathing",
                "High blood sugar + nausea",
            ],
            "distractors": [
                "Alcohol Intoxication",
                "Hyperosmolar State",
                "Lactic Acidosis",
            ],
        },
        {
            "condition": "Meningitis",
            "symptoms": [
                "Stiff neck",
                "Sensitivity to light",
                "Sudden severe headache with fever",
            ],
            "distractors": [
                "Migraine",
                "Subarachnoid Hemorrhage",
                "Encephalitis",
            ],
        },
        {
            "condition": "Myocardial Infarction",
            "symptoms": [
                "Crushing chest pain",
                "Left arm numbness",
                "Sweating + jaw pain",
            ],
            "distractors": [
                "Aortic Dissection",
                "Stable Angina",
                "Pericarditis",
            ],
        },
        {
            "condition": "Ectopic Pregnancy",
            "symptoms": [
                "Sharp one-sided pelvic pain",
                "Missed period",
                "Vaginal bleeding in early pregnancy",
            ],
            "distractors": ["Ovarian Cyst", "Appendicitis", "Miscarriage"],
        },
        {
            "condition": "Stroke (CVA)",
            "symptoms": [
                "Sudden facial droop on one side",
                "Arm weakness",
                "Slurred speech",
            ],
            "distractors": ["Bell's Palsy", "Hypoglycemia", "Todd's Paralysis"],
        },
        {
            "condition": "Septic Shock",
            "symptoms": [
                "High fever + confusion",
                "Very low blood pressure",
                "Rapid breathing after infection",
            ],
            "distractors": ["Cardiogenic Shock", "Anaphylaxis", "Heat Stroke"],
        },
        {
            "condition": "Aortic Dissection",
            "symptoms": [
                "Sudden tearing chest pain radiating to the back",
                "Unequal blood pressure in arms",
                "Pulse deficit",
            ],
            "distractors": [
                "Myocardial Infarction",
                "Pulmonary Embolism",
                "Pericarditis",
            ],
        },
        {
            "condition": "Tension Pneumothorax",
            "symptoms": [
                "Absent breath sounds on one side",
                "Tracheal deviation away from affected side",
                "Respiratory distress",
            ],
            "distractors": [
                "Hemothorax",
                "Pleural Effusion",
                "Cardiac Tamponade",
            ],
        },
    ]

    TIME_ATTACK_POOL = [
        {
            "scenario": (
                "A man is brought in after a car accident. He's loudly arguing with a paramedic "
                "about who's at fault, complaining about the dent in his brand-new car, and keeps "
                "asking a nurse to charge his phone. He then casually says he's had a crushing pain "
                "in his left arm and jaw for the past 20 minutes."
            ),
            "answer": "Crushing left arm and jaw pain",
            "distractors": [
                "Anger about car damage",
                "Concern over phone battery",
                "Argument with paramedic",
            ],
        },
        {
            "scenario": (
                "A teenage girl after a school fight. She's crying about her broken nails, insisting "
                "'the other girl started it', and asking if she can leave soon. Her friend mentions "
                "in passing that she hit the back of her head on concrete and one of her pupils "
                "looks bigger than the other."
            ),
            "answer": "Unequal pupils after head impact",
            "distractors": [
                "Emotional distress",
                "Minor nail injury",
                "Desire to leave",
            ],
        },
        {
            "scenario": (
                "An elderly man complaining loudly about his cold hospital meal and demanding a "
                "different pillow. His daughter says he's been 'a bit confused since breakfast'. "
                "He's also having trouble finding words mid-sentence and his right hand feels weak. "
                "He wants to know if his favourite show is on."
            ),
            "answer": "Sudden confusion, word-finding difficulty, and one-sided weakness",
            "distractors": [
                "Cold meal complaint",
                "Pillow discomfort",
                "TV request",
            ],
        },
        {
            "scenario": (
                "A young woman post-surgery, cheerfully texting and asking about discharge time. "
                "She mentions her calf has been 'a bit sore and puffy' since yesterday. She also "
                "just said her chest feels tight and she's slightly short of breath 'out of nowhere'."
            ),
            "answer": "Post-op calf swelling with sudden chest tightness and dyspnea",
            "distractors": [
                "Eagerness to be discharged",
                "Routine post-op soreness",
                "Texting activity",
            ],
        },
        {
            "scenario": (
                "A construction worker refusing to sit down, insisting he's 'totally fine'. He smells "
                "of alcohol and is telling jokes. A coworker who came with him whispers that he fell "
                "from scaffolding about 2 meters, seemed fine for an hour, but then got increasingly "
                "drowsy and complained of a headache."
            ),
            "answer": "Fall with lucid interval followed by drowsiness and headache",
            "distractors": [
                "Alcohol intoxication",
                "Refusal to sit",
                "Telling jokes",
            ],
        },
        {
            "scenario": (
                "A marathon runner complaining that the finish line medic is overreacting. She says "
                "she's just tired and a bit dizzy. She's also been drinking water non-stop for hours. "
                "A bystander notes her speech has become slightly slurred and she stumbled twice "
                "despite claiming to feel fine."
            ),
            "answer": "Slurred speech and stumbling after excessive water intake",
            "distractors": [
                "Race fatigue",
                "General dizziness",
                "Self-reported wellness",
            ],
        },
        {
            "scenario": (
                "A man in the waiting room complaining he's been waiting too long, demanding to "
                "speak to a manager. He keeps rubbing his stomach. His wife says he mentioned "
                "the pain started around his navel this morning and has moved to the lower right. "
                "He vomited once and has a temperature of 38.5°C."
            ),
            "answer": "Migratory pain navel→lower right with fever and vomiting",
            "distractors": [
                "Frustration about wait time",
                "Desire to escalate complaint",
                "General stomach rubbing",
            ],
        },
        {
            "scenario": (
                "A diabetic patient brought in by family who says he 'seems off'. He's irritable, "
                "yelling that he doesn't need to be there. He's sweating through his shirt despite "
                "the cool room, his hands are shaking visibly, and he keeps saying odd things that "
                "don't make sense. Family says he skipped lunch."
            ),
            "answer": "Sweating, shaking, confusion, and irritability in a diabetic who skipped a meal",
            "distractors": [
                "Irritability and refusal of care",
                "Warm room assumption",
                "Family overreaction",
            ],
        },
        {
            "scenario": (
                "A 30-year-old woman brought in after a spa day. She says she feels 'a bit warm'. "
                "She's been in a hot tub for 3 hours, had two cocktails, and keeps checking her "
                "Instagram. She then mentions she is 10 weeks pregnant and has had sharp right-sided "
                "pelvic pain since this morning with some light bleeding."
            ),
            "answer": "Sharp unilateral pelvic pain with bleeding in early pregnancy",
            "distractors": [
                "Mild warmth from hot tub",
                "Alcohol consumption",
                "Social media use",
            ],
        },
        {
            "scenario": (
                "A child, brought in by a panicking parent who says he ate 'some berries from the "
                "garden'. The child is giggling and playing with a toy. The parent is describing "
                "every plant in their garden in detail. The child's pupils are very dilated, his "
                "skin is flushed and dry, and his heart rate is 130."
            ),
            "answer": "Dilated pupils, flushed dry skin, and tachycardia after berry ingestion",
            "distractors": [
                "Child playing normally",
                "Parent describing all garden plants",
                "Giggling and good mood",
            ],
        },
    ]

    LAB_PUZZLE_POOL = [
        {
            "scenario": "Three emergency blood vials lost their labels. Match each patient to the correct analysis tube using the clues.",
            "clues": [
                "Patient A's sample agglutinates when exposed to Anti-A serum.",
                "The acid-buffer tube is reserved for universal donors (Type O).",
                "Patient C cannot tolerate the cold-chain tube — their sample degrades below 10°C.",
            ],
            "choices": [
                "A → Anti-A tube | B → Acid-buffer tube | C → Heat-stable tube",
                "A → Acid-buffer tube | B → Anti-A tube | C → Cold-chain tube",
                "A → Heat-stable tube | B → Anti-A tube | C → Acid-buffer tube",
                "A → Cold-chain tube | B → Heat-stable tube | C → Anti-A tube",
            ],
            "answer": "A → Anti-A tube | B → Acid-buffer tube | C → Heat-stable tube",
        },
        {
            "scenario": "Three medication syringes were mixed up. Use the clues to route each drug to the correct IV line.",
            "clues": [
                "Drug X must never be administered alongside calcium — it precipitates.",
                "Line 2 is the calcium gluconate line.",
                "Drug Y is compatible with all lines but requires a pH above 7.",
                "Line 3 has a pH of 6.5.",
            ],
            "choices": [
                "Drug X → Line 1 | Drug Y → Line 3 | Drug Z → Line 2",
                "Drug X → Line 3 | Drug Y → Line 1 | Drug Z → Line 2",
                "Drug X → Line 1 | Drug Y → Line 2 | Drug Z → Line 3",
                "Drug X → Line 2 | Drug Y → Line 1 | Drug Z → Line 3",
            ],
            "answer": "Drug X → Line 1 | Drug Y → Line 2 | Drug Z → Line 3",
        },
        {
            "scenario": "Three toxicology samples need routing. Each sample requires a specific preservation tube.",
            "clues": [
                "Sample A is a volatile compound — it must go into the sealed airtight tube.",
                "Sample B will degrade under UV light.",
                "The room-temperature tube is the only one left for sample C.",
            ],
            "choices": [
                "A → Airtight | B → UV-shielded | C → Room-temp",
                "A → UV-shielded | B → Airtight | C → Room-temp",
                "A → Room-temp | B → UV-shielded | C → Airtight",
                "A → Airtight | B → Room-temp | C → UV-shielded",
            ],
            "answer": "A → Airtight | B → UV-shielded | C → Room-temp",
        },
        {
            "scenario": "Three patient cultures need different incubation conditions. Assign each culture to the right incubator.",
            "clues": [
                "Culture A is an anaerobic bacteria — it dies in oxygen.",
                "Culture B grows only at exactly 37°C aerobically.",
                "The CO₂ incubator is the only remaining option for culture C.",
            ],
            "choices": [
                "A → Anaerobic chamber | B → Standard 37°C | C → CO₂ incubator",
                "A → CO₂ incubator | B → Anaerobic chamber | C → Standard 37°C",
                "A → Standard 37°C | B → CO₂ incubator | C → Anaerobic chamber",
                "A → CO₂ incubator | B → Standard 37°C | C → Anaerobic chamber",
            ],
            "answer": "A → Anaerobic chamber | B → Standard 37°C | C → CO₂ incubator",
        },
        {
            "scenario": "Three pediatric drug vials need to be matched to the correct patient wristband.",
            "clues": [
                "Vial 1 contains penicillin — the patient with the penicillin allergy (patient β) must NOT receive it.",
                "Patient α's prescription requires the anticoagulant, which is in vial 3.",
                "Vial 2 contains the antiemetic designated for patient β.",
            ],
            "choices": [
                "Vial 1 → Patient γ | Vial 2 → Patient β | Vial 3 → Patient α",
                "Vial 1 → Patient α | Vial 2 → Patient γ | Vial 3 → Patient β",
                "Vial 1 → Patient β | Vial 2 → Patient α | Vial 3 → Patient γ",
                "Vial 1 → Patient γ | Vial 2 → Patient α | Vial 3 → Patient β",
            ],
            "answer": "Vial 1 → Patient γ | Vial 2 → Patient β | Vial 3 → Patient α",
        },
        {
            "scenario": "Three blood cross-match samples need to be filed in the correct specimen rack.",
            "clues": [
                "Rack A is for Rh-negative samples only.",
                "Patient 2's sample is Rh-positive and requires the centrifuge rack.",
                "The frozen storage rack is for Patient 3's sample, which is fragile.",
            ],
            "choices": [
                "Patient 1 → Rack A (Rh–) | Patient 2 → Centrifuge rack | Patient 3 → Frozen rack",
                "Patient 1 → Centrifuge rack | Patient 2 → Rack A (Rh–) | Patient 3 → Frozen rack",
                "Patient 1 → Frozen rack | Patient 2 → Centrifuge rack | Patient 3 → Rack A (Rh–)",
                "Patient 1 → Rack A (Rh–) | Patient 2 → Frozen rack | Patient 3 → Centrifuge rack",
            ],
            "answer": "Patient 1 → Rack A (Rh–) | Patient 2 → Centrifuge rack | Patient 3 → Frozen rack",
        },
        {
            "scenario": "Three reagent bottles fell off the shelf and lost their labels. Re-assign each to the correct test.",
            "clues": [
                "Bottle A turns pink in the presence of glucose.",
                "The lipid panel test requires a clear reagent that reacts with triglycerides — bottle C.",
                "Bottle B reacts with hemoglobin and is needed for the CBC test.",
            ],
            "choices": [
                "A → Glucose test | B → CBC test | C → Lipid panel",
                "A → CBC test | B → Glucose test | C → Lipid panel",
                "A → Lipid panel | B → CBC test | C → Glucose test",
                "A → Glucose test | B → Lipid panel | C → CBC test",
            ],
            "answer": "A → Glucose test | B → CBC test | C → Lipid panel",
        },
        {
            "scenario": "Three organ transport coolers were mixed up at the airport. Restore the correct organ-to-cooler assignment.",
            "clues": [
                "The heart requires a cooler that maintains exactly 4°C.",
                "The cornea transport cooler uses a special saline medium, not ice.",
                "The liver must be in the largest cooler with oxygenated perfusion fluid.",
            ],
            "choices": [
                "Heart → 4°C ice cooler | Cornea → Saline medium | Liver → Perfusion cooler",
                "Heart → Perfusion cooler | Cornea → 4°C ice cooler | Liver → Saline medium",
                "Heart → Saline medium | Cornea → 4°C ice cooler | Liver → Perfusion cooler",
                "Heart → 4°C ice cooler | Cornea → Perfusion cooler | Liver → Saline medium",
            ],
            "answer": "Heart → 4°C ice cooler | Cornea → Saline medium | Liver → Perfusion cooler",
        },
    ]

    @classmethod
    def generate(
        cls, user_id: int, cooldown_secs: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        puzzle_type = random.choices(cls.TYPES, weights=cls.WEIGHTS, k=1)[0]
        now = int(time.time())
        if puzzle_type == "triage":
            return cls._triage(user_id, now + cooldown_secs, cls.GC_TRIAGE)
        elif puzzle_type == "time_attack":
            return cls._time_attack(
                user_id, now + cls.TIME_ATTACK_SECONDS, cls.GC_TIME_ATTACK
            )
        elif puzzle_type == "lab_matching":
            return cls._lab(user_id, now + cooldown_secs, cls.GC_LAB)
        else:
            return cls._dosing(user_id, now + cooldown_secs, cls.GC_DOSING)

    @classmethod
    def _triage(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        target = random.choice(cls.DIAGNOSIS_POOL)
        symptoms = list(target["symptoms"])
        random.shuffle(symptoms)

        choices = [target["condition"]] + list(target["distractors"])
        random.shuffle(choices)
        correct_idx = choices.index(target["condition"])

        symptom_list = "\n".join(f"  • {s}" for s in symptoms)
        opts = "\n".join(f"{OPT[i]}. {c}" for i, c in enumerate(choices))
        text = "🏥 <b>Emergency Triage</b>\n\n"
        text += f"A patient arrives with:\n{symptom_list}\n\n"
        text += "What is the most likely diagnosis?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:doctor:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(len(choices))
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _time_attack(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        entry = random.choice(cls.TIME_ATTACK_POOL)
        choices = [entry["answer"]] + list(entry["distractors"])
        random.shuffle(choices)
        correct_idx = choices.index(entry["answer"])

        opts = "\n".join(f"{OPT[i]}. {c}" for i, c in enumerate(choices))
        text = f"⚡ <b>TIME ATTACK — {cls.TIME_ATTACK_SECONDS} SECONDS!</b>\n\n"
        text += f"{entry['scenario']}\n\n"
        text += "🚨 <b>Identify the life-threatening finding NOW!</b>\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:doctor:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(len(choices))
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _lab(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        entry = random.choice(cls.LAB_PUZZLE_POOL)
        choices = list(entry["choices"])
        correct_idx = choices.index(entry["answer"])

        clue_list = "\n".join(
            f"  {i + 1}. {c}" for i, c in enumerate(entry["clues"])
        )
        opts = "\n".join(f"{OPT[i]}. {c}" for i, c in enumerate(choices))
        text = "🔬 <b>Lab Matching Puzzle</b>\n\n"
        text += f"{entry['scenario']}\n\n"
        text += f"<b>Clues:</b>\n{clue_list}\n\n"
        text += "Which assignment is entirely correct?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:doctor:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(len(choices))
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _dosing(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        patients = [
            "Officer Rex",
            "Chef Cleo",
            "Mayor Miles",
            "Farmer Felix",
            "Baker Bella",
            "Teacher Terry",
            "Pilot Pedro",
            "Sailor Sam",
            "Artist Aria",
            "Mechanic Marco",
        ]
        patient = random.choice(patients)
        patient_id = f"PAT-{random.randint(1000, 9999)}"

        weight = random.randint(40, 90)
        dose_per_kg = random.choice([2, 3, 5])
        concentration = random.choice([10, 20, 25])
        hours = random.choice([2, 4, 6])

        total_mg = weight * dose_per_kg
        total_ml = total_mg / concentration
        correct_rate = round(total_ml / hours, 1)

        choices: set[str] = {str(correct_rate)}
        while len(choices) < 4:
            wrong = round(
                correct_rate + random.choice([-5.5, -2.0, 1.5, 3.0, 5.0]), 1
            )
            if wrong > 0:
                choices.add(str(wrong))
        sorted_choices = sorted(choices, key=float)
        correct_idx = sorted_choices.index(str(correct_rate))

        opts = "\n".join(
            f"{OPT[i]}. {c} mL/hr" for i, c in enumerate(sorted_choices)
        )
        text = f"🏥 <b>IV Dosing — {patient}</b>\n"
        text += f"<code>Patient ID: {patient_id}</code>\n\n"
        text += f"⚖️ Weight: <b>{weight} kg</b> · Protocol: <b>{dose_per_kg} mg/kg</b>\n"
        text += f"💉 IV concentration: <b>{concentration} mg/mL</b>\n"
        text += f"⏱ Infuse evenly over <b>{hours} hours</b>\n\n"
        text += "What rate (mL/hr) do you set on the pump?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:doctor:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(len(sorted_choices))
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── Police ───────────────────────────────────────────────────────────────────


class PolicePuzzles:
    GC_ALIBI = 0
    GC_CYBER = 1
    GC_TIMEBOMB = 2

    TYPES = ["alibi", "cyber", "timebomb"]
    WEIGHTS = [70, 20, 10]
    TIMEBOMB_SECONDS = 45

    _FIRST_NAMES = [
        "Slick",
        "Mad Dog",
        "Shadow",
        "Ghost",
        "Trigger",
        "Bullet",
        "Babs",
        "Flash",
        "Big Red",
        "Zigzag",
        "Ice Pick",
        "Knuckles",
        "Neon",
        "Razor",
        "Sparky",
    ]
    _LAST_NAMES = [
        "Willy",
        "Sammy",
        "Dynamite",
        "Gary",
        "Molly",
        "Miller",
        "Vance",
        "Cruz",
        "Kane",
        "Ross",
        "Quinn",
        "Drake",
        "Frost",
        "Bell",
        "Steel",
    ]
    _CRIME_SCENES = [
        "Docks District",
        "Neon Boulevard",
        "Industrial Core",
        "Financial Plaza",
        "Harbor Front",
        "Riverside Market",
        "Old Town Square",
        "Central Station",
    ]
    _SCENE_ALIASES = {
        "Docks District": "the shipyard harbor",
        "Neon Boulevard": "the downtown strip",
        "Industrial Core": "the warehouse district",
        "Financial Plaza": "the banking square",
        "Harbor Front": "the pier area",
        "Riverside Market": "the waterfront stalls",
        "Old Town Square": "the cobblestone plaza",
        "Central Station": "the rail terminal",
    }
    _VEHICLES = [
        "Sports Coupe",
        "Heavy Cargo Van",
        "Electric Motorcycle",
        "Luxury Sedan",
        "Pickup Truck",
        "Delivery Truck",
    ]
    _TIMES = ["21:00", "22:00", "23:00", "00:00", "01:00", "02:00"]
    _CYBER_BASES = [15, 22, 33, 45, 52, 60, 75]
    _WIRES = ["Blue Wire", "Green Wire", "Red Wire", "Yellow Wire"]

    @classmethod
    def generate(
        cls, user_id: int, cooldown_secs: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        puzzle_type = random.choices(cls.TYPES, weights=cls.WEIGHTS, k=1)[0]
        now = int(time.time())
        if puzzle_type == "alibi":
            return cls._alibi(user_id, now + cooldown_secs, cls.GC_ALIBI)
        elif puzzle_type == "cyber":
            return cls._cyber(user_id, now + cooldown_secs, cls.GC_CYBER)
        else:
            return cls._timebomb(
                user_id, now + cls.TIMEBOMB_SECONDS, cls.GC_TIMEBOMB
            )

    @classmethod
    def _make_name(cls, used: set[str]) -> str:
        for _ in range(50):
            name = f"{random.choice(cls._FIRST_NAMES)} {random.choice(cls._LAST_NAMES)}"
            if name not in used:
                return name
        return f"Suspect-{random.randint(100, 999)}"

    @classmethod
    def _alibi(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        scene = random.choice(cls._CRIME_SCENES)
        vehicle = random.choice(cls._VEHICLES)
        time_ = random.choice(cls._TIMES)
        alias = cls._SCENE_ALIASES[scene]

        used: set[str] = set()
        names = [cls._make_name(used) for _ in range(4)]
        for n in names:
            used.add(n)

        wrong_vehicle = random.choice([
            v for v in cls._VEHICLES if v != vehicle
        ])
        wrong_vehicle2 = random.choice([
            v for v in cls._VEHICLES if v not in (vehicle, wrong_vehicle)
        ])
        wrong_time = random.choice([t for t in cls._TIMES if t != time_])
        wrong_time2 = random.choice([t for t in cls._TIMES if t != time_])

        # Guilty:     correct vehicle + correct time + correct location (alias)
        # Innocent 1: correct vehicle + correct location, WRONG time  — shares 2/3 clues
        # Innocent 2: WRONG vehicle + correct time + correct location  — shares 2/3 clues
        # Innocent 3: wrong vehicle + wrong time + no location match   — zero clues
        suspects = [
            (
                names[0],
                (
                    f"I was in my {vehicle} near {alias} around {time_} — "
                    f"just passing through, nothing illegal."
                ),
            ),
            (
                names[1],
                (
                    f"Yeah, I drive a {vehicle} and I pass through {alias} sometimes. "
                    f"That night I was there around {wrong_time}, well before any trouble."
                ),
            ),
            (
                names[2],
                (
                    f"I was near {alias} around {time_}, walking home. "
                    f"I ride a {wrong_vehicle} — never drove that night."
                ),
            ),
            (
                names[3],
                (
                    f"I was driving my {wrong_vehicle2} on the other side of town "
                    f"until {wrong_time2}. Never went near that area."
                ),
            ),
        ]
        random.shuffle(suspects)
        guilty_name = names[0]
        correct_idx = next(
            i for i, (n, _) in enumerate(suspects) if n == guilty_name
        )

        opts = "\n".join(
            f'{OPT[i]}. <b>{name}</b>: "<i>{stmt}</i>"'
            for i, (name, stmt) in enumerate(suspects)
        )
        text = "🚔 <b>Case File: Alibi Check</b>\n\n"
        text += f"A <b>{vehicle}</b> fled <b>{scene}</b> at <b>{time_}</b>.\n\n"
        text += f'<b>Witness:</b> <i>"Saw a {vehicle} leaving {alias} at exactly {time_}."</i>\n\n'
        text += "Cross-check vehicle, time, <i>and</i> location. Only one alibi matches all three — that's the liar.\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:police:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _cyber(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        base = random.choice(cls._CYBER_BASES)
        multiplier = random.randint(3, 7)
        offset = random.choice([3, 5, 7, 10, 12, 15])
        encoded = base * multiplier + offset

        # Trap: forgot to subtract offset first (encoded // base)
        trap_no_sub = encoded // base
        # Trap: added offset instead of subtracting ((encoded + offset) // base)
        trap_add = (encoded + offset) // base

        choices: set[int] = {multiplier}
        for trap in (trap_no_sub, trap_add):
            if trap != multiplier and trap > 1:
                choices.add(trap)
        while len(choices) < 4:
            candidate = multiplier + random.choice([-2, -1, 1, 2, 3])
            if candidate > 1:
                choices.add(candidate)
        sorted_opts = sorted(choices)
        correct_idx = sorted_opts.index(multiplier)

        opts = "\n".join(f"{OPT[i]}. {v}" for i, v in enumerate(sorted_opts))
        text = "📡 <b>Cyber Decryption</b>\n\n"
        text += (
            f"Intercepted frequency: <b>{encoded} MHz</b>\n"
            f"Jamming offset: <b>+{offset} MHz</b>\n"
            f"Scanner base frequency: <b>{base} MHz</b>\n\n"
        )
        text += "Step 1: remove the jam offset. Step 2: divide by the base. What multiplier unlocks the signal?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:police:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(len(sorted_opts))
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _timebomb(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        serial = f"BOMB-{random.randint(1000, 9999)}"
        num_str = serial[5:]
        digits = [int(d) for d in num_str]
        last_dig = digits[-1]
        digit_sum = sum(digits)
        threshold = random.choice([10, 12, 14])

        high_sum = digit_sum > threshold
        even_last = last_dig % 2 == 0

        wires = list(cls._WIRES)
        random.shuffle(wires)
        sorted_wires = sorted(wires)

        # 2×2 matrix → alphabetical wire index
        # high_sum+even → 0 (1st), high_sum+odd → 1 (2nd)
        # low_sum+even  → 2 (3rd), low_sum+odd  → 3 (4th)
        wire_alpha_idx = (0 if high_sum else 2) + (0 if even_last else 1)
        correct_wire = sorted_wires[wire_alpha_idx]
        correct_idx = wires.index(correct_wire)

        parity_word = "EVEN" if even_last else "ODD"
        sum_symbol = ">" if high_sum else "≤"

        opts = "\n".join(f"{OPT[i]}. {w}" for i, w in enumerate(wires))
        text = f"💣 <b>BOMB DEFUSAL — {cls.TIMEBOMB_SECONDS}s</b>\n\n"
        text += f"Serial: <code>{serial}</code>\n"
        text += f"Digit sum: <b>{digit_sum}</b>  |  Last digit: <b>{last_dig}</b> ({parity_word})\n\n"
        text += (
            "<b>Cut table (alphabetical order):</b>\n"
            f"  Sum &gt; {threshold} + last EVEN → 1st\n"
            f"  Sum &gt; {threshold} + last ODD  → 2nd\n"
            f"  Sum ≤ {threshold} + last EVEN → 3rd\n"
            f"  Sum ≤ {threshold} + last ODD  → 4th\n\n"
        )
        text += f"This serial: sum {sum_symbol} {threshold}, last digit {parity_word}. Which wire?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:police:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── Gangster ─────────────────────────────────────────────────────────────────


class GangsterPuzzles:
    GC_SNITCH = 0
    GC_LAUNDER = 1
    GC_VAULT = 2

    TYPES = ["snitch", "launder", "vault"]
    WEIGHTS = [70, 20, 10]
    TIMEBOMB_SECONDS = 45

    _FIRST_NAMES = [
        "Tommy",
        "Lucky",
        "Vinnie",
        "Tony",
        "Razor",
        "Smokey",
        "Skinny",
        "Dante",
        "Marco",
        "Sal",
        "Bruno",
        "Nico",
        "Carmine",
        "Luca",
        "Frankie",
    ]
    _LAST_NAMES = [
        "Two-Times",
        "The Knife",
        "Scaletta",
        "Marconi",
        "Gotti",
        "Capone",
        "Ricci",
        "Bianchi",
        "Moretti",
        "Conti",
        "Ferraro",
        "Vitale",
        "Mancini",
        "De Luca",
        "Esposito",
    ]
    _DROP_LOCATIONS = [
        "Customs Warehouse",
        "North Pier Slums",
        "Luxury Casino Vault",
        "Chemical Plant",
        "Meatpacking District",
        "Underground Parking",
        "Harbor Freight Yard",
        "Abandoned Foundry",
    ]
    _LOCATION_ALIASES = {
        "Customs Warehouse": "the federal impound lot",
        "North Pier Slums": "the marine shipyard zone",
        "Luxury Casino Vault": "the high-roller cash terminal",
        "Chemical Plant": "the industrial bio-hazard grid",
        "Meatpacking District": "the cold-storage loading bay",
        "Underground Parking": "the sub-level concrete garage",
        "Harbor Freight Yard": "the container logistics hub",
        "Abandoned Foundry": "the derelict smelting complex",
    }
    _VEHICLES = [
        "Cargo Van",
        "Armored Truck",
        "Speedboat",
        "Blacked-out SUV",
        "Flatbed Lorry",
        "Motorbike",
    ]
    _TIMES = ["00:00", "01:00", "02:00", "03:00", "04:00", "23:00"]
    _LAUNDER_BASES = [10000, 12000, 14000, 15000, 18000, 22000]
    _RELAYS = ["Brass Relay", "Copper Relay", "Gold Relay", "Silver Relay"]

    @classmethod
    def generate(
        cls, user_id: int, cooldown_secs: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        puzzle_type = random.choices(cls.TYPES, weights=cls.WEIGHTS, k=1)[0]
        now = int(time.time())
        if puzzle_type == "snitch":
            return cls._snitch(user_id, now + cooldown_secs, cls.GC_SNITCH)
        elif puzzle_type == "launder":
            return cls._launder(user_id, now + cooldown_secs, cls.GC_LAUNDER)
        else:
            return cls._vault(user_id, now + cls.TIMEBOMB_SECONDS, cls.GC_VAULT)

    @classmethod
    def _make_name(cls, used: set[str]) -> str:
        for _ in range(50):
            name = f"{random.choice(cls._FIRST_NAMES)} {random.choice(cls._LAST_NAMES)}"
            if name not in used:
                return name
        return f"Unknown-{random.randint(100, 999)}"

    @classmethod
    def _snitch(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        location = random.choice(cls._DROP_LOCATIONS)
        vehicle = random.choice(cls._VEHICLES)
        time_ = random.choice(cls._TIMES)
        alias = cls._LOCATION_ALIASES[location]

        used: set[str] = set()
        names = [cls._make_name(used) for _ in range(4)]
        for n in names:
            used.add(n)

        wrong_vehicle = random.choice([
            v for v in cls._VEHICLES if v != vehicle
        ])
        wrong_time = random.choice([t for t in cls._TIMES if t != time_])

        suspects = [
            (
                names[0],
                (
                    f"I was hanging around {alias} near {time_} in my personal car, "
                    f"but I didn't see any action or cops."
                ),
            ),
            (
                names[1],
                (
                    f"I was cross-town all night moving the crew's {wrong_vehicle} to the safehouse. "
                    f"I have a full alibi."
                ),
            ),
            (
                names[2],
                (
                    f"I went completely off-grid and was asleep in my apartment by {wrong_time}. "
                    f"My phone logs prove it."
                ),
            ),
            (
                names[3],
                (
                    f"My vehicle was broken down, so I ditched the drop and grabbed a burner taxi "
                    f"to the south hideout."
                ),
            ),
        ]
        random.shuffle(suspects)
        rat_name = names[0]
        correct_idx = next(
            i for i, (n, _) in enumerate(suspects) if n == rat_name
        )

        briefing = (
            f"Our contraband drop at <b>{location}</b> got raided — "
            f"SWAT arrived in a <b>{vehicle}</b> at exactly <b>{time_}</b>. "
            f"Find the rat who leaked it."
        )
        opts = "\n".join(
            f'{OPT[i]}. <b>{name}</b>: "<i>{stmt}</i>"'
            for i, (name, stmt) in enumerate(suspects)
        )
        text = "🔫 <b>Syndicate Desk: Root Out The Rat</b>\n\n"
        text += f"{briefing}\n\n"
        text += "Which crew member is the snitch?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = (
            f"work:gangster:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        )
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _launder(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        base = random.choice(cls._LAUNDER_BASES)
        multiplier = random.randint(4, 8)
        target = base * multiplier

        offsets: set[int] = {multiplier}
        while len(offsets) < 4:
            candidate = multiplier + random.choice([-2, -1, 1, 2, 3])
            if candidate > 1:
                offsets.add(candidate)
        sorted_opts = sorted(offsets)
        correct_idx = sorted_opts.index(multiplier)

        opts = "\n".join(
            f"{OPT[i]}. \xd7{v}" for i, v in enumerate(sorted_opts)
        )
        text = "💸 <b>Offshore Shell Cleaner Terminal</b>\n\n"
        text += (
            f"Raw street take: <b>${base:,}</b>\n"
            f"Clean ledger target: <b>${target:,}</b>\n\n"
        )
        text += "Select the correct banking multiplier code.\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = (
            f"work:gangster:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        )
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _vault(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        serial = f"SAFE-{random.randint(1000, 9999)}"
        last_dig = int(serial[-1])
        is_even = last_dig % 2 == 0

        relays = list(cls._RELAYS)
        random.shuffle(relays)
        sorted_relays = sorted(relays)

        correct_relay = sorted_relays[0] if is_even else sorted_relays[-1]
        correct_idx = relays.index(correct_relay)

        parity_word = "EVEN" if is_even else "ODD"
        position = "FIRST" if is_even else "LAST"
        rule = (
            f"Serial ends in <b>{last_dig}</b> ({parity_word}) — "
            f"short the relay that is <b>{position}</b> alphabetically."
        )

        opts = "\n".join(f"{OPT[i]}. {r}" for i, r in enumerate(relays))
        text = f"🚨 <b>ALARM TRIP — {cls.TIMEBOMB_SECONDS}s</b>\n\n"
        text += f"Vault serial: <code>{serial}</code>\n\n"
        text += f"Rule: {rule}\n\n"
        text += "Which relay do you short?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = (
            f"work:gangster:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        )
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── Thief ────────────────────────────────────────────────────────────────────


class ThiefPuzzles:
    GC_MARK = 0
    GC_FENCE = 1
    GC_LASER = 2

    TYPES = ["mark", "fence", "laser"]
    WEIGHTS = [70, 20, 10]
    TIMEBOMB_SECONDS = 45

    _CODE_PREFIXES = [
        "Sly",
        "Cat",
        "Ghost",
        "Silk",
        "Phantom",
        "Shadow",
        "Nimble",
        "Viper",
        "Swift",
        "Silver",
        "Onyx",
        "Azure",
        "Marble",
        "Ember",
        "Cobalt",
    ]
    _CODE_SUFFIXES = [
        "Fox",
        "Burglar",
        "Prowler",
        "Mac",
        "Finch",
        "Whisper",
        "Lynx",
        "Rook",
        "Drake",
        "Crane",
        "Sparrow",
        "Kestrel",
        "Marten",
        "Ferret",
        "Dagger",
    ]
    _VENUES = [
        "Grand Plaza Gala",
        "Art Gallery Auction",
        "Penthouse Soiree",
        "Embassy Ball",
        "Rooftop Garden Party",
        "Museum Charity Night",
    ]
    _LOOT_ITEMS = [
        "Emerald Necklace",
        "Sapphire Ring",
        "Diamond Tiara",
        "Ruby Brooch",
        "Amber Pendant",
        "Onyx Cufflinks",
    ]
    _LOOT_ALIASES = {
        "Emerald Necklace": "the green gemstone choker",
        "Sapphire Ring": "the blue velvet band",
        "Diamond Tiara": "the crystalline crown piece",
        "Ruby Brooch": "the crimson chest pin",
        "Amber Pendant": "the golden resin droplet",
        "Onyx Cufflinks": "the jet-black sleeve studs",
    }
    _TIMES = ["20:00", "21:00", "22:00", "23:00", "00:00", "01:00"]
    _FENCE_BASES = [5000, 6500, 8000, 9500, 11000, 13000]
    _NODES = ["Amber Node", "Jade Node", "Ruby Node", "Topaz Node"]

    @classmethod
    def generate(
        cls, user_id: int, cooldown_secs: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        puzzle_type = random.choices(cls.TYPES, weights=cls.WEIGHTS, k=1)[0]
        now = int(time.time())
        if puzzle_type == "mark":
            return cls._mark(user_id, now + cooldown_secs, cls.GC_MARK)
        elif puzzle_type == "fence":
            return cls._fence(user_id, now + cooldown_secs, cls.GC_FENCE)
        else:
            return cls._laser(user_id, now + cls.TIMEBOMB_SECONDS, cls.GC_LASER)

    @classmethod
    def _make_codename(cls, used: set[str]) -> str:
        for _ in range(50):
            name = f"Code: {random.choice(cls._CODE_PREFIXES)} {random.choice(cls._CODE_SUFFIXES)}"
            if name not in used:
                return name
        return f"Code: Target-{random.randint(100, 999)}"

    @classmethod
    def _mark(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        venue = random.choice(cls._VENUES)
        loot = random.choice(cls._LOOT_ITEMS)
        time_ = random.choice(cls._TIMES)
        alias = cls._LOOT_ALIASES[loot]

        used: set[str] = set()
        names = [cls._make_codename(used) for _ in range(4)]
        for n in names:
            used.add(n)

        wrong_loot = random.choice([l for l in cls._LOOT_ITEMS if l != loot])
        wrong_time = random.choice([t for t in cls._TIMES if t != time_])

        guests = [
            (
                names[0],
                (
                    f"Seen heading to the south terrace to deposit {alias} with staff "
                    f"at exactly {time_}."
                ),
            ),
            (
                names[1],
                (
                    f"Spotted near the buffet all evening, showing off a {wrong_loot} "
                    f"to other guests."
                ),
            ),
            (
                names[2],
                (
                    f"Arrived fashionably late through the main gate near {wrong_time}, "
                    f"carrying a leather briefcase."
                ),
            ),
            (
                names[3],
                (
                    "Left early via a private driver — no notable jewelry visible."
                ),
            ),
        ]
        random.shuffle(guests)
        target_name = names[0]
        correct_idx = next(
            i for i, (n, _) in enumerate(guests) if n == target_name
        )

        briefing = (
            f"Venue: <b>{venue}</b> — a VIP is carrying a <b>{loot}</b> "
            f"and will secure it at <b>{time_}</b>. Spot them first."
        )
        opts = "\n".join(
            f'{OPT[i]}. <b>{name}</b>: "<i>{obs}</i>"'
            for i, (name, obs) in enumerate(guests)
        )
        text = "🕵️ <b>Thief Binoculars: Spot the VIP Mark</b>\n\n"
        text += f"{briefing}\n\n"
        text += "Which guest is the target?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:thief:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _fence(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        base = random.choice(cls._FENCE_BASES)
        multiplier = random.randint(3, 6)
        offer = base * multiplier

        offsets: set[int] = {multiplier}
        while len(offsets) < 4:
            candidate = multiplier + random.choice([-2, -1, 1, 2])
            if candidate > 1:
                offsets.add(candidate)
        sorted_opts = sorted(offsets)
        correct_idx = sorted_opts.index(multiplier)

        opts = "\n".join(
            f"{OPT[i]}. \xd7{v}" for i, v in enumerate(sorted_opts)
        )
        text = "🧳 <b>Black Market Pawn Terminal</b>\n\n"
        text += (
            f"Stash melt value: <b>${base:,}</b>\n"
            f"Broker's lump-sum offer: <b>${offer:,}</b>\n\n"
        )
        text += "What is the street valuation multiplier?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:thief:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    @classmethod
    def _laser(
        cls, user_id: int, expires_at: int, game_code: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        serial = f"GRID-{random.randint(1000, 9999)}"
        last_dig = int(serial[-1])
        is_even = last_dig % 2 == 0

        nodes = list(cls._NODES)
        random.shuffle(nodes)
        sorted_nodes = sorted(nodes)

        correct_node = sorted_nodes[0] if is_even else sorted_nodes[-1]
        correct_idx = nodes.index(correct_node)

        parity_word = "EVEN" if is_even else "ODD"
        position = "FIRST" if is_even else "LAST"
        rule = (
            f"Grid serial ends in <b>{last_dig}</b> ({parity_word}) — "
            f"disrupt the node that is <b>{position}</b> alphabetically."
        )

        opts = "\n".join(f"{OPT[i]}. {n}" for i, n in enumerate(nodes))
        text = f"🚨 <b>LASER GRID ALERT — {cls.TIMEBOMB_SECONDS}s</b>\n\n"
        text += f"Grid serial: <code>{serial}</code>\n\n"
        text += f"Rule: {rule}\n\n"
        text += "Which node do you disrupt?\n\n"
        text += f"<blockquote>{opts}</blockquote>"

        prefix = f"work:thief:{user_id}:{expires_at}:{game_code}:{correct_idx}"
        buttons = [
            [
                InlineKeyboardButton(text=OPT[i], callback_data=f"{prefix}:{i}")
                for i in range(4)
            ]
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)
