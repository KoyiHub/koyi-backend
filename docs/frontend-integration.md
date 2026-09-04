# Koyi — Frontend Integration Guide

The contract between `koyi-backend` and `koyi-web`: every endpoint, what it
does, what it takes, what it returns, and which page calls it.

This document covers **what is being built**, not only what exists today. An
endpoint marked *Planned* is agreed in design and safe to build a page
against; its shape may still move, and this document moves with it. When an
implementation changes something that matters to the frontend, the change
lands here in the same commit.

**Status legend**

| | |
|---|---|
| **Built** | Implemented, tested, callable now |
| **Planned** | Agreed design, not yet implemented |
| **Proposed** | Needs a decision before building |

---

## 1. The product in one page

Koyi is a foundational literacy and numeracy platform for Grades 1–6. It runs
a Teaching-at-the-Right-Level loop:

```
Assess → Diagnose → Place → Group → Teach (AI plan) → Reassess
```

It is **not** an exam system, though the assessment step borrows an exam-like
interface because that is a familiar way to collect answers on a device.

Three things follow that shape the UI, and are worth holding onto:

1. **Levels 1–5 are developmental bands, not grades.** A Level 4 child is not
   "Primary 4". Grade and class are organisational only — they never drive
   assessment, placement or grouping.
2. **Literacy and numeracy levels move independently.** A child can be Level 4
   in numeracy and Level 2 in literacy. Never show one combined level, one
   overall score, or a single "strong / weak" verdict.
3. **Placement is absolute.** Each assessment sets the level outright. A child
   placed at Level 4 who later assesses at Level 2 *is* Level 2. There is no
   promotion ladder and no carry-forward.

### The three surfaces

| Surface | Who | Auth | Base |
|---|---|---|---|
| School management | School admin account | JWT + OTP | `/api/v1/school/` |
| Teacher | Teacher account | JWT | `/api/v1/teacher/` |
| Assessment runner | A child, mid-sitting | Session code | `/api/v1/student/` |

There is a fourth role — product admin, us — deliberately out of scope here.

---

## 2. Conventions

### Base URL

`VITE_API_URL` is `/api`, proxied to the backend in dev. Every path below is
written from that root, so `/v1/teacher/assessments/` is requested as
`/api/v1/teacher/assessments/`.

### Authentication

**School and teacher** use JWT bearer tokens:

```http
Authorization: Bearer <access>
```

Access tokens last 15 minutes; refresh tokens last 7 days, rotate on use, and
the old one is blacklisted.

**A child sitting an assessment does not authenticate.** They hold no account,
no password and no role. They give two codes once — the paper's, and their own
personal one — and the server returns a session string the client holds for the
sitting and returns on every request:

```http
X-Sitting-Session: <session>
```

This is a capability, not an identity — holding it permits exactly one
assignment and nothing else. It expires after 3 hours
(`SITTING_SESSION_HOURS`). Do not put it in `Authorization`; the two are
separate schemes and neither is accepted where the other belongs.

### Errors

Every 4xx and 5xx uses one envelope:

```json
{
  "error": {
    "type": "validation_error",
    "message": "Human-readable sentence, safe to show a user.",
    "detail": { "fln_level": ["Outside the range for letter_sounds."] },
    "request_id": "9f2c1a..."
  }
}
```

`message` is written to be shown as-is. `detail` maps field names to errors for
inline form display. `request_id` is echoed in `X-Request-ID` and appears in
every server log line for that request — include it in bug reports.

Status codes: `400` validation, `401` missing or bad credentials, `403`
authenticated but not permitted, `404` not found *or not yours* (the two are
deliberately indistinguishable), `409` conflict.

### Pagination

List endpoints return 25 per page:

```json
{ "count": 120, "next": "...?page=2", "previous": null, "results": [ ... ] }
```

Endpoints marked *unpaginated* return a bare array — reference data small
enough that paging would only add a round trip.

### Identifiers

Every id is a UUID. Two human-typed identifiers exist and are **case
insensitive** on input:

- **Student id** — e.g. `GHS-S-00042`, issued by the school, printed on the
  child's card. Globally unique. **Not a credential** — it is known to every
  classmate, and nothing accepts it as proof of identity.
- **Assessment code** — e.g. `KRPX7T`, six characters minted at publish. Says
  *which paper*. Shared by everyone sitting it.
- **Assignment code** — e.g. `9M4X2B`, six characters minted when a child is
  assigned. Says *which child*. Unique within one paper, and the thing that
  makes a sitting theirs.

Both codes avoid `O/0`, `I/1`, `S/5`, `Z/2`, because a child reads them off a
printed sheet and a misread character costs them a sitting.

The assignment code is stored in the clear so a teacher can read it back to a
child who has lost theirs. It has no expiry of its own — it stops working when
the assessment closes.

---

## 3. Path changes needed in `koyi-web`

The frontend currently calls paths that do not match the backend. These are the
canonical ones; the client needs updating.

