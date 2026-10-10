"""Synthetic residents for performance testing. No real resident data.

Every name, comment, rotation and evaluator here is invented. The comments are
written to look like committee notes and MedHub comments so that AUC's router
treats them the way it treats real ones: most carry an ontology keyword and are
routed by keyword; about one in six carries none and goes through the
retrieval engine's semantic fallback (the Nemotron embed + rerank containers on
a Spark), exactly as a real comment with no keyword would.

The output is deterministic (fixed seed), so every serving engine sees the same
ten residents with the same comments in the same order.

    python synthetic_residents.py > synthetic_residents.json
"""

import json
import random

SEED = 20261009

# Comment counts per resident: a spread from a light record to a heavy one.
VOLUMES = [12, 16, 20, 24, 28, 32, 38, 44, 52, 60]

ROTATIONS = ["Wards A", "Wards B", "MICU", "CCU", "Night Float", "Ambulatory Block",
             "Geriatrics", "ED Elective", "Cardiology Consults", "VA Clinic"]
EVALUATORS = ["Attending A", "Attending B", "Attending C", "Chief Resident",
              "Nurse Educator", "Clinic Preceptor", "Fellow"]
SOURCES = ["attending", "program director", "chief resident", "peer", "nursing"]
DOMAIN_TAGS = ["Patient Care", "Medical Knowledge", "Systems-Based Practice",
               "Practice-Based Learning", "Professionalism", "Communication", "General"]

# (text) — each line carries at least one ontology keyword for its target area.
KEYWORDED = [
    # PC1 history
    "History taking is organized and hypothesis-driven; she reliably asks about the chief complaint timeline before jumping to the exam.",
    "Medication reconciliation on admission was incomplete twice this block, missing home anticoagulants that the pharmacist caught.",
    "Uses secondary sources well, calling the nursing home and the outpatient cardiologist to fill gaps in the patient interview.",
    # PC2 exam
    "Physical exam is thorough and he picked up a new diastolic murmur that the overnight team had missed.",
    "Bedside examination is rushed on busy call days; abnormal findings are sometimes documented without being re-checked.",
    "Exam technique for volume status has improved a lot since the start of the year.",
    # PC3 reasoning
    "Clinical reasoning is strong; her differential diagnosis for the syncope admission was prioritized and well defended on rounds.",
    "Showed some anchoring on a pneumonia diagnosis and premature closure before the CT came back showing a PE.",
    "Presents a clear clinical impression and can articulate why lower-probability items stay on the differential diagnosis.",
    # PC4 inpatient
    "Inpatient management of a complicated GI bleed was excellent; the management plan anticipated the need for a second scope.",
    "Triage of new admissions overnight showed good judgment about acuity and triage when the list grew to fourteen patients.",
    "Needs prompting to think about high-value care; ordered daily labs on stable patients for most of the week.",
    # PC5 outpatient
    "In clinic, chronic disease management for diabetic patients is methodical and preventive care gaps are closed at each visit.",
    "Used motivational interviewing with a patient ambivalent about statins and documented a realistic follow-up plan.",
    "Outpatient management for patients with multiple chronic conditions is still overwhelming for him; visits run long.",
    # PC6 digital
    "Built smart phrases and order sets for the heart failure clinic that the whole team now uses in the EHR.",
    "Telehealth visits are efficient, though she sometimes forgets to confirm the patient's location at the start.",
    # MK1
    "Explains the pathophysiology of hepatorenal syndrome clearly to students and connects it to the bedside findings.",
    "Basic science knowledge of acid-base physiology is a relative weakness that showed up on the in-training exam.",
    # MK2
    "Therapeutic knowledge is solid; she weighed the risks and benefits of anticoagulation in an elderly fall-risk patient carefully.",
    "Antibiotic stewardship is a strength; he narrows coverage promptly once cultures result.",
    "Needed help with treatment selection for refractory hyponatremia and was unsure of the indications and contraindications for tolvaptan.",
    # MK3
    "Diagnostic testing is thoughtful; she discussed pretest probability before ordering a D-dimer.",
    "Test interpretation of ABGs and ECGs is reliable and fast during rapid responses.",
    "Tends to order broad panels without a diagnostic strategy; we discussed sensitivity and specificity in feedback.",
    # SBP1
    "Led a QI project on reducing telemetry overuse that cut unnecessary days by a fifth on the ward.",
    "Filed a safety event report after a near miss with an insulin order and walked the team through the root cause analysis.",
    "Disclosed a medication error to a family with the attending present and handled the error disclosure with honesty.",
    # SBP2
    "Hand-off quality is excellent; I-PASS sign-out is complete and the night team rarely has to call.",
    "Strong care coordination for complex discharges, working closely with case management and social work.",
    "Transitions of care need work; two patients left without a follow-up appointment arranged.",
    # SBP3
    "Understands observation versus inpatient status and explains cost of care considerations to families.",
    "Gave a noon conference on Medicare and Medicaid coverage gaps that residents found very useful.",
    # PBLI1
    "Brings primary literature to rounds and led an excellent journal club on SGLT2 inhibitors in heart failure.",
    "Applies clinical practice guidelines appropriately but sometimes struggles to frame a focused clinical question.",
    "Critical appraisal skills are developing; she identified the main bias in the trial we discussed.",
    # PBLI2
    "Actively seeks feedback after every call night and has an individualized learning plan with concrete goals.",
    "Self-reflection is limited; when given feedback about note timeliness he became defensive rather than curious.",
    "Shows humility and adaptability after difficult cases and talks openly about performance gaps.",
    # PROF1
    "Professional behavior on the wards is exemplary; she accepts responsibility for misses without deflecting.",
    "Punctuality has been an issue on the ambulatory block, arriving late to clinic three times this month.",
    "Had one professionalism lapse during a heated exchange with a consultant; he apologized the same day.",
    # PROF2
    "Handled an ethical dilemma about decision-making capacity in a patient refusing dialysis with maturity.",
    "Requested an ethics consult appropriately and respected patient autonomy throughout.",
    # PROF3
    "Discharge summaries are consistently late, and work-hour logs were not completed on time this quarter.",
    "Very reliable; follow-through on pending results after discharge is excellent.",
    "Completes administrative tasks on time and is known for accountability on the team.",
    # PROF4
    "Seemed close to burnout during the ICU month; we connected her with wellness resources and she used them.",
    "Models self-care and resilience for interns and talks openly about the emotional challenges of physician work.",
    # ICS1
    "Builds rapport quickly with patients and uses teach-back routinely before discharge.",
    "Ran a family meeting with empathy and clear language, using an interpreter skillfully for a Spanish-speaking family.",
    "Shared decision making with patients is a strength; she lays out options without steering.",
    # ICS2
    "Team communication is clear; uses closed-loop communication during codes and consult calls.",
    "Conflict resolution with nursing staff has improved; he now gives and receives feedback calmly.",
    "Consultation request and response could be more focused; questions to consultants are sometimes vague.",
    # ICS3
    "Documentation is concise and thorough; progress notes are among the best on the service.",
    "Note quality slips on post-call days with copy-forward errors in the assessment and plan.",
    "Avoids stigmatizing language in notes and has coached interns on it.",
]

