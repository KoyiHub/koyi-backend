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

A fourth thing follows from how placement is computed, and it shapes several
screens: **a level is what a child needs taught next, not what they have
mastered.** It is the lowest level the paper probed that they did not pass. So
"Level 2" reads as *working on Level 2*, never as *completed Level 2* — and a
child who fails Level 3 while passing Level 4 is placed at 3, because that is
the gap.

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

List endpoints return 25 per page. The envelope carries enough for **numbered
controls** — "page 3 of 11" — not just previous and next, because the rosters
here run to a few hundred children:

```json
{
  "count": 240,
  "page": 3,
  "num_pages": 10,
  "page_size": 25,
  "next": "...?page=4",
  "previous": "...?page=2",
  "results": [ ... ]
}
```

`?page=` and `?page_size=` are accepted. `page_size` is **capped at 100** — a
client asking for 5000 gets 100 back, and `page_size` in the response says what
was actually applied, so always read it rather than echoing what you sent.

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

#### `POST /v1/school/auth/register/` — Built

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

#### `POST /v1/school/auth/register/verify/` — Built

```json
{ "email": "admin@greenwood.edu.ng", "code": "492013" }
```

`200` → `{ "access": "...", "refresh": "...", "school": { ... } }`

#### `POST /v1/school/auth/login/` — Built

```json
{ "email": "admin@greenwood.edu.ng", "password": "..." }
```

`200` → `{ "otp_required": true, "challenge": "...", "expires_at": "..." }`

**No token comes back from this call.** A correct password gets you a challenge
and an emailed code, nothing more. Hold the challenge in memory for the second
step — it is a bearer secret, so do not put it in `localStorage` or the URL.

A wrong password, an address nobody has registered, and a *teacher* posting
their credentials here all return the same `401` with the same message. Do not
try to tell them apart in the UI; there is nothing to tell apart.

#### `POST /v1/school/auth/login/verify/` — Built

```json
{ "challenge": "...", "code": "492013" }
```

`200` → `{ "access": "...", "refresh": "...", "user": {...}, "school": {...} }`

#### Password reset — Built

```
POST /v1/school/auth/password/reset/request/   { email }        → always 200
POST /v1/school/auth/password/reset/verify/    { email, code }  → { reset_token, expires_at }
POST /v1/school/auth/password/reset/confirm/   { reset_token, password, password_confirm } → 204
```

The request step returns `200` whether or not the address exists, so the form
cannot be used to discover which schools are registered. Say "if that address
is registered, a code is on its way" rather than "code sent".

Three steps rather than two because the six digits arrive in a notification
anyone glancing at the phone can read. `reset_token` is what actually sets the
password, and it never leaves the browser that asked for it.

#### Rules that apply to every code

The same machinery is behind registration, sign-in, password reset and the
deletion confirmations, so the UI can treat them identically:

*   **Six digits, ten minutes.** Show the expiry; offer resend after it passes.
*   **Single use, and issuing a new one retires the old.** A user who taps
    resend twice has exactly one working code — the newest.
*   **Five wrong attempts burn the code.** After that even the right digits are
    refused, and the person must request a new one. Surface this rather than
    letting them keep typing: "That code is no longer valid. Request a new one."
*   **Every failure is one `400` with one message.** Expired, wrong, already
    spent and out of attempts are deliberately indistinguishable.
*   **These endpoints are rate limited** (15/min, shared across the group). A
    `429` means slow down, not that anything is wrong with the code.

