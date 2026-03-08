# Design Pattern

## Button Standard (Use In Every New Template)

Use this as the default standard for all add/edit/search forms in management/student/teacher templates.

### 1) Action Buttons

- `Search` button:
  - class: `ui mini green button`
  - icon: `<i class="icon search"></i>`
- `Clear` button:
  - class: `ui mini red button`
  - icon: `<i class="icon times circle"></i>`
- `Add/Save` button:
  - id/class: `id="saveBtn"` and `class="saveBtn ui mini green button"`
- `Update` button:
  - id/class: `id="saveBtnUpdate"` and `class="saveBtnUpdate ui mini orange button"`
  - default hidden
- `Saving...` loading button:
  - id/class: `id="saveBtnLoad"` and `class="saveBtnLoad ui right labeled icon button green"`
  - default hidden

### 2) Required JS Behavior

- Use global loading helpers from `homeApp/templates/homeApp/base.html`:
  - `showLoading()`
  - `showUpdateLoading()`
  - `hideLoading()`
- Do not manually show all save/update/load buttons together.
- On validation error, call `hideLoading()` immediately.

### 3) Clear/Reset Rules

Every form must have a local clear function (example: `clearAndReset()`):

- clear input/select/textarea/file values
- clear semantic dropdown values (`.dropdown('clear')`)
- remove validation errors: `$('.ui.form .field').removeClass('error')`
- hide results section/table container
- clear DataTable rows if initialized (`table.clear().draw()`)
- restore placeholders/options for dependent dropdowns (student/exam/subject etc.)
- keep hidden edit IDs only where required (edit forms)

### 4) Validation Rules

- mark required wrapper as `.field.required` in template
- on search/save click:
  - validate required fields
  - add `.error` class on missing fields
  - prevent request when invalid
  - show `requiredFieldError()`
- remove `.error` on field change

### 5) Standard Button Markup Snippet

```html
<button type="button" class="ui mini green button" onclick="searchData()">
  <i class="icon search"></i>
  Search
</button>
<button type="button" class="ui mini red button" onclick="clearAndReset()">
  <i class="icon times circle"></i>
  Clear
</button>
```

### 6) Standard Save/Update/Loading Snippet

```html
<button type="button" id="saveBtn" class="ui mini green button saveBtn" onclick="addData()">
  <i class="icon plus square"></i>
  Add
</button>
<button type="button" id="saveBtnUpdate" class="ui mini orange button saveBtnUpdate" style="display:none;" onclick="editData()">
  <i class="icon redo"></i>
  Update
</button>
<button type="button" id="saveBtnLoad" class="ui right labeled icon button green saveBtnLoad" style="display:none;">
  Saving ...
  <i class="checkmark icon"></i>
</button>
```