| Frontend calls today | Canonical path | Note |
|---|---|---|
| `/v1/auth/teacher/login/` | `/v1/teacher/auth/login/` | Surface first, then concern |
| `/v1/auth/school-admin/login/` | `/v1/school/auth/login/` | |
| `/v1/auth/school-admin/verify-device/` | `/v1/school/auth/login/verify/` | OTP step |
| `/v1/schools/register/` | `/v1/school/auth/register/` | |
| `/v1/schools/verify-email/` | `/v1/school/auth/register/verify/` | |
| `/v1/schools/verify-email/resend/` | `/v1/school/auth/otp/resend/` | |
| `/v1/auth/me/` | `/v1/teacher/auth/me/` or `/v1/school/profile/` | Differs per surface |
| `/v1/auth/logout/`, `/v1/auth/token/refresh/` | unchanged | Shared, already correct |

Everything is grouped **surface first** (`/school/`, `/teacher/`, `/student/`)
so that permissions, routing and the OpenAPI tags line up with each other.

---

## 4. School management API

### 4.1 Authentication

Registration and login are both two-step, with an emailed OTP.

#### `POST /v1/school/auth/register/` — Planned

Creates the school and its management login, then emails a code.

```json
{
  "email": "admin@greenwood.edu.ng",
  "password": "...",
  "password_confirm": "...",
  "name": "Greenwood Primary School",
  "abbreviation": "GHS",
  "class_system": "grade"
}
```

`201` → `{ "id": "...", "email": "...", "otp_sent": true }`

`abbreviation` is 2–12 uppercase letters or digits, globally unique, and
becomes the prefix of every student and teacher id this school ever issues. It
**cannot be changed afterwards** — say so on the form.

#### `POST /v1/school/auth/register/verify/` — Planned

```json
{ "email": "admin@greenwood.edu.ng", "code": "492013" }
```

`200` → `{ "access": "...", "refresh": "...", "school": { ... } }`

#### `POST /v1/school/auth/login/` — Built (OTP step Planned)

```json
{ "email": "admin@greenwood.edu.ng", "password": "..." }
```

`200` → `{ "otp_required": true, "challenge": "..." }` once OTP lands. Today it
returns the token pair directly.

#### `POST /v1/school/auth/login/verify/` — Planned

```json
{ "challenge": "...", "code": "492013" }
```

`200` → `{ "access": "...", "refresh": "...", "user": {...}, "school": {...} }`

#### Password reset — Planned

```
POST /v1/school/auth/password/reset/request/   { email }        → always 200
POST /v1/school/auth/password/reset/verify/    { email, code }  → { reset_token }
POST /v1/school/auth/password/reset/confirm/   { reset_token, password }
```

The request step returns `200` whether or not the address exists, so the form
cannot be used to discover which schools are registered. Say "if that address
is registered, a code is on its way" rather than "code sent".

### 4.2 Profile

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/school/profile/` | The acting school's record | Built |
| `PATCH` | `/v1/school/profile/` | Name, logo, current session | Built |
| `POST` | `/v1/school/profile/password/change/` | Change own password | Planned |

`GET` returns:

```json
{
  "id": "...", "name": "Greenwood Primary School", "abbreviation": "GHS",
  "email": "admin@greenwood.edu.ng", "email_verified": true,
  "class_system": "grade",
  "logo": { "id": "...", "url": "https://...", "type": "image" },
  "current_session": { "id": "...", "start_year": 2025, "end_year": 2026, "label": "2025/2026" }
}
```

`abbreviation` is read-only after registration — a `PATCH` including it is
rejected with `400`.

### 4.3 Reference data

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/school/grades/` | Grades we define. Read-only, unpaginated | Built |
| `GET` | `/v1/school/sessions/` | Academic sessions, unpaginated | Built |
| `GET` | `/v1/school/classes/` | This school's own classes, unpaginated | Built |
| `POST` | `/v1/school/classes/` | Create a class | Planned |
| `DELETE` | `/v1/school/classes/{id}/` | Delete an empty class | Planned |

**Grades are ours, classes are theirs.** A school picks a grade from our list
and names its own arm within it. The class picker is therefore a grade dropdown
plus a free-text arm — not a single flat list.

`POST /v1/school/classes/` takes `{ "grade": "<uuid>", "name": "A" }`.
Uniqueness is per school, so two schools both having "Grade 1 A" is fine.

`DELETE` is refused with `400` while any student is still in the class. The UI
should offer "transfer these students first" and link to the transfer flow
rather than presenting a delete that fails.

### 4.4 Teachers

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/school/teachers/` | List. `?search=&school_class=` | Built |
| `POST` | `/v1/school/teachers/` | Create teacher + login | Built |
| `GET` | `/v1/school/teachers/{id}/` | One teacher | Built |
| `PATCH` | `/v1/school/teachers/{id}/` | Update | Built |
| `POST` | `/v1/school/teachers/{id}/disable/` | Revoke the login | Planned |
| `POST` | `/v1/school/teachers/{id}/enable/` | Restore it | Planned |
| `POST` | `/v1/school/teachers/{id}/password-reset/` | Email them a reset | Planned |
| `POST` | `/v1/school/teachers/{id}/delete/request/` | Sends a 2FA code | Planned |
| `POST` | `/v1/school/teachers/{id}/delete/confirm/` | `{ code }` | Planned |

`POST` takes `{ email, password, first_name, last_name, school_class? }` and
returns the teacher including a generated `teacher_id`. **Surface that id
prominently** — it is what they sign in with, not their email.

Creating a teacher requires a verified school email (`403` otherwise).

Deletion is two-step behind an emailed code and never cascades: assessments
they authored survive with the author cleared.

### 4.5 Students

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/school/students/` | List. `?search=&school_class=` | Built |
| `POST` | `/v1/school/students/` | Admit a student | Built |
| `GET` | `/v1/school/students/{id}/` | One student | Built |
| `PATCH` | `/v1/school/students/{id}/` | Update | Built |
| `GET` | `/v1/school/students/{id}/fln/` | Levels + assessment scores | Planned |
| `POST` | `/v1/school/students/{id}/disable/` | Disable | Planned |
| `POST` | `/v1/school/students/{id}/enable/` | Re-enable | Planned |
| `POST` | `/v1/school/students/{id}/delete/request/` | Sends a 2FA code | Planned |
| `POST` | `/v1/school/students/{id}/delete/confirm/` | `{ code }` | Planned |
| `POST` | `/v1/school/students/transfer/` | `{ student_ids, to_class }` | Planned |
| `POST` | `/v1/school/students/transfer-class/` | `{ from_class, to_class }` | Planned |