### 4.2 Profile

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/school/profile/` | The acting school's record | Built |
| `PATCH` | `/v1/school/profile/` | Name, logo, current session | Built |
| `POST` | `/v1/school/profile/password/change/` | Change own password | Built |

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
| `POST` | `/v1/school/classes/` | Create a class | Built |
| `DELETE` | `/v1/school/classes/{id}/` | Delete an empty class | Built |

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
| `POST` | `/v1/school/teachers/{id}/disable/` | Revoke the login | Built |
| `POST` | `/v1/school/teachers/{id}/enable/` | Restore it | Built |
| `POST` | `/v1/school/teachers/{id}/password-reset/` | Email them a reset | Built |
| `POST` | `/v1/school/teachers/{id}/delete/request/` | Sends a 2FA code | Built |
| `POST` | `/v1/school/teachers/{id}/delete/confirm/` | `{ code }` | Built |

`POST` takes `{ email, password, first_name, last_name, school_class? }` and
returns the teacher including a generated `teacher_id`. **Surface that id
prominently** — it is what they sign in with, not their email.

Creating a teacher requires a verified school email (`403` otherwise).

**Disable is the common case; delete is not.** Disabling revokes the login and
leaves everything they authored exactly where it is, and is one click to undo.
Present it as the primary action and keep delete out of the way.

Deletion is two-step. `delete/request/` emails a six-digit code **to the signed-in
administrator, not to the teacher**, and `delete/confirm/` takes `{ code }`. Say
who the code went to on the confirm dialog, or people will look in the wrong
inbox. The code is bound to that one teacher: requesting a second deletion
invalidates the first code, and confirming the wrong record returns `400`.

Nothing they authored is destroyed. The teacher's row is hidden immediately and
purged after 90 days; their assessments survive with the author cleared.

`password-reset/` emails the teacher a code they can set a new password with —
admin-triggered, because a teacher who cannot sign in is standing in front of
someone who can.

### 4.5 Students

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/school/students/` | List. `?search=&school_class=` | Built |
| `POST` | `/v1/school/students/` | Admit a student | Built |
| `GET` | `/v1/school/students/{id}/` | One student | Built |
| `PATCH` | `/v1/school/students/{id}/` | Update | Built |
| `GET` | `/v1/school/students/{id}/fln/` | Levels + assessment scores | Built |
| `POST` | `/v1/school/students/{id}/disable/` | Disable | Built |
| `POST` | `/v1/school/students/{id}/enable/` | Re-enable | Built |
| `POST` | `/v1/school/students/{id}/delete/request/` | Sends a 2FA code | Built |
| `POST` | `/v1/school/students/{id}/delete/confirm/` | `{ code }` | Built |
| `POST` | `/v1/school/students/transfer/` | `{ student_ids, to_class }` | Built |
| `POST` | `/v1/school/students/transfer-class/` | `{ from_class, to_class }` | Built |

`POST` takes first/last name, date of birth, gender, class, and guardian name,
phone, email and relationship. **`guardian_email` is optional** — many guardians
will not have one — but it is where an assessment link is sent, so the form
should say what it is for rather than presenting it as another blank field.

`student_id` is generated — never accepted as input and never editable. It
identifies the child in lists and on their card; it is **not** what they type to
sit an assessment (that is the assignment code).

An **active student always has a class**; only a disabled one may sit outside
the structure. The API enforces this, so a UI that lets someone clear a class
without disabling first will hit a `400` — and so will re-enabling a child who
has no class, which is worth catching on the form.

Deletion works exactly as it does for teachers: `delete/request/` emails a code
to the signed-in administrator, `delete/confirm/` takes `{ code }`, and the row
is hidden immediately rather than destroyed. A child's results cascade off that
row, so the delay is doing real work — the purge finishes the job after 90 days.
A removed child keeps their `student_id`; it is never reissued.

**Transfers** come in two shapes, and the UI should too:

```
POST /v1/school/students/transfer/        { student_ids: [...], to_class }  → { moved, to_class }
POST /v1/school/students/transfer-class/  { from_class, to_class }          → { moved, to_class }
```

The first is a multi-select on the roster; the second is the end-of-year move.
Both refuse a class or a child belonging to another school with a `400`. The
whole-class move writes **one** entry in the activity feed rather than one per
child, so do not expect the feed to itemise it.

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

### 4.6 Activity feed — Built

```
GET /v1/school/activity/?teacher=&student=&school_class=&action=&occurred_from=&occurred_to=
```

Every core action in the product writes a row: papers published and assigned,
sections started and submitted, children placed, groups formed, students
transferred, disabled or removed.

