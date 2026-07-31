# UI/UX Developer intake

Dimensions worth drawing out of a casual UI/UX request:

- **Screen/component** in question
- **Design reference** — Figma link, screenshot, or description if no file exists
- **States to support** — default, hover, loading, error, empty, disabled
- **Responsive behavior** — breakpoints that matter
- **Accessibility notes** — contrast, keyboard nav, screen-reader labels
- **Interaction/animation details**, if any beyond static layout

## Example

**Input:** "کارت‌های محصول توی موبایل بهم ریخته‌ست، پدینگش درست نیست و تصویر کراپ نشده درست"

**Plan:**
```
## Plan: Fix product card layout on mobile
**Goal:** Product cards render correctly on mobile widths (image crop, spacing).
**Scope:**
- Fix padding/spacing on the product card component at mobile breakpoints
- Correct image crop/aspect ratio so images don't distort or overflow
**Acceptance criteria:**
- Cards look correct at common mobile widths (e.g. 375px, 414px)
- Image aspect ratio is consistent across cards regardless of source image dimensions
**Out of scope:**
- Desktop/tablet layout (already correct, per report)
**Open questions:**
- Is there a Figma reference for the intended mobile spacing, or should the agent match the desktop card's proportions?
```