`POST` takes first/last name, date of birth, gender, class, and guardian name,
phone and relationship. `student_id` is generated — never accepted as input,
and never editable, because it is what the child types to sit an assessment.

An **active student always has a class**; only a disabled one may sit outside
the structure. The API enforces this, so a UI that lets someone clear a class
without disabling first will hit a `400`.

`/fln/` returns the school-level view — levels and scores, not the full
diagnostic breakdown, which is the teacher's view:

```json
{
  "student": { "id": "...", "full_name": "Amina Yusuf", "student_id": "GHS-S-00042" },
  "literacy_level": 2, "numeracy_level": 4, "last_assessed_at": "2026-09-01T09:14:00Z",
  "recent_results": [
    { "assessment": "Term 1 baseline", "date": "...", "percentage": "68.00", "status": "graded" }
  ]
}
```

### 4.6 Activity feed — Planned

`GET /v1/school/activity/?teacher=&student=&class=&action=&from=&to=`

Every core action in the product writes a row: papers published and assigned,
sections started and submitted, children placed, groups formed, students
transferred or disabled.

```json
{
  "id": "...", "action": "section_started",
  "label": "Assessment started #KRPX7T",
  "description": "Amina Yusuf started Reading.",
  "teacher": null,
  "student": { "id": "...", "full_name": "Amina Yusuf" },
  "school_class": { "id": "...", "label": "Grade 2 A" },
  "assessment": { "id": "...", "name": "Term 1 baseline" },
  "occurred_at": "2026-09-01T09:14:00Z"
}
```

`label` and `description` are written server-side to be shown as-is. Do not
reconstruct them from the ids — the wording is designed to survive the
referenced rows being renamed or removed.

### 4.7 Overview — Built

`GET /v1/school/overview/` → counts of students, teachers and assessments,
active assessments, a status breakdown, average graded score, and the current
session label.

> **Design note.** This currently leads with an average score. Once placement
> lands it should lead with **level distribution** — how many children sit at
> each of Levels 1–5, per domain. That is the number a school acts on; an
> average across two independent domains is close to meaningless.

---

## 5. Teacher API

### 5.1 Authentication

#### `POST /v1/teacher/auth/login/` — Built

```json
{ "teacher_id": "GHS-T-00007", "password": "..." }
```

**Teachers sign in with their teacher id, not an email.** It is what the school
issued them, it carries the school abbreviation as a prefix, and it is globally
unique — so no separate school field is needed. Input is case insensitive.

`200`:

```json
{
  "access": "...", "refresh": "...",
  "user": { "id": "...", "email": "...", "role": "teacher" },
  "teacher": {
    "id": "...", "teacher_id": "GHS-T-00007", "full_name": "Ada Obi",
    "school": { "id": "...", "name": "Greenwood Primary School" },
    "school_class": "..."
  }
}
```