```json
{
  "id": "...", "action": "section_started",
  "label": "Assessment started #KRPX7T",
  "description": "Amina Yusuf started Reading.",
  "teacher": null,
  "student": { "id": "...", "name": "Amina Yusuf" },
  "school_class": { "id": "...", "name": "Grade 2 A" },
  "assessment": { "id": "...", "name": "Term 1 baseline" },
  "metadata": {},
  "occurred_at": "2026-09-01T09:14:00Z"
}
```

Every related object is `{ id, name }` or `null` — the same shape whichever
one it is, so one component renders all four.

`label` and `description` are written server-side to be shown as-is. Do not
reconstruct them from the ids — the wording is designed to survive the
referenced rows being renamed or removed, which for a log of deletions is most
of the point.

**This feed is cursor-paginated, not page-numbered.** Rows land in it while
someone is reading, so an offset would skip and repeat entries. The envelope is
`{ next, previous, results }` with opaque cursor URLs and **no `count`** — build
an infinite scroll or a "load more", not numbered controls. Everything else in
this API uses the page-number envelope from §2.

`action` must be one of the values in `ActivityAction`; an unknown one is a
`400` rather than a silently ignored filter. `occurred_from` and `occurred_to`
are ISO-8601 timestamps.

### 4.7 Overview — Built

`GET /v1/school/overview/` → counts of students, teachers and assessments,
active assessments, a status breakdown, **level distribution**, average graded
score, and the current session label.

```json
{
  "students": 240, "teachers": 12,
  "assessments": 8, "active_assessments": 2,
  "assessment_status_breakdown": { "draft": 3, "published": 3, "closed": 2 },
  "level_distribution": {
    "levels": {
      "literacy": { "1": 41, "2": 88, "3": 52, "4": 19, "5": 4 },
      "numeracy": { "1": 30, "2": 74, "3": 61, "4": 28, "5": 11 }
    },
    "unplaced": { "literacy": 36, "numeracy": 36 }
  },
  "average_graded_score": "64.20",
  "current_session": "2025/2026"
}
```

**Lead the page with `level_distribution`.** It is the number a school acts on:
two bar charts, literacy and numeracy side by side, Levels 1–5 across the
bottom. Every level is keyed even at zero, so plot them all — dropping empty
levels makes the spread look narrower than it is.

`unplaced` is per domain and counts active children no assessment has reached
yet. It is usually the most actionable figure on the dashboard, so give it a
line of its own rather than folding it into the chart.

`average_graded_score` is kept because a school that has always had one will
look for it, but it averages across two independent abilities and describes
neither. Do not give it the headline.

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

#### Question types

The closed set, and which are option-based. The authoring form's validation
(above) turns on this distinction, so it is worth stating exactly.

| `question_type` | Answered by | Marking |
|---|---|---|
| `single_choice` | Picking **one** option | Deterministic, instant |
| `multiple_choice` | Picking **several** options | Deterministic, instant. Exact set match — a partly-right selection is wrong |
| `true_false` | Picking one of two options | Deterministic, instant |
| `number` | Typing a number | Deterministic, instant. Compared numerically, so `" 22 "` matches `22` |
| `text` | Typing words | AI, asynchronous |
| `audio` | Speaking | Transcribed, then AI, asynchronous |
| `file_upload` | Uploading a file | **No marker.** Stays pending for a teacher |

**Option-based** means `single_choice`, `multiple_choice` and `true_false`.
Those three require at least one option with `is_correct: true`; every other
type must send **no options at all**. Both are `400`s.

`number` should carry an `answer` object with the expected value — without one
the item cannot be marked and stays pending, which is an authoring fault rather
than a child's error.

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

#### Guardian links — Planned

Links are **sent on demand by the teacher**, never automatically on assignment.
A paper is often assigned days before it opens, and the teacher decides when a
guardian should hear about it.

```
POST /v1/teacher/assessments/{id}/assignments/{assignmentId}/send-link/
POST /v1/teacher/assessments/{id}/assignments/send-links/   { assignment_ids }
```

