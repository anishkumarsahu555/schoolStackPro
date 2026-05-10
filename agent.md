# SchoolStack Feature Implementation Guide

This file is the default implementation guide for any new feature added to this repository. Use it as the first reference before creating models, views, APIs, templates, services, or JavaScript.

The goal is consistency:

- keep the same information architecture
- preserve the current Django monolith structure
- reuse the shared UI shell and page behaviors
- follow the same session, role, and soft-delete rules
- avoid introducing isolated patterns that do not match the rest of the app

## 1. Existing App Shape

This project is a Django monolith with role-oriented apps:

- `homeApp`: authentication, shared shell, branding, session/bootstrap, base template, middleware
- `managementApp`: admin/owner-facing academic and operational features
- `financeApp`: finance models and business services
- `teacherApp`: teacher-facing pages and APIs
- `studentApp`: student-facing pages and APIs
- `utils`: shared decorators, response wrappers, validators, logging, helpers

New work should fit into this structure instead of creating a parallel architecture.

## 2. High-Level Design Pattern

When adding a feature, follow this flow:

1. Add or extend data in the app that owns the domain.
2. Keep business rules in helpers/services when logic is more than simple CRUD.
3. Keep page-rendering views focused on context preparation and template rendering.
4. Put AJAX/data endpoints in the app's `api/` module.
5. Build UI on top of the existing base shell and Fomantic/DataTables conventions.
6. Respect role checks, session scoping, school scoping, and `isDeleted=False`.

In short:

- models hold state
- services/helpers hold business logic
- views assemble context
- templates render UI
- API views handle interactive table/form actions

## 3. Non-Negotiable Conventions

### 3.1 Role-based app ownership

- Admin/Owner features belong in `managementApp`
- Teacher features belong in `teacherApp`
- Student features belong in `studentApp`
- Shared auth/session/layout concerns belong in `homeApp`
- Cross-cutting domain logic can live in `financeApp/services.py`, `managementApp/services/`, or `utils/`

Do not put teacher/student/admin behavior into one mixed template or view unless the existing app already centralizes it.

### 3.2 Session and school scoping

Almost every feature is scoped to the active school session and school.

Always look for:

- `request.session['current_session']`
- `Id`
- `SchoolID`
- `currentSessionYear`

When querying app data, prefer filtering by:

- `sessionID_id=current_session_id`
- `schoolID_id=current_school_id`
- `isDeleted=False`

If a feature is session-sensitive and you skip this, it will behave incorrectly.

### 3.3 Soft delete first

Most domain models use `isDeleted` instead of hard deletion.

Rules:

- filter active rows with `isDeleted=False`
- when "deleting", prefer soft delete unless the domain clearly requires hard delete
- keep list/detail APIs consistent with existing active-record filtering

### 3.4 Audit fields

Many models track:

- `datetime`
- `lastUpdatedOn`
- `lastEditedBy`
- `updatedByUserID`

If you add a new model that behaves like other management/finance entities, include the same audit style unless there is a strong reason not to.

## 4. Backend Implementation Pattern

### 4.1 Models

When adding new domain tables, match the current style:

- use Django ORM models inside the owning app
- use foreign keys like `schoolID`, `sessionID`, `studentID`, `teacherID`
- keep naming consistent with the current schema style
- add `isDeleted` for mutable business records
- add indexes for common filtered columns like session, school, user, and deleted status

Do not introduce a separate repository layer or DTO abstraction unless the codebase already requires it.

### 4.2 Views

Page views should remain thin.

Good page view responsibilities:

- check authentication and groups
- derive `current_session`
- fetch summary or initial page context
- render the correct template

Use decorators consistent with the codebase:

- `@login_required` or `@custom_login_required`
- `@check_groups(...)`

### 4.3 API views

Interactive actions belong in `api/views_api.py` for that app.

Keep API handlers responsible for:

- input parsing
- validation
- service/helper invocation
- JSON response formatting

Prefer the existing response pattern:

- `SuccessResponse(...)`
- `ErrorResponse(...)`
- `_api_response(...)` where already used

Return payloads consistent with the current UI expectations:

- `success`
- `message`
- `data`
- extra metadata only when needed

### 4.4 Services and business logic

If a feature has non-trivial rules, move them into:

- `financeApp/services.py`
- `managementApp/services/...`
- a focused helper in the owning app

Use services for:

- multi-step create/update operations
- cross-model sync
- posting/reversal logic
- generated numbering
- lock/approval workflows
- session rollover/import behavior

Avoid large business-rule blocks directly inside view functions.

### 4.5 Transactions and validation

When multiple records change together:

- use `transaction.atomic`
- validate early
- fail with explicit user-facing errors

Use Django validation or helper validation functions instead of silent failure.

## 5. Frontend Implementation Pattern

### 5.1 Layout inheritance

Follow the existing template chain:

- base shell: `homeApp/templates/homeApp/base.html`
- admin shell: `managementApp/templates/managementApp/index.html`
- teacher/student pages follow their own role shell patterns

For admin/owner pages, new templates should usually:

```django
{% extends 'managementApp/index.html' %}
```

Do not build isolated standalone HTML pages for management features.

### 5.2 UI stack

The current app standard is:

- server-rendered Django templates
- Fomantic UI / Semantic-style markup
- jQuery for interactions
- DataTables for listing screens
- Chart.js for dashboards and summaries

New screens should fit this stack. Do not introduce a separate SPA framework for one page.