`401` for a wrong password, an unknown id, or a disabled account — **the same
message for all three**, so the form cannot be used to discover which teacher
ids exist. Show it verbatim; do not add "check your teacher id" hints that
undo this.

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/teacher/auth/me/` | The acting teacher | Built |
| `POST` | `/v1/teacher/auth/password/change/` | Change own password | Planned |
| `POST` | `/v1/teacher/auth/password/reset/request/` | Emails a link | Planned |
| `POST` | `/v1/teacher/auth/password/reset/confirm/` | `{ token, password }` | Planned |

### 5.2 Taxonomy and question bank — Built

The taxonomy is the spine of the product: **14 skills, 55 subskills**, each
tagged to a domain and bounded by a level range.

#### `GET /v1/teacher/bank/skills/?domain=literacy`

Unpaginated.

```json
[
  {
    "id": "...", "code": "phonological_awareness", "name": "Phonological Awareness",
    "domain": "literacy", "min_level": 1, "max_level": 2, "is_core": true,
    "subskills": [
      { "id": "...", "code": "lit_rhyming_and_onset_sounds", "name": "Rhyming and onset sounds",
        "min_level": null, "max_level": null, "level_range": [1, 2] }
    ]
  }
]
```

**`level_range` is the field to build the level picker from.** It resolves the
subskill's own bounds when set and falls back to the parent skill's. A question
tagged outside it is rejected at authoring time, so bound the control rather
than letting a teacher discover the limit through an error.

#### `GET /v1/teacher/bank/questions/`

Filters: `?domain=&skill=&subskill=&fln_level=&type=&search=`. Paginated.

```json
{
  "id": "...", "content": "Which letter makes this sound?",
  "type": "single_choice", "layout": "media_grid_choice", "fln_level": 1,
  "subskill": { "id": "...", "code": "...", "name": "Letter sounds", "level_range": [1, 3] },
  "skill_name": "Alphabetic Knowledge & Phonics", "domain": "literacy",
  "contents": [ { "type": "text", "display_order": 1, "text_content": "...", "media_id": null } ],
  "options": [ { "type": "text", "value": "B", "is_correct": true } ]
}
```

`GET /v1/teacher/bank/questions/{id}/` returns one, same shape.

**The bank is read-only to teachers.** They select from it or write their own;
neither path writes back here. Selecting is a **client-side prefill** — pull the
question, fill the authoring form, let the teacher edit freely, and post the
whole thing as a new assessment question with `source_question_id` set.

### 5.3 Authoring: draft, then publish

Authoring is incremental. A paper stays a `draft` while it is built, one small
request at a time, so nothing is lost if the teacher leaves. **Publish is a
one-way door** — after it, children may sit the paper, so it stops moving.

#### `POST /v1/teacher/assessments/` — Built

```json
{ "name": "Term 1 baseline", "instructions": "", "opens_at": null, "closes_at": null }
```

`201` → the assessment with `"status": "draft"` and `"code": ""`.

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/teacher/assessments/` | List, with sections | Built |
| `GET` | `/v1/teacher/assessments/{id}/` | One paper | Built |
| `PATCH` | `/v1/teacher/assessments/{id}/` | Edit a draft | Built |
| `DELETE` | `/v1/teacher/assessments/{id}/` | Delete a draft | Built |

`PATCH` and `DELETE` return `400` once published — a paper children may have sat
cannot be edited or destroyed. Hide the controls rather than letting the click
fail.

#### Sections — Built

A **section is one sitting**: one domain, whatever skills the teacher chose,
mixed levels. Children take them one at a time, on different days if need be.

```
GET    /v1/teacher/assessments/{id}/sections/
POST   /v1/teacher/assessments/{id}/sections/
PATCH  /v1/teacher/assessments/{id}/sections/{sectionId}/
DELETE /v1/teacher/assessments/{id}/sections/{sectionId}/
```

`POST`:

```json
{
  "domain": "literacy",
  "name": "Reading",
  "instructions": "Read each word aloud.",
  "timer": "00:20:00",
  "covers": ["<subskill uuid>", "..."]
}
```

`covers` declares what the section is *meant* to probe. It powers the coverage
warning — a section that claims to cover blending but carries no blending items
is flagged before a child ever sits it.

`timer` is `HH:MM:SS` or `null` for untimed. It bounds the sitting from the
moment the child opens the section, not from when the paper was assigned.

#### Questions — Built

```
GET /v1/teacher/assessments/{id}/sections/{sectionId}/questions/
PUT /v1/teacher/assessments/{id}/sections/{sectionId}/questions/
```

**`PUT`, not `POST`, and it replaces the whole list.** The client owns the
ordered array and sends it entire, so a retry after a dropped connection cannot
leave a section holding duplicates. Order is taken from array position.

```json
{
  "questions": [
    {
      "subskill_id": "<uuid>",
      "fln_level": 1,
      "question_type": "single_choice",
      "layout": "media_grid_choice",
      "text": "Which letter makes this sound?",
      "description": "",
      "point": "1.00",
      "source_question_id": "<uuid or null>",
      "contents": [
        { "type": "audio", "display_order": 1, "media_id": "<uuid>", "caption": "" },
        { "type": "text", "display_order": 2, "text_content": "Tap the letter you hear." }
      ],
      "options": [
        { "type": "text", "value": "B", "is_correct": true },
        { "type": "text", "value": "D", "is_correct": false }
      ],
      "answer": null
    }
  ]
}
```

`source_question_id` is set when the item came from the bank and `null` when the
teacher wrote it. It is what lets a child's performance on the same item be
compared across rounds — always send it when prefilling from the bank.

**Validation the form must mirror**, because each is a `400`:

| Rule | Message |
|---|---|
| `fln_level` inside the subskill's `level_range` | *"Letter sounds is only assessed at levels 1 to 3."* |
| Subskill domain matches the section's | *"…is a numeracy subskill, but this section is literacy."* |
| Option questions carry ≥1 option, one correct | field errors on `options` |
| Non-option questions carry no options | field error |
| `speech_response_prompt` → `audio` type, no options | *"A speech prompt cannot carry answer options."* |
| `comparison_panel_choice` → exactly 2–3 options | *"A comparison panel compares two or three things."* |
| Option-rendering layout → option-based type | *"…renders options, but text questions have none."* |

#### Question layouts

Five, fixed. A layout the client cannot render is useless, so this is a closed
set rather than something the backend can add to unilaterally.

| Layout | Use when |
|---|---|
| `media_grid_choice` | Stimulus is an image, icon set or number; options are short (a word or number). |
| `media_list_choice` | Options are long — sentence-length phrases. |
| `comparison_panel_choice` | The child compares 2–3 things and each option is itself rich. |
| `speech_response_prompt` | The task is spoken. No options; `question_type` must be `audio`. |
| `passage_comprehension_choice` | Stimulus is a passage, quoted story, or numbered image sequence. |

