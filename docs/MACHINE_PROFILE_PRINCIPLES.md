# Machine Profile Principles

## Core Direction

- Prefer simple and easy-to-use configuration over fine-grained taxonomy.
- For app element selection, default to dynamic loading by element `kind`.
- Use `plane` only when the physics really requires it, such as separating horizontal and vertical correctors.
- Treat `preset` as optional convenience for default values, not as the primary mechanism for defining selectable elements.
- When a selectable candidate set can be derived from machine-native element types, do not duplicate that list in app workflow JSON.

## Preferred Order Of Design Choices

1. Solve the problem with `kind`.
2. If needed, add `plane`.
3. If still needed, add a small number of simple tags.
4. Only introduce finer app-specific roles when the simpler choices are proven insufficient.

## What To Avoid

- Avoid building large per-app or per-workflow role systems by default.
- Avoid making a machine config hard to read just to encode every usage scenario.
- Avoid coupling app usability to many hand-maintained preset lists when the candidates can be derived from machine-native element types.
