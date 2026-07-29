# Legacy multi-section UI (archived Phase 2)

Removed from the live product. Moon Scanner now serves only:

- `static/index.html`
- `static/js/moon.js`
- `static/css/moon.css`

These files were the old trenches / runners / lottery UI (`app.js` + `style.css`).

Restore only if you need to debug cloud clients still calling legacy APIs.
Do not re-wire them into `index.html` without a product decision.
