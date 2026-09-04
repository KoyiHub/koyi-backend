# Koyi backend — context handoff

Everything a new session needs to pick this up. Written after phase 7, with the
whole build order complete.

Read this first, then [`docs/frontend-integration.md`](./frontend-integration.md)
for the endpoint contract. The design document is an artifact:
<https://claude.ai/code/artifact/317bf3fa-74ca-47dd-a353-d9c6dc472047> — it is the
anchor for *why*, and section 2 of it wins over anything that contradicts it.

---

## 1. What this is

A foundational literacy and numeracy (FLN) platform for Nigerian primary
schools, Grades 1–6. It runs a Teaching-at-the-Right-Level loop:

```
Assess → Diagnose → Place → Group → Teach (AI plan) → Reassess
```

It is **not** an exam system, though the assessment step borrows an exam-like
interface because that is a familiar way to collect answers on a device. Four
things about it govern nearly every design decision:

1. **Levels 1–5 are developmental bands, not grades.** A Level 4 child is not
   "Primary 4". Grade and class are organisational only — they never drive
   assessment, placement or grouping.
2. **Literacy and numeracy move independently.** There is no combined level, no
   overall score, no "strong / weak" verdict. An average across the two
   describes neither.
3. **Placement is absolute.** Each assessment sets the level outright. A child
   placed at Level 4 who later assesses at Level 2 *is* Level 2. No promotion
   ladder, no carry-forward.
4. **A level is what a child needs taught next, not what they have mastered.**
   It is the lowest level the paper *probed* that they did not pass. "Level 2"
   reads as *working on Level 2*, never *completed Level 2*.

Point 4 has a consequence that has already bitten once and will again: placement
counts **only probed levels**. A paper spanning Levels 3–5 must not floor every
child who sits it.

---

## 2. Stack and conventions

Django 5.2 + DRF, `uv`, Python 3.13, pytest, ruff, mypy (django-stubs),
Celery + Redis, PostgreSQL (SQLite in tests). 12 apps, ~18k lines, 284 tests.

```
make check     # ruff, ruff format --check, mypy, deploy check, migration check
make test      # pytest, parallel
make run       # dev server on :8000
```

`make check` currently reports exactly one warning — a dev `SECRET_KEY` — and
that is expected; production supplies a real one. Anything else is a regression.

### Layering, which is enforced by habit rather than by a linter

```
views  →  services  →  repositories  →  ORM
```

- **Views** check permissions, hand validated input to a service, and render the
  result. They never filter by a school id taken from the request.
- **Services** own business rules, raise `ApplicationError` subclasses from
  `apps/common/services.py`, and never touch `request` or DRF types.
- **Repositories** own tenant-scoped queries. Every portal repository takes the
  acting `School` at construction, so a view that forgets to filter still cannot
  serve another tenant's rows.
- **Serializers** validate and render only.