Both return the number sent and any that could not be, with a reason — a
guardian with no contact details on file is the common case, and the teacher
needs to see which children those are rather than assuming everyone was reached.

Assignment rows carry `link_sent_at` so the UI can show who has been contacted
and offer "send" or "send again" per row.

**Email is the only channel.** There is no SMS infrastructure, and
`guardian_phone_number` is informational — never a delivery route.

`guardian_email` is **optional**, because many guardians will not have one. So a
child with no address on file simply cannot be sent a link: the send endpoints
must report those children rather than failing silently, and the printed roster
is how their codes reach them instead.

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

### 5.5 Results and analytics

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/teacher/assessments/{id}/analytics/` | Aggregates + AI narrative | **Built** |
| `GET` | `/v1/teacher/assessments/{id}/analytics/roster/` | Who needs help | **Built** |
| `GET` | `/v1/teacher/students/{id}/skills/` | One child, by skill | **Built** |
| `GET` | `/v1/teacher/assessments/{id}/results/` | Every student: progress, score, level | Planned |
| `GET` | `/v1/teacher/assessments/{id}/results/{studentId}/responses/` | The review view | **Built** |
| `GET` | `/v1/teacher/assessments/{id}/review-queue/` | Responses the AI could not settle |

The **responses** endpoint returns each question *as the child saw it* — layout,
content blocks, options in order — annotated with what they chose and which was
correct, so the review screen renders green/red without a second lookup.

Placement runs automatically when a child submits their last section, so
results appear without anyone triggering them.

It runs in **two passes**, and the UI has to expect that. Choice and number
items are marked instantly and the child is placed on those. Written and spoken
answers go to the AI marker afterwards, and when they land the child is placed
again — so a level can change a few minutes after first appearing, without
anything having been wrong.

Two consequences worth building for:

- **Always show `marking_status`.** A teacher opening analytics early sees
  numbers that are still settling and needs to know it.
- **A response with `is_correct: null` is pending, not wrong.** It happens when
  the model was unavailable, when its confidence was too low to act on, or when
  a recording failed. Those need a teacher, and the review screen should offer
  that rather than rendering them as errors.

#### `GET /v1/teacher/assessments/{id}/analytics/`

Numbers computed deterministically, with an AI narrative laid over them. Pass
`?narrative=false` to skip generating the prose — **the key stays, as `null`**,
so a tile that does not want it need not branch on key existence.

Three things to build around:

- **`level_distribution` is the headline**, not `average_percentage`. Every
  level is keyed even at zero, so a chart that drops empty levels would read as
  a narrower spread than the class has. Both domains are always present.
- **`narrative` is `null` when the model was unavailable.** The figures are the
  diagnosis; the prose is a convenience over them. Render the page without it.
- **`most_missed` is by subskill at a level**, not by question. "Simple
  inference at Level 3" is teachable; "Q12" is not.

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
  "average_percentage": "68.00",
  "warnings": ["12 of 240 answers are still being marked. These figures will change."],
  "narrative": { "summary": "...", "attention": "Simple inference", "strength": "Phonics" }
}
```

#### `GET /v1/teacher/assessments/{id}/analytics/roster/`

Who needs help. Filter with `?domain=literacy&level=2`.

```json
[
  { "student_id": "...", "full_name": "Amina Yusuf", "school_class": "Grade 2 A",
    "literacy_level": 2, "numeracy_level": 4,
    "weak_subskills": ["Simple inference (L3)", "Blending (L2)"] }
]
```

#### `GET /v1/teacher/students/{id}/skills/`

One child, by skill, with the level context a percentage alone cannot carry.

```json
{
  "student_id": "...", "full_name": "Amina Yusuf",
  "literacy_level": 2, "numeracy_level": 4, "last_assessed_at": "...",
  "skills": [
    { "skill_name": "Alphabetic Knowledge & Phonics", "domain": "literacy",
      "highest_level_passed": 2, "broke_down_at": 3,
      "weak_subskills": ["Consonant blends and digraphs (L3)"] }
  ],
  "movement": [ { "domain": "literacy", "previous": 1, "current": 2, "direction": "up" } ],
  "narrative": { "summary": "...", "attention": "...", "strength": "" }
}
```

