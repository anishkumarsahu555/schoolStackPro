# Certificate Studio UX Blueprint

This document defines the next-stage UX and architecture for `certificateApp`.

Goal:

- make the certificate module feel modern and simple
- keep it highly customizable without overwhelming school admins
- move toward a Canva-like editing experience
- stay aligned with the current Django + server-rendered app structure

This is a design and implementation blueprint, not just a visual wishlist.

## 1. Product Direction

The certificate module should evolve into a `Certificate Studio`.

It should feel like:

- a clean template library
- a focused editor canvas
- a smart content generator
- a reliable print/PDF output tool

It should not feel like:

- a long settings form
- a developer-only layout editor
- a disconnected set of preview pages

The right mental model is:

1. pick a certificate type
2. choose a design
3. customize layout visually
4. preview with real student/teacher data
5. generate or print

## 2. UX Principles

### 2.1 Progressive complexity

Default users should be able to make a good certificate in minutes.

So the UI should have 3 levels:

- `Quick`: choose preset + edit text + generate
- `Customize`: colors, fonts, spacing, logo, seal, sections
- `Advanced`: move blocks, use background artwork, image-overlay fields, custom sections

Do not show all controls at once.

### 2.2 Visual before technical

Admins think in terms like:

- make the name bigger
- move the signature lower
- add school logo
- use a more formal look

They do not think in:

- margin-left mm
- titleAlignment
- borderStyle enum
- overlaySchema JSON

So the editor should present:

- canvas interactions first
- inspector controls second
- raw technical fields only as fallback

### 2.3 One source of truth

Today the system has too many approximations:

- design library preview
- design detail preview
- create-design preview
- generator live stage
- final certificate render
- PDF render

All of these should move toward one shared certificate rendering system.

The desired hierarchy is:

- `certificate renderer core`
- `preview adapters`
- `pdf/print adapters`

Not separate handcrafted preview HTML for each page.

### 2.4 Preset-led customization

Full freedom is useful, but blank-canvas editing is hard for school admins.

So the best UX is:

- start from a strong preset
- duplicate preset
- customize visually

This is how Canva feels simple while still being flexible.

## 3. Ideal User Journey

### 3.1 Template Library

`/certificates/designs/`

This page should become a richer design gallery with:

- category filters
- style filters
- paper filters
- search
- template preview cards
- `Use Template`
- `Duplicate`
- `Customize`

Suggested sections:

- `Formal`
- `Academic`
- `Celebration`
- `Forms`
- `Image-Based`
- `My Custom Designs`

### 3.2 Design Studio

`/certificates/designs/create/`

This should become the main Canva-like editor.

Primary layout:

- left sidebar: layers and block list
- center: live certificate canvas
- right sidebar: inspector controls
- top toolbar: undo, redo, duplicate, preview mode, save

Tabs inside studio:

- `Layout`
- `Style`
- `Content`
- `Assets`
- `Advanced`

### 3.3 Generator

`/certificates/generate/`

This page should become a simplified issue flow, not the main design editor.

It should focus on:

- certificate type
- design selection
- recipient selection
- smart text merge
- final preview
- generate / print / PDF

The generator should reuse the same renderer as the studio, but with fewer controls.

## 4. Target Feature Set

### 4.1 Smart design system

Each design should support:

- typography pair
- accent color
- text color
- surface/background tone
- spacing profile
- border/frame profile
- header layout
- footer layout
- logo/seal/signature visibility

This means design editing can be partly token-based instead of totally manual.

Suggested token groups:

- `theme`
- `typography`
- `spacing`
- `header`
- `body`
- `footer`
- `assets`

### 4.2 Block-based layout

The editor should treat certificate sections as blocks:

- top line
- school header
- title
- subtitle
- recipient name
- recipient subtitle
- body
- meta strip
- footer note
- seal
- signature

Each block should support:

- show/hide
- move up/down
- align
- width
- spacing
- typography overrides

This is better than hardcoding one layout per design page.

### 4.3 Image-overlay mode

This already exists and should be improved instead of replaced.

Needed upgrades:

- visible layer list
- select multiple fields
- duplicate field
- lock field
- snap guides
- canvas zoom presets
- inline rename of overlay field
- drag handles for width/height
- optional rotate
- field presets for common placeholders

### 4.4 Hand-fill form mode

This should become a first-class design mode.

Useful for schools that want:

- printed blank forms
- partial manual writing
- office counter issue flow

Form mode should support:

- line row presets
- dotted/solid underline style
- signature area presets
- date field presets
- optional school branding at top

### 4.5 Smart content

The generator should support:

- placeholder merge with real school/student/teacher data
- smart fallback values
- tone presets
- suggested body copy by certificate type
- auto-preview of recipient class/designation

Longer-term smart features:

- AI-assisted copy rewrite
- “make it more formal”
- “shorten for A5”
- “convert to hand-fill form”

## 5. Recommended UI Model