#### `GET /v1/teacher/assessments/{id}/coverage/` — Built

What the paper can actually establish about a child. Call it as the teacher
builds, not only before publishing.

```json
{
  "assessment_id": "...", "question_count": 24,
  "domains": ["literacy", "numeracy"],
  "levels_probed": [1, 2, 3],
  "sections": [
    { "section_id": "...", "section_name": "Reading", "domain": "literacy",
      "question_count": 12,
      "cells": [ { "subskill_id": "...", "subskill_name": "Letter sounds",
                   "skill_id": "...", "skill_name": "Alphabetic Knowledge & Phonics",
                   "domain": "literacy", "fln_level": 1, "item_count": 3 } ],
      "gaps": ["Blending"] }
  ],
  "warnings": [
    "Every question is at level 1. A paper that probes one level can confirm it but cannot find where a child actually sits."
  ]
}
```

**`levels_probed` is the number that matters.** Placement reads a skill × level
grid, so a paper covering one level cannot place anyone regardless of how many
questions it holds. Render `cells` as that grid and show `warnings` prominently
while authoring — this is the single most valuable screen for getting a usable
paper.

#### `POST /v1/teacher/assessments/{id}/publish/` — Built

No body. Validates, mints the code, locks the paper.

`200` → the assessment with `"status": "published"`, a six-character `code`, and
`published_at`.

Refusals (`400`): no sections, a section with no questions, `closes_at` before
`opens_at`, or already published.

**Show the code large and copyable** on success — it is what children type, and
it is likely written on a board.

### 5.4 Assignment — Built

```
GET    /v1/teacher/assessments/{id}/assignments/
POST   /v1/teacher/assessments/{id}/assignments/
DELETE /v1/teacher/assessments/{id}/assignments/{assignmentId}/
```

`POST` accepts three ways of saying who, because teachers think in classes far
more often than in individuals:

```json
{ "student_ids": ["<uuid>"], "class_ids": ["<uuid>"], "all_my_students": false }
```

At least one is required. `201` returns **only the newly created** assignments —
an empty array means everyone named was already assigned, which is a normal
outcome, not an error. Assigning twice is a deliberate no-op so adding a
latecomer to an assigned class just works.

Students outside the teacher's school, and disabled students, are silently
skipped. If the returned count is lower than what was selected, say so plainly.

`DELETE` is refused with `400` once a child has started — their work would go
with it. Only `not_started` can be withdrawn.

Assignment rows carry the child's personal code:

```json
{ "id": "...", "student": "...", "student_name": "Amina Yusuf",
  "student_id": "GHS-S-00042", "school_class": "Grade 2 A",
  "code": "9M4X2B",
  "status": "not_started", "started_at": null, "submitted_at": null }
```

`status` is `not_started` → `in_progress` → `finished` → `graded`.

#### `GET /v1/teacher/assessments/{id}/assignments/roster/` — Built

The printable code sheet. With a code per child there is no longer one thing to
write on a board, so the classroom path needs this.

```json
{
  "assessment_id": "...", "assessment_name": "Term 1 baseline",
  "assessment_code": "KRPX7T", "opens_at": null, "closes_at": null,
  "rows": [ { "student_name": "Amina Yusuf", "student_id": "GHS-S-00042",
              "school_class": "Grade 2 A", "code": "9M4X2B",
              "status": "not_started" } ]
}
```

Render it print-first: the assessment code once at the top, then a row per
child. Codes should be legible at arm's length and easy to cut into slips.

### 5.5 Results and analytics — Planned

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/teacher/assessments/{id}/results/` | Every student: progress, score, level |
| `GET` | `/v1/teacher/assessments/{id}/results/{studentId}/` | One child's outcome |
| `GET` | `/v1/teacher/assessments/{id}/results/{studentId}/responses/` | The review view |
| `GET` | `/v1/teacher/assessments/{id}/analytics/` | Aggregates + AI narrative |
| `GET` | `/v1/teacher/assessments/{id}/analytics/roster/` | Who needs help |

The **responses** endpoint returns each question *as the child saw it* — layout,
content blocks, options in order — annotated with what they chose and which was
correct, so the review screen renders green/red without a second lookup.

**Analytics** must lead with level distribution, not a class average:

```json
{
  "marking_status": { "total": 240, "marked": 240, "pending": 0 },
  "level_distribution": {
    "literacy": { "1": 4, "2": 11, "3": 8, "4": 2, "5": 0 },
    "numeracy": { "1": 1, "2": 6, "3": 12, "4": 5, "5": 1 }
  },
  "participation": { "assigned": 30, "submitted": 28 },
  "skill_matrix": [ { "skill_name": "Reading Comprehension", "domain": "literacy",
                      "levels": { "3": { "passed": 12, "total": 25 } } } ],
  "most_missed": [ { "subskill_name": "Simple inference", "fln_level": 3, "failed_pct": 68 } ],
  "narrative": { "summary": "...", "tags": [ { "type": "attention", "label": "Comprehension" } ] }
}
```

**Always show `marking_status`.** Free-form and audio items are marked
asynchronously, so a teacher opening analytics early sees numbers that are still
settling — they need to know that.

### 5.6 Students and groups — Planned

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/teacher/students/` | Students in the teacher's classes |
| `GET` | `/v1/teacher/students/{id}/` | Profile: levels, weak subskills, groups |
| `GET` | `/v1/teacher/students/{id}/skills/` | Per-skill breakdown + movement |
| `GET` | `/v1/teacher/students/{id}/lesson-plan/` | Personalisation delta |
| `GET` | `/v1/teacher/groups/` | Groups the teacher owns |
| `POST` | `/v1/teacher/groups/` | Create one manually |
| `GET` | `/v1/teacher/groups/{id}/` | Members and criteria |
| `POST` | `/v1/teacher/groups/{id}/members/` | Add a child |
| `DELETE` | `/v1/teacher/groups/{id}/members/{studentId}/` | Remove one |
| `GET` | `/v1/teacher/groups/{id}/lesson-plan/` | The group's plan |
| `POST` | `/v1/teacher/lesson-plans/{id}/feedback/` | `{ was_helpful: true }` |