#### `GET /v1/teacher/assessments/{id}/results/{studentId}/responses/`

One child's paper, in sitting order, each question **as they saw it** — layout,
content blocks, options — annotated with what happened.

```json
{
  "student_id": "...", "full_name": "Amina Yusuf",
  "assessment_id": "...", "assessment_name": "Term 1 baseline",
  "status": "graded",
  "items_attempted": 11, "items_correct": 7, "pending": 2,
  "percentage": "63.64",
  "questions": [
    {
      "id": "...", "order": 1, "text": "Which letter makes this sound?",
      "question_type": "single_choice", "layout": "media_grid_choice",
      "fln_level": 1, "subskill_name": "Letter sounds",
      "skill_name": "Alphabetic Knowledge & Phonics", "section_name": "Reading",
      "contents": [ ... ],
      "options": [
        { "id": "...", "value": "B", "is_correct": true,  "was_selected": false },
        { "id": "...", "value": "D", "is_correct": false, "was_selected": true }
      ],
      "response": {
        "id": "...", "text_value": "", "transcript": "",
        "is_correct": false, "awarded_points": "0.00",
        "graded_by": "auto", "grading_confidence": null,
        "error_type": "substitution",
        "observation_note": "Chose the visually similar letter."
      }
    }
  ]
}
```

Every option carries **both** `is_correct` and `was_selected`, so the green and
red highlighting needs no cross-referencing and no second request. This is the
only endpoint that serves the answer key — to a teacher, after the fact. The
runner never receives it.

`response` is `null` when the child did not answer at all. Inside it,
**`is_correct: null` means pending, not wrong** — the AI marker has not reached
it, its confidence was too low to act on, or a recording failed. Those are the
`pending` count, and the UI should offer a teacher the decision rather than
rendering them as errors. `items_attempted` and `items_correct` count only what
was actually marked.

`movement` compares the last two placements per domain. `direction` is `up`,
`down`, `same` or `new` — and **`down` is not a failure to hide.** Placement is
absolute, so a child can move down, and that is a reading rather than a
regression to explain away.

**Always show `marking_status`.** Free-form and audio items are marked
asynchronously, so a teacher opening analytics early sees numbers that are still
settling — they need to know that.

