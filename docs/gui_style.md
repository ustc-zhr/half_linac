# GUI Style Notes

This note captures the current operator GUI direction used by the Control Room app in `src/apps/launcher/main.py` and `src/virtual_machine/common/mainVM.py`.

## Core Pattern
- Use a compact header panel with one main title, primary actions on the right, and no explanatory subtitle text.
- Put runtime state in a single horizontal status strip directly below the header.
- Keep the main workspace below the status strip focused on actions and settings only.
- Hide always-visible activity logs unless a workflow specifically needs them; prefer status bar messages and internal log buffers.

## Status Strip
- Use short all-caps labels such as `MODE`, `ACTIVE`, `CORE`, `TOOLS`, `CONNECTION`, `CONFIG`, `CURRENT`.
- Each item uses a straight left accent bar, not rounded brackets or decorative shapes.
- Status text should sit on a clean background with no extra text box outline.
- Use three tones only: neutral/subtle, warning, active/success.

## Theme Direction
- Keep one dark theme and one light theme with the same structure and spacing.
- Dark theme should feel technical and restrained rather than saturated.
- Light theme should stay warm-neutral and low-glare rather than pure blue-white.
- Theme switching should remain a small icon action in the top-right corner.

## Control Groups
- Use clear group titles and avoid extra helper text under the titles.
- Keep group titles slightly larger than body text so sections can be scanned quickly.
- Prefer vertical button stacks inside groups unless width clearly supports a tighter grid.
- Avoid category-by-category rainbow coloring. Default buttons should share one calm base palette, with running state used as the main emphasis.

## Spacing
- Keep header action controls aligned to the same fixed height.
- Default windows should avoid large decorative empty zones below the main control groups.
- Use compact but readable vertical spacing; reduce stretch-first layouts unless extra whitespace is intentional.