### 5.3 Shared base helpers

Before writing custom JavaScript, check whether the base shell already provides the behavior.

Shared patterns already exist for:

- loading button state
- required field errors
- notifications/toasts
- DataTable defaults

Important rule:

- reuse global helpers from `homeApp/templates/homeApp/base.html`
- do not redefine the same global helper names inside new templates

### 5.4 Form and button standard

Use this as the default standard for add/edit/search forms across management, student, and teacher templates.

#### Action buttons

- `Search`: `ui mini green button` with search icon
- `Clear`: `ui mini red button` with clear/times icon
- `Add/Save`: `id="saveBtn"` and class `saveBtn ui mini green button`
- `Update`: `id="saveBtnUpdate"` and class `saveBtnUpdate ui mini orange button`
- `Saving...`: `id="saveBtnLoad"` and class `saveBtnLoad ui right labeled icon button green`

#### Save/update/loading behavior

Use the shared loading helpers:

- `showLoading()`
- `showUpdateLoading()`
- `hideLoading()`

Rules:

- never manually show all save/update/load buttons together
- on validation error call `hideLoading()` immediately
- update mode should show the update button, not the add button

#### Validation behavior

- mark required wrappers with `.field.required`
- on save/search validate required inputs before request
- add `.error` to missing fields
- call `requiredFieldError()` when invalid
- remove `.error` after user correction

#### Clear/reset behavior

Every interactive form should have a reset helper such as `clearAndReset()`.

That reset should:

- clear text/select/textarea/file inputs
- clear semantic dropdowns with `.dropdown('clear')`
- remove field errors
- clear or hide result sections
- reset DataTables if the page owns one
- restore dependent dropdown placeholders if applicable

### 5.5 Listing screens

For list pages:

- use DataTables
- provide search/filter/reset flow
- keep edit/delete actions in a compact action column
- use the same small Fomantic button style already used in management pages

If the page is already server-rendered with AJAX-filled tables, keep that pattern.

### 5.6 Visual language

The current visual direction is:

- rounded cards/segments
- soft gradients for highlight sections
- compact action buttons
- dashboard cards and summary chips
- CSS variables from `base.html`

When adding new custom CSS:

- prefer existing CSS variables such as `--app-bg`, `--app-text`, `--app-muted`, `--app-brand`
- keep radius/shadow scale visually aligned with existing pages
- make sure dark theme variables do not break the page

## 6. Routing Pattern

Follow the existing URL strategy:

- page routes in app `urls.py`
- API routes in `api/urls_api.py`
- namespaced includes from project `schoolStackPro/urls.py`

For new functionality:

- create a page route for the screen
- create separate API routes for async operations
- keep route names descriptive and consistent with existing snake_case naming

## 7. Query Pattern

Querysets should usually:

- filter by `isDeleted=False`
- filter by current `sessionID`
- filter by current `schoolID` where relevant
- use `select_related` for foreign keys accessed in lists/details
- use `prefetch_related` where repeated child access would otherwise cause N+1 queries

For dashboards and reports:

- aggregate in the database
- serialize chart labels/values with `json.dumps`

## 8. Feature Placement Rules

Use this quick mapping:

- school/session/class/student/teacher/attendance/exam/marks/parents/events: `managementApp`
- finance receipts, ledgers, payroll, controls, numbering, accounting logic: `financeApp` plus `managementApp` screens
- shared auth/profile/license/branding/shell: `homeApp`
- teacher-only workflows: `teacherApp`
- student-only workflows: `studentApp`

If a feature is primarily an admin page over finance data, UI can live in `managementApp` while business rules stay in `financeApp/services.py`.

## 9. Recommended New Feature Template

When adding a new feature, prefer this checklist:

1. Add/extend model fields in the owning app.
2. Add migrations.
3. Add or extend service/helper logic for business rules.
4. Add page view in `views.py`.
5. Add API handlers in `api/views_api.py` if the page is interactive.
6. Add routes in both `urls.py` and `api/urls_api.py` if needed.
7. Add template extending the correct role shell.
8. Reuse shared button/loading/validation patterns.
9. Filter everything by school/session/deleted status.
10. Add tests for the main happy path and at least one failure path.

## 10. Anti-Patterns To Avoid

Do not:

- create standalone page styles that ignore `base.html`
- hardcode business rules in templates
- skip session/school filters
- hard delete records that follow the app's soft-delete model
- return ad-hoc JSON shapes when shared response helpers already exist
- duplicate helper functions already present in shared files
- mix teacher/student/admin concerns into one unclear module
- add a new frontend framework for isolated screens

## 11. If You Are Adding A New Management Page

Default expectation:

- route in `managementApp/urls.py`
- page view in `managementApp/views.py`
- async handlers in `managementApp/api/views_api.py`
- template under `managementApp/templates/managementApp/...`
- template extends `managementApp/index.html`
- DataTable for lists
- Fomantic form styles
- shared loading and validation helpers

## 12. If You Are Adding A New Teacher Or Student Page

Follow the same principles, but keep role-specific logic inside:

- `teacherApp/...`
- `studentApp/...`

Use the bootstrap helpers already present in those apps to resolve session context before adding new custom logic.

## 13. Final Rule

Any new feature should look like it belonged to this codebase before it was added.

When in doubt:

- copy the nearest existing feature pattern
- preserve route naming and template inheritance
- keep business logic out of templates
- reuse shared UI helpers
- respect role, session, school, and soft-delete boundaries