Multi-tenancy is enforced *in repositories*, not in views. This is load-bearing:
a cross-tenant hole was found in exactly the one place that broke the rule
(`ReferenceDataRepository.get_class()` was unscoped, letting a school attach a
teacher to another school's class).

### Other conventions worth matching

- **One migration per module.** A stated preference. Two apps have two
  migrations each; nothing has three.
- **Comments explain *why*, not what.** The codebase reads as prose in places
  and that is deliberate — match the density and register of the file you are
  editing rather than the repo average.
- **Tasks take ids, never model instances**, and every one is safe to run twice.
- **Frozen dataclass DTOs** for anything crossing a service boundary
  (`apps/*/dto.py`).
- **Enums live next to their app's models**; cross-cutting ones in
  `apps/common/enums.py`.

---

## 3. Build order — all complete

| Phase | Commit | What landed |
|---|---|---|
| P0 | `1163833` | Taxonomy (`Skill`/`Subskill`), Question rework, `SchoolClass` tenant scoping, `Student.is_active` + check constraint, `Activity` as the audit log, `Parent` dropped |
| P1 | `2bd23d8` | Assessment authoring, sections, bank browsing, coverage preview, publish |
| P2 | `f3f80c3` | Assignment by class, the sitting flow, sequential section unlock |
| — | `8884534` | Per-child sitting codes (`AssessmentAssignment.code`) and `docs/frontend-integration.md` |
| — | `a61923a` | `Student.guardian_email` |
| P3 | `de816f1` | Objective marking, the skill matrix, the five placement rules, `StudentProfile` |
| P4 | `382f6ed` | The AI layer — provider protocol, Ollama + scripted adapters, transcription, 8 prompt documents |
| P5 | `faee824` | Analytics: level distribution, the "who needs help" roster, movement between rounds |
| P6 | `95cf431` | Rule-based groups with membership history; two-tier lesson plans |
| P7 | `bf5b27a` | School management: OTP, two-step deletions, transfers, class lifecycle, activity feed, retention purge |

Two earlier commits (`bd6c17a`, `77916ba`) predate the phase plan — they are the
original data models and the JWT auth the phases were then built on top of.

Current branch: `feat/data-model`. Main branch: `main`. Nothing has been merged
to `main` yet.

---

## 4. The decisions that are load-bearing

These are the ones where a reasonable-looking change would break something
non-obvious. Each is defended in a docstring at the location named.

### Placement — `apps/assessments/placement.py`

Fully deterministic. **No model call anywhere in this path**, because placement
has to be reproducible, unit-testable, explainable to a head teacher, and free.

```
70% of items in a (skill, level) cell   → the cell passes
80% of a skill's cells at a level       → the skill passes at that level
PlacementRule.required_skills           → the level passes
lowest PROBED level not passed          → the placement
```

Thresholds are **seeded rows, not constants in code**, so changing one and
re-running applies it without re-marking anything.

`MarkingService._selection_matches` returns `None` — not `False` — when a
question has no answer key. An authoring fault must never land in a child's
diagnosis; the response stays pending for a human instead.

### Sittings — `apps/student_portal/sessions.py`

A child has **no `User` row**. An earlier attempt at a `StudentPrincipal` that
claimed `is_authenticated` and answered `has_perm` was deleted outright — it was
describing a user that does not exist. What happens instead: the child types the
paper's code plus **their own per-assignment code** (`AssessmentAssignment.code`,
retrievable by the teacher at any time), and gets an opaque session back. It is
a capability, not an identity.

A student id is public and never proves anything. Do not reintroduce it as an
auth factor.

### Verification codes — `apps/users/verification.py`

One table behind registration, the second factor at sign-in, password reset and
the two-step deletions. What makes six digits safe is **not the length**: ten
minute expiry, single use with issuing retiring the old, and **five wrong
guesses burn the code itself** — counted on the row rather than on the address,
so an attacker with a thousand hosts still gets five tries.

The two secrets on that row are hashed differently on purpose. Read the
docstring on `VerificationCode` before changing either.

### Soft deletes — `apps/schools/models.py`, `apps/common/tasks.py`

`Student` and `Teacher` hide on delete and are destroyed by a nightly purge
after `DELETED_ROW_RETENTION_DAYS` (90). The delay is doing real work: a child's
results cascade off that row.

**`generate_student_id` / `generate_teacher_id` must use `all_objects`.** A
hidden row still holds its number and the unique constraint does not know the
difference. Switching those back to `objects` reissues retired ids.

### Groups — `apps/instruction/grouping.py`

Two clocks, deliberately out of step. **Membership is live** — a child who
progresses past a group's criteria leaves immediately, because the profile must
always say where they actually are. **The group holds** for `STABILITY_DAYS`
(14) so a plan written for it survives long enough to be delivered.

A group with **no criteria matches nobody**. Matching everybody is the more
dangerous reading of the same silence.

A teacher's manual addition (`join_reason == ADDED`) is never closed by the
rules engine.

### The AI layer — `apps/ai/`

Provider-swappable behind `LLMClient`. Grammar-constrained decoding (Ollama's
`format` takes the JSON Schema), `temperature: 0`. **`AI_ENABLED=False` by
default** — the whole loop then runs against `ScriptedClient`, so a missing
provider degrades rather than breaks.

Eight job types, each with its own prompt documents seeded from markdown in
`apps/ai/documents/` and pinned by content hash, so an edit creates a new row
and every call records which version it used.

