# Product and UI Design

Baseline: 2026-11-17. This file preserves the conventions in `agent.md` and `design_pattern.md` and identifies the current shared implementation. Requirements below guide new work; they do not imply every existing screen already complies.

## Experience and information architecture

SchoolsStack serves school owners/admins, delegated staff, teachers, and students. Keep academic-session context visible, show only permitted navigation/actions, and keep common operations compact. Finance and operational screens belong within the existing management experience where applicable. Role portals must retain their own navigation and workflows.

A page should make its purpose, selected school/session context, primary action, filters, results, and status understandable without exposing implementation details. Distinguish an empty dataset from filtered-out results, denied access, an expired license, and a failed request.

## Shell and template contract

- Base: `homeApp/templates/homeApp/base.html`.
- Management shell: `managementApp/templates/managementApp/index.html`.
- Teacher/student pages: inspect the nearest page in the matching app and preserve its role-shell inheritance.
- Base blocks include `title`, `css`, `sidebar`, `page_title`, `page_subtitle`, `page_actions`, `body`, and `js`.
- Place feature templates under their owning app's template namespace.
- Use namespaced URL reversal and Django escaping. Do not interpolate untrusted values into raw HTML/JavaScript.

Typical management structure (choose additional blocks supported by the inherited shell):

```django
{% extends 'managementApp/index.html' %}
{% block page_title %}Feature title{% endblock %}
{% block page_subtitle %}A short description of the workflow{% endblock %}
{% block body %}
  <section class="ui segment">
    <!-- Filters, form, or results using existing components. -->
  </section>
{% endblock %}
```

## Existing visual system

The shell loads jQuery 3.6.1 and Fomantic UI 2.9.0 and provides DataTables integrations. Keep the current server-rendered and AJAX interaction model. Charts should follow the existing dashboard patterns; do not add an isolated frontend framework.

| Token | Current light value / purpose |
| --- | --- |
| `--app-bg` | `#f5f7fa`, page background |
| `--app-text` | `#1f2937`, primary text |
| `--app-surface` | `#ffffff`, cards and panels |
| `--app-border` | `#dfe3ea`, boundaries |
| `--app-muted` | `#5f6368`, secondary text |
| `--app-brand` | `#1a73e8`, brand/action accent |
| `--app-hover`, `--app-active` | Hover and selected states |
| `--app-radius-sm/md/lg` | 10 / 14 / 18 px |
| `--app-shadow-sm/md` | Shared elevation; use variables directly |

Dark-theme overrides exist in the base. Use semantic variables rather than copying light colors into feature CSS. Preserve rounded segments/cards, compact controls, summary chips, and restrained highlight gradients. Scope new CSS to the page to avoid altering unrelated templates.

## Action and form standard

| Action | Markup convention |
| --- | --- |
| Search | `ui mini green button`, search icon |
| Clear | `ui mini red button`, times-circle icon |
| Add/Save | `id="saveBtn"`, `saveBtn ui mini green button` |
| Update | `id="saveBtnUpdate"`, `saveBtnUpdate ui mini orange button`, initially hidden |
| Saving | `id="saveBtnLoad"`, `saveBtnLoad ui right labeled icon button green`, initially hidden |

Use explicit labels; icons supplement text. Button color alone must not communicate meaning. Keep IDs unique on a page; for multiple form instances inspect the shared helpers' scoping support rather than duplicating IDs.

Reuse `showLoading()`, `showUpdateLoading()`, `hideLoading()`, and `requiredFieldError()` from the base shell. The base currently contains multiple loading-helper definitions; inspect the effective implementation when diagnosing behavior (T-010). Do not add another global override in a feature page.

### Form lifecycle

1. Initialize dropdowns, mode, and dependent fields.
2. Validate required wrappers marked `.field.required`; flag missing values with `.error`.
3. If invalid, prevent submission, call `requiredFieldError()`, and restore loading state immediately.
4. Show the correct add/update loading state while a request is active; prevent duplicate writes.
5. Handle successful completion, validation errors, permission errors, network failure, and unexpected server errors separately.
6. Restore controls on every completion path; refresh affected tables and display a clear result.
7. Remove field errors when corrected and retain user input when recovery is possible.

Client validation improves usability; the server must still validate data, scope, and permissions. Use the existing CSRF submission pattern for modifying requests.

### Reset behavior

Provide a local helper such as `clearAndReset()` that clears text/select/textarea/file controls, clears Fomantic dropdowns via `.dropdown('clear')`, removes field errors, hides stale result sections, and resets dependent options/placeholders. Reset the page's table using the appropriate client-side or server-side DataTables mechanism. Preserve hidden edit IDs only when the current workflow requires them; otherwise leave edit mode fully.

## Lists, dashboards, and reports

Use DataTables for comparable management listing screens. Keep filter/search/reset interactions consistent, show active filters, and place permitted edit/delete controls in a compact action column. Inspect the actual endpoint's pagination/filter/response contract before changing table initialization. Use database aggregation for dashboard values and safely serialized chart data.

Reports, certificates, and ID cards have distinct print/export needs. Check page breaks, long names, logos, missing images, school/session labels, and generated identifiers. Treat preview, generated artifact, and saved/issued state as separate steps where the domain requires it.

## Responsive and accessible behavior

For changed screens, check desktop and narrow mobile layouts, light/dark themes, long text, empty results, and loading/error states. Preserve the teacher/student mobile shell styles. Tables may need horizontal scrolling; dialogs and primary actions must remain reachable.

Use labeled inputs, sensible keyboard focus order, visible focus, accessible names for icon-only actions, and readable contrast. Associate errors with the relevant field. Return focus sensibly after dialogs. Do not hide essential state only in hover content or color. These are acceptance criteria for new UI, not a claim of completed accessibility auditing.

## Permission-aware interaction

Hide controls the user cannot perform, but test direct API access independently. Read-only roles must not see misleading write buttons. If access changes during a session, handle the server's denial without leaving a permanent spinner. License restrictions need explanatory UI distinct from insufficient role permissions.

## UI completion evidence

Record the changed route and role, relevant school/session fixture, viewports/themes checked, happy/error/empty states checked, and any remaining limitation. Screenshots or browser checks should use synthetic data. Update this file when a shared component or interaction convention changes; page-specific findings belong in TASKS.md or MEMORY.md.