### 5.1 Top toolbar

Studio top bar:

- back
- design name
- save
- save as copy
- undo
- redo
- preview zoom
- desktop / print / pdf mode

### 5.2 Left sidebar: Layers

Show ordered blocks:

- Header
- Logo
- Title
- Subtitle
- Recipient
- Body
- Meta
- Footer
- Seal
- Signature

Actions:

- show/hide
- lock
- duplicate
- reorder

For image-overlay designs, this becomes a real layer panel.

### 5.3 Right sidebar: Inspector

When a block is selected, show only relevant controls.

Example for recipient block:

- font family
- font size
- font weight
- color
- alignment
- width
- line height
- spacing above/below

Example for paper:

- page size
- orientation
- margins
- bleed-safe warning

### 5.4 Bottom or floating quick actions

Provide friendly shortcuts:

- `Formalize`
- `Center Content`
- `Use School Colors`
- `Add Logo`
- `Show Seal`
- `Use A4`
- `Make Printable Form`

These should trigger a group of changes, not one field.

## 6. Recommended Architecture

### 6.1 Keep the current backend shape

Keep:

- `models.py`
- `views.py`
- `api/views_api.py`
- `services.py`

Do not introduce a separate SPA backend.

### 6.2 Add design config layers

Current `CertificateDesign` is good, but it needs a richer structured config.

Recommended new JSON fields:

- `themeConfig`
- `layoutConfig`
- `visibilityConfig`
- `assetConfig`

Example intent:

- `themeConfig`: fonts, color system, shadow level, visual family
- `layoutConfig`: widths, spacing, block positions, variant rules
- `visibilityConfig`: show/hide block toggles
- `assetConfig`: logo position, seal position, signature position

This is better than overloading everything into `customCss`.

### 6.3 Add editor view models in API

Generator/design APIs should return richer UI payloads:

- design tokens
- block config
- preview-ready recipient data
- available placeholder schema
- block presets

That reduces brittle client-side reconstruction.

### 6.4 Build a shared renderer contract

The shared renderer should consume:

- `design`
- `design tokens/config`
- `content payload`
- `render mode`

Outputs:

- preview HTML
- print HTML
- PDF HTML

This should drive:

- design detail
- design studio preview
- generator live stage
- issue preview
- issue print
- issue PDF

## 7. Smart Design Modes

Recommended explicit modes:

- `preset_html`
- `custom_html`
- `image_overlay`
- `form_sheet`

These are clearer than only `html` and `image_overlay`.

## 8. Phased Implementation Plan

### Phase 1: Make current flows coherent

- unify preview behavior across library, detail, studio, generator, issue preview
- fix design selection/live stage reliability
- move preview rendering closer to shared certificate renderer
- add better empty states and loading states
- reduce redundant controls

### Phase 2: Introduce real studio layout

- redesign create page as a 3-panel studio
- layer panel
- inspector panel
- toolbar
- block selection
- block visibility toggles

### Phase 3: Design tokens and smart presets

- add `themeConfig`
- add `layoutConfig`
- preset duplication flow
- quick actions like `Formalize`, `Compact`, `Prize Day`

### Phase 4: Better image-overlay editing

- multi-select
- grouping
- resize handles
- rotate
- better snapping
- placeholder presets
- layer rename

### Phase 5: Smart generator

- real recipient-aware preview merge
- content suggestions by certificate type
- better preview fidelity to final render
- one-click duplicate from existing certificate issue

## 9. Suggested Immediate Improvements

These will give the biggest UX lift quickly:

1. Turn `create_design` into a true studio layout.
2. Use one shared preview renderer between studio and generator.
3. Add block-based controls for HTML designs.
4. Add design duplication from library/detail.
5. Add style presets with quick actions.
6. Make generator simpler and more issue-focused.
7. Keep paper styling fixed across dark mode.

## 10. UX Copy Direction

The module should use calmer, clearer product language.

Prefer:

- `Template Library`
- `Design Studio`
- `Live Preview`
- `Use Template`
- `Duplicate Template`
- `Quick Customize`
- `Advanced Controls`

Avoid overly technical labels as defaults:

- `overlaySchema`
- `borderStyle`
- `titleAlignment`
- `customCss`

Those can exist in advanced mode only.

## 11. What “Canva-like” Means Here

For this app, Canva-like should mean:

- visual editing
- presets first
- drag/select/inspect workflow
- easy duplication
- immediate feedback
- polished canvas and controls

It should not mean:

- a fully separate frontend stack
- a huge freeform graphics tool
- no structure or no templates

The school workflow still benefits from guided structure.

## 12. Final Recommendation

Best direction for this repo:

- keep Django rendering
- keep Chromium PDF
- add a richer design-config model
- build a layered `Certificate Studio`
- make generator leaner and smarter
- make the editor more visual and less form-heavy

The winning UX is:

- `Canva-like editing`
- with `school-friendly simplicity`
- on top of `structured certificate templates`