The model never makes a diagnosis. Its job is turning a deterministic diagnosis
into language a teacher can act on.

### Lesson plans — `apps/instruction/plans.py`

Canonical library (authored once per subskill × level) → group adaptation →
student delta. A failed adaptation serves the **canonical plan** with
`status=FALLBACK`; only a plan with nothing behind it is `FAILED`. Both are
honest states, not errors. A student note is written only where a child diverges
from their group by two or more weaknesses.

---

## 5. What is not built

Nothing is blocked; these are simply out of scope so far.

**Teacher surface, still marked *Planned* in the frontend doc:**

- `POST /v1/teacher/auth/password/change/`
- `POST /v1/teacher/auth/password/reset/request/` and `/confirm/`
- Guardian links — sending an assignment code to a guardian's email **on demand
  by the teacher**, never automatically. Decided, not implemented.
- `GET /v1/teacher/assessments/{id}/results/` — the per-paper results table
- `GET /v1/teacher/students/` — students in the teacher's classes

The three verification pieces would reuse `apps/users/verification.py`
unchanged; only the endpoints are missing.

**The product admin surface** — the third role alongside school and teacher — is
deliberately out of scope throughout and needs planning separately.

**Never exercised against the real thing:**

- No model has ever run against these prompts. All eight job types have guidance
  and the plumbing is fully tested, but every test uses `ScriptedClient`.
- Whisper has never transcribed a real child. Accuracy on Nigerian-accented
  child speech reading English is the open risk, and the confidence gate that
  routes poor transcripts to a teacher is untested against real audio.
- Placement thresholds are seeded defaults. Numeracy Level 4 requires all three
  of its skills, the strictest cell in the system; only real cohorts will show
  whether that is right.

---

## 6. Open decisions

These need the user, not a code change. Full versions in section 11 of the
artifact.

| Question | Why it matters |
|---|---|
| **Is there an assessment cycle** — a round object between the academic session and an assessment? | The one most likely to bite. Movement currently compares the last two placements, which works but cannot answer "this term". |
| **Where does a school's resource tier live?** It is currently per group. | A school-level default a group can override is probably right, but it is a schema change. |
| **Are teacher password resets self-service, admin-triggered, or both?** | Admin-triggered is built. Whether the teacher login page also offers self-service changes that page's scope. |
| **Does school management see individual results, or only levels?** | Shapes `/students/{id}/fln/`, which currently returns levels plus recent scores. |
| **Should the send-link flow offer printing inline** for children with no guardian email? | The roster already prints every code, so the capability exists. |
| **What happens when a sitting session expires mid-section** — resume or restart? | Answers are saved on every change, so resuming is possible. It is a fairness question. |
| **Does the runner need to work on a phone**, or tablet and up? | Sets layout minimums for the child-facing app. |

---

## 7. Working agreements

Two standing instructions, both in project memory and both absolute:

1. **Never commit until the user has reviewed and explicitly asks.** Build,
   verify, report — then wait.
2. **Commits omit the `Co-Authored-By` trailer.**

Other things learned about how this user works:

- They push back on abstractions that exist to satisfy machinery rather than the
  domain. The `StudentPrincipal` deletion is the canonical example, and the
  instinct was right.
- They correct specifics precisely and expect the correction to propagate — to
  the code, the frontend doc, and the artifact, in the same change.
- They ask for the artifact and `docs/frontend-integration.md` to be kept current
  as implementation moves. Treat both as part of the deliverable, not as
  documentation to catch up on later.
- When something in the design turns out to be wrong, they want it recorded in
  the artifact rather than quietly fixed.

---

## 8. If you are picking this up cold

Fastest path to being useful:

1. `make check && make test` — confirm 284 passing and one expected warning.
2. Read `apps/assessments/placement.py` top to bottom. It is the heart of the
   product and the file whose docstrings explain the most.
3. Skim `docs/frontend-integration.md` §1 and §3 for the surface layout, then
   the section for whatever you are touching.
4. Open the artifact's section 2 (the settled-decisions table) before proposing
   anything structural. It wins over everything else.