Groups have **rule-based dynamic membership**: criteria on level, skill,
subskill and class, all optional and ANDed. Children join when they match and
leave when they progress past it. Groups form at **4 children** and hold for
**14 days** (or one assessment cycle) before restructuring, so a lesson plan
survives long enough to be taught.

A child may be in several groups; one per domain is `is_primary` and drives the
plan the teacher actually runs. Surface that one and treat the rest as advisory.

**Lesson plans are advice, not documents.** There is no edit workflow. The only
feedback is a thumbs up/down plus whether it was opened.

---

## 6. Assessment runner API (student)

The whole surface a child touches. No account, no password, no permissions.

### `POST /v1/student/assessment/verify/` — Built

No auth, and **rate limited** (10/min). The one place a child types anything.

```json
{ "assessment_code": "KRPX7T", "code": "9M4X2B" }
```

Both are case insensitive. `assessment_code` says which paper; `code` is the
child's own, from their card or the guardian link. **A student id is not
accepted** — it is public, so it never proved anything.

`200`:

```json
{
  "session": "8Kf2...opaque...",
  "expires_at": "2026-09-04T14:22:00Z",
  "assessment": {
    "assessment_id": "...", "name": "Term 1 baseline",
    "instructions": "...", "code": "KRPX7T",
    "student_name": "Amina Yusuf", "status": "not_started",
    "sections": [
      { "id": "...", "name": "Reading", "domain": "literacy", "order": 1,
        "timer": "00:20:00", "status": "unlocked", "question_count": 12,
        "started_at": null, "submitted_at": null, "expires_at": null },
      { "id": "...", "name": "Numbers", "domain": "numeracy", "order": 2,
        "timer": null, "status": "locked", "question_count": 10 }
    ]
  }
}
```

Store `session` and send it as `X-Sitting-Session` on every subsequent request.

**Every failure returns the same message** — either code wrong, not assigned,
disabled, already finished. Do not add hints that distinguish them; that is what
stops the form being used to discover real codes. A `429` means the rate limit
was hit: say "too many attempts, wait a minute", not "wrong code".

`400` is returned for a paper that has closed or not yet opened, with a message
safe to show a child.

Verifying again **replaces** the session, so moving to another tablet ends the
first one.

### `GET /v1/student/assessment/` — Built

The instruction page. Same `assessment` object as above. Poll or refetch after
each section submit; the `status` values drive the buttons.

Section `status`: `locked` → `unlocked` → `in_progress` → `submitted`.

### `POST /v1/student/assessment/sections/{sectionId}/start/` — Built

Opens a section and returns its questions. Starts the timer if there is one.

```json
{
  "section": { "id": "...", "status": "in_progress", "expires_at": "2026-09-04T11:42:00Z" },
  "questions": [
    { "id": "...", "order": 1, "text": "Which letter makes this sound?",
      "question_type": "single_choice", "layout": "media_grid_choice",
      "point": "1.00", "subskill_name": "Letter sounds",
      "contents": [ { "type": "audio", "display_order": 1,
                      "media": { "id": "...", "url": "...", "type": "audio" } } ],
      "options": [ { "id": "...", "type": "text", "value": "B", "media": null } ] }
  ]
}
```

**Options carry no `is_correct`.** The runner never receives the answer key.

`400` if the section is locked (finish earlier ones first) or already submitted.

### `PUT /v1/student/assessment/responses/{questionId}/` — Built

One answer. Safe to call on every change — it upserts.

```json
{ "text_value": "", "media_id": null, "option_ids": ["<option uuid>"] }
```

`400` if the section has not been started, or if its timer has expired
(*"Time is up for this section."*).

### `POST /v1/student/assessment/sections/{sectionId}/submit/` — Built

Finishes a section and returns the refreshed overview with the next one
unlocked.

**Submitting the last section finalises the paper on its own.** There is no
further submit call. When the response comes back with `"status": "finished"`,
go to the completion screen. Do not build a final confirm step — a child who
answers everything and misses one more tap would lose the sitting.

---

## 7. Pages

What exists in `koyi-web` today, what changes, and what is missing. Paths are
frontend routes.

### 7.1 Public