# No ontology keyword at all: these exercise the retrieval engine's fallback.
UNKEYWORDED = [
    "Always the calmest person in the room when a patient decompensates.",
    "Interns on her team say they learn more from her than from anyone else this year.",
    "Struggled noticeably during the first two weeks of the cardiac unit but caught up quickly.",
    "Comes to rounds prepared with overnight events, vitals trends and a plan for every patient.",
    "Would trust him to run the unit overnight without backup.",
    "Sometimes talks over students on rounds and needs to give them more room.",
    "Took on extra admissions when a colleague was out sick without being asked.",
    "Presentations are long and include too much detail for the audience.",
    "Asked thoughtful questions at the Friday teaching conference.",
    "Works well under pressure and keeps the team focused during busy admitting days.",
    "Was slow to escalate a deteriorating patient to the senior on one occasion.",
    "Nurses specifically mentioned how approachable she is at three in the morning.",
]


def make_resident(i, n_comments, rng):
    """One synthetic resident with n_comments comments, in AUC's {text, label} shape."""
    pgy = 1 + (i % 3)
    label = f"Synthetic Resident {i + 1:02d}, PGY-{pgy}"
    comments = []
    for k in range(n_comments):
        if rng.random() < 1 / 6:
            text = rng.choice(UNKEYWORDED)
        else:
            text = rng.choice(KEYWORDED)
        day = 1 + (k * 7) % 28
        month = 1 + (k // 4) % 9
        if rng.random() < 0.55:
            src = rng.choice(SOURCES)
            dom = rng.choice(DOMAIN_TAGS)
            comments.append({
                "text": text,
                "label": f"committee note, source={src}, tagged domain={dom}, "
                         f"2026-{month:02d}-{day:02d} 10:00:00",
            })
        else:
            comments.append({
                "text": text,
                "label": f"MedHub eval, rotation={rng.choice(ROTATIONS)}, "
                         f"evaluator={rng.choice(EVALUATORS)}, score={rng.choice([3, 3.5, 4, 4.5, 5, 6, 7])}, "
                         f"2026-{month:02d}-{day:02d}",
            })
    return {"id": f"synthetic-{i + 1:02d}", "label": label, "comments": comments}


def build():
    rng = random.Random(SEED)
    return [make_resident(i, n, rng) for i, n in enumerate(VOLUMES)]


if __name__ == "__main__":
    print(json.dumps({"seed": SEED, "residents": build()}, indent=1))