### 5.6 Groups and lesson plans

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET` | `/v1/teacher/groups/` | Groups the teacher owns. `?status=` | **Built** |
| `POST` | `/v1/teacher/groups/` | Create one with criteria | **Built** |
| `POST` | `/v1/teacher/groups/form/` | Form groups for shared weaknesses | **Built** |
| `GET` | `/v1/teacher/groups/{id}/` | Criteria and current members | **Built** |
| `DELETE` | `/v1/teacher/groups/{id}/` | Archive it | **Built** |
| `GET` | `/v1/teacher/groups/{id}/members/` | Membership history. `?current=true` | **Built** |
| `POST` | `/v1/teacher/groups/{id}/members/` | Add a child by hand | **Built** |
| `DELETE` | `/v1/teacher/groups/{id}/members/{studentId}/` | Remove one | **Built** |
| `GET` | `/v1/teacher/groups/{id}/lesson-plan/` | The group's plan | **Built** |
| `POST` | `/v1/teacher/groups/{id}/lesson-plan/` | Generate or regenerate | **Built** |
| `GET` | `/v1/teacher/students/{id}/lesson-plan/` | A child's personal note | **Built** |
| `POST` | `/v1/teacher/lesson-plans/{id}/feedback/` | `{ was_helpful }` | **Built** |
| `GET` | `/v1/teacher/students/` | Students in the teacher's classes | Planned |

#### How membership works

Criteria on **level, skill, subskill and class** — all optional, all ANDed. A
group with no criteria matches **nobody**, deliberately: matching everybody
would be the more dangerous reading of the same silence.

**Two clocks, deliberately out of step**, and the UI should reflect both:

- **Membership is live.** A child who progresses past the criteria leaves the
  moment placement says so. Show current membership as current.
- **The group is slow.** It holds for a **stability window** (14 days) before
  restructuring may touch it, so a plan survives long enough to be delivered.
  `stable_until` says when that ends.

A group that falls below **4 children** is *flagged*, not dissolved — dissolving
mid-window strands whoever is left. Surface it as "this group has got small"
rather than removing it.

A child a teacher adds **by hand is never removed by the rules**. Their
judgement outranks a criterion they did not write, and `join_reason` tells the
two apart (`matched` vs `added`).

Membership is **history, not a toggle**: a child who leaves and rejoins has two
rows. `left_at: null` means current.

#### Creating a group

```json
{
  "name": "Level 2 literacy",
  "domain": "literacy",
  "resource_tier": "basic",
  "criteria": [
    { "type": "level", "level": 2, "comparator": "eq" },
    { "type": "subskill", "subskill": "<uuid>" }
  ]
}
```

Criterion types: `level` (needs `level`, and `comparator` of `eq`/`gte`/`lte`),
`skill`, `subskill`, `class`. A rule naming nothing is a `400` — it would
otherwise match everyone.

**The group is filled on creation**, so the response already carries `size` and
`members`. A teacher should see who matched, not an empty group they cannot
tell apart from a broken rule.

`resource_tier` is `minimal` (chalkboard and voice), `basic` (paper, printed
materials) or `equipped` (manipulatives, some devices). It is part of the plan's
identity, not a label: a plan naming materials the room does not have is worse
than no plan, because the teacher finds out mid-lesson.

#### Lesson plans

`POST` to generate — it returns **`202` and runs in the background**, because
generation takes tens of seconds. Poll the `GET`.

`status` is the field to build around:

| `status` | Meaning |
|---|---|
| `ready` | Adapted to this group |
| `fallback` | The canonical plan, because adaptation failed. **Still teachable** — present it normally, not as an error |
| `failed` | Nothing could be generated. `content` is empty; say so plainly |
| `generating` | In flight |

`content` carries `objective`, `duration_minutes`, `materials`, `steps` (each
with `teacher_does`, `children_do`, `minutes`), `checks`, `common_errors`,
`success_criteria` and `note`.

`member_snapshot` is who was in the group when the plan was written, so what a
teacher is holding stays coherent as children move.

**Plans are advice, not documents.** There is no edit workflow and no approval
queue. The only feedback is `was_helpful` plus `opened_at`, which the `GET`
sets on first read.

A **student lesson plan** is a short note beside the group plan, generated only
for a child whose weaknesses diverge from the group's. A `404` means the group
plan already covers them — that is the normal case, not a gap.

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
| `/login/school-admin` | **Rewire** | New path. Two steps: password returns a `challenge`, never a token. |
| `/login/verify-device` | **Rewire** | → `/v1/school/auth/login/verify/`. Posts `{ challenge, code }` — the challenge from the previous step, held in memory. |
| `/login/teacher` | **Change** | **Field is `teacher_id`, not email.** Single field plus password. Label it "Teacher ID" and show the format, e.g. `GHS-T-00007`. |
| — | **Add** | `/login/teacher/forgot-password` |
| — | **Add** | `/login/school-admin/forgot-password` |

`/teacher/signup` should be **removed**. Teachers do not self-register; a school
admin creates them. Leaving it invites accounts that can never be linked to a
school.

### 7.3 School management

| Route | Status | Notes |
|---|---|---|
| `/school-admin/dashboard` | **Revise** | Lead with `level_distribution` — two charts, literacy and numeracy — and the `unplaced` count. The average is secondary. |
| `/school-admin/teachers` | Keep | Show `teacher_id` in the table — it is their sign-in |
| `/school-admin/teachers/new` | Keep | Show the generated id on success, with a copy control |
| `/school-admin/teachers/:id` | **Extend** | Disable/enable as the primary action; password reset; two-step delete behind a code emailed to *you*, not to them |
| `/school-admin/students` | Keep | Add an active/disabled filter |
| `/school-admin/students/new` | **Change** | Never ask for a student id — it is generated. Show it on success. |
| `/school-admin/students/:id` | **Extend** | FLN panel (two levels, side by side) from `/fln/`; disable/enable; two-step delete |
| `/school-admin/classes` | Keep | |
| `/school-admin/classes/new` | **Change** | Grade dropdown (ours) + arm name (theirs) → `POST /v1/school/classes/` |
| `/school-admin/classes/:id` | **Extend** | Refuse delete while occupied; link to transfer |
| — | **Add** | `/school-admin/students/transfer` — two modes, one per endpoint: multi-select and whole-class |
| — | **Add** | `/school-admin/activity` — the filterable feed. **Cursor-paginated**: infinite scroll or "load more", no page numbers. |
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

### School admin: signing in again

```
/login/school-admin        POST /v1/school/auth/login/          → { challenge }, code emailed
/login/verify-device       POST /v1/school/auth/login/verify/   → tokens
```

Hold `challenge` in memory between the two. A `400` on the second step means
"request a new code", whatever actually went wrong.

### School admin: removing a child

```
/school-admin/students/:id POST .../students/{id}/delete/request/   → code emailed to YOU
  confirm dialog           POST .../students/{id}/delete/confirm/   { code } → 204