| Route | Status | Notes |
|---|---|---|
| `/` welcome | Keep | |
| `/how-it-works`, `/features` | **Revise** | Copy must reflect FLN levels, not exams. No "grades" or "term tests". |
| `/get-started` | Keep | |
| `/verify-email` | **Rewire** | Now the registration OTP step → `/v1/school/auth/register/verify/` |
| `/ready` | Keep | |
| `/about`, `/contact` | Keep | Not written yet |

### 7.2 Sign-in

| Route | Status | Notes |
|---|---|---|
| `/login` role chooser | Keep | Two roles only — product admin is not a public login |
| `/login/school-admin` | **Rewire** | New path; add the OTP step |
| `/login/verify-device` | **Rewire** | → `/v1/school/auth/login/verify/` |
| `/login/teacher` | **Change** | **Field is `teacher_id`, not email.** Single field plus password. Label it "Teacher ID" and show the format, e.g. `GHS-T-00007`. |
| — | **Add** | `/login/teacher/forgot-password` |
| — | **Add** | `/login/school-admin/forgot-password` |

`/teacher/signup` should be **removed**. Teachers do not self-register; a school
admin creates them. Leaving it invites accounts that can never be linked to a
school.

### 7.3 School management

| Route | Status | Notes |
|---|---|---|
| `/school-admin/dashboard` | **Revise** | Lead with level distribution once placement lands, not an average score |
| `/school-admin/teachers` | Keep | Show `teacher_id` in the table — it is their sign-in |
| `/school-admin/teachers/new` | Keep | Show the generated id on success, with a copy control |
| `/school-admin/teachers/:id` | **Extend** | Add disable/enable, trigger password reset, 2FA delete |
| `/school-admin/students` | Keep | Add an active/disabled filter |
| `/school-admin/students/new` | **Change** | Never ask for a student id — it is generated. Show it on success. |
| `/school-admin/students/:id` | **Extend** | Add the FLN panel (two levels, side by side), disable/enable, 2FA delete |
| `/school-admin/classes` | Keep | |
| `/school-admin/classes/new` | **Change** | Grade dropdown (ours) + arm name (theirs) |
| `/school-admin/classes/:id` | **Extend** | Refuse delete while occupied; link to transfer |
| — | **Add** | `/school-admin/students/transfer` — multi-select and whole-class modes |
| — | **Add** | `/school-admin/activity` — the filterable feed |
| `/school-admin/settings` | Keep | Abbreviation must be read-only |

### 7.4 Teacher

| Route | Status | Notes |
|---|---|---|
| `/teacher/dashboard` | **Revise** | Cards should key off levels and weak subskills, not scores |
| `/teacher/dashboard/activity` | Keep | |
| `/teacher/dashboard/attention` | Keep | Becomes the "who needs help" roster |
| `/teacher/dashboard/ai-insights` | Keep | Narrative from `/analytics/` |
| `/teacher/dashboard/class-performance` | **Revise** | Skill × level grid, not a score chart |
| `/teacher/assessments` | Keep | Show `status` and `code` per row |
| `/teacher/assessments/create` | **Rework** | See below — the biggest change |
| `/teacher/assessments/create/assign` | Keep | Add whole-class and all-students modes |
| `/teacher/assessments/:id` | **Extend** | Publish control, the code, coverage summary |
| `/teacher/assessments/:id/analytics` | **Revise** | Lead with level distribution; add marking status |
| — | **Add** | `/teacher/assessments/:id/coverage` — or a panel inside create |
| — | **Add** | `/teacher/assessments/:id/responses/:studentId` — the review view |
| — | **Add** | `/teacher/assessments/:id/roster` — printable code sheet |
| `/teacher/question-bank` | **Change** | Read-only browse + "use this question" prefill. No create/edit here. |
| `/teacher/students` | Keep | |
| `/teacher/students/:id` | **Revise** | Two levels, per-skill breakdown with level context, movement since last |
| `/teacher/students/groups` | Keep | |
| `/teacher/students/groups/:id` | **Extend** | Criteria, membership history, the group's lesson plan |
| `/teacher/progress` | Keep | |
| `/teacher/users`, `/teacher/users/:id` | **Remove** | Teachers do not manage users; that is the school admin's job |
| `/teacher/assessment`, `/teacher/assessment/results` | **Remove** | Superseded by `/teacher/assessments/*` |
| `/teacher/assessment/session` | **Remove** | A child never sits inside the teacher shell |

#### The create-assessment page

This is the largest rework. It is currently a single form; it needs to become a
**draft workspace** matching draft-then-publish.

```
Step 1  Details        POST   /v1/teacher/assessments/          → draft id
Step 2  Sections       POST   .../sections/                     → one per sitting
Step 3  Questions      PUT    .../sections/{id}/questions/       → per section
Step 4  Coverage       GET    .../coverage/                     → warnings
Step 5  Publish        POST   .../publish/                      → the code
        Assign         POST   .../assignments/                  → separate page
```

Things the page must get right:

- **Save as you go.** The draft exists from step 1; every later call is against a
  real id. Nothing should be held only in browser state.
- **Questions are replaced wholesale.** Keep the section's array in local state
  and `PUT` the whole thing. Never try to patch one question.
- **Bank selection is a prefill.** Fetch the bank question, populate the form,
  let the teacher edit, keep `source_question_id`.