```

The child disappears from every list at once. Their results are destroyed 90
days later by the retention purge, not immediately — which is what makes a
misclick survivable.

### School admin: end of year

```
/school-admin/students/transfer  POST /v1/school/students/transfer-class/  { from_class, to_class }
/school-admin/classes/:id        DELETE /v1/school/classes/{id}/           → 204 once empty
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
| Should a teacher be able to send a link to a child with no guardian contact, by printing the code instead? | Assignment page fallbacks |
| Are teacher password resets self-service, admin-triggered, or both? | Login page scope. **Admin-triggered is built** (`/teachers/{id}/password-reset/`); whether the teacher login page also offers self-service is still open. |
| Does the school admin see individual assessment results, or only levels? | `/students/{id}/fln/` shape |
| Should the runner work on a phone, or tablet and up only? | Layout minimums |
| What happens if a child's session expires mid-section — resume, or restart? | Runner error handling |
| Is there a printable class code sheet for handing out codes? | New page |

---

## 11. Change log

| Date | Change |
|---|---|
| 2026-09-04 | First version. Covers phases 0–2 as built, phases 3–7 as designed. |
| 2026-09-04 | Groups and lesson plans are built. Membership is live while the group holds for a stability window; a thin group is flagged rather than dissolved; a failed adaptation serves the canonical plan rather than an error. |
| 2026-09-04 | The response review endpoint is built. Pagination now carries `page`, `num_pages` and `page_size` for numbered controls — §2 previously documented the bare DRF default, which was wrong. The `question_type` closed set is documented. |
| 2026-09-04 | Analytics, the roster and the student skill breakdown are built, each with an optional AI narrative that degrades to null. |
| 2026-09-04 | The AI layer is built: provider-swappable marking of written and spoken answers, and subskill/level suggestion for authored questions. Marking now runs in two passes, so a level can change once free-form answers land. |
| 2026-09-04 | Marking, the skill x level matrix and placement are built. Placement fires automatically on the final section submit. A level is the lowest probed level not passed — what to teach next, not what is mastered. |
| 2026-09-04 | Email is the only channel for guardian links. The guardian phone number is informational and nothing is ever sent to it. |
| 2026-09-04 | Guardian links are sent on demand by the teacher, not automatically on assignment. `Student.guardian_email` added — optional, since many guardians will not have one. |
| 2026-09-04 | Sittings now open with two codes. The student id is no longer accepted — it is public, so it never proved anything. Each assignment carries its own code, readable by the teacher and embeddable in a guardian link. Adds the printable roster and rate limiting on verify. |