- **Bound the level picker** by the chosen subskill's `level_range`.
- **Show coverage live.** A teacher should see "you are only probing level 2"
  while building, not at publish.
- **Publish is irreversible.** Confirm it, and say plainly that the paper cannot
  be edited afterwards.

### 7.5 Assessment runner

The child-facing app. Bare chrome, no sidebar, large tap targets.

| Route | Status | Notes |
|---|---|---|
| — | **Add** | `/assessment` — **the entry page. Missing today.** Two fields: assessment code and the child's own code. This is the whole authentication. Accepts `?a=<assessment>&c=<personal>` from a guardian link, auto-fills both, and submits. |
| — | **Add** | `/assessment/instructions` — sections in order, one start button unlocked |
| `/assessment/session` | **Rewire** | Currently runs on mock data. Drive from `start/`, `responses/`, `submit/`. |
| `/assessment/session/summary` | Keep | Reached automatically when the last section returns `finished` |

Rules for this surface:

- **Never show a score to a child.** Placement is not a mark.
- **Never render `is_correct`** — the API does not send it, and it should stay
  that way.
- The instruction page is the hub. After each section, return here.
- Autosave every answer with `PUT`; assume connections drop.
- If a request returns `401`, the session has expired — send them back to
  `/assessment` to enter the codes again, not to a login page.
- After exchanging codes from a guardian link, **strip them from the URL**
  (`history.replaceState`) so they do not sit in browser history.

---

## 8. Flows end to end

### Teacher: from nothing to a sat paper

```
login/                     POST /v1/teacher/auth/login/
assessments/create         POST /v1/teacher/assessments/                  → draft
                           POST .../sections/                             → "Reading" (literacy)
                           POST .../sections/                             → "Numbers" (numeracy)
   bank browse             GET  /v1/teacher/bank/skills/
                           GET  /v1/teacher/bank/questions/?subskill=…
                           PUT  .../sections/{reading}/questions/
                           PUT  .../sections/{numbers}/questions/
   coverage check          GET  .../coverage/                             → warnings
   publish                 POST .../publish/                              → code "KRPX7T"
assessments/create/assign  POST .../assignments/  { class_ids: [...] }
   monitor                 GET  .../assignments/                          → who has started
   after the deadline      GET  .../analytics/                            → levels, narrative
```

### Child: sitting the paper

```
/assessment                POST /v1/student/assessment/verify/            → session
   (or a guardian link:    /assessment?a=KRPX7T&c=9M4X2B — auto-fills and submits)
/assessment/instructions   GET  /v1/student/assessment/                   → section 1 unlocked
/assessment/session        POST .../sections/{1}/start/                   → questions + timer
                           PUT  .../responses/{q}/                        → per answer
                           POST .../sections/{1}/submit/                  → section 2 unlocks
/assessment/instructions   (returns here)
/assessment/session        POST .../sections/{2}/start/
                           POST .../sections/{2}/submit/                  → status "finished"
/assessment/session/summary
```

### School admin: setting up

```
/get-started               POST /v1/school/auth/register/                 → OTP emailed
/verify-email              POST /v1/school/auth/register/verify/          → tokens
/school-admin/classes/new  GET  /v1/school/grades/    then POST /v1/school/classes/
/school-admin/teachers/new POST /v1/school/teachers/                      → teacher_id issued
/school-admin/students/new POST /v1/school/students/                      → student_id issued
```

---

## 9. Things to keep out of the UI

Patterns that would fight the product, several of which appear in the current
designs:

- **A single overall score or level.** Literacy and numeracy are independent.
- **"Strong" / "Weak" badges.** Ranking children is what this product exists to
  avoid. Say what they can do and what comes next.
- **Comparing a class to a school average.** A class with more Level 1 children
  is differently composed, not worse.
- **Grade or term framing on assessments.** "Primary 4", "Term 1 Mid-Term" — a
  diagnostic round is not a termly exam and a level is not a year group.
- **Actions that do not exist.** "Assign Syllable Module", "Print Worksheets",
  "Schedule 1-on-1" are in a mock but have no backend. The answer to *what next*
  is the lesson plan.
- **A skill percentage without a level.** "Phonics 95%" is meaningless alone —
  95% at Level 1 and at Level 4 are different children.
- **A final submit button after the last section.** It finalises itself.
- **Hints that distinguish auth failures.** Sameness is the security property.

---

## 10. Open questions

| Question | Blocks |
|---|---|
| Are guardian links emailed automatically on assignment, or sent on demand? | Assignment page |
| Are teacher password resets self-service, admin-triggered, or both? | Login page scope |
| Does the school admin see individual assessment results, or only levels? | `/students/{id}/fln/` shape |
| Should the runner work on a phone, or tablet and up only? | Layout minimums |
| What happens if a child's session expires mid-section — resume, or restart? | Runner error handling |
| Is there a printable class code sheet for handing out codes? | New page |

---

## 11. Change log

| Date | Change |
|---|---|
| 2026-09-04 | First version. Covers phases 0–2 as built, phases 3–7 as designed. |
| 2026-09-04 | Sittings now open with two codes. The student id is no longer accepted — it is public, so it never proved anything. Each assignment carries its own code, readable by the teacher and embeddable in a guardian link. Adds the printable roster and rate limiting on verify. |
